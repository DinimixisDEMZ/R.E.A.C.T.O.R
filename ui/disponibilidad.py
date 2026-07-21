"""
Pestaña de Disponibilidad: Verificación de compatibilidad BPF de schedulers.
"""

import hashlib
import json
import os
import re
import shutil
import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib

from utils.helpers import log, limpiar_texto
from core.operations import OperationCancelled
from core.scx import ScxState
from core.database import (
    cargar_compatibilidad,
    limpiar_compatibilidad,
    obtener_historial_compatibilidad,
    reemplazar_compatibilidad,
)


_ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_ERRORES_BPF = (
    "failed to load bpf",
    "failed to load bpf object",
    "failed to load object",
    "failed to load program",
    "bpf program load failed",
    "bpf prog load failed",
    "libbpf: error",
    "libbpf: failed",
    "verifier error",
    "verifier rejected",
    "bpf verifier",
    "verification failed",
    "failed to attach",
    "failed to register",
    "relocation failed",
    "unknown kfunc",
    "unsupported kfunc",
    "operation not permitted",
    "permission denied",
    "invalid argument",
    "no such file or directory",
    "segmentation fault",
    "traceback (most recent call last)",
)
_PATRONES_EVIDENCIA_ARRANQUE = (
    re.compile(
        r"(?:(?:bpf|sched_ext|scx)\s+)?scheduler"
        r"(?:\s+['\"\w.-]+)?\s+(?:has\s+)?started"
        r"(?:\s+successfully)?[.!]?"
    ),
    re.compile(
        r"started\s+(?:the\s+)?(?:(?:bpf|sched_ext|scx)\s+)?scheduler"
        r"(?:\s+successfully)?[.!]?"
    ),
    re.compile(
        r"(?:(?:bpf|sched_ext|scx)\s+)?scheduler\s+(?:is\s+)?running"
        r"(?:\s+successfully)?[.!]?"
    ),
    re.compile(r"(?:struct_ops\s+registered|registered\s+struct_ops)(?:\s+successfully)?[.!]?"),
    re.compile(r"sched_ext_ops\s+enabled(?:\s+successfully)?[.!]?"),
    re.compile(r"(?:attached\s+sched_ext|sched_ext\s+attached)(?:\s+successfully)?[.!]?"),
    re.compile(r"press\s+ctrl-c\s+to\s+(?:exit|stop|shutdown).*"),
    re.compile(r"switching\s+all\s+tasks(?:\s+to\s+.+)?"),
    re.compile(r"active"),
)
_FORMATO_CONTEXTO_COMPATIBILIDAD = "reactor.compatibility-context"
_VERSION_CONTEXTO_COMPATIBILIDAD = 1
_BINARIO_NO_CAPTURADO = object()


def _detalle_salida_bpf(stdout, stderr, fallback):
    texto = _ANSI_RE.sub("", f"{stdout or ''}\n{stderr or ''}")
    lineas = [linea.strip() for linea in texto.splitlines() if linea.strip()]
    return (lineas[-1] if lineas else fallback)[:500]


def _hay_evidencia_arranque(normalizado):
    for linea in normalizado.splitlines():
        linea = linea.strip()
        if not linea:
            continue
        linea = re.sub(r"^(?:\[[^\]]+\]\s*)+", "", linea)
        linea = re.sub(
            r"^(?:info|notice|status)\b\s*[:\-]?\s*",
            "",
            linea,
        )
        if any(patron.fullmatch(linea) for patron in _PATRONES_EVIDENCIA_ARRANQUE):
            return True
    return False


def _clasificar_salida_bpf(returncode, stdout="", stderr=""):
    """Clasifica una ejecución BPF exigiendo evidencia positiva de arranque."""
    try:
        codigo = int(returncode)
    except (TypeError, ValueError, OverflowError):
        codigo = -1
    combinado = f"{stdout or ''}\n{stderr or ''}"
    normalizado = _ANSI_RE.sub("", combinado).casefold()
    detalle = _detalle_salida_bpf(
        stdout,
        stderr,
        f"Error de salida ({codigo})",
    )

    error_explicito = any(marcador in normalizado for marcador in _ERRORES_BPF)
    error_de_linea = re.search(
        r"(?im)^\s*(?:\[[^\]]*(?:error|fatal|panic)[^\]]*\]"
        r"|(?:error|fatal|panic)(?:\s|:|$))",
        normalizado,
    )
    if error_explicito or error_de_linea:
        return False, detalle, False

    evidencia_arranque = _hay_evidencia_arranque(normalizado)

    if codigo in {124, 137}:
        if evidencia_arranque:
            return True, "Disponible (Residente)", False
        return False, "Timeout sin evidencia de arranque o residencia", False

    if codigo == 0:
        if evidencia_arranque:
            return True, "Disponible (Arranque verificado)", False
        return False, "Finalizó sin evidencia de arranque o residencia", False

    if evidencia_arranque:
        return False, f"Arrancó pero terminó con error ({codigo}): {detalle}", False
    return False, detalle, False


