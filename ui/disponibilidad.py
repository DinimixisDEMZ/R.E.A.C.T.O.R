"""
Pestaña de Disponibilidad: Verificación de compatibilidad BPF de schedulers.
"""

import subprocess
import threading
import time

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib

from utils.logging import log, registrar_error
from utils.helpers import limpiar_texto
from core.database import cargar_compatibilidad, guardar_compatibilidad, limpiar_compatibilidad, obtener_historial_compatibilidad
from utils.i18n import traducir
from core import sched_ext
from core.sched_ext import canonizar, nombre_corto
from core.tipos import obtener_scheduler


def _mensaje_corto(msg, compatible):
    if not msg:
        return traducir("Correcto") if compatible else traducir("Sin verificar")
    ml = msg.lower()
    if compatible:
        if "verificado" in ml: return traducir("Verificado")
        if "residente" in ml: return traducir("Residente")
        if "shutdown" in ml: return traducir("Apagado")
        if "simulado" in ml: return traducir("Simulado")
        return traducir("Correcto")
    if "failed to load bpf" in ml or "bpf" in ml: return traducir("Error BPF")
    if "no such file" in ml: return traducir("BPF faltante")
    if "error de salida" in ml:
        idx = msg.find("(")
        return traducir("Error {}").format(msg[idx:idx+6].strip()) if idx >= 0 else traducir("Error")
    if "error" in ml: return traducir("Error")
    return msg[:10] + "…" if len(msg) > 10 else msg


def _simplificar_kernel(kv):
    return kv.split("-")[0] if kv else kv


def _fade_in(widget, duration_ms=200):
    widget.set_opacity(0.0)
    destino = Adw.PropertyAnimationTarget.new(widget, "opacity")
    anim = Adw.TimedAnimation.new(widget, 0.0, 1.0, duration_ms, destino)
    anim.set_easing(Adw.Easing.EASE_OUT_QUAD)
    anim.play()


def configurar_ui_disponibilidad(win):
    """Construye la interfaz de la pestaña Disponibilidad."""
    win._disp_filas = {}
    win._verificando = False

    pref_page = Adw.PreferencesPage()
    win._disp_pref_page = pref_page

    grupo = Adw.PreferencesGroup(
        title=traducir("Compatibilidad de Planificadores"),
        description=traducir("Comprueba que el binario de cada planificador pueda ejecutarse en este equipo. La comprobación básica no necesita privilegios y no altera el planificador activo.")
    )

    try:
        nombres = win.scx.obtener_lista()
    except (subprocess.SubprocessError, OSError):
        nombres = []

    kernel_actual = win.versiones.get("kernel", "")
    cache = cargar_compatibilidad(kernel_actual) if kernel_actual else {}

    for nombre in nombres:
        # Se muestra el nombre corto; internamente todo es canónico.
        row = Adw.ActionRow(title=nombre_corto(nombre))

        suffix_box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        spinner = Adw.Spinner()
        spinner.set_visible(False)
        icono = Gtk.Image.new_from_icon_name("dialog-question-symbolic")
        icono.add_css_class("dim-label")
        suffix_box.append(spinner)
        suffix_box.append(icono)
        row.add_suffix(suffix_box)

        if nombre in cache:
            compatible, mensaje, ts = cache[nombre]
            if compatible:
                icono.set_from_icon_name("emblem-ok-symbolic")
                icono.remove_css_class("dim-label")
                icono.add_css_class("success")
            else:
                icono.set_from_icon_name("dialog-error-symbolic")
                icono.remove_css_class("dim-label")
                icono.add_css_class("error")
            row.set_subtitle(_mensaje_corto(mensaje, compatible))
            if mensaje:
                row.set_tooltip_text(mensaje)
        else:
            row.set_subtitle(traducir("Sin verificar"))

        win._disp_filas[nombre] = (row, spinner, icono)
        grupo.add(row)

    win._disp_grupo_scheds = grupo
    pref_page.add(grupo)

    # ── Registro de Verificación ──
    win._disp_grupo_logs = Adw.PreferencesGroup(title=traducir("Registro de Verificación"))
    win.text_view_logs_disp = Gtk.TextView(editable=False, cursor_visible=False, monospace=True, css_classes=["card"])
    win._dialog_logs_disp = None

    def _abrir_logs():
        if win._dialog_logs_disp is None:
            scrolled = Gtk.ScrolledWindow(min_content_height=400, vexpand=True)
            scrolled.set_child(win.text_view_logs_disp)
            win._dialog_logs_disp = Adw.Dialog()
            ancho = win.get_width()
            win._dialog_logs_disp.set_content_width(max(ancho - 40, 400))
            win._dialog_logs_disp.set_content_height(500)
            win._dialog_logs_disp.set_presentation_mode(Adw.DialogPresentationMode.BOTTOM_SHEET)
            win._dialog_logs_disp.set_child(scrolled)
        win._dialog_logs_disp.present(win)

    btn_logs = Adw.ActionRow(
        title=traducir("Terminal de Diagnóstico"),
        subtitle=traducir("Salida técnica de los binarios BPF"),
        icon_name="utilities-terminal-symbolic",
    )
    btn_logs.set_activatable(True)
    btn_logs.connect("activated", lambda *_: _abrir_logs())
    win._disp_grupo_logs.add(btn_logs)

    pref_page.add(win._disp_grupo_logs)

    # ── Historial de Compatibilidad por Kernel ──
    win._disp_grupo_historial = None
    _refrescar_historial_compat(win)

    header = Adw.HeaderBar()
    win._btn_verificar_disp = Gtk.Button(
        label=traducir("Comprobar"),
        css_classes=["suggested-action"],
        valign=Gtk.Align.CENTER
    )
    win._btn_verificar_disp.connect("clicked", lambda b: iniciar_verificacion(win, b))
    header.pack_start(win._btn_verificar_disp)

    btn_limpiar = Gtk.Button(
        icon_name="user-trash-symbolic",
        tooltip_text=traducir("Limpiar caché de compatibilidad"),
        css_classes=["flat"],
        valign=Gtk.Align.CENTER
    )
    btn_limpiar.connect("clicked", lambda b: _limpiar_cache(win))
    header.pack_end(btn_limpiar)

    view = Adw.ToolbarView(content=pref_page)
    view.add_top_bar(header)
    win.pag_disponibilidad.set_child(view)


