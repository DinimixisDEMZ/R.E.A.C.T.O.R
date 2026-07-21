"""
Pestaña de Automatización: Detección inteligente del mejor scheduler.
"""

import copy
import datetime
import json
import math
import random
import secrets
import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib

from core.benchmark import correr_benchmark
from core.operations import OperationCancelled
from core.hybrid import correr_hybrid
from core.scx import BASE_SYSTEM_NAME, ScxState
from core.scoring import calcular_scores_finales, calcular_valor_grafico, _MAPA_CHART, HYBRID_TYPES
from core.database import guardar_run_completo, consultar_runs_auto, cargar_resultados_de_run
from utils.helpers import log
from widgets.legend import crear_chip_leyenda


TIPOS_CANONICOS = (
    "cpu",
    "threads",
    "memory",
    "latencia_fork",
    "latencia_compile",
    "latencia_loaded",
)
PLAN_PRUEBAS = (
    ("cpu", "Latencia"),
    ("threads", "Multitarea"),
    ("memory", "Eficiencia"),
    ("fork", "Fork+Exec"),
    ("compile", "Compilación"),
    ("loaded", "Bajo Carga"),
)
PESOS_PREDETERMINADOS = (0.45, 0.45, 0.10)
SEGUNDOS_BENCHMARK = 5
MAX_ESPERA_TERMICA = 15


def _normalizar_pesos(pesos):
    """Valida tres pesos no negativos y devuelve una suma estable de uno."""
    try:
        valores = tuple(pesos)
    except TypeError as exc:
        raise ValueError("Se requieren exactamente tres pesos.") from exc
    if len(valores) != 3:
        raise ValueError("Se requieren exactamente tres pesos.")

    numeros = []
    for valor in valores:
        if isinstance(valor, bool):
            raise ValueError("Los pesos deben ser números finitos no negativos.")
        try:
            numero = float(valor)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(
                "Los pesos deben ser números finitos no negativos."
            ) from exc
        if not math.isfinite(numero) or numero < 0:
            raise ValueError("Los pesos deben ser números finitos no negativos.")
        numeros.append(numero)

    escala = max(numeros)
    if escala <= 0:
        raise ValueError("Al menos un peso debe ser mayor que cero.")
    escalados = tuple(numero / escala for numero in numeros)
    total = math.fsum(escalados)
    return tuple(numero / total for numero in escalados)


def _preparar_orden_candidatos(schedulers, semilla=None):
    """Incluye Sistema Base y mezcla candidatos de forma reproducible."""
    unicos = []
    vistos = {BASE_SYSTEM_NAME.casefold()}
    for scheduler in schedulers:
        if not isinstance(scheduler, str):
            continue
        nombre = scheduler.strip()
        clave = nombre.casefold()
        if not nombre or clave in vistos:
            continue
        vistos.add(clave)
        unicos.append(nombre)
    if not unicos:
        raise ValueError("Seleccione al menos un scheduler SCX.")

    if semilla is None:
        semilla = secrets.randbits(64)
    if isinstance(semilla, bool) or not isinstance(semilla, int):
        raise ValueError("La semilla del orden debe ser un entero.")

    orden = [BASE_SYSTEM_NAME, *unicos]
    random.Random(semilla).shuffle(orden)
    return tuple(orden), semilla


def _crear_metadata_auto(
    orden,
    pesos,
    temperaturas,
    configuracion,
    estado,
    semilla,
):
    """Construye metadata JSON-safe sin depender de GTK ni mutar entradas."""
    if estado not in {"completed", "partial"}:
        raise ValueError("El estado automático debe ser completed o partial.")
    pesos = tuple(float(peso) for peso in pesos)
    if len(pesos) != 3:
        raise ValueError("Se requieren exactamente tres pesos efectivos.")
    metadata = {
        "candidate_order": list(orden),
        "shuffle_seed": semilla,
        "effective_weights": {
            "potencia": pesos[0],
            "respuesta": pesos[1],
            "fluidez": pesos[2],
        },
        "temperatures": copy.deepcopy(dict(temperaturas or {})),
        "configuration": copy.deepcopy(dict(configuracion or {})),
        "status": estado,
    }
    development_mode = metadata["configuration"].get("development_mode")
    if isinstance(development_mode, bool):
        metadata["development_mode"] = development_mode
    for clave in ("scheduler_snapshot", "compatibility_context"):
        if clave in metadata["configuration"]:
            metadata[clave] = copy.deepcopy(metadata["configuration"][clave])
    return metadata


def _recomendacion_desde_scores(scores):
    """Evita proclamar un ganador cuando solo existe Sistema Base."""
    if not scores or len(scores) < 2:
        return None
    if not any(nombre != BASE_SYSTEM_NAME for nombre in scores):
        return None
    return max(scores, key=lambda nombre: scores[nombre]["score"])


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


class _ScxEnSesion:
    """Delega lecturas al manager y aplica estados solo mediante la sesión."""

    def __init__(self, manager, sesion):
        self._manager = manager
        self._sesion = sesion

    def aplicar(self, objetivo):
        return self._sesion.aplicar(objetivo)

    def __getattr__(self, nombre):
        return getattr(self._manager, nombre)


def _conservar_ganador_en_sesion(sesion, objetivo):
    keep_current = getattr(sesion, "keep_current_as_winner", None)
    if callable(keep_current):
        return keep_current()
    scheduler = objetivo.scheduler or BASE_SYSTEM_NAME
    return sesion.conservar_ganador(scheduler, objetivo.mode or "auto")


def _resetear_grafico(grafico):
    """Usa la API del gráfico cuando exista y completa el fallback antiguo."""
    reset = getattr(grafico, "reset", None)
    if callable(reset):
        reset()
        return

    detener_pulso = getattr(grafico, "detener_pulso", None)
    if callable(detener_pulso):
        detener_pulso()
    cantidad = int(getattr(grafico, "num_categorias", 6) or 6)
    grafico.datos_raw = {}
    grafico.valores_animados = {}
    grafico.max_por_categoria = [0.0] * cantidad
    grafico.max_animados = [0.0] * cantidad
    grafico.ocultos = set()
    grafico.focus_animado = {}
    grafico.highlight_sc = None
    grafico._hover_x = 0.0
    grafico._hover_y = 0.0
    grafico._pulse_active = False
    grafico.queue_draw()


def _eliminar_fuente_auto(win, atributo):
    source_id = getattr(win, atributo, None)
    if source_id is None:
        return
    source_remove = getattr(GLib, "source_remove", None)
    if callable(source_remove):
        try:
            source_remove(source_id)
        except (AttributeError, TypeError):
            pass
    setattr(win, atributo, None)


def _cancelar_timers_auto(win):
    _eliminar_fuente_auto(win, "_auto_progress_timer_id")
    _eliminar_fuente_auto(win, "_auto_hide_timer_id")


def _nueva_generacion_auto(win):
    generacion = int(getattr(win, "_auto_generation", 0) or 0) + 1
    win._auto_generation = generacion
    return generacion


def _generacion_auto_vigente(win, generacion):
    return (
        getattr(win, "_ui_alive", True)
        and generacion == getattr(win, "_auto_generation", None)
    )


def _vaciar_leyenda(win):
    leyenda = getattr(win, "box_leyenda", None)
    if leyenda is None:
        return
    while (child := leyenda.get_first_child()):
        leyenda.remove(child)


def _operacion_sigue_activa(win, operation_id):
    if operation_id is None:
        return False
    operaciones = getattr(win, "operaciones", None)
    if operaciones is None:
        return False
    try:
        state = operaciones.state
    except (AttributeError, RuntimeError):
        return False
    return (
        state is not None
        and getattr(state, "operation_id", None) == operation_id
    )


def invalidar_estado_automatizacion(win):
    """Descarta toda presentación y callback perteneciente al run anterior."""
    _cancelar_timers_auto(win)
    generacion = _nueva_generacion_auto(win)

    win.en_proceso_auto = False
    win._auto_operation_id = None
    win._brutos_finales = {}
    win._scores_finales = {}
    win.ganador_final = None
    win._auto_contexto_aplicable = False
    win._auto_permitir_aplicar = False
    win._auto_pesos_validos = False
    win._auto_development_mode = None
    win._auto_source_versions = None
    win._auto_compatibility_context = None
    win._auto_source_status = None

    boton_auto = getattr(win, "btn_auto", None)
    if boton_auto is not None:
        boton_auto.set_label("Determinar")
        boton_auto.set_sensitive(True)
        boton_auto.add_css_class("suggested-action")
        boton_auto.remove_css_class("destructive-action")

    boton_aplicar = getattr(win, "btn_aplicar_recomendado", None)
    apply_operation_id = getattr(win, "_auto_apply_operation_id", None)
    if not _operacion_sigue_activa(win, apply_operation_id):
        win._auto_apply_operation_id = None
    if boton_aplicar is not None:
        boton_aplicar.set_label("Aplicar recomendado")
        boton_aplicar.set_visible(False)
        boton_aplicar.set_sensitive(False)

    if hasattr(win, "_filas_ranking") and hasattr(win, "fila_ganador"):
        _limpiar_filas_ranking(win)
        win.fila_ganador.set_title("Motor en reposo")
        win.fila_ganador.set_subtitle("Esperando al escaneo...")
        win.fila_ganador.set_expanded(False)

    grafico = getattr(win, "grafico", None)
    if grafico is not None:
        _resetear_grafico(grafico)
    _vaciar_leyenda(win)

    win._historial_runs = []
    win._indice_historial = -1
    if hasattr(win, "lbl_nav"):
        win.lbl_nav.set_label("")
    if hasattr(win, "btn_nav_prev"):
        win.btn_nav_prev.set_sensitive(False)
    if hasattr(win, "btn_nav_next"):
        win.btn_nav_next.set_sensitive(False)

    if hasattr(win, "revealer_pesos"):
        win.revealer_pesos.set_reveal_child(False)
    if hasattr(win, "barra_progreso"):
        win.barra_progreso.set_visible(False)
        win.barra_progreso.set_fraction(0.0)
    if hasattr(win, "revealer_tiempo"):
        win.revealer_tiempo.set_reveal_child(False)
    win.progreso_actual = 0.0
    win.progreso_objetivo = 0.0
    win.segundos_actuales = 0.0
    win.segundos_objetivos = 0.0
    return generacion