def _compatibilidad_dev_determinista(nombre):
    """Simula compatibilidad estable entre procesos y versiones de Python."""
    datos = str(nombre).casefold().encode("utf-8", errors="replace")
    valor = int.from_bytes(hashlib.sha256(datos).digest()[:8], "big") % 100
    return valor < 75


def _nombre_binario_scheduler(nombre):
    nombre = str(nombre or "").strip()
    return nombre if nombre.startswith("scx_") else f"scx_{nombre}"


def _identidad_binario_scheduler(nombre):
    nombre = str(nombre)
    nombre_binario = _nombre_binario_scheduler(nombre)
    identidad = {
        "scheduler": nombre,
        "name": nombre_binario,
        "realpath": None,
        "size": None,
        "mtime_ns": None,
        "missing": True,
    }
    try:
        resuelto = shutil.which(nombre_binario)
        if resuelto is None:
            return identidad
        ruta_real = os.path.realpath(os.fsdecode(os.fspath(resuelto)))
        identidad["realpath"] = ruta_real
        metadata = os.stat(ruta_real)
        identidad.update(
            size=int(metadata.st_size),
            mtime_ns=int(metadata.st_mtime_ns),
            missing=False,
        )
    except (OSError, TypeError, ValueError, OverflowError):
        pass
    return identidad