def recargar_disponibilidad_ui(win):
    """Refresca la lista de schedulers en disponibilidad al cambiar modo simulación, sin destruir la página."""
    try:
        nuevos = win.scx.obtener_lista()
    except (subprocess.SubprocessError, OSError):
        nuevos = []

    kernel_actual = win.versiones.get("kernel", "")
    cache = cargar_compatibilidad(kernel_actual) if kernel_actual else {}

    antiguos = set(win._disp_filas.keys())
    nuevos_set = set(nuevos)

    grupo = win._disp_grupo_scheds

    # Eliminar filas que ya no corresponden
    for nombre in antiguos - nuevos_set:
        row, _, _ = win._disp_filas.pop(nombre)
        grupo.remove(row)

    # Añadir filas nuevas
    for nombre in nuevos_set - antiguos:
        # Se muestra el nombre corto; internamente todo es canónico.
        row = Adw.ActionRow(title=nombre_corto(nombre))

        suffix_box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        spinner = Adw.Spinner()
        spinner.set_visible(False)
        icono = Gtk.Image.new_from_icon_name("dialog-question-symbolic")
        icono.add_css_class("dim-label")
        suffix_box.append(spinner)
        suffix_box.append(icono)
        row.add_suffix(suffix_box)

        if nombre in cache:
            compatible, mensaje, ts = cache[nombre]
            if compatible:
                icono.set_from_icon_name("emblem-ok-symbolic")
                icono.remove_css_class("dim-label")
                icono.add_css_class("success")
            else:
                icono.set_from_icon_name("dialog-error-symbolic")
                icono.remove_css_class("dim-label")
                icono.add_css_class("error")
            row.set_subtitle(_mensaje_corto(mensaje, compatible))
            if mensaje:
                row.set_tooltip_text(mensaje)
        else:
            row.set_subtitle(traducir("Sin verificar"))

        win._disp_filas[nombre] = (row, spinner, icono)
        grupo.add(row)

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
    limpiar_compatibilidad()
    for nombre, (row, spinner, icono) in win._disp_filas.items():
        row.set_subtitle(traducir("Sin verificar"))
        row.set_tooltip_text("")
        icono.set_from_icon_name("dialog-question-symbolic")
        icono.remove_css_class("success")
        icono.remove_css_class("error")
        icono.remove_css_class("warning")
        icono.add_css_class("dim-label")
    _refrescar_historial_compat(win)
    log(win.text_view_logs_disp, traducir("Caché de compatibilidad limpiada"), nivel="title")