def _invalidar_auto_schedulers(win):
    """Vacía el checklist cuando la compatibilidad deja de ser válida."""
    for row, _check in tuple(win._auto_sched_checks.values()):
        win._auto_sched_listbox.remove(row)
    win._auto_sched_checks.clear()
    _actualizar_subtitulo_scheds(win)


def _toggle_all_scheds(win, state):
    for _, check in win._auto_sched_checks.values():
        check.set_active(state)
    _actualizar_subtitulo_scheds(win)


def _actualizar_subtitulo_scheds(win):
    if not win._auto_sched_checks:
        win._auto_expander.set_subtitle("0/0 seleccionados")
        win._auto_expander.set_visible(False)
        return
    total = len(win._auto_sched_checks)
    checked = sum(1 for _, c in win._auto_sched_checks.values() if c.get_active())
    win._auto_expander.set_subtitle(f"{checked}/{total} seleccionados")
    win._auto_expander.set_visible(total > 0)


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


def _normalizar_snapshot_nombres(nombres):
    if nombres is None:
        return ()
    if isinstance(nombres, str):
        nombres = (nombres,)
    try:
        nombres = tuple(nombres)
    except Exception:
        return ()
    unicos = []
    vistos = set()
    for valor in nombres:
        if not isinstance(valor, str):
            continue
        nombre = valor.strip()
        clave = nombre.casefold()
        if not nombre or clave == BASE_SYSTEM_NAME.casefold() or clave in vistos:
            continue
        vistos.add(clave)
        unicos.append(nombre)
    return tuple(unicos)