def _capturar_contexto_compatibilidad(win, nombres):
    versiones = getattr(win, "versiones", {}) or {}
    schedulers = sorted(
        _snapshot_nombres(win, nombres),
        key=lambda nombre: (nombre.casefold(), nombre),
    )
    identidades = [
        _identidad_binario_scheduler(nombre) for nombre in schedulers
    ]
    contexto = {
        "format": _FORMATO_CONTEXTO_COMPATIBILIDAD,
        "version": _VERSION_CONTEXTO_COMPATIBILIDAD,
        "development_mode": bool(getattr(win, "modo_desarrollador", False)),
        "versions": {
            "kernel": (
                None
                if versiones.get("kernel") is None
                else str(versiones.get("kernel"))
            ),
            "scxctl": (
                None
                if versiones.get("scxctl") is None
                else str(versiones.get("scxctl"))
            ),
        },
        "schedulers": identidades,
    }
    serializado = json.dumps(
        contexto,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    clave = hashlib.sha256(serializado).hexdigest()
    binarios = {
        identidad["scheduler"]: (
            None if identidad["missing"] else identidad["realpath"]
        )
        for identidad in identidades
    }
    return clave, binarios


def contexto_compatibilidad_actual(win, nombres):
    """Devuelve la identidad estable del entorno de compatibilidad actual."""
    contexto, _binarios = _capturar_contexto_compatibilidad(win, nombres)
    return contexto


def _programar_ui(win, callback, *args):
    programar = getattr(win, "ejecutar_en_ui", None)
    if callable(programar):
        return programar(callback, *args)
    return GLib.idle_add(callback, *args)


def _mostrar_toast(win, mensaje, *, alta=False):
    mostrar = getattr(win, "mostrar_toast", None)
    if callable(mostrar):
        mostrar(mensaje, alta=alta)
        return
    toast = Adw.Toast.new(str(mensaje))
    if alta:
        toast.set_priority(Adw.ToastPriority.HIGH)
    win.toast_overlay.add_toast(toast)


def _mostrar_operacion_ocupada(win):
    mostrar = getattr(win, "mostrar_operacion_ocupada", None)
    if callable(mostrar):
        mostrar()
        return
    state = win.operaciones.state
    nombre = state.name if state is not None else "otra operación"
    _mostrar_toast(
        win,
        f"Operación ocupada: '{nombre}' sigue en curso.",
        alta=True,
    )


def _mensaje_corto(msg, compatible):
    if not msg:
        return "OK" if compatible else "Sin verificar"
    ml = msg.lower()
    if compatible:
        if "verificado" in ml: return "Verificado"
        if "residente" in ml: return "Residente"
        if "shutdown" in ml: return "Shutdown"
        if "simulado" in ml: return "Simulado"
        return "OK"
    if "failed to load bpf" in ml or "bpf" in ml: return "BPF failed"
    if "no such file" in ml: return "BPF missing"
    if "error de salida" in ml:
        idx = msg.find("(")
        return "Error " + msg[idx:idx+6].strip() if idx >= 0 else "Error"
    if "error" in ml: return "Error"
    return msg[:10] + "…" if len(msg) > 10 else msg


def _simplificar_kernel(kv):
    return kv.split("-")[0] if kv else kv


def _fade_in(widget, duration_ms=200):
    """Anima la opacidad de un widget de 0 a 1."""
    widget.set_opacity(0.0)
    steps = 10
    interval = max(duration_ms // steps, 10)
    state = {"step": 0}

    def _tick():
        state["step"] += 1
        widget.set_opacity(state["step"] / steps)
        return state["step"] < steps

    GLib.timeout_add(interval, _tick)


def _nombres_desde_modelo(win):
    modelo = getattr(win, "modelo_schedulers", None)
    if modelo is None:
        return ()
    get_n_items = getattr(modelo, "get_n_items", None)
    if not callable(get_n_items):
        return ()

    nombres = []
    get_string = getattr(modelo, "get_string", None)
    get_item = getattr(modelo, "get_item", None)
    for indice in range(get_n_items()):
        if callable(get_string):
            nombre = get_string(indice)
        elif callable(get_item):
            item = get_item(indice)
            item_get_string = getattr(item, "get_string", None)
            nombre = item_get_string() if callable(item_get_string) else item
        else:
            break
        nombres.append(nombre)
    return tuple(nombres)


def _snapshot_nombres(win, nombres=None):
    valores = _nombres_desde_modelo(win) if nombres is None else nombres
    if isinstance(valores, str):
        valores = (valores,)
    unicos = []
    vistos = set()
    for valor in valores or ():
        if not isinstance(valor, str):
            continue
        nombre = valor.strip()
        clave = nombre.casefold()
        if not nombre or clave in vistos:
            continue
        vistos.add(clave)
        unicos.append(nombre)
    return tuple(unicos)


def _compatibles_desde_cache(cache):
    return [
        nombre
        for nombre, (compatible, _mensaje, _timestamp) in cache.items()
        if compatible
    ]


def _cache_cubre_snapshot(cache, nombres):
    return bool(cache) and set(cache) == set(nombres)


def _cargar_snapshot_contextual(win, nombres):
    versiones = getattr(win, "versiones", {}) or {}
    kernel = str(versiones.get("kernel") or "")
    for _intento in range(2):
        contexto, binarios = _capturar_contexto_compatibilidad(win, nombres)
        cache = (
            cargar_compatibilidad(kernel, environment_key=contexto)
            if kernel
            else {}
        )
        contexto_final, binarios_finales = _capturar_contexto_compatibilidad(
            win,
            nombres,
        )
        if contexto_final != contexto or binarios_finales != binarios:
            continue

        cache = dict(cache or {})
        verificada = _cache_cubre_snapshot(cache, nombres)
        if not verificada:
            cache = {}
        compatibles = _compatibles_desde_cache(cache) if verificada else None
        return contexto, cache, compatibles, verificada, binarios

    return contexto_final, {}, None, False, binarios_finales


def _refrescar_checklist_auto(win, nombres):
    if not hasattr(win, "_auto_sched_checks"):
        return
    try:
        if getattr(win, "compatibles", None) is None:
            from ui.automatizacion import _invalidar_auto_schedulers

            _invalidar_auto_schedulers(win)
        else:
            from ui.automatizacion import _refrescar_auto_schedulers

            _refrescar_auto_schedulers(win, nombres=nombres)
    except ImportError:
        pass


def _bloqueo_estado_disponibilidad(win):
    bloqueo = getattr(win, "_disp_state_lock", None)
    if bloqueo is None:
        bloqueo = threading.Lock()
        win._disp_state_lock = bloqueo
    return bloqueo


def _aplicar_cache_a_fila(row, spinner, icono, entrada):
    spinner.set_visible(False)
    icono.set_visible(True)
    for css_class in ("success", "error", "dim-label", "warning"):
        icono.remove_css_class(css_class)

    if entrada is None:
        row.set_subtitle("Sin verificar")
        row.set_tooltip_text("")
        icono.set_from_icon_name("dialog-question-symbolic")
        icono.add_css_class("dim-label")
        return

    compatible, mensaje, _timestamp = entrada
    row.set_subtitle(_mensaje_corto(mensaje, compatible))
    row.set_tooltip_text(mensaje or "")
    if compatible:
        icono.set_from_icon_name("emblem-ok-symbolic")
        icono.add_css_class("success")
    else:
        icono.set_from_icon_name("dialog-error-symbolic")
        icono.add_css_class("error")


def _aplicar_cache_a_filas(win, cache):
    for nombre, (row, spinner, icono) in win._disp_filas.items():
        _aplicar_cache_a_fila(row, spinner, icono, cache.get(nombre))


def _crear_fila_disponibilidad(nombre, cache):
    row = Adw.ActionRow(title=nombre)
    suffix_box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
    spinner = Adw.Spinner()
    spinner.set_visible(False)
    icono = Gtk.Image.new_from_icon_name("dialog-question-symbolic")
    suffix_box.append(spinner)
    suffix_box.append(icono)
    row.add_suffix(suffix_box)
    _aplicar_cache_a_fila(row, spinner, icono, cache.get(nombre))
    return row, spinner, icono


def setup_disponibilidad_ui(win):
    """Construye la interfaz de la pestaña Disponibilidad."""
    win._disp_filas = {}
    win._verificando = False
    win._disp_generation = 0
    win._disp_state_lock = threading.Lock()
    win._compatibility_context = None

    pref_page = Adw.PreferencesPage()
    win._disp_pref_page = pref_page

    grupo = Adw.PreferencesGroup(
        title="Compatibilidad de Planificadores",
        description="Comprueba si el programa BPF de cada planificador puede cargarse en el kernel actual. La verificación requiere privilegios de administrador."
    )

    nombres = _snapshot_nombres(win)
    (
        contexto,
        cache,
        compatibles,
        _verificada,
        _binarios,
    ) = _cargar_snapshot_contextual(win, nombres)
    win._compatibility_context = contexto
    win.compatibles = compatibles

    for nombre in nombres:
        row, spinner, icono = _crear_fila_disponibilidad(nombre, cache)
        win._disp_filas[nombre] = (row, spinner, icono)
        grupo.add(row)

    _refrescar_checklist_auto(win, nombres)

    win._disp_grupo_scheds = grupo
    pref_page.add(grupo)

    # ── Registro de Verificación ──
    win._disp_grupo_logs = Adw.PreferencesGroup(title="Registro de Verificación")
    win.expander_logs_disp = Adw.ExpanderRow(title="Terminal de Diagnóstico", subtitle="Salida técnica de los binarios BPF", icon_name="utilities-terminal-symbolic")

    win.text_view_logs_disp = Gtk.TextView(editable=False, cursor_visible=False, monospace=True, css_classes=["card"])
    caja_log_disp = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
    scrolled_disp = Gtk.ScrolledWindow(min_content_height=200, vexpand=True)
    scrolled_disp.set_child(win.text_view_logs_disp)
    caja_log_disp.append(scrolled_disp)
    win.expander_logs_disp.add_row(caja_log_disp)
    win._disp_grupo_logs.add(win.expander_logs_disp)

    pref_page.add(win._disp_grupo_logs)

    # ── Historial de Compatibilidad por Kernel ──
    win._disp_grupo_historial = None
    _refrescar_historial_compat(win)

    header = Adw.HeaderBar()
    win._btn_verificar_disp = Gtk.Button(
        label="Comprobar",
        css_classes=["suggested-action"],
        valign=Gtk.Align.CENTER
    )
    win._btn_verificar_disp.connect("clicked", lambda b: iniciar_verificacion(win, b))
    header.pack_start(win._btn_verificar_disp)

    win._btn_limpiar_disp = Gtk.Button(
        icon_name="user-trash-symbolic",
        tooltip_text="Limpiar caché de compatibilidad",
        css_classes=["flat"],
        valign=Gtk.Align.CENTER
    )
    win._btn_limpiar_disp.connect("clicked", lambda _button: _limpiar_cache(win))
    header.pack_end(win._btn_limpiar_disp)

    view = Adw.ToolbarView(content=pref_page)
    view.add_top_bar(header)
    win.pag_disponibilidad.set_child(view)


def recargar_disponibilidad_ui(win, nombres=None):
    """Refresca filas desde un snapshot ya obtenido fuera del hilo GTK."""
    nuevos = _snapshot_nombres(win, nombres)
    with _bloqueo_estado_disponibilidad(win):
        (
            contexto,
            cache,
            compatibles,
            verificada,
            _binarios,
        ) = _cargar_snapshot_contextual(win, nuevos)
        win._disp_generation = int(
            getattr(win, "_disp_generation", 0) or 0
        ) + 1
        win._compatibility_context = contexto
        win.compatibles = compatibles

    antiguos = set(win._disp_filas.keys())
    nuevos_set = set(nuevos)

    grupo = win._disp_grupo_scheds

    # Eliminar filas que ya no corresponden
    for nombre in antiguos - nuevos_set:
        row, _, _ = win._disp_filas.pop(nombre)
        grupo.remove(row)

    # Añadir filas nuevas
    for nombre in nuevos:
        if nombre in antiguos:
            continue
        row, spinner, icono = _crear_fila_disponibilidad(nombre, cache)
        win._disp_filas[nombre] = (row, spinner, icono)
        grupo.add(row)

    _aplicar_cache_a_filas(win, cache)
    _refrescar_checklist_auto(win, nuevos)
    if hasattr(win, "nav_disponibilidad"):
        _actualizar_badge_compatibilidad(
            win,
            win.compatibles or (),
            verificada,
        )
    _refrescar_historial_compat(win)


def _actualizar_fila(r, s, i, ok, texto, warn):
    """Actualiza el estado visual de una fila de scheduler."""
    s.set_visible(False)
    i.set_visible(True)
    r.set_subtitle(_mensaje_corto(texto, ok))
    r.set_tooltip_text(texto)
    for cls in ["success", "error", "dim-label", "warning"]:
        i.remove_css_class(cls)

    if ok:
        if warn:
            i.set_from_icon_name("dialog-warning-symbolic")
            i.add_css_class("warning")
        else:
            i.set_from_icon_name("emblem-ok-symbolic")
            i.add_css_class("success")
    else:
        i.set_from_icon_name("dialog-error-symbolic")
        i.add_css_class("error")


def _limpiar_cache(win):
    """Limpia la caché de compatibilidad y resetea las filas."""
    if win._verificando:
        _mostrar_toast(win, "Espere a que termine la verificación activa.")
        return
    with _bloqueo_estado_disponibilidad(win):
        limpiar_compatibilidad()
        win._disp_generation = int(
            getattr(win, "_disp_generation", 0) or 0
        ) + 1
        win._compatibility_context = None
    win.compatibles = None
    for nombre, (row, spinner, icono) in win._disp_filas.items():
        row.set_subtitle("Sin verificar")
        row.set_tooltip_text("")
        icono.set_from_icon_name("dialog-question-symbolic")
        icono.remove_css_class("success")
        icono.remove_css_class("error")
        icono.remove_css_class("warning")
        icono.add_css_class("dim-label")
    win.nav_disponibilidad.add_css_class("pulse-warning")
    imagen = win.nav_disponibilidad.get_child().get_first_child()
    if isinstance(imagen, Gtk.Image):
        for clase in ("success", "error"):
            imagen.remove_css_class(clase)
        imagen.set_from_icon_name("dialog-information-symbolic")
    try:
        from ui.automatizacion import _invalidar_auto_schedulers

        _invalidar_auto_schedulers(win)
    except ImportError:
        pass
    _refrescar_historial_compat(win)
    log(win.text_view_logs_disp, "Caché de compatibilidad limpiada", True)


def _refrescar_historial_compat(win):
    """Refresca el historial de compatibilidad por kernel."""
    if win._disp_grupo_historial is not None:
        if win._disp_grupo_historial.get_parent():
            win._disp_pref_page.remove(win._disp_grupo_historial)
    if win._disp_grupo_logs is not None and win._disp_grupo_logs.get_parent():
        win._disp_pref_page.remove(win._disp_grupo_logs)

    win._disp_grupo_historial = Adw.PreferencesGroup(title="Historial de Compatibilidad")
    win._disp_grupo_historial.add_css_class("history-group")

    datos = obtener_historial_compatibilidad()
    lookup = {}
    for d in datos:
        lookup[(d["scheduler_name"], d["kernel_version"])] = d

    scheds = sorted(win._disp_filas.keys())
    if not scheds:
        scheds = sorted(_snapshot_nombres(win))
    if not scheds:
        scheds = sorted(set(d["scheduler_name"] for d in datos))

    agregados = []
    for sched in scheds:
        sched_data = [d for d in datos if d["scheduler_name"] == sched]
        if not sched_data:
            continue
        total = len(sched_data)
        ok_count = sum(1 for d in sched_data if d["is_compatible"])

        if ok_count == total:
            sub = f"{total}/{total} verificados"
        elif ok_count == 0:
            sub = f"{total}/{total} fallidos"
        else:
            sub = f"{ok_count}/{total} verificados"

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        lbl_name = Gtk.Label(label=sched, xalign=0, css_classes=["heading"])
        box.append(lbl_name)
        lbl_sub = Gtk.Label(label=sub, xalign=0, css_classes=["caption", "dim-label"])
        _fade_in(lbl_sub, 300)
        box.append(lbl_sub)

        chips_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4, margin_top=4)

        for kv in sorted(set(d["kernel_version"] for d in sched_data)):
            entry = lookup.get((sched, kv))
            if entry is None:
                continue

            compatible = entry["is_compatible"]
            msg = entry.get("message", "")
            korto = _mensaje_corto(msg, compatible)
            kv_brief = _simplificar_kernel(kv)

            if compatible:
                css_bg = "success"
                icon_name = "emblem-ok-symbolic"
            else:
                css_bg = "error"
                icon_name = "dialog-error-symbolic"

            chip = Gtk.Box(spacing=4, valign=Gtk.Align.CENTER)
            chip.add_css_class("chip")
            chip.add_css_class(css_bg)
            chip.set_tooltip_text(f"{sched} @ {kv}\n{korto}: {msg}")

            icon = Gtk.Image.new_from_icon_name(icon_name)
            icon.set_pixel_size(14)
            chip.append(icon)
            chip.append(Gtk.Label(label=kv_brief))

            _fade_in(chip, 400)
            chips_box.append(chip)

        box.append(chips_box)
        win._disp_grupo_historial.add(box)
        agregados.append(sched)
        if len(agregados) > 0:
            sep = Gtk.Separator(margin_top=4, margin_bottom=4)
            sep.set_visible(True)
            win._disp_grupo_historial.add(sep)

    if not agregados:
        lbl = Gtk.Label(label="Esperando datos…", css_classes=["dim-label", "caption"], margin_top=8, margin_bottom=8)
        _fade_in(lbl, 400)
        win._disp_grupo_historial.add(lbl)

    win._disp_pref_page.add(win._disp_grupo_logs)
    win._disp_pref_page.add(win._disp_grupo_historial)


def _marcar_fila_verificando(row, spinner, icono):
    row.set_subtitle("Verificando...")
    icono.set_visible(False)
    spinner.set_visible(True)


def _actualizar_badge_compatibilidad(win, compatibles, verificada):
    imagen = win.nav_disponibilidad.get_child().get_first_child()
    if not verificada:
        win.nav_disponibilidad.add_css_class("pulse-warning")
        if isinstance(imagen, Gtk.Image):
            for clase in ("success", "error"):
                imagen.remove_css_class(clase)
            imagen.set_from_icon_name("dialog-information-symbolic")
        return

    win.nav_disponibilidad.remove_css_class("pulse-warning")
    if not isinstance(imagen, Gtk.Image):
        return
    for clase in ("success", "error"):
        imagen.remove_css_class(clase)
    if compatibles:
        imagen.set_from_icon_name("emblem-ok-symbolic")
        imagen.add_css_class("success")
    else:
        imagen.set_from_icon_name("dialog-error-symbolic")
        imagen.add_css_class("error")


def _verificar_binario_bpf(
    scx_manager,
    token,
    nombre,
    timeout_bin,
    log_view,
    binario_capturado=_BINARIO_NO_CAPTURADO,
):
    token.raise_if_cancelled()
    binario_nombre = _nombre_binario_scheduler(nombre)
    if binario_capturado is _BINARIO_NO_CAPTURADO:
        try:
            binario = shutil.which(binario_nombre)
        except (OSError, TypeError) as exc:
            return False, f"No se pudo resolver {binario_nombre}: {exc}", False
    else:
        binario = binario_capturado
    if not binario:
        return False, f"Binario {binario_nombre} no encontrado en PATH", False
    if not timeout_bin:
        return False, "Binario timeout no encontrado en PATH", False

    token.raise_if_cancelled()
    log(log_view, f"Probando {binario_nombre} ({binario})...")
    resultado = scx_manager.ejecutar_con_sudo(
        [timeout_bin, "-k", "1", "5", binario],
        timeout=8,
        cancel_token=token,
    )
    token.raise_if_cancelled()
    salida = f"{resultado.stdout or ''}\n{resultado.stderr or ''}".strip()
    if salida:
        limpia = limpiar_texto(salida)
        if limpia:
            log(log_view, f"Resumen de {binario_nombre}:\n{limpia}")
    return _clasificar_salida_bpf(
        resultado.returncode,
        resultado.stdout,
        resultado.stderr,
    )


def _asegurar_snapshot_verificacion_vigente(
    win,
    generacion,
    modo_desarrollador,
    contexto=None,
    nombres=None,
):
    if (
        generacion != getattr(win, "_disp_generation", None)
        or modo_desarrollador
        != bool(getattr(win, "modo_desarrollador", False))
    ):
        raise RuntimeError(
            "Cambió la lista, el modo o el contexto durante la verificación."
        )

    if nombres is not None:
        filas_actuales = getattr(win, "_disp_filas", None)
        if filas_actuales is not None and tuple(filas_actuales) != tuple(nombres):
            raise RuntimeError(
                "Cambió la lista, el modo o el contexto durante la verificación."
            )

    if contexto is None:
        return
    if getattr(win, "_compatibility_context", contexto) != contexto:
        raise RuntimeError(
            "Cambió la lista, el modo o el contexto durante la verificación."
        )
    if contexto_compatibilidad_actual(win, nombres or ()) != contexto:
        raise RuntimeError(
            "Cambió la lista, el modo o el contexto durante la verificación."
        )


def _aplicar_ui_verificacion_si_vigente(
    win,
    generacion,
    modo_desarrollador,
    contexto,
    nombres,
    callback,
    *args,
):
    if not bool(getattr(win, "_verificando", True)):
        return False
    try:
        _asegurar_snapshot_verificacion_vigente(
            win,
            generacion,
            modo_desarrollador,
            contexto,
            nombres,
        )
    except RuntimeError:
        return False
    callback(*args)
    return False


def _worker_verificacion(
    win,
    handle,
    scx_manager,
    filas,
    modo_desarrollador,
    kernel,
    log_view,
    cache_anterior=None,
    compatibles_anteriores=None,
    cache_verificada_anterior=None,
    generacion=None,
    contexto=None,
    nombres_snapshot=None,
    binarios_snapshot=None,
):
    cache_anterior = dict(cache_anterior or {})
    if compatibles_anteriores is not None:
        compatibles_anteriores = list(compatibles_anteriores)
    if cache_verificada_anterior is None:
        cache_verificada_anterior = (
            bool(cache_anterior) or compatibles_anteriores is not None
        )
    if nombres_snapshot is None:
        nombres_snapshot = tuple(nombre for nombre, *_resto in filas)
    else:
        nombres_snapshot = tuple(nombres_snapshot)
    if binarios_snapshot is None:
        contexto_actual, binarios_snapshot = _capturar_contexto_compatibilidad(
            win,
            nombres_snapshot,
        )
        if contexto is None:
            contexto = contexto_actual
    else:
        binarios_snapshot = dict(binarios_snapshot)
    if contexto is None:
        contexto = contexto_compatibilidad_actual(win, nombres_snapshot)
    verificaciones = []
    completada = False
    cancelada = False
    error = None
    sesion = None
    try:
        try:
            timeout_bin = None if modo_desarrollador else shutil.which("timeout")
            _asegurar_snapshot_verificacion_vigente(
                win,
                generacion,
                modo_desarrollador,
                contexto,
                nombres_snapshot,
            )
            with scx_manager.sesion(handle.token) as sesion:
                for nombre, row, spinner, icono in filas:
                    handle.check_cancelled()
                    _asegurar_snapshot_verificacion_vigente(
                        win,
                        generacion,
                        modo_desarrollador,
                        contexto,
                        nombres_snapshot,
                    )
                    sesion.aplicar(ScxState())
                    handle.check_cancelled()
                    _asegurar_snapshot_verificacion_vigente(
                        win,
                        generacion,
                        modo_desarrollador,
                        contexto,
                        nombres_snapshot,
                    )
                    _programar_ui(
                        win,
                        _aplicar_ui_verificacion_si_vigente,
                        win,
                        generacion,
                        modo_desarrollador,
                        contexto,
                        nombres_snapshot,
                        _marcar_fila_verificando,
                        row,
                        spinner,
                        icono,
                    )
                    if modo_desarrollador:
                        disponible = _compatibilidad_dev_determinista(nombre)
                        mensaje = (
                            "Disponible (Simulado determinista)"
                            if disponible
                            else "Programa incompatible (Simulado determinista)"
                        )
                        advertencia = False
                    else:
                        disponible, mensaje, advertencia = _verificar_binario_bpf(
                            scx_manager,
                            handle.token,
                            nombre,
                            timeout_bin,
                            log_view,
                            binarios_snapshot.get(nombre),
                        )
                    handle.check_cancelled()
                    _asegurar_snapshot_verificacion_vigente(
                        win,
                        generacion,
                        modo_desarrollador,
                        contexto,
                        nombres_snapshot,
                    )
                    verificaciones.append((nombre, disponible, mensaje))
                    _programar_ui(
                        win,
                        _aplicar_ui_verificacion_si_vigente,
                        win,
                        generacion,
                        modo_desarrollador,
                        contexto,
                        nombres_snapshot,
                        _actualizar_fila,
                        row,
                        spinner,
                        icono,
                        disponible,
                        mensaje,
                        advertencia,
                    )
                handle.check_cancelled()
                _asegurar_snapshot_verificacion_vigente(
                    win,
                    generacion,
                    modo_desarrollador,
                    contexto,
                    nombres_snapshot,
                )
            restore_error = getattr(sesion, "restore_error", None)
            if restore_error is not None:
                detalle = RuntimeError(
                    f"Falló la restauración SCX: {restore_error}"
                )
                if isinstance(restore_error, BaseException):
                    raise detalle from restore_error
                raise detalle
            if not handle.token.seal():
                raise OperationCancelled("La operación fue cancelada.")
            with _bloqueo_estado_disponibilidad(win):
                _asegurar_snapshot_verificacion_vigente(
                    win,
                    generacion,
                    modo_desarrollador,
                    contexto,
                    nombres_snapshot,
                )
                reemplazar_compatibilidad(
                    kernel,
                    tuple(verificaciones),
                    environment_key=contexto,
                )
            completada = True
            log(log_view, "VERIFICACIÓN FINALIZADA", True)
        except OperationCancelled:
            cancelada = True
            restore_error = getattr(sesion, "restore_error", None)
            if restore_error is not None:
                error = f"Falló la restauración SCX: {restore_error}"
            log(log_view, "VERIFICACIÓN CANCELADA", True)
        except Exception as exc:
            error = str(exc) or exc.__class__.__name__
            restore_error = getattr(sesion, "restore_error", None)
            if restore_error is not None and restore_error is not exc:
                error += f"; además falló la restauración SCX: {restore_error}"
            log(log_view, f"Error durante la verificación: {error}", es_error=True)
    except Exception as exc:
        error = str(exc) or exc.__class__.__name__
        log(log_view, f"Error exterior de verificación: {error}", es_error=True)
    finally:
        handle.release()
        _programar_ui(
            win,
            _finalizar_verificacion,
            win,
            tuple(verificaciones),
            completada,
            cancelada,
            error,
            cache_anterior,
            compatibles_anteriores,
            bool(cache_verificada_anterior),
            modo_desarrollador,
            generacion,
            contexto,
            nombres_snapshot,
        )


def _finalizar_verificacion(
    win,
    verificaciones,
    completada,
    cancelada,
    error,
    cache_anterior=None,
    compatibles_anteriores=None,
    cache_verificada_anterior=False,
    modo_verificado=None,
    generacion=None,
    contexto_verificado=None,
    nombres_snapshot=None,
):
    win._verificando = False
    win._btn_verificar_disp.set_sensitive(True)
    win._btn_limpiar_disp.set_sensitive(True)
    snapshot_obsoleto = (
        generacion is not None
        and generacion != getattr(win, "_disp_generation", None)
    )
    modo_obsoleto = (
        isinstance(modo_verificado, bool)
        and modo_verificado
        != bool(getattr(win, "modo_desarrollador", False))
    )
    nombres_capturados = (
        None if nombres_snapshot is None else tuple(nombres_snapshot)
    )
    nombres_actuales = tuple(getattr(win, "_disp_filas", {}))
    lista_obsoleta = (
        nombres_capturados is not None
        and nombres_actuales != nombres_capturados
    )
    contexto_obsoleto = False
    if contexto_verificado is not None:
        contexto_obsoleto = (
            getattr(win, "_compatibility_context", contexto_verificado)
            != contexto_verificado
            or contexto_compatibilidad_actual(
                win,
                nombres_capturados or (),
            )
            != contexto_verificado
        )
    if modo_obsoleto or snapshot_obsoleto or lista_obsoleta or contexto_obsoleto:
        nombres_actuales = nombres_actuales or _snapshot_nombres(win)
        recargar_disponibilidad_ui(win, nombres_actuales)
        _mostrar_toast(
            win,
            "Se descartó una verificación de un contexto anterior.",
        )
        return

    nombres = nombres_actuales
    nombres_verificados = tuple(
        nombre for nombre, _compatible, _mensaje in verificaciones
    )
    if completada and nombres_verificados != nombres:
        recargar_disponibilidad_ui(win, nombres)
        _mostrar_toast(
            win,
            "Cambió la lista de schedulers; vuelva a verificar la compatibilidad.",
            alta=True,
        )
        return

    if completada:
        if contexto_verificado is not None:
            win._compatibility_context = contexto_verificado
        win.compatibles = [
            nombre
            for nombre, compatible, _mensaje in verificaciones
            if compatible
        ]
        cache_nueva = {
            nombre: (compatible, mensaje, None)
            for nombre, compatible, mensaje in verificaciones
        }
        _aplicar_cache_a_filas(win, cache_nueva)
        _actualizar_badge_compatibilidad(win, win.compatibles, True)
        _refrescar_checklist_auto(win, nombres)
        _mostrar_toast(
            win,
            f"Compatibilidad verificada: {len(win.compatibles)} scheduler(s).",
        )
    else:
        win.compatibles = (
            None
            if compatibles_anteriores is None
            else list(compatibles_anteriores)
        )
        _aplicar_cache_a_filas(win, dict(cache_anterior or {}))
        _actualizar_badge_compatibilidad(
            win,
            win.compatibles or (),
            bool(cache_verificada_anterior),
        )
        _refrescar_checklist_auto(win, nombres)
        if error:
            _mostrar_toast(win, f"Falló la verificación: {error}", alta=True)
        elif cancelada:
            _mostrar_toast(win, "Verificación de compatibilidad cancelada.")

    _refrescar_historial_compat(win)
    sincronizar = getattr(win, "sincronizar_sistema", None)
    if callable(sincronizar):
        sincronizar()


def iniciar_verificacion(win, btn=None):
    """Inicia una verificación BPF exclusiva y restaurable."""
    if win._verificando:
        _mostrar_toast(win, "La verificación ya está en curso.")
        return

    if not win._disp_filas:
        _mostrar_toast(win, "No hay schedulers para verificar.", alta=True)
        return

    modo_desarrollador = bool(win.modo_desarrollador)

    def proceder():
        if win._verificando:
            _mostrar_toast(win, "La verificación ya está en curso.")
            return
        if modo_desarrollador != bool(win.modo_desarrollador):
            _mostrar_toast(
                win,
                "El modo cambió durante la autorización; repita la verificación.",
                alta=True,
            )
            return

        filas = tuple(
            (nombre, row, spinner, icono)
            for nombre, (row, spinner, icono) in win._disp_filas.items()
        )
        if not filas:
            _mostrar_toast(win, "No hay schedulers para verificar.", alta=True)
            return

        nombres_snapshot = tuple(nombre for nombre, *_resto in filas)
        with _bloqueo_estado_disponibilidad(win):
            (
                contexto,
                cache_anterior,
                compatibles_anteriores,
                cache_verificada_anterior,
                binarios_snapshot,
            ) = _cargar_snapshot_contextual(win, nombres_snapshot)
            if contexto != getattr(win, "_compatibility_context", None):
                win._disp_generation = int(
                    getattr(win, "_disp_generation", 0) or 0
                ) + 1
            win._compatibility_context = contexto
            win.compatibles = (
                None
                if compatibles_anteriores is None
                else list(compatibles_anteriores)
            )
            generacion = getattr(win, "_disp_generation", 0)
        _aplicar_cache_a_filas(win, cache_anterior)
        _refrescar_checklist_auto(win, nombres_snapshot)

        versiones = getattr(win, "versiones", {}) or {}
        kernel = str(versiones.get("kernel") or "")

        handle = win.operaciones.try_acquire("verificacion de compatibilidad")
        if handle is None:
            _mostrar_operacion_ocupada(win)
            return

        scx_manager = win.scx
        log_view = win.text_view_logs_disp
        win._verificando = True
        win._btn_verificar_disp.set_sensitive(False)
        win._btn_limpiar_disp.set_sensitive(False)
        log(log_view, "INICIANDO VERIFICACIÓN DE COMPATIBILIDAD BPF", True)

        worker = lambda: _worker_verificacion(
            win,
            handle,
            scx_manager,
            filas,
            modo_desarrollador,
            kernel,
            log_view,
            cache_anterior,
            compatibles_anteriores,
            cache_verificada_anterior,
            generacion,
            contexto,
            nombres_snapshot,
            binarios_snapshot,
        )
        try:
            threading.Thread(target=worker).start()
        except Exception as exc:
            handle.release()
            _finalizar_verificacion(
                win,
                [],
                False,
                False,
                str(exc) or exc.__class__.__name__,
                cache_anterior,
                compatibles_anteriores,
                cache_verificada_anterior,
                modo_desarrollador,
                generacion,
                contexto,
                nombres_snapshot,
            )

    win.solicitar_sudo_si_necesario(proceder)