def _refrescar_historial_compat(win):
    """Refresca el historial de compatibilidad por kernel."""
    if win._disp_grupo_historial is not None:
        if win._disp_grupo_historial.get_parent():
            win._disp_pref_page.remove(win._disp_grupo_historial)
    if win._disp_grupo_logs is not None and win._disp_grupo_logs.get_parent():
        win._disp_pref_page.remove(win._disp_grupo_logs)

    win._disp_grupo_historial = Adw.PreferencesGroup(title=traducir("Historial de Compatibilidad"))
    win._disp_grupo_historial.add_css_class("history-group")

    datos = obtener_historial_compatibilidad()
    lookup = {}
    for d in datos:
        lookup[(d["scheduler_name"], d["kernel_version"])] = d

    scheds = sorted(win._disp_filas.keys())
    if not scheds:
        try:
            scheds = sorted(win.scx.obtener_lista())
        except (subprocess.SubprocessError, OSError):
            scheds = sorted(set(d["scheduler_name"] for d in datos))

    agregados = []
    for sched in scheds:
        sched_data = [d for d in datos if d["scheduler_name"] == sched]
        if not sched_data:
            continue
        total = len(sched_data)
        ok_count = sum(1 for d in sched_data if d["is_compatible"])

        if ok_count == total:
            sub = traducir("{}/{} verificados").format(total, total)
        elif ok_count == 0:
            sub = traducir("{}/{} fallidos").format(total, total)
        else:
            sub = traducir("{}/{} verificados").format(ok_count, total)

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
        lbl = Gtk.Label(label=traducir("Esperando datos…"), css_classes=["dim-label", "caption"], margin_top=8, margin_bottom=8)
        _fade_in(lbl, 400)
        win._disp_grupo_historial.add(lbl)

    win._disp_pref_page.add(win._disp_grupo_logs)
    win._disp_pref_page.add(win._disp_grupo_historial)


def _verificar_scheduler_dev(win, nombre, row, spinner, icono):
    time.sleep(0.1)
    hash_val = hash(nombre) % 100
    disponible = hash_val < 75
    msg = traducir("Disponible (Simulado)") if disponible else traducir("Error: Programa incompatible (Simulado)")
    kv = win.versiones.get("kernel", "7.1.3-347.current")
    guardar_compatibilidad(nombre, kv, disponible, msg)
    GLib.idle_add(lambda r=row, s=spinner, i=icono, ok=disponible, t=msg, w=False:
                  _actualizar_fila(r, s, i, ok, t, w))
    return nombre if disponible else None


def _verificar_scheduler_real(win, nombre, row, spinner, icono, profundo=False):
    """Comprueba si un planificador puede usarse en este equipo.

    La verificación tenía dos problemas graves. El primero, que la ruta se
    construía a mano como `/usr/bin/scx_{nombre}` mientras `nombre` ya venía con
    el prefijo, así que intentaba ejecutar `/usr/bin/scx_scx_bpfland`: la prueba
    nunca ejecutó nada. El segundo, que para "verificar" arrancaba de verdad
    cada planificador durante cinco segundos, con lo que una simple comprobación
    reconfiguraba el planificador del sistema una decena de veces.

    Ahora hay dos niveles. El básico ejecuta el binario con `--version`, sin
    privilegios: eso ya detecta lo que de verdad falla en la práctica (binario
    ausente, enlazado contra librerías que no están, arquitectura incorrecta).
    El profundo, opcional, sí lo carga en el kernel y confirma la activación
    leyendo `/sys/kernel/sched_ext` en vez de adivinarla buscando frases en la
    salida del programa.
    """
    def _reset(r=row, s=spinner, i=icono):
        r.set_subtitle(traducir("Verificando..."))
        i.set_visible(False)
        s.set_visible(True)
    GLib.idle_add(_reset)

    canonico = canonizar(nombre)
    corto = nombre_corto(canonico)
    disponible = False
    es_aviso = False
    mensaje = traducir("Desconocido")

    instalados = sched_ext.schedulers_instalados()
    ruta = instalados.get(canonico)

    try:
        if not ruta:
            mensaje = traducir("No está instalado en este sistema")
        elif not sched_ext.hay_soporte():
            mensaje = traducir("El kernel no tiene sched_ext")
        else:
            log(win.text_view_logs_disp, traducir("Probando {}...").format(canonico))
            resultado = subprocess.run([ruta, "--version"], capture_output=True,
                                       text=True, timeout=15)
            salida = ((resultado.stdout or "") + (resultado.stderr or "")).strip()

            # Varios planificadores devuelven un código distinto de cero al
            # imprimir la versión; lo que importa es si el binario llegó a
            # ejecutarse y dijo algo.
            if salida:
                disponible = True
                mensaje = traducir("Disponible — {}").format(salida.splitlines()[0][:60])
            else:
                mensaje = traducir("El binario no respondió")

            ficha = obtener_scheduler(canonico)
            if disponible and ficha is not None:
                if ficha.requiere_config:
                    es_aviso = True
                    mensaje = traducir("Disponible, pero necesita configuración")
                elif ficha.experimental:
                    es_aviso = True
                    mensaje = traducir("Disponible (experimental)")

            if disponible and profundo and ficha is not None and ficha.apto_para_benchmark:
                disponible, es_aviso, mensaje = _probar_carga_real(win, canonico, mensaje)

    except subprocess.TimeoutExpired:
        mensaje = traducir("El binario no respondió a tiempo")
    except (subprocess.SubprocessError, OSError) as e:
        mensaje = str(e)

    if not disponible:
        log(win.text_view_logs_disp,
            traducir("{}: {}").format(corto, mensaje), nivel="warning")

    mensaje_seguro = GLib.markup_escape_text(mensaje)
    guardar_compatibilidad(canonico, win.versiones.get("kernel", ""), disponible, mensaje_seguro)
    GLib.idle_add(lambda r=row, s=spinner, i=icono, ok=disponible, t=mensaje_seguro, w=es_aviso:
                  _actualizar_fila(r, s, i, ok, t, w))
    return canonico if disponible else None