def _canonizar_contexto_compatibilidad(contexto):
    if contexto is None:
        return None
    try:
        serializado = json.dumps(
            contexto,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return json.loads(serializado)
    except (TypeError, ValueError, OverflowError, json.JSONDecodeError):
        return None


def _versiones_relevantes(versiones):
    if not isinstance(versiones, dict):
        return {"kernel": None, "scxctl": None}
    return {
        "kernel": versiones.get("kernel"),
        "scxctl": versiones.get("scxctl"),
    }


def _snapshot_scheduler_actual(win, *, explicito=True):
    if hasattr(win, "_scheduler_snapshot"):
        valores = getattr(win, "_scheduler_snapshot", None)
    elif explicito:
        raise ValueError(
            "No hay un snapshot vigente de schedulers; actualice la disponibilidad."
        )
    else:
        valores = _nombres_desde_modelo(win)
    return _normalizar_snapshot_nombres(valores)


def _contexto_compatibilidad_vigente(win, nombres):
    try:
        from ui.disponibilidad import contexto_compatibilidad_actual

        calculado = _canonizar_contexto_compatibilidad(
            contexto_compatibilidad_actual(win, nombres)
        )
    except Exception:
        return None
    almacenado = _canonizar_contexto_compatibilidad(
        getattr(win, "_compatibility_context", None)
    )
    if calculado is None or almacenado is None or calculado != almacenado:
        return None
    return calculado


def _seleccionados_auto(win):
    checks = getattr(win, "_auto_sched_checks", {})
    seleccionados = (
        nombre
        for nombre, (_row, check) in checks.items()
        if check.get_active()
    )
    return _normalizar_snapshot_nombres(seleccionados)


def _capturar_huella_auto(win):
    seleccionados = _seleccionados_auto(win)
    if not seleccionados:
        raise ValueError("Seleccione al menos un scheduler SCX.")

    pesos = _normalizar_pesos(
        (
            win.slider_pot.get_value(),
            win.slider_resp.get_value(),
            win.slider_flu.get_value(),
        )
    )
    snapshot = _snapshot_scheduler_actual(win)
    compatibles = getattr(win, "compatibles", None)
    if compatibles is None:
        raise ValueError(
            "La compatibilidad no está verificada para el snapshot actual."
        )
    compatibles = _normalizar_snapshot_nombres(compatibles)
    if not set(seleccionados).issubset(snapshot):
        raise ValueError(
            "Cambió la lista de schedulers; actualice la disponibilidad."
        )
    if not set(seleccionados).issubset(compatibles):
        raise ValueError(
            "La selección contiene schedulers sin compatibilidad vigente."
        )

    contexto = _contexto_compatibilidad_vigente(win, snapshot)
    if contexto is None:
        raise ValueError(
            "La caché de compatibilidad no corresponde al entorno actual."
        )
    return {
        "seleccionados": seleccionados,
        "pesos": pesos,
        "development_mode": bool(win.modo_desarrollador),
        "scheduler_snapshot": snapshot,
        "compatibility_context": contexto,
        "source_versions": _versiones_relevantes(getattr(win, "versiones", {})),
    }


def _configuracion_autorizada_coincide(win, configuracion):
    requeridas = {
        "seleccionados",
        "pesos",
        "development_mode",
        "scheduler_snapshot",
        "compatibility_context",
        "source_versions",
    }
    if not isinstance(configuracion, dict) or not requeridas.issubset(configuracion):
        return False
    try:
        actual = _capturar_huella_auto(win)
        esperado = {
            "seleccionados": tuple(configuracion["seleccionados"]),
            "pesos": tuple(configuracion["pesos"]),
            "development_mode": configuracion["development_mode"],
            "scheduler_snapshot": tuple(configuracion["scheduler_snapshot"]),
            "compatibility_context": _canonizar_contexto_compatibilidad(
                configuracion["compatibility_context"]
            ),
            "source_versions": _versiones_relevantes(
                configuracion["source_versions"]
            ),
        }
    except Exception:
        return False
    if esperado["compatibility_context"] is None or esperado != actual:
        return False

    semilla = configuracion.get("semilla")
    if isinstance(semilla, bool) or not isinstance(semilla, int):
        return False
    try:
        orden = tuple(configuracion.get("orden", ()))
    except TypeError:
        return False
    candidatos = (*esperado["seleccionados"], BASE_SYSTEM_NAME)
    return (
        len(orden) == len(candidatos)
        and len(set(orden)) == len(orden)
        and set(orden) == set(candidatos)
    )


def _refrescar_auto_schedulers(win, nombres=None):
    """Actualiza el checklist desde un snapshot ya obtenido fuera de GTK."""
    snapshot = _normalizar_snapshot_nombres(
        _nombres_desde_modelo(win) if nombres is None else nombres
    )
    if not bool(win.modo_desarrollador):
        compatibles = getattr(win, "compatibles", None)
        if compatibles is None:
            snapshot = ()
        else:
            permitidos = {
                nombre.casefold()
                for nombre in compatibles
                if isinstance(nombre, str)
            }
            snapshot = tuple(
                nombre for nombre in snapshot if nombre.casefold() in permitidos
            )
    nombres = snapshot

    # Filas existentes
    existentes = set(win._auto_sched_checks.keys())

    # Eliminar las que ya no están
    for nombre in existentes - set(nombres):
        row, _ = win._auto_sched_checks.pop(nombre)
        win._auto_sched_listbox.remove(row)

    # Añadir las nuevas
    for nombre in nombres:
        if nombre in win._auto_sched_checks:
            continue
        check = Gtk.CheckButton()
        row = Adw.ActionRow(title=nombre, activatable_widget=check)
        row.add_suffix(check)
        check.set_active(True)
        check.connect("toggled", lambda _check: _actualizar_subtitulo_scheds(win))
        win._auto_sched_checks[nombre] = (row, check)
        win._auto_sched_listbox.append(row)

    _actualizar_subtitulo_scheds(win)
    scores = getattr(win, "_scores_finales", None) or {}
    if scores:
        _actualizar_recomendacion_ranking(win, scores)


def setup_automatizacion_ui(win):
    """Construye la interfaz de la pestaña Automatización (Detección Inteligente).
    
    Args:
        win: Instancia de VentanaSimple
    """
    pref_page = Adw.PreferencesPage()
    grupo_auto = Adw.PreferencesGroup(title="Detección Inteligente", description="Escanear rendimiendo general de cada planificador y obtener el mejor.")

    win.barra_progreso = Gtk.ProgressBar(margin_top=6, margin_bottom=6, visible=False)
    win.revealer_tiempo = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
    win.label_tiempo = Gtk.Label(label="", css_classes=["caption", "dim-label"], margin_bottom=12)
    win.revealer_tiempo.set_child(win.label_tiempo)

    grupo_auto.add(win.barra_progreso)
    grupo_auto.add(win.revealer_tiempo)

    win.btn_auto = Gtk.Button(label="Determinar", css_classes=["suggested-action"], margin_top=6, margin_bottom=6)
    win.btn_auto.connect("clicked", lambda b: gestionar_click_auto(win, b))
    grupo_auto.add(win.btn_auto)

    win._auto_sched_checks = {}
    win._auto_expander = Adw.ExpanderRow(
        title="Planificadores a analizar",
        subtitle="",
        icon_name="org.gnome.Settings-applications-symbolic",
        expanded=False
    )

    btn_select_all = Gtk.Button(label="Marcar todos", css_classes=["flat"])
    btn_select_none = Gtk.Button(label="Desmarcar todos", css_classes=["flat"])
    btn_select_all.connect("clicked", lambda b: _toggle_all_scheds(win, True))
    btn_select_none.connect("clicked", lambda b: _toggle_all_scheds(win, False))

    caja_toggle_scheds = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, margin_start=12, margin_top=6, margin_bottom=6)
    caja_toggle_scheds.append(Gtk.Label(label="Seleccionar:", css_classes=["dim-label"]))
    caja_toggle_scheds.append(btn_select_all)
    caja_toggle_scheds.append(btn_select_none)
    win._auto_expander.add_row(caja_toggle_scheds)

    win._auto_sched_listbox = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
    win._auto_expander.add_row(win._auto_sched_listbox)

    grupo_auto.add(win._auto_expander)

    win.grupo_ganador = Adw.PreferencesGroup(title="Resultado del Diagnóstico")
    win.fila_ganador = Adw.ExpanderRow(title="Motor en reposo", subtitle="Esperando al escaneo...", icon_name="org.gnome.Settings-device-diagnostics-symbolic")
    win._filas_ranking = []
    win.grupo_ganador.add(win.fila_ganador)
    win.btn_aplicar_recomendado = Gtk.Button(
        label="Aplicar recomendado",
        css_classes=["suggested-action"],
        margin_top=6,
        margin_bottom=6,
        visible=False,
        sensitive=False,
    )
    win.btn_aplicar_recomendado.connect(
        "clicked", lambda _button: confirmar_aplicar_recomendado(win)
    )
    win.grupo_ganador.add(win.btn_aplicar_recomendado)

    grupo_logs_auto = Adw.PreferencesGroup(title="Registro de Optimización")
    win.expander_logs_auto = Adw.ExpanderRow(title="Terminal de Análisis", subtitle="Registro técnico detallado", icon_name="utilities-terminal-symbolic")

    win.text_view_logs_auto = Gtk.TextView(editable=False, cursor_visible=False, monospace=True, css_classes=["card"])
    caja_log_auto = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
    scrolled_auto = Gtk.ScrolledWindow(min_content_height=300, vexpand=True)
    scrolled_auto.set_child(win.text_view_logs_auto)
    caja_log_auto.append(scrolled_auto)
    win.expander_logs_auto.add_row(caja_log_auto)
    grupo_logs_auto.add(win.expander_logs_auto)

    grupo_visual = Adw.PreferencesGroup(title="Análisis en Tiempo Real")
    caja_visual = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=6, margin_bottom=6)

    frame_grafico = Gtk.Frame(css_classes=["card"])
    frame_grafico.set_child(win.grafico)
    caja_visual.append(frame_grafico)

    win.box_leyenda = Adw.WrapBox(
        halign=Gtk.Align.CENTER,
        child_spacing=12,
        line_spacing=12,
        margin_top=10
    )
    caja_visual.append(win.box_leyenda)
    grupo_visual.add(caja_visual)

    win._historial_runs = []
    win._indice_historial = -1
    win._ajustando_pesos = False
    win._auto_operation_id = None
    win._auto_apply_operation_id = None
    win._auto_generation = 0
    win._auto_progress_timer_id = None
    win._auto_hide_timer_id = None
    win._auto_contexto_aplicable = False
    win._auto_permitir_aplicar = False
    win._auto_pesos_validos = True
    win._auto_development_mode = None
    win._auto_source_versions = None
    win._auto_compatibility_context = None
    win._auto_source_status = None
    win._pesos_auto_efectivos = PESOS_PREDETERMINADOS

    grupo_pesos = Adw.PreferencesGroup(title="Ajustar Pesos", description="Modifique la importancia de cada dimensión. Los resultados se recalculan al instante.")

    win.revealer_pesos = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
    caja_pesos = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=6, margin_bottom=6)

    def _crear_slider_peso(nombre, default):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12, margin_start=6, margin_end=6)
        lbl = Gtk.Label(label=nombre, width_chars=12, xalign=1)
        lbl_val = Gtk.Label(label=f"{default:.0f}%", css_classes=["monospace", "accent"], width_chars=4, xalign=0)
        adj = Gtk.Adjustment(value=default, lower=0, upper=100, step_increment=1, page_increment=10)
        scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adj, hexpand=True, draw_value=False)
        scale.set_size_request(200, -1)
        row.append(lbl)
        row.append(scale)
        row.append(lbl_val)
        return row, scale, lbl_val

    win._row_pot, win.slider_pot, win._lbl_pot = _crear_slider_peso("Potencia", 45)
    win._row_resp, win.slider_resp, win._lbl_resp = _crear_slider_peso("Respuesta", 45)
    win._row_flu, win.slider_flu, win._lbl_flu = _crear_slider_peso("Fluidez", 10)

    caja_pesos.append(win._row_pot)
    caja_pesos.append(win._row_resp)
    caja_pesos.append(win._row_flu)

    btn_reset_pesos = Gtk.Button(label="Restaurar 45/45/10", css_classes=["flat"], margin_top=6, halign=Gtk.Align.CENTER)
    btn_reset_pesos.connect("clicked", lambda b: _restaurar_pesos(win))

    def _on_peso_changed(win, slider, lbl):
        if win._ajustando_pesos:
            return
        lbl.set_label(f"{slider.get_value():.0f}%")
        if hasattr(win, '_brutos_finales') and win._brutos_finales:
            _recalcular_ranking(win)

    win.slider_pot.connect("value-changed", lambda s: _on_peso_changed(win, s, win._lbl_pot))
    win.slider_resp.connect("value-changed", lambda s: _on_peso_changed(win, s, win._lbl_resp))
    win.slider_flu.connect("value-changed", lambda s: _on_peso_changed(win, s, win._lbl_flu))

    caja_pesos.append(btn_reset_pesos)
    win.revealer_pesos.set_child(caja_pesos)
    grupo_pesos.add(win.revealer_pesos)

    pref_page.add(grupo_auto)
    pref_page.add(grupo_visual)
    pref_page.add(win.grupo_ganador)
    pref_page.add(grupo_pesos)
    pref_page.add(grupo_logs_auto)

    header = Adw.HeaderBar()

    btn_info = Gtk.MenuButton(icon_name="dialog-information-symbolic")
    popover = Gtk.Popover()
    lbl_info = Gtk.Label(label="Procure no tener nada abierto\npara no afectar el análisis.", margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
    popover.set_child(lbl_info)
    btn_info.set_popover(popover)
    header.pack_start(btn_info)

    win.btn_nav_prev = Gtk.Button(icon_name="go-previous-symbolic", tooltip_text="Run anterior", sensitive=False)
    win.btn_nav_next = Gtk.Button(icon_name="go-next-symbolic", tooltip_text="Run siguiente", sensitive=False)
    win.lbl_nav = Gtk.Label(label="", css_classes=["caption", "dim-label"], margin_start=6, margin_end=6)

    win.btn_nav_prev.connect("clicked", lambda b: _navegar_historial(win, -1))
    win.btn_nav_next.connect("clicked", lambda b: _navegar_historial(win, 1))

    header.pack_start(win.btn_nav_prev)
    header.pack_start(win.lbl_nav)
    header.pack_start(win.btn_nav_next)

    btn_borrar = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Limpiar Análisis")
    btn_borrar.connect("clicked", lambda _button: limpiar_ranking_auto(win))
    header.pack_end(btn_borrar)

    _refrescar_historial(win)
    _cargar_ultimo_run(win)
    _refrescar_auto_schedulers(win)

    def cancelar_fuentes_al_cerrar(*_args):
        _cancelar_timers_auto(win)
        _nueva_generacion_auto(win)
        return False

    win.connect("close-request", cancelar_fuentes_al_cerrar)

    view = Adw.ToolbarView(content=pref_page)
    view.add_top_bar(header)
    win.pag_automatizacion.set_child(view)


def _capturar_configuracion_auto(win):
    configuracion = _capturar_huella_auto(win)
    orden, semilla = _preparar_orden_candidatos(
        configuracion["seleccionados"]
    )
    configuracion.update({
        "orden": orden,
        "semilla": semilla,
    })
    return configuracion


def gestionar_click_auto(win, btn):
    """Maneja el click del botón Determinar/Detener."""
    if win.en_proceso_auto:
        operation_id = win._auto_operation_id
        solicitada = win.operaciones.cancel_current(
            expected_name="automatizacion",
            expected_operation_id=operation_id,
        )
        if solicitada:
            win.btn_auto.set_label("Deteniendo...")
            win.btn_auto.set_sensitive(False)
            log(win.text_view_logs_auto, "DETENIENDO ANÁLISIS...", es_titulo=True)
        else:
            _mostrar_toast(
                win,
                "La automatización ya está finalizando y no admite cancelación.",
            )
        return

    if win.compatibles is None:
        _mostrar_toast(
            win,
            "Primero verifique la Disponibilidad para evitar bloqueos del sistema.",
            alta=True,
        )
        win.split_view.set_content(win.pag_disponibilidad)
        return

    try:
        configuracion = _capturar_configuracion_auto(win)
    except ValueError as exc:
        _mostrar_toast(win, str(exc), alta=True)
        return

    configuracion_autorizada = copy.deepcopy(configuracion)
    win.solicitar_sudo_si_necesario(
        lambda: iniciar_auto_test(win, btn, configuracion_autorizada)
    )


def _esperar_cancelable(token, segundos):
    if token.wait(max(0.0, float(segundos))):
        token.raise_if_cancelled()
    token.raise_if_cancelled()


def _temperatura_valida(valor):
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return None
    valor = float(valor)
    return valor if math.isfinite(valor) and 1.0 <= valor <= 150.0 else None


def _calibrar_termica(sensor, token, muestras=3, intervalo=0.5):
    valores = []
    for indice in range(muestras):
        token.raise_if_cancelled()
        temperatura = _temperatura_valida(sensor.obtener_temp())
        if temperatura is not None:
            valores.append(temperatura)
        if indice + 1 < muestras:
            _esperar_cancelable(token, intervalo)
    return math.fsum(valores) / len(valores) if valores else None


def _actualizar_progreso_ui(
    win,
    progreso,
    segundos,
    subtitulo,
    generacion,
):
    if (
        not getattr(win, "_ui_alive", True)
        or not getattr(win, "en_proceso_auto", False)
    ):
        return False
    if not _generacion_auto_vigente(win, generacion):
        return False

    progreso = max(0.0, min(1.0, progreso))
    segundos = max(0.0, segundos)
    win.progreso_actual = progreso
    win.progreso_objetivo = progreso
    win.segundos_actuales = segundos
    win.segundos_objetivos = segundos
    win.barra_progreso.set_fraction(progreso)
    minutos, segundos_visibles = int(segundos // 60), int(segundos % 60)
    win.label_tiempo.set_label(
        f"Tiempo estimado restante: {minutos:02d}:{segundos_visibles:02d}"
    )
    win.fila_ganador.set_subtitle(subtitulo)
    return False


def _configurar_grafico_auto(grafico):
    grafico.num_categorias = 6
    grafico.categorias = [
        "Context\nSwitch",
        "Carga\nMixta",
        "Mutex",
        "Fork",
        "Compile",
        "Bajo\nCarga",
    ]


def _poblar_grafico_desde_brutos(win, brutos):
    _configurar_grafico_auto(win.grafico)
    _resetear_grafico(win.grafico)
    _vaciar_leyenda(win)
    for scheduler, datos_scheduler in brutos.items():
        win.grafico.registrar_scheduler(scheduler)
        crear_chip_leyenda(scheduler, win.grafico, win.box_leyenda)
        for tipo, resultado in datos_scheduler.items():
            chart_idx = _MAPA_CHART.get(tipo)
            if chart_idx is None:
                continue
            valor = calcular_valor_grafico(resultado, tipo)
            win.grafico.actualizar_dato(scheduler, chart_idx, valor)
    win.grafico.queue_draw()


def _preparar_interfaz_auto(win, orden, generacion):
    _cancelar_timers_auto(win)
    win.en_proceso_auto = True
    win._auto_contexto_aplicable = False
    win._auto_permitir_aplicar = False
    win._auto_pesos_validos = True
    win.ganador_final = None
    win.btn_aplicar_recomendado.set_visible(False)
    win.btn_aplicar_recomendado.set_sensitive(False)
    win.btn_auto.set_label("Detener")
    win.btn_auto.set_sensitive(True)
    win.btn_auto.add_css_class("destructive-action")
    win.btn_auto.remove_css_class("suggested-action")
    win.btn_nav_prev.set_sensitive(False)
    win.btn_nav_next.set_sensitive(False)
    win.barra_progreso.set_visible(True)
    win.revealer_tiempo.set_reveal_child(True)
    win.progreso_actual = 0.0
    win.progreso_objetivo = 0.0
    win.segundos_actuales = 0.0
    win.segundos_objetivos = (
        len(orden) * len(PLAN_PRUEBAS) * SEGUNDOS_BENCHMARK
    )
    win.barra_progreso.set_fraction(0.0)
    win._auto_progress_timer_id = None

    _configurar_grafico_auto(win.grafico)
    _resetear_grafico(win.grafico)
    _vaciar_leyenda(win)
    _actualizar_progreso_ui(
        win,
        0.0,
        win.segundos_objetivos,
        "Preparando el análisis...",
        generacion,
    )


def _ejecucion_completa(orden, brutos, scores):
    esperados = set(TIPOS_CANONICOS)
    return set(scores) == set(orden) and all(
        esperados.issubset(brutos.get(scheduler, {})) for scheduler in orden
    )


def _actualizar_lider_provisional(win, generacion, lider):
    if (
        not getattr(win, "en_proceso_auto", False)
        or not _generacion_auto_vigente(win, generacion)
    ):
        return False
    win.fila_ganador.set_title(f"Mejor equilibrio provisional: {lider}")
    return False


def _ejecutar_mediciones(
    win,
    scx_manager,
    sensor,
    token,
    orden,
    pesos,
    modo_desarrollador,
    log_view,
    brutos,
    resultados,
    temperaturas,
    generacion,
):
    total_pasos = len(orden) * len(PLAN_PRUEBAS)
    pasos_completados = 0

    log(log_view, "Calibrando base térmica del sistema...", es_titulo=True)
    temperatura_base = _calibrar_termica(sensor, token)
    temperaturas["baseline_c"] = temperatura_base
    if temperatura_base is None or temperatura_base < 10.0:
        umbral = None
        log(log_view, "Sensor térmico no disponible. Se omite la espera térmica.")
    else:
        umbral = temperatura_base + 5.0
        log(
            log_view,
            f"Calibración completa: Base {temperatura_base:.1f}°C | "
            f"Umbral: {umbral:.1f}°C",
        )
    temperaturas["cooldown_threshold_c"] = umbral
    temperaturas["before_candidate_c"] = {}

    for scheduler in orden:
        token.raise_if_cancelled()
        temperatura = _temperatura_valida(sensor.obtener_temp())
        temperaturas["before_candidate_c"][scheduler] = temperatura

        espera = 0
        while (
            umbral is not None
            and temperatura is not None
            and temperatura > umbral
            and espera < MAX_ESPERA_TERMICA
        ):
            _programar_ui(
                win,
                _actualizar_progreso_ui,
                win,
                pasos_completados / total_pasos,
                (total_pasos - pasos_completados) * SEGUNDOS_BENCHMARK,
                f"Enfriando... {temperatura:.1f}°C (Límite: {umbral:.1f}°C)",
                generacion,
            )
            _esperar_cancelable(token, 1.0)
            espera += 1
            temperatura = _temperatura_valida(sensor.obtener_temp())
        if espera >= MAX_ESPERA_TERMICA and temperatura is not None and temperatura > umbral:
            log(
                log_view,
                "Continuando análisis: no se alcanzó el umbral térmico.",
                es_error=True,
            )

        token.raise_if_cancelled()
        objetivo = (
            ScxState()
            if scheduler == BASE_SYSTEM_NAME
            else ScxState(scheduler, "auto")
        )
        try:
            if scheduler == BASE_SYSTEM_NAME:
                log(
                    log_view,
                    "Evaluando rendimiento nativo del kernel...",
                    es_titulo=True,
                )
            else:
                log(log_view, f"Activando {scheduler}...", es_titulo=True)
            aplicar = getattr(scx_manager, "aplicar", None)
            if callable(aplicar):
                aplicar(objetivo)
            else:
                scx_manager.restaurar_estado(objetivo)
        except Exception as exc:
            log(
                log_view,
                f"No se pudo preparar {scheduler}: {exc}",
                es_error=True,
            )
            continue

        _esperar_cancelable(token, 1.5)
        brutos.setdefault(scheduler, {})

        for indice, (tipo_solicitado, nombre_prueba) in enumerate(PLAN_PRUEBAS):
            token.raise_if_cancelled()
            progreso = (pasos_completados + 1) / total_pasos
            restantes = total_pasos - (pasos_completados + 1)
            _programar_ui(
                win,
                _actualizar_progreso_ui,
                win,
                progreso,
                restantes * SEGUNDOS_BENCHMARK,
                f"Analizando {scheduler}: {nombre_prueba} ({indice + 1}/6)...",
                generacion,
            )

            if tipo_solicitado in HYBRID_TYPES:
                resultado = correr_hybrid(
                    tipo_solicitado,
                    scx_manager,
                    log_view,
                    tiempo=SEGUNDOS_BENCHMARK,
                    logs=True,
                    modo_dev=modo_desarrollador,
                    cancel_token=token,
                )
            else:
                resultado = correr_benchmark(
                    tipo_solicitado,
                    scx_manager,
                    log_view,
                    tiempo=SEGUNDOS_BENCHMARK,
                    logs=True,
                    modo_dev=modo_desarrollador,
                    cancel_token=token,
                )
            token.raise_if_cancelled()
            pasos_completados += 1

            if not resultado:
                log(
                    log_view,
                    f"{scheduler}: {nombre_prueba} no produjo un resultado válido.",
                    es_error=True,
                )
                continue
            tipo_resultado = resultado.get("tipo")
            if tipo_resultado not in TIPOS_CANONICOS:
                log(
                    log_view,
                    f"{scheduler}: tipo de resultado inesperado {tipo_resultado!r}.",
                    es_error=True,
                )
                continue
            if resultado.get("sched") != scheduler:
                log(
                    log_view,
                    f"{scheduler}: el resultado pertenece a "
                    f"{resultado.get('sched')!r}; se descarta.",
                    es_error=True,
                )
                continue

            brutos[scheduler][tipo_resultado] = resultado
            resultados.append(resultado)

        scores_vuelo = calcular_scores_finales(
            brutos,
            pesos=pesos,
            tipos=TIPOS_CANONICOS,
        )
        lider = _recomendacion_desde_scores(scores_vuelo)
        if lider is not None:
            _programar_ui(
                win,
                _actualizar_lider_provisional,
                win,
                generacion,
                lider,
            )


def _worker_automatizacion(
    win,
    handle,
    scx_manager,
    sensor,
    orden,
    pesos,
    modo_desarrollador,
    versiones,
    log_view,
    semilla,
    generacion,
    *,
    seleccionados=None,
    scheduler_snapshot=None,
    compatibility_context=None,
):
    resultado_final = {
        "brutos": {},
        "scores": {},
        "pesos": pesos,
        "orden": orden,
        "status": "partial",
        "run_id": None,
        "cancelado": False,
        "error": None,
        "development_mode": modo_desarrollador,
        "generation": generacion,
        "source_versions": copy.deepcopy(dict(versiones)),
        "compatibility_context": copy.deepcopy(compatibility_context),
    }
    brutos = resultado_final["brutos"]
    resultados = []
    temperaturas = {}
    seleccion_configurada = list(
        seleccionados
        if seleccionados is not None
        else (
            scheduler
            for scheduler in orden
            if scheduler != BASE_SYSTEM_NAME
        )
    )
    configuracion = {
        "benchmark_seconds": SEGUNDOS_BENCHMARK,
        "cooldown_max_seconds": MAX_ESPERA_TERMICA,
        "canonical_test_types": list(TIPOS_CANONICOS),
        "selected_scx": seleccion_configurada,
        "seleccionados": seleccion_configurada,
        "development_mode": modo_desarrollador,
    }
    if scheduler_snapshot is not None:
        configuracion["scheduler_snapshot"] = list(scheduler_snapshot)
    if compatibility_context is not None:
        configuracion["compatibility_context"] = copy.deepcopy(
            compatibility_context
        )

    sesion = None
    try:
        try:
            with scx_manager.sesion(handle.token) as sesion:
                estado_inicial = sesion.initial_state
                configuracion["initial_scheduler"] = (
                    estado_inicial.scheduler or BASE_SYSTEM_NAME
                )
                configuracion["initial_mode"] = estado_inicial.mode
                scx_en_sesion = _ScxEnSesion(scx_manager, sesion)
                argumentos_medicion = (
                    win,
                    scx_en_sesion,
                    sensor,
                    handle.token,
                    orden,
                    pesos,
                    modo_desarrollador,
                    log_view,
                    brutos,
                    resultados,
                    temperaturas,
                )
                _ejecutar_mediciones(
                    *argumentos_medicion,
                    generacion=generacion,
                )
                handle.check_cancelled()
            if not handle.token.seal():
                raise OperationCancelled("La operación fue cancelada.")
        except OperationCancelled:
            resultado_final["cancelado"] = True
            restore_error = getattr(sesion, "restore_error", None)
            if restore_error is not None:
                resultado_final["error"] = (
                    f"Falló la restauración SCX: {restore_error}"
                )
            log(log_view, "ANÁLISIS CANCELADO POR EL USUARIO", es_titulo=True)
        except Exception as exc:
            resultado_final["error"] = str(exc) or exc.__class__.__name__
            restore_error = getattr(sesion, "restore_error", None)
            if restore_error is not None and restore_error is not exc:
                resultado_final["error"] += (
                    f"; además falló la restauración SCX: {restore_error}"
                )
            log(
                log_view,
                f"La automatización falló: {resultado_final['error']}",
                es_error=True,
            )

        scores = calcular_scores_finales(
            brutos,
            pesos=pesos,
            tipos=TIPOS_CANONICOS,
        )
        resultado_final["scores"] = scores
        completado = (
            not resultado_final["cancelado"]
            and resultado_final["error"] is None
            and _ejecucion_completa(orden, brutos, scores)
        )
        estado = "completed" if completado else "partial"
        resultado_final["status"] = estado
        configuracion["eligible_candidates"] = list(scores)

        if resultados:
            metadata = _crear_metadata_auto(
                orden,
                pesos,
                temperaturas,
                configuracion,
                estado,
                semilla,
            )
            try:
                resultado_final["run_id"] = guardar_run_completo(
                    versiones,
                    resultados,
                    run_type="auto",
                    status=estado,
                    metadata=metadata,
                )
            except Exception as exc:
                detalle = str(exc) or exc.__class__.__name__
                resultado_final["error"] = (
                    f"{resultado_final['error']}; {detalle}"
                    if resultado_final["error"]
                    else detalle
                )
                log(
                    log_view,
                    f"No se pudo guardar el run automático: {detalle}",
                    es_error=True,
                )

        if scores:
            porcentajes = " | ".join(
                f"{peso * 100:.1f}%" for peso in pesos
            )
            log(
                log_view,
                f"ANÁLISIS FINAL (pesos efectivos: {porcentajes})",
                es_titulo=True,
            )
            for scheduler, data in scores.items():
                log(
                    log_view,
                    f"{scheduler.upper()} | Score: {data['score']:.1f}% "
                    f"(Pot: {data['pot'] / 100:.2f}, "
                    f"Resp: {data['resp'] / 100:.2f}, "
                    f"Flz: {data['flu'] / 100:.2f})",
                )
    except Exception as exc:
        resultado_final["error"] = str(exc) or exc.__class__.__name__
        log(
            log_view,
            f"Error exterior de automatización: {resultado_final['error']}",
            es_error=True,
        )
    finally:
        handle.release()
        _programar_ui(win, _finalizar_worker_auto, win, resultado_final)


def iniciar_auto_test(win, btn=None, configuracion=None):
    """Inicia el ciclo completo de detección automatizada."""
    try:
        if configuracion is None:
            configuracion = _capturar_configuracion_auto(win)
        campos_huella = {
            "seleccionados",
            "scheduler_snapshot",
            "compatibility_context",
            "source_versions",
        }
        tiene_huella = campos_huella.issubset(configuracion)
        ventana_real = any(
            hasattr(win, atributo)
            for atributo in (
                "_auto_sched_checks",
                "_scheduler_snapshot",
                "_compatibility_context",
            )
        )
        if tiene_huella:
            if not _configuracion_autorizada_coincide(win, configuracion):
                _mostrar_toast(
                    win,
                    "La configuración cambió durante la autorización; "
                    "inicie el análisis de nuevo.",
                    alta=True,
                )
                return
        elif ventana_real:
            _mostrar_toast(
                win,
                "La configuración autorizada no contiene un contexto válido.",
                alta=True,
            )
            return

        orden = tuple(configuracion["orden"])
        pesos = tuple(configuracion["pesos"])
        semilla = configuracion["semilla"]
        modo_desarrollador = configuracion["development_mode"]
        seleccionados = tuple(
            configuracion.get(
                "seleccionados",
                (
                    scheduler
                    for scheduler in orden
                    if scheduler != BASE_SYSTEM_NAME
                ),
            )
        )
        scheduler_snapshot = configuracion.get("scheduler_snapshot")
        compatibility_context = configuracion.get("compatibility_context")
    except (ValueError, OverflowError) as exc:
        _mostrar_toast(win, str(exc), alta=True)
        return
    except (KeyError, TypeError, AttributeError):
        _mostrar_toast(win, "La configuración del análisis no es válida.", alta=True)
        return

    if not isinstance(modo_desarrollador, bool):
        _mostrar_toast(win, "La procedencia del análisis no es válida.", alta=True)
        return
    if modo_desarrollador != bool(win.modo_desarrollador):
        _mostrar_toast(
            win,
            "El modo cambió durante la autorización; inicie el análisis de nuevo.",
            alta=True,
        )
        return

    handle = win.operaciones.try_acquire("automatizacion")
    if handle is None:
        _mostrar_operacion_ocupada(win)
        return

    scx_manager = win.scx
    sensor = win.sensor
    versiones = dict(win.versiones)
    log_view = win.text_view_logs_auto
    generacion = invalidar_estado_automatizacion(win)
    win._auto_operation_id = handle.operation_id
    win._pesos_auto_efectivos = pesos
    win._auto_pesos_validos = True
    win._auto_development_mode = modo_desarrollador
    if tiene_huella:
        win._auto_source_versions = copy.deepcopy(versiones)
        win._auto_compatibility_context = copy.deepcopy(
            compatibility_context
        )
        win._auto_source_status = "running"

    worker = lambda: _worker_automatizacion(
        win,
        handle,
        scx_manager,
        sensor,
        orden,
        pesos,
        modo_desarrollador,
        versiones,
        log_view,
        semilla,
        generacion,
        seleccionados=seleccionados,
        scheduler_snapshot=scheduler_snapshot,
        compatibility_context=compatibility_context,
    )
    try:
        _preparar_interfaz_auto(win, orden, generacion)
        threading.Thread(target=worker).start()
    except Exception as exc:
        handle.release()
        _finalizar_worker_auto(
            win,
            {
                "brutos": {},
                "scores": {},
                "pesos": pesos,
                "orden": orden,
                "status": "partial",
                "run_id": None,
                "cancelado": False,
                "error": str(exc) or exc.__class__.__name__,
                "development_mode": modo_desarrollador,
                "generation": generacion,
                "source_versions": copy.deepcopy(versiones),
                "compatibility_context": copy.deepcopy(
                    compatibility_context
                ),
            },
        )


def _limpiar_filas_ranking(win):
    for fila in win._filas_ranking:
        win.fila_ganador.remove(fila)
    win._filas_ranking.clear()


def _procedencia_auto_coincide(win):
    procedencia = getattr(win, "_auto_development_mode", None)
    return (
        isinstance(procedencia, bool)
        and procedencia == bool(win.modo_desarrollador)
    )


def _usa_contrato_seguridad_auto(win):
    return any(
        hasattr(win, atributo)
        for atributo in (
            "_auto_source_versions",
            "_auto_compatibility_context",
            "_auto_source_status",
            "_scheduler_snapshot",
            "_compatibility_context",
        )
    )


def _versiones_auto_coinciden(win):
    fuente = _versiones_relevantes(
        getattr(win, "_auto_source_versions", None)
    )
    actuales = _versiones_relevantes(getattr(win, "versiones", None))
    for clave in ("kernel", "scxctl"):
        origen = fuente[clave]
        actual = actuales[clave]
        if (
            not isinstance(origen, str)
            or not origen.strip()
            or not isinstance(actual, str)
            or not actual.strip()
            or origen != actual
        ):
            return False
    return True


def _candidato_aplicable_en_entorno(win, ganador):
    if not _usa_contrato_seguridad_auto(win):
        return True
    if not isinstance(ganador, str) or not ganador:
        return False
    if getattr(win, "_auto_source_status", None) != "completed":
        return False
    if not _versiones_auto_coinciden(win):
        return False

    try:
        snapshot = _snapshot_scheduler_actual(win)
    except ValueError:
        return False

    if ganador != BASE_SYSTEM_NAME:
        compatibles = getattr(win, "compatibles", None)
        if compatibles is None:
            return False
        if ganador not in _normalizar_snapshot_nombres(compatibles):
            return False
        if ganador not in snapshot:
            return False

    almacenado = _canonizar_contexto_compatibilidad(
        getattr(win, "_auto_compatibility_context", None)
    )
    vigente = _contexto_compatibilidad_vigente(win, snapshot)
    return almacenado is not None and almacenado == vigente


def _ocultar_recomendacion(win):
    win._auto_permitir_aplicar = False
    win.ganador_final = None
    win.btn_aplicar_recomendado.set_visible(False)
    win.btn_aplicar_recomendado.set_sensitive(False)


def _recomendacion_aplicable_actual(win, recomendado=None):
    ganador = getattr(win, "ganador_final", None)
    return bool(
        ganador
        and (recomendado is None or ganador == recomendado)
        and getattr(win, "_auto_contexto_aplicable", False)
        and getattr(win, "_auto_pesos_validos", False)
        and getattr(win, "_auto_permitir_aplicar", False)
        and _procedencia_auto_coincide(win)
        and _candidato_aplicable_en_entorno(win, ganador)
    )


def _actualizar_recomendacion_ranking(win, scores):
    ganador = _recomendacion_desde_scores(scores)
    aplicable = bool(
        ganador is not None
        and getattr(win, "_auto_contexto_aplicable", False)
        and getattr(win, "_auto_pesos_validos", False)
        and _procedencia_auto_coincide(win)
        and _candidato_aplicable_en_entorno(win, ganador)
    )
    win._auto_permitir_aplicar = aplicable
    win.ganador_final = ganador if aplicable else None
    win.btn_aplicar_recomendado.set_visible(aplicable)
    win.btn_aplicar_recomendado.set_sensitive(aplicable)
    if aplicable:
        score_ganador = scores[ganador]["score"]
        win.fila_ganador.set_title(f"Recomendado: {ganador}")
        win.fila_ganador.set_subtitle(
            f"'{ganador}' ofrece la mejor propuesta integral con un "
            f"{score_ganador:.1f}% de eficacia de sistema."
        )
    elif scores:
        win.fila_ganador.set_title("Resultados sin recomendación aplicable")
        win.fila_ganador.set_subtitle(
            "Se requiere una comparación completa con al menos un scheduler SCX."
        )


def _marcar_pesos_invalidos(win):
    win._auto_pesos_validos = False
    win._scores_finales = {}
    _ocultar_recomendacion(win)
    _limpiar_filas_ranking(win)
    win.fila_ganador.set_title("Pesos sin ponderación")
    win.fila_ganador.set_subtitle(
        "Asigne un valor mayor que cero a al menos una dimensión."
    )


def _poblar_ranking(win, pesos=None):
    """Puebla el ranking estricto de seis categorías y devuelve sus scores."""
    _limpiar_filas_ranking(win)
    brutos = getattr(win, "_brutos_finales", None)
    if not brutos:
        win._scores_finales = {}
        _ocultar_recomendacion(win)
        return {}

    try:
        pesos = _normalizar_pesos(
            pesos if pesos is not None else win._pesos_auto_efectivos
        )
    except ValueError:
        _marcar_pesos_invalidos(win)
        return {}
    win._auto_pesos_validos = True
    win._pesos_auto_efectivos = pesos
    scores = calcular_scores_finales(
        brutos,
        pesos=pesos,
        tipos=TIPOS_CANONICOS,
    )
    win._scores_finales = scores

    ordenados = sorted(scores.items(), key=lambda x: x[1]['score'], reverse=True)

    for idx, (name, data) in enumerate(ordenados):
        row = Adw.ActionRow(title=name)
        row.set_subtitle(
            f"Potencia: {data['pot']/100:.2f} | "
            f"Respuesta: {data['resp']/100:.2f} | "
            f"Fluidez: {data['flu']/100:.2f}"
        )

        talla_pct = max(70, 180 - (idx * 30))
        opacidad = max(0.4, 1.0 - (idx * 0.15))

        cls = ["bold"]
        if idx < 2:
            cls.append("accent")

        lbl_score = Gtk.Label(css_classes=cls)
        lbl_score.set_markup(f"<span size='{talla_pct}%'>{data['score']:.1f}%</span>")
        lbl_score.set_opacity(opacidad)
        row.add_suffix(lbl_score)

        if idx == 0:
            win.fila_ganador.set_expanded(True)
            row.add_css_class("success")

        win.fila_ganador.add_row(row)
        win._filas_ranking.append(row)

    _actualizar_recomendacion_ranking(win, scores)
    return scores


def _restaurar_pesos(win):
    """Restaura los sliders a 45/45/10."""
    win._ajustando_pesos = True
    win.slider_pot.set_value(45)
    win.slider_resp.set_value(45)
    win.slider_flu.set_value(10)
    win._lbl_pot.set_label("45%")
    win._lbl_resp.set_label("45%")
    win._lbl_flu.set_label("10%")
    win._ajustando_pesos = False
    if hasattr(win, '_brutos_finales') and win._brutos_finales:
        _recalcular_ranking(win)


def _recalcular_ranking(win):
    """Lee los sliders y repuebla el ranking."""
    try:
        pesos = _normalizar_pesos(
            (
                win.slider_pot.get_value(),
                win.slider_resp.get_value(),
                win.slider_flu.get_value(),
            )
        )
    except ValueError:
        _marcar_pesos_invalidos(win)
        return
    win._auto_pesos_validos = True
    win._pesos_auto_efectivos = pesos
    _poblar_ranking(win, pesos=pesos)


def _reconstruir_brutos(resultados):
    """Convierte resultados persistidos sin inventar métricas de respuesta."""
    brutos = {}
    for r in resultados:
        sc = r["scheduler_name"]
        tt = r["test_type"]
        if sc not in brutos:
            brutos[sc] = {}
        brutos[sc][tt] = {
            "tipo": tt,
            "valor": r["valor"],
            "response": r.get("response"),
            "response_kind": r.get("response_kind"),
            "p95": r.get("p95"),
            "fairness": r.get("fairness"),
            "sched": sc,
            "modo": r.get("modo", "auto"),
        }
    return brutos


def _pesos_desde_metadata(metadata):
    if not isinstance(metadata, dict):
        return None
    efectivos = metadata.get("effective_weights")
    if not isinstance(efectivos, dict):
        return None
    try:
        return _normalizar_pesos(
            (
                efectivos["potencia"],
                efectivos["respuesta"],
                efectivos["fluidez"],
            )
        )
    except (KeyError, ValueError):
        return None


def _development_mode_desde_metadata(metadata):
    if not isinstance(metadata, dict):
        return None
    development_mode = metadata.get("development_mode")
    if isinstance(development_mode, bool):
        return development_mode
    configuracion = metadata.get("configuration")
    if not isinstance(configuracion, dict):
        return None
    development_mode = configuracion.get("development_mode")
    return development_mode if isinstance(development_mode, bool) else None


def _contexto_compatibilidad_desde_metadata(metadata):
    if not isinstance(metadata, dict):
        return None
    contexto = metadata.get("compatibility_context")
    if contexto is None:
        configuracion = metadata.get("configuration")
        if isinstance(configuracion, dict):
            contexto = configuracion.get("compatibility_context")
    return _canonizar_contexto_compatibilidad(contexto)


def _metadata_auto_aplicable(metadata):
    if not isinstance(metadata, dict) or metadata.get("status") != "completed":
        return False
    if _pesos_desde_metadata(metadata) is None:
        return False
    configuracion = metadata.get("configuration")
    if not isinstance(configuracion, dict):
        return False

    modo_superior = metadata.get("development_mode")
    modo_configurado = configuracion.get("development_mode")
    if (
        not isinstance(modo_superior, bool)
        or not isinstance(modo_configurado, bool)
        or modo_superior != modo_configurado
    ):
        return False

    contexto_superior = _canonizar_contexto_compatibilidad(
        metadata.get("compatibility_context")
    )
    contexto_configurado = _canonizar_contexto_compatibilidad(
        configuracion.get("compatibility_context")
    )
    if (
        contexto_superior is None
        or contexto_configurado is None
        or contexto_superior != contexto_configurado
    ):
        return False

    orden = _orden_esperado_desde_run({"metadata": metadata})
    seleccionados = configuracion.get("seleccionados")
    selected_scx = configuracion.get("selected_scx")
    snapshot_superior = metadata.get("scheduler_snapshot")
    snapshot_configurado = configuracion.get("scheduler_snapshot")
    colecciones = (
        seleccionados,
        selected_scx,
        snapshot_superior,
        snapshot_configurado,
    )
    if orden is None or any(
        not isinstance(valores, (list, tuple)) for valores in colecciones
    ):
        return False
    seleccionados_normalizados = _normalizar_snapshot_nombres(seleccionados)
    selected_scx_normalizados = _normalizar_snapshot_nombres(selected_scx)
    snapshot_superior = _normalizar_snapshot_nombres(snapshot_superior)
    snapshot_configurado = _normalizar_snapshot_nombres(snapshot_configurado)
    if (
        len(seleccionados_normalizados) != len(seleccionados)
        or len(selected_scx_normalizados) != len(selected_scx)
        or len(snapshot_superior) != len(metadata["scheduler_snapshot"])
        or len(snapshot_configurado) != len(configuracion["scheduler_snapshot"])
        or seleccionados_normalizados != selected_scx_normalizados
        or snapshot_superior != snapshot_configurado
    ):
        return False
    candidatos = tuple(
        scheduler for scheduler in orden if scheduler != BASE_SYSTEM_NAME
    )
    return (
        len(seleccionados_normalizados) == len(candidatos)
        and set(seleccionados_normalizados) == set(candidatos)
        and set(seleccionados_normalizados).issubset(snapshot_superior)
    )


def _mostrar_pesos_en_sliders(win, pesos):
    valores = tuple(peso * 100.0 for peso in pesos)
    win._ajustando_pesos = True
    try:
        win.slider_pot.set_value(valores[0])
        win.slider_resp.set_value(valores[1])
        win.slider_flu.set_value(valores[2])
        win._lbl_pot.set_label(f"{valores[0]:.0f}%")
        win._lbl_resp.set_label(f"{valores[1]:.0f}%")
        win._lbl_flu.set_label(f"{valores[2]:.0f}%")
    finally:
        win._ajustando_pesos = False


def _orden_esperado_desde_run(run):
    metadata = run.get("metadata")
    if not isinstance(metadata, dict):
        return None
    orden = metadata.get("candidate_order")
    if not isinstance(orden, (list, tuple)) or len(orden) < 2:
        return None
    if any(not isinstance(nombre, str) or not nombre for nombre in orden):
        return None
    if len(set(orden)) != len(orden) or BASE_SYSTEM_NAME not in orden:
        return None
    if not any(nombre != BASE_SYSTEM_NAME for nombre in orden):
        return None
    return tuple(orden)


def _motivo_run_historico_no_aplicable(win, run, brutos, scores):
    if run.get("status") != "completed":
        return "El run no está completado; el ranking se muestra solo como referencia."

    development_mode = _development_mode_desde_metadata(run.get("metadata"))
    if development_mode is None:
        return (
            "El run no conserva su procedencia de simulación; "
            "se muestra solo como referencia."
        )
    if development_mode != bool(win.modo_desarrollador):
        origen = "simulado" if development_mode else "real"
        destino = "simulado" if win.modo_desarrollador else "real"
        return (
            f"El run es {origen} y el modo actual es {destino}; "
            "no se puede aplicar su recomendación."
        )

    kernel_actual = getattr(win, "versiones", {}).get("kernel")
    kernel_run = run.get("kernel_version")
    if kernel_run != kernel_actual:
        return (
            f"El kernel del run ({kernel_run or 'desconocido'}) no coincide con "
            f"el actual ({kernel_actual or 'desconocido'})."
        )

    orden = _orden_esperado_desde_run(run)
    if orden is None or not _ejecucion_completa(orden, brutos, scores):
        return (
            "El ranking no contiene la comparación completa esperada; "
            "se muestra solo como referencia."
        )

    metadata = run.get("metadata")
    if not _metadata_auto_aplicable(metadata):
        return (
            "La metadata del run está incompleta o dañada; "
            "se muestra solo como referencia."
        )

    scxctl_actual = getattr(win, "versiones", {}).get("scxctl")
    scxctl_run = run.get("scxctl_version")
    if (
        not isinstance(scxctl_run, str)
        or not scxctl_run.strip()
        or not isinstance(scxctl_actual, str)
        or not scxctl_actual.strip()
        or scxctl_run != scxctl_actual
    ):
        return (
            f"La versión scxctl del run ({scxctl_run or 'desconocida'}) no "
            f"coincide con la actual ({scxctl_actual or 'desconocida'})."
        )

    ganador = _recomendacion_desde_scores(scores)
    if ganador is None:
        return "El run no conserva un ganador válido; se muestra solo como referencia."
    try:
        snapshot = _snapshot_scheduler_actual(win)
    except ValueError:
        snapshot = ()

    if ganador != BASE_SYSTEM_NAME:
        compatibles = getattr(win, "compatibles", None)
        if compatibles is None or ganador not in compatibles:
            return (
                "El ganador ya no figura entre los schedulers compatibles; "
                "se muestra solo como referencia."
            )
        if ganador not in snapshot:
            return (
                "El ganador ya no existe en el snapshot actual de schedulers; "
                "se muestra solo como referencia."
            )

    contexto_run = _contexto_compatibilidad_desde_metadata(metadata)
    contexto_actual = _contexto_compatibilidad_vigente(win, snapshot)
    if contexto_run is None or contexto_actual is None:
        return (
            "El contexto de compatibilidad no es válido; "
            "se muestra solo como referencia."
        )
    if contexto_run != contexto_actual:
        return (
            "El contexto de compatibilidad cambió desde ese run; "
            "se muestra solo como referencia."
        )
    return None


def _refrescar_historial_publico(win):
    """Refresca la página pública si ya expuso su callback perezoso."""
    callback = getattr(win, "refrescar_historial", None)
    if not callable(callback):
        return False
    callback()
    return True


def _refrescar_historial(win):
    """Recarga la lista de runs desde la BD y actualiza los botones de navegación."""
    try:
        runs = consultar_runs_auto()
    except Exception as exc:
        runs = []
        _mostrar_toast(
            win,
            f"No se pudo validar el historial automático: {exc}",
            alta=True,
        )
    win._historial_runs = runs

    if not runs:
        win._indice_historial = -1
        win.lbl_nav.set_label("")
        win.btn_nav_prev.set_sensitive(False)
        win.btn_nav_next.set_sensitive(False)
        return

    # Si estamos viendo el último run, mantener la posición al final
    if win._indice_historial < 0 or win._indice_historial >= len(runs):
        win._indice_historial = len(runs) - 1
    elif win._indice_historial == len(runs) - 2 and len(runs) > 0:
        # Nuevo run añadido al final, avanzar
        win._indice_historial = len(runs) - 1

    _actualizar_botones_nav(win)


def _actualizar_botones_nav(win):
    """Actualiza el estado y etiqueta de los botones de navegación."""
    idx = win._indice_historial
    total = len(win._historial_runs)

    win.btn_nav_prev.set_sensitive(idx > 0)
    win.btn_nav_next.set_sensitive(idx < total - 1)

    if 0 <= idx < total:
        ts = win._historial_runs[idx].get("timestamp", 0)
        dt = datetime.datetime.fromtimestamp(ts)
        win.lbl_nav.set_label(f"Run {idx+1}/{total} — {dt.strftime('%d/%m %H:%M')}")
    else:
        win.lbl_nav.set_label("")


def _cargar_run_historico(win, indice, *, mostrar_toast=True):
    if indice < 0 or indice >= len(win._historial_runs):
        return False

    _cancelar_timers_auto(win)
    _nueva_generacion_auto(win)
    run = win._historial_runs[indice]
    win._indice_historial = indice
    win.en_proceso_auto = False
    win._auto_operation_id = None
    if hasattr(win, "barra_progreso"):
        win.barra_progreso.set_visible(False)
    if hasattr(win, "revealer_tiempo"):
        win.revealer_tiempo.set_reveal_child(False)
    win._auto_contexto_aplicable = False
    win._auto_pesos_validos = True
    metadata = run.get("metadata")
    win._auto_development_mode = _development_mode_desde_metadata(
        metadata
    )
    win._auto_source_versions = {
        "kernel": run.get("kernel_version"),
        "scxctl": run.get("scxctl_version"),
    }
    win._auto_compatibility_context = (
        _contexto_compatibilidad_desde_metadata(metadata)
    )
    win._auto_source_status = run.get("status")
    _ocultar_recomendacion(win)

    try:
        resultados = cargar_resultados_de_run(run["id"])
        brutos = _reconstruir_brutos(resultados)
    except Exception as exc:
        win._brutos_finales = {}
        win._scores_finales = {}
        _limpiar_filas_ranking(win)
        win.revealer_pesos.set_reveal_child(False)
        win.fila_ganador.set_title("Run histórico no aplicable")
        win.fila_ganador.set_subtitle(
            f"No se pudieron validar sus datos: {exc}"
        )
        _actualizar_botones_nav(win)
        if mostrar_toast:
            _mostrar_toast(
                win,
                "El run histórico contiene datos inválidos.",
                alta=True,
            )
        return False
    if not brutos:
        win._brutos_finales = {}
        win._scores_finales = {}
        _limpiar_filas_ranking(win)
        _poblar_grafico_desde_brutos(win, {})
        win.revealer_pesos.set_reveal_child(False)
        win.fila_ganador.set_title("Run sin resultados")
        win.fila_ganador.set_subtitle("Este run no contiene datos válidos.")
        _actualizar_botones_nav(win)
        if mostrar_toast:
            _mostrar_toast(win, "Este run no contiene datos válidos.")
        return False

    win._brutos_finales = brutos
    pesos = _pesos_desde_metadata(metadata)
    if pesos is None:
        pesos = win._pesos_auto_efectivos
    else:
        _mostrar_pesos_en_sliders(win, pesos)
    win._pesos_auto_efectivos = pesos

    _poblar_grafico_desde_brutos(win, brutos)
    win.fila_ganador.set_expanded(True)
    win.revealer_pesos.set_reveal_child(True)
    win.btn_auto.set_label("Determinar")
    win.btn_auto.set_sensitive(True)
    win.btn_auto.add_css_class("suggested-action")
    win.btn_auto.remove_css_class("destructive-action")

    scores = _poblar_ranking(win, pesos=pesos)
    motivo_no_aplicable = _motivo_run_historico_no_aplicable(
        win,
        run,
        brutos,
        scores,
    )
    win._auto_contexto_aplicable = motivo_no_aplicable is None
    _actualizar_recomendacion_ranking(win, scores)
    if not scores:
        win.fila_ganador.set_title("Sin resultados completos")
        win.fila_ganador.set_subtitle(
            "Este run no contiene las seis categorías válidas para ningún candidato."
        )
    elif motivo_no_aplicable is not None:
        win.fila_ganador.set_title("Resultados históricos no aplicables")
        win.fila_ganador.set_subtitle(motivo_no_aplicable)

    _actualizar_botones_nav(win)
    if mostrar_toast:
        if motivo_no_aplicable is None:
            mensaje = f"Cargado: {run.get('kernel_version', '')}"
        else:
            mensaje = f"Run histórico no aplicable: {motivo_no_aplicable}"
        _mostrar_toast(win, mensaje)
    return True


def _cargar_ultimo_run(win):
    """Carga explícitamente el run más reciente al construir la página."""
    if not win._historial_runs:
        return False
    return _cargar_run_historico(
        win,
        len(win._historial_runs) - 1,
        mostrar_toast=False,
    )


def _navegar_historial(win, direccion):
    """Navega al run anterior (-1) o siguiente (+1) en el historial."""
    return _cargar_run_historico(win, win._indice_historial + direccion)


def finalizar_auto_test(win, generacion):
    """Restaura controles y navegación tras cualquier salida del worker."""
    if not getattr(win, "_ui_alive", True):
        return False
    if not _generacion_auto_vigente(win, generacion):
        return False

    _cancelar_timers_auto(win)
    win.en_proceso_auto = False
    win._auto_operation_id = None
    detener_pulso = getattr(win.grafico, "detener_pulso", None)
    if callable(detener_pulso):
        detener_pulso()
    win.btn_auto.set_label("Determinar")
    win.btn_auto.set_sensitive(True)
    win.btn_auto.add_css_class("suggested-action")
    win.btn_auto.remove_css_class("destructive-action")
    _actualizar_botones_nav(win)
    win.progreso_actual = 1.0
    win.progreso_objetivo = 1.0
    win.segundos_actuales = 0.0
    win.segundos_objetivos = 0.0
    win.barra_progreso.set_fraction(1.0)
    win.revealer_tiempo.set_reveal_child(False)

    timer = {"id": None}

    def ocultar_progreso():
        if getattr(win, "_auto_hide_timer_id", None) != timer["id"]:
            return False
        win._auto_hide_timer_id = None
        if not _generacion_auto_vigente(win, generacion):
            return False
        win.barra_progreso.set_visible(False)
        return False

    timeout_add = getattr(GLib, "timeout_add", None)
    if callable(timeout_add):
        timer["id"] = timeout_add(500, ocultar_progreso)
        win._auto_hide_timer_id = timer["id"]
    else:
        win.barra_progreso.set_visible(False)
    return True


def _finalizar_worker_auto(win, resultado):
    """Aplica en GTK el desenlace inmutable producido por el worker."""
    generacion = resultado.get("generation")
    if not getattr(win, "_ui_alive", True):
        return False
    if generacion is None or not _generacion_auto_vigente(win, generacion):
        return False
    finalizado = finalizar_auto_test(win, generacion)
    if finalizado is False:
        return False

    brutos = resultado.get("brutos") or {}
    scores = resultado.get("scores") or {}
    pesos = tuple(resultado.get("pesos") or PESOS_PREDETERMINADOS)
    cancelado = bool(resultado.get("cancelado"))
    error = resultado.get("error")
    estado = resultado.get("status")
    usa_seguridad = _usa_contrato_seguridad_auto(win) or any(
        clave in resultado
        for clave in ("source_versions", "compatibility_context")
    )
    win._brutos_finales = brutos
    win._pesos_auto_efectivos = pesos
    development_mode = resultado.get("development_mode")
    win._auto_development_mode = (
        development_mode if isinstance(development_mode, bool) else None
    )
    if usa_seguridad:
        if "source_versions" in resultado:
            win._auto_source_versions = copy.deepcopy(
                resultado.get("source_versions")
            )
        if "compatibility_context" in resultado:
            win._auto_compatibility_context = (
                _canonizar_contexto_compatibilidad(
                    resultado.get("compatibility_context")
                )
            )
        win._auto_source_status = estado
    win._auto_pesos_validos = True
    win._auto_contexto_aplicable = (
        estado == "completed" and bool(scores) and not cancelado and not error
    )
    win._auto_permitir_aplicar = False

    if brutos:
        if hasattr(win, "grafico") and hasattr(win, "box_leyenda"):
            _poblar_grafico_desde_brutos(win, brutos)
        win.revealer_pesos.set_reveal_child(True)
        scores = _poblar_ranking(win, pesos=pesos)
    else:
        _ocultar_recomendacion(win)

    if error:
        win._auto_contexto_aplicable = False
        _ocultar_recomendacion(win)
        win.fila_ganador.set_title("Automatización finalizada con errores")
        win.fila_ganador.set_subtitle(str(error))
        _mostrar_toast(win, f"Error en la automatización: {error}", alta=True)
    elif cancelado:
        win._auto_contexto_aplicable = False
        _ocultar_recomendacion(win)
        win.fila_ganador.set_title("Motor en reposo")
        win.fila_ganador.set_subtitle("Análisis interrumpido por el usuario.")
        win.fila_ganador.set_icon_name("org.gnome.Settings-device-diagnostics-symbolic")
        if resultado.get("run_id") is None:
            mensaje = "Automatización cancelada sin resultados persistibles."
        else:
            mensaje = "Automatización cancelada; resultados parciales guardados."
        _mostrar_toast(win, mensaje)
    elif not scores:
        win.fila_ganador.set_title("Sin recomendación")
        win.fila_ganador.set_subtitle(
            "Ningún candidato completó las seis pruebas requeridas."
        )
        _mostrar_toast(win, "La automatización no produjo un ranking completo.")
    elif win.ganador_final is None:
        _mostrar_toast(win, "No hay una comparación SCX suficiente para recomendar.")
    else:
        _mostrar_toast(
            win,
            f"Detección finalizada: {win.ganador_final}",
        )

    if resultado.get("run_id") is not None:
        _refrescar_historial(win)
        _refrescar_historial_publico(win)
    return True


def confirmar_aplicar_recomendado(win):
    """Solicita confirmación antes de cambiar el scheduler del sistema."""
    recomendado = getattr(win, "ganador_final", None)
    if not _recomendacion_aplicable_actual(win, recomendado):
        if recomendado and not _procedencia_auto_coincide(win):
            mensaje = (
                "La recomendación pertenece a un modo distinto del actual y "
                "no se puede aplicar."
            )
        else:
            mensaje = "No hay una recomendación aplicable."
        _mostrar_toast(win, mensaje, alta=True)
        return

    if recomendado == BASE_SYSTEM_NAME:
        accion = "detener SCX y volver a Sistema Base"
    else:
        accion = f"activar {recomendado} en modo auto"
    dialogo = Adw.AlertDialog(
        title="Aplicar recomendación",
        body=f"Se va a {accion}. El análisis no cambia el sistema por sí solo.",
    )
    dialogo.add_response("cancel", "Cancelar")
    dialogo.add_response("apply", "Aplicar recomendado")
    dialogo.set_default_response("cancel")
    dialogo.set_close_response("cancel")
    dialogo.set_response_appearance(
        "apply",
        Adw.ResponseAppearance.SUGGESTED,
    )

    def responder(_dialogo, respuesta):
        if respuesta == "apply":
            if not _recomendacion_aplicable_actual(win, recomendado):
                _mostrar_toast(
                    win,
                    "La recomendación dejó de ser aplicable antes de autorizar.",
                    alta=True,
                )
                return
            win.solicitar_sudo_si_necesario(
                lambda: _iniciar_aplicacion_recomendada(win, recomendado)
            )

    dialogo.connect("response", responder)
    dialogo.present(win)


def _asegurar_aplicacion_vigente(
    win,
    recomendado,
    generacion,
    development_mode,
):
    if (
        not getattr(win, "_ui_alive", True)
        or generacion != getattr(win, "_auto_generation", None)
        or development_mode != getattr(win, "_auto_development_mode", None)
        or development_mode != bool(win.modo_desarrollador)
        or not _recomendacion_aplicable_actual(win, recomendado)
    ):
        raise OperationCancelled(
            "La recomendación cambió antes de modificar el sistema."
        )


def _iniciar_aplicacion_recomendada(win, recomendado):
    if not _recomendacion_aplicable_actual(win, recomendado):
        _mostrar_toast(
            win,
            "La recomendación dejó de ser aplicable y no se modificó el sistema.",
            alta=True,
        )
        return

    generacion = getattr(win, "_auto_generation", None)
    development_mode = getattr(win, "_auto_development_mode", None)
    handle = win.operaciones.try_acquire("aplicar recomendado")
    if handle is None:
        _mostrar_operacion_ocupada(win)
        return

    scx_manager = win.scx
    objetivo = (
        ScxState()
        if recomendado == BASE_SYSTEM_NAME
        else ScxState(recomendado, "auto")
    )
    operation_id = handle.operation_id
    win._auto_apply_operation_id = operation_id
    win.btn_aplicar_recomendado.set_sensitive(False)
    win.btn_aplicar_recomendado.set_label("Aplicando...")
    if hasattr(win, "btn_nav_prev"):
        win.btn_nav_prev.set_sensitive(False)
    if hasattr(win, "btn_nav_next"):
        win.btn_nav_next.set_sensitive(False)

    def worker():
        error = None
        try:
            _asegurar_aplicacion_vigente(
                win,
                recomendado,
                generacion,
                development_mode,
            )
            with scx_manager.sesion(handle.token) as sesion:
                handle.check_cancelled()
                _asegurar_aplicacion_vigente(
                    win,
                    recomendado,
                    generacion,
                    development_mode,
                )
                sesion.aplicar(objetivo)
                handle.check_cancelled()
                _asegurar_aplicacion_vigente(
                    win,
                    recomendado,
                    generacion,
                    development_mode,
                )
                _conservar_ganador_en_sesion(sesion, objetivo)
        except Exception as exc:
            error = str(exc) or exc.__class__.__name__
        finally:
            handle.release()
            _programar_ui(
                win,
                _finalizar_aplicacion_recomendada,
                win,
                recomendado,
                error,
                generacion,
                operation_id,
            )

    try:
        threading.Thread(target=worker).start()
    except Exception as exc:
        handle.release()
        _finalizar_aplicacion_recomendada(
            win,
            recomendado,
            str(exc) or exc.__class__.__name__,
            generacion,
            operation_id,
        )


def _finalizar_aplicacion_recomendada(
    win,
    recomendado,
    error,
    generacion,
    operation_id,
):
    if not getattr(win, "_ui_alive", True):
        return False

    if generacion != getattr(win, "_auto_generation", None):
        if _usa_contrato_seguridad_auto(win):
            if (
                getattr(win, "_auto_apply_operation_id", None) == operation_id
                and not _operacion_sigue_activa(win, operation_id)
            ):
                win._auto_apply_operation_id = None
            return False

    if getattr(win, "_auto_apply_operation_id", None) != operation_id:
        return False
    win._auto_apply_operation_id = None
    win.btn_aplicar_recomendado.set_label("Aplicar recomendado")
    if hasattr(win, "_historial_runs"):
        _actualizar_botones_nav(win)
    sincronizar = getattr(win, "sincronizar_sistema", None)
    if callable(sincronizar):
        sincronizar()

    if generacion != getattr(win, "_auto_generation", None):
        return False
    sigue_vigente = _recomendacion_aplicable_actual(win, recomendado)
    win.btn_aplicar_recomendado.set_visible(sigue_vigente)
    win.btn_aplicar_recomendado.set_sensitive(sigue_vigente)
    if error:
        _mostrar_toast(win, f"No se pudo aplicar la recomendación: {error}", alta=True)
        return False
    _mostrar_toast(win, f"Recomendación aplicada: {recomendado}")
    return True


def limpiar_ranking_auto(win):
    """Limpia los resultados de la detección automática."""
    if win.en_proceso_auto:
        return
    win.text_view_logs_auto.get_buffer().set_text("")
    invalidar_estado_automatizacion(win)