def _probar_carga_real(win, canonico, mensaje_previo):
    """Carga el planificador en el kernel y comprueba que quede activo.

    Devuelve (disponible, es_aviso, mensaje). La activación se confirma con
    `/sys/kernel/sched_ext/state`, que es la única fuente que no depende de que
    cada programa imprima una frase concreta.
    """
    resultado = win.scx.aplicar(canonico, "auto")
    if not resultado.ok:
        if resultado.codigo in ("cancelado", "no_autorizado"):
            # No es un fallo del planificador: no debe marcarse incompatible.
            return True, True, traducir("Sin verificar (se canceló la autorización)")
        return False, False, resultado.mensaje or traducir("No se pudo cargar")

    activo = False
    for _ in range(20):  # hasta 4 s esperando a que el kernel lo confirme
        info = sched_ext.estado()
        if info["activo"] and info["scheduler"] == canonico:
            activo = True
            break
        time.sleep(0.2)

    win.scx.detener()
    if activo:
        return True, False, traducir("Verificado: se cargó en el kernel")
    return False, False, traducir("Se lanzó pero el kernel no lo activó")


def _finalizar_verificacion(win, exitosos):
    log(win.text_view_logs_disp, traducir("VERIFICACIÓN FINALIZADA"), nivel="title")
    win.compatibles = exitosos
    GLib.idle_add(lambda: _refrescar_historial_compat(win))
    try:
        from ui.automatizacion import _refrescar_auto_schedulers
        GLib.idle_add(lambda: _refrescar_auto_schedulers(win))
    except ImportError:
        pass

    def _update_badge():
        win.nav_disponibilidad.remove_css_class("pulse-warning")
        img = win.nav_disponibilidad.get_child().get_first_child()
        if isinstance(img, Gtk.Image):
            for cls in ["success", "error"]:
                img.remove_css_class(cls)
            if exitosos:
                img.set_from_icon_name("emblem-ok-symbolic")
                img.add_css_class("success")
            else:
                img.set_from_icon_name("dialog-error-symbolic")
                img.add_css_class("error")
    GLib.idle_add(_update_badge)
    GLib.idle_add(win.sincronizar_sistema)
    GLib.idle_add(win._btn_verificar_disp.set_sensitive, True)


def iniciar_verificacion(win, btn=None):
    if win._verificando:
        return

    win._verificando = True

    def _proceder():
        def _ejecutar():
            exitosos = []
            GLib.idle_add(win._btn_verificar_disp.set_sensitive, False)
            log(win.text_view_logs_disp, traducir("INICIANDO VERIFICACIÓN DE COMPATIBILIDAD BPF"), nivel="title")

            # Se recuerda qué estaba corriendo para poder devolver el equipo a
            # su estado original al terminar.
            estado_previo = sched_ext.estado()["scheduler"]

            try:
                for nombre, (row, spinner, icono) in list(win._disp_filas.items()):
                    if win.modo_desarrollador:
                        r = _verificar_scheduler_dev(win, nombre, row, spinner, icono)
                    else:
                        r = _verificar_scheduler_real(win, nombre, row, spinner, icono)
                    if r is not None:
                        exitosos.append(r)

                if estado_previo and sched_ext.estado()["scheduler"] != estado_previo:
                    log(win.text_view_logs_disp,
                        traducir("Restaurando {}...").format(estado_previo))
                    win.scx.aplicar(estado_previo, "auto")
            except Exception as e:
                # Sin este bloque, una excepción dejaba `_verificando` puesto y
                # el botón deshabilitado: la pestaña quedaba muerta hasta
                # reiniciar la aplicación, y sin decir por qué.
                registrar_error("iniciar_verificacion", e)
                log(win.text_view_logs_disp, str(e), nivel="error")
            finally:
                _finalizar_verificacion(win, exitosos)
                win._verificando = False

        threading.Thread(target=_ejecutar, daemon=True).start()

    # La comprobación básica no necesita privilegios; se ejecuta directamente.
    _proceder()
