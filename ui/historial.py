"""
Pestaña de Historial: muestra benchmarks anteriores guardados en SQLite.
Incluye versiones del entorno, filtros, resultados y gráfico de tendencia.
"""

import math
import time
from collections.abc import Mapping
from datetime import datetime
from numbers import Real

try:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk
except (ImportError, ValueError):
    Adw = Gtk = None

from core.database import (
    consultar_historial,
    contar_historial,
    eliminar_historial,
    obtener_schedulers_historial,
)

_HISTORY_ROW_LIMIT = 200

_TEST_TYPES = [
    ("", "Todos"),
    ("cpu", "Context Switching"),
    ("threads", "Carga Mixta"),
    ("memory", "Sincronización"),
    ("latencia_fork", "Fork+Exec"),
    ("latencia_compile", "Compilación Paralela"),
    ("latencia_loaded", "Bajo Carga"),
]

_DATE_RANGES = [
    (7, "Últimos 7 días"),
    (30, "Últimos 30 días"),
    (90, "Últimos 90 días"),
    (365, "Último año"),
    (0, "Todo"),
]

_TEST_NAMES = dict(_TEST_TYPES)
_LATENCY_TYPES = {
    "latencia_fork",
    "latencia_compile",
    "latencia_loaded",
}
_RESPONSE_KINDS_US = {
    "p95_us",
    "mean_context_switch_us",
    "mean_mutex_us",
    "mean_wall_time_per_bogo_op_us_proxy",
}


def _numero_finito(valor):
    if isinstance(valor, bool) or not isinstance(valor, Real):
        return None
    try:
        numero = float(valor)
    except (OverflowError, TypeError, ValueError):
        return None
    return numero if math.isfinite(numero) else None


def _numero_no_negativo(valor):
    numero = _numero_finito(valor)
    return numero if numero is not None and numero >= 0.0 else None


def _texto_no_vacio(valor, predeterminado=""):
    if not isinstance(valor, str):
        return predeterminado
    texto = valor.strip()
    return texto or predeterminado


def _texto_metadata(valor, predeterminado="desconocido", limite=120):
    """Devuelve metadatos breves y seguros para etiquetas de la UI."""
    texto = _texto_no_vacio(valor)
    if not texto:
        return predeterminado
    texto = " ".join(texto.split())
    return texto[:limite] or predeterminado


def preparar_filtros_historial(
    scheduler=None,
    test_type=None,
    days=30,
    *,
    solo_comparables=True,
    kernel_actual=None,
    now=None,
):
    """Normaliza los filtros de lista y gráfico sin depender de GTK."""
    if not isinstance(solo_comparables, bool):
        raise ValueError("solo_comparables debe ser booleano")

    scheduler = _texto_no_vacio(scheduler) or None
    if scheduler and scheduler.casefold() == "todos":
        scheduler = None

    test_type = _texto_no_vacio(test_type) or None
    if test_type and test_type.casefold() == "todos":
        test_type = None

    days_value = _numero_finito(days)
    if days_value is None:
        raise ValueError("days debe ser un número finito")

    if now is None:
        now_value = time.time()
    else:
        now_value = _numero_finito(now)
        if now_value is None:
            raise ValueError("now debe ser un número finito")

    date_from = (
        now_value - (days_value * 86400.0)
        if days_value > 0.0
        else None
    )
    kernel_actual = _texto_no_vacio(kernel_actual) or None
    return {
        "scheduler": scheduler,
        "test_type": test_type,
        "days": days_value,
        "date_from": date_from,
        "solo_comparables": solo_comparables,
        "status": "completed" if solo_comparables else None,
        "kernel_version": kernel_actual if solo_comparables else None,
    }


def es_resultado_comparable(resultado, kernel_actual):
    """Comprueba comparabilidad sin aceptar metadatos ausentes o corruptos."""
    if not isinstance(resultado, Mapping):
        return False
    kernel_actual = _texto_no_vacio(kernel_actual)
    return bool(kernel_actual) and (
        resultado.get("status") == "completed"
        and _texto_no_vacio(resultado.get("kernel_version")) == kernel_actual
    )


def filtrar_resultados_historial(
    resultados,
    *,
    solo_comparables,
    kernel_actual,
):
    """Descarta filas inválidas y aplica el filtro comparable en memoria."""
    if not isinstance(solo_comparables, bool):
        raise ValueError("solo_comparables debe ser booleano")
    try:
        filas = list(resultados)
    except TypeError:
        return []

    validas = [fila for fila in filas if isinstance(fila, Mapping)]
    if not solo_comparables:
        return validas
    return [
        fila
        for fila in validas
        if es_resultado_comparable(fila, kernel_actual)
    ]


def preparar_metadata_historial(resultado):
    """Normaliza la procedencia mostrada sin inventar valores históricos."""
    if not isinstance(resultado, Mapping):
        resultado = {}
    return {
        "status": _texto_metadata(resultado.get("status")),
        "modo": _texto_metadata(resultado.get("modo")),
        "kernel_version": _texto_metadata(resultado.get("kernel_version")),
        "run_type": _texto_metadata(resultado.get("run_type")),
    }


def extraer_respuesta_historial(resultado):
    """Extrae response y conserva p95 solo como lectura histórica."""
    if not isinstance(resultado, Mapping):
        return None

    tiene_response = (
        "response" in resultado and resultado.get("response") is not None
    )
    if tiene_response:
        valor = _numero_no_negativo(resultado.get("response"))
        origen = "response"
    else:
        valor = _numero_no_negativo(resultado.get("p95"))
        origen = "p95_historico"

    if valor is None:
        return None

    response_kind = _texto_no_vacio(resultado.get("response_kind")) or None
    if response_kind == "p95_us":
        etiqueta = "p95"
    elif origen == "p95_historico":
        etiqueta = "Respuesta histórica"
    elif response_kind:
        etiqueta = f"Respuesta [{response_kind[:80]}]"
    else:
        etiqueta = "Respuesta"

    usa_microsegundos = response_kind in _RESPONSE_KINDS_US
    return {
        "valor": valor,
        "response_kind": response_kind,
        "etiqueta": etiqueta,
        "unidad": "µs" if usa_microsegundos else "",
        "origen": origen,
    }


def _formatear_numero(valor, decimales=1):
    return f"{valor:,.{decimales}f}"


def formatear_resultado_historial(resultado):
    """Formatea un resultado sin atribuir semántica falsa a response/p95."""
    if not isinstance(resultado, Mapping):
        return "Resultado no válido"

    test_type = _texto_no_vacio(resultado.get("test_type"))
    valor = _numero_no_negativo(resultado.get("valor"))
    if valor is None:
        return "Resultado no válido"

    if test_type in _LATENCY_TYPES:
        partes = [f"{_formatear_numero(valor)} µs"]
    elif test_type in {"cpu", "threads", "memory"}:
        partes = [f"{_formatear_numero(valor)} ops/s"]
    else:
        partes = [_formatear_numero(valor)]

    respuesta = extraer_respuesta_historial(resultado)
    if respuesta is not None:
        unidad = f" {respuesta['unidad']}" if respuesta["unidad"] else ""
        partes.append(
            f"{respuesta['etiqueta']}: "
            f"{_formatear_numero(respuesta['valor'], 3)}{unidad}"
        )

    if test_type in _LATENCY_TYPES:
        partes.append("menor es mejor")
    return " · ".join(partes)


def formatear_timestamp_historial(timestamp):
    timestamp = _numero_finito(timestamp)
    if timestamp is None:
        return "Fecha no disponible"
    try:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")
    except (OSError, OverflowError, ValueError):
        return "Fecha no disponible"


def preparar_datos_tendencia(
    resultados,
    test_type,
    *,
    scheduler=None,
    limit=_HISTORY_ROW_LIMIT,
):
    """Valida y separa series por scheduler, modo y kernel para Cairo."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError("limit debe ser un entero positivo")

    if not test_type:
        return {
            "datos": [],
            "schedulers": (),
            "series": (),
            "mensaje": (
                "Selecciona un tipo de prueba para mostrar una tendencia "
                "sin mezclar unidades."
            ),
            "etiqueta_eje": "",
            "menor_es_mejor": False,
        }

    menor_es_mejor = test_type in _LATENCY_TYPES
    etiqueta_eje = (
        "Duración (µs) · menor es mejor ↓"
        if menor_es_mejor
        else "Rendimiento (ops/s) · mayor es mejor ↑"
    )

    scheduler_filtrado = _texto_no_vacio(scheduler) or None
    puntos = []
    for resultado in resultados:
        if not isinstance(resultado, Mapping):
            continue
        if resultado.get("test_type") != test_type:
            continue
        if resultado.get("status") != "completed":
            continue
        timestamp = _numero_finito(resultado.get("timestamp"))
        valor = _numero_no_negativo(resultado.get("valor"))
        scheduler_nombre = _texto_metadata(
            resultado.get("scheduler_name"),
            predeterminado="",
        )
        modo = _texto_metadata(resultado.get("modo"), predeterminado="")
        kernel = _texto_metadata(
            resultado.get("kernel_version"),
            predeterminado="",
        )
        if scheduler_filtrado and scheduler_nombre != scheduler_filtrado:
            continue
        if (
            timestamp is None
            or valor is None
            or not scheduler_nombre
            or not modo
            or not kernel
        ):
            continue
        if formatear_timestamp_historial(timestamp) == "Fecha no disponible":
            continue
        series_key = (scheduler_nombre, modo, kernel)
        series_label = (
            f"{scheduler_nombre} · modo {modo} · kernel {kernel}"
        )
        puntos.append(
            {
                "timestamp": timestamp,
                "valor": valor,
                "scheduler_name": scheduler_nombre,
                "modo": modo,
                "kernel_version": kernel,
                "series_key": series_key,
                "series_label": series_label,
                "detalle": formatear_resultado_historial(resultado),
            }
        )

    # consultar_historial devuelve primero los más recientes. Ordenar aquí hace
    # el helper seguro para cualquier iterable y conserva los últimos N puntos.
    puntos.sort(key=lambda punto: punto["timestamp"], reverse=True)
    puntos = puntos[:limit]
    puntos.sort(key=lambda punto: punto["timestamp"])
    schedulers = tuple(
        sorted(
            {punto["scheduler_name"] for punto in puntos},
            key=str.casefold,
        )
    )
    series_por_clave = {
        punto["series_key"]: punto["series_label"] for punto in puntos
    }
    series = tuple(
        {
            "key": clave,
            "label": series_por_clave[clave],
        }
        for clave in sorted(
            series_por_clave,
            key=lambda clave: tuple(parte.casefold() for parte in clave),
        )
    )
    return {
        "datos": puntos,
        "schedulers": schedulers,
        "series": series,
        "mensaje": "" if puntos else "No hay datos válidos para estos filtros.",
        "etiqueta_eje": etiqueta_eje,
        "menor_es_mejor": menor_es_mejor,
    }


def calcular_limites_tendencia(valores):
    """Calcula límites finitos e incluye el cero como referencia honesta."""
    validos = [
        numero
        for valor in valores
        if (numero := _numero_finito(valor)) is not None
    ]
    if not validos:
        return 0.0, 1.0

    minimo = min(0.0, min(validos))
    maximo = max(0.0, max(validos))
    if minimo == maximo:
        return (minimo, minimo + 1.0) if minimo >= 0.0 else (minimo - 1.0, maximo)
    if minimo == 0.0:
        ampliado = maximo * 1.1
        if math.isfinite(ampliado):
            maximo = ampliado
    elif maximo == 0.0:
        ampliado = minimo * 1.1
        if math.isfinite(ampliado):
            minimo = ampliado
    else:
        margen = (maximo - minimo) * 0.1
        if math.isfinite(margen):
            minimo_ampliado = minimo - margen
            maximo_ampliado = maximo + margen
            if math.isfinite(minimo_ampliado) and math.isfinite(maximo_ampliado):
                minimo = minimo_ampliado
                maximo = maximo_ampliado
    return minimo, maximo


def _generar_color_hash(nombre):
    from utils.helpers import generar_color_hash

    return generar_color_hash(nombre)


def _require_gtk():
    if Gtk is None or Adw is None:
        raise RuntimeError("GTK4 y Libadwaita son necesarios para construir la UI")


def _crear_modelo_cadenas(valores):
    modelo = Gtk.StringList()
    for valor in valores:
        modelo.append(valor)
    return modelo


def setup_historial_ui(win):
    _require_gtk()
    page = Adw.PreferencesPage(title="Historial")

    header = Adw.HeaderBar()
    view = Adw.ToolbarView(content=page)
    view.add_top_bar(header)
    win.pag_historial.set_child(view)

    grupo_env = Adw.PreferencesGroup(title="Entorno del Sistema")
    page.add(grupo_env)

    versiones = getattr(win, "versiones", {})
    for titulo, valor in [
        ("Kernel", versiones.get("kernel", "—")),
        ("scxctl", versiones.get("scxctl", "—")),
        ("stress-ng", versiones.get("stressng", "—")),
        ("hyperfine", versiones.get("hyperfine", "—")),
    ]:
        fila = Adw.ActionRow(title=titulo, subtitle=valor or "—")
        fila.add_css_class("property")
        grupo_env.add(fila)

    grupo_filtros = Adw.PreferencesGroup(title="Filtros")
    page.add(grupo_filtros)

    win._hist_combo_sched = Adw.ComboRow(
        title="Scheduler",
        model=_crear_modelo_cadenas(["Todos"]),
    )
    grupo_filtros.add(win._hist_combo_sched)

    win._hist_combo_test = Adw.ComboRow(
        title="Tipo de Prueba",
        model=_crear_modelo_cadenas(nombre for _, nombre in _TEST_TYPES),
    )
    grupo_filtros.add(win._hist_combo_test)

    win._hist_combo_fecha = Adw.ComboRow(
        title="Rango de Fechas",
        model=_crear_modelo_cadenas(nombre for _, nombre in _DATE_RANGES),
    )
    win._hist_combo_fecha.set_selected(1)
    grupo_filtros.add(win._hist_combo_fecha)

    win._hist_switch_comparables = Adw.SwitchRow(
        title="Solo resultados comparables",
        subtitle="Runs completados con el kernel actual",
        active=True,
    )
    grupo_filtros.add(win._hist_switch_comparables)

    win._hist_lbl_contador = Gtk.Label(
        label="0 resultados registrados",
        css_classes=["dim-label", "caption"],
    )
    win._hist_lbl_contador.set_xalign(0)
    grupo_filtros.add(win._hist_lbl_contador)

    grupo_resultados = Adw.PreferencesGroup(title="Resultados Históricos")
    page.add(grupo_resultados)

    win._hist_box_resultados = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=0,
    )
    win._hist_box_resultados.add_css_class("boxed-list")
    scroller = Gtk.ScrolledWindow()
    scroller.set_child(win._hist_box_resultados)
    scroller.set_min_content_height(200)
    scroller.set_max_content_height(400)
    scroller.set_hexpand(True)
    grupo_resultados.add(scroller)

    grupo_tendencia = Adw.PreferencesGroup(title="Tendencia de Rendimiento")
    page.add(grupo_tendencia)
    win._hist_grupo_tendencia = grupo_tendencia

    win._hist_chart = Gtk.DrawingArea()
    win._hist_chart.set_content_height(280)
    win._hist_chart.set_hexpand(True)
    win._hist_chart.set_vexpand(True)
    win._hist_chart.add_css_class("card")
    win._hist_chart_state = preparar_datos_tendencia([], None)
    win._hist_chart_data = []
    win._hist_chart_scheds = set()
    win._hist_chart_hover = None

    motion = Gtk.EventControllerMotion()
    motion.connect("motion", _on_chart_hover, win)
    motion.connect("leave", _on_chart_leave, win)
    win._hist_chart.add_controller(motion)

    win._hist_chart.set_draw_func(_dibujar_tendencia, win)
    grupo_tendencia.add(win._hist_chart)

    win._hist_box_leyenda = Gtk.FlowBox()
    win._hist_box_leyenda.set_valign(Gtk.Align.CENTER)
    win._hist_box_leyenda.set_halign(Gtk.Align.START)
    win._hist_box_leyenda.set_column_spacing(8)
    win._hist_box_leyenda.set_row_spacing(4)
    grupo_tendencia.add(win._hist_box_leyenda)

    grupo_acciones = Adw.PreferencesGroup(title="Acciones")
    page.add(grupo_acciones)

    btn_borrar = Gtk.Button(
        label="Borrar Historial",
        css_classes=["destructive-action"],
    )
    btn_borrar.set_halign(Gtk.Align.START)
    btn_borrar.connect("clicked", _al_borrar_historial, win)
    grupo_acciones.add(btn_borrar)

    win._hist_refreshing = False
    win._hist_combo_sched.connect("notify::selected", _al_cambiar_filtros, win)
    win._hist_combo_test.connect("notify::selected", _al_cambiar_filtros, win)
    win._hist_combo_fecha.connect("notify::selected", _al_cambiar_filtros, win)
    win._hist_switch_comparables.connect(
        "notify::active",
        _al_cambiar_filtros,
        win,
    )

    # Los productores existentes pueden invocar este callback sin importar el
    # módulo, y el evento map cubre guardados automáticos hechos en otra página.
    win.refrescar_historial = lambda: refrescar_historial_ui(win)
    win.pag_historial.connect("map", lambda *_args: refrescar_historial_ui(win))
    refrescar_historial_ui(win)


def _scheduler_seleccionado(win):
    combo = win._hist_combo_sched
    modelo = combo.get_model()
    indice = combo.get_selected()
    if modelo is None or indice <= 0 or indice >= modelo.get_n_items():
        return None
    return modelo.get_string(indice)


def _reconstruir_modelo_schedulers(win, seleccion_previa):
    schedulers = []
    for scheduler in obtener_schedulers_historial():
        nombre = _texto_no_vacio(scheduler)
        if nombre and nombre not in schedulers:
            schedulers.append(nombre)
    schedulers.sort(key=str.casefold)
    valores = ["Todos", *schedulers]
    win._hist_combo_sched.set_model(_crear_modelo_cadenas(valores))
    try:
        indice = valores.index(seleccion_previa) if seleccion_previa else 0
    except ValueError:
        indice = 0
    win._hist_combo_sched.set_selected(indice)


def _filtros_desde_ui(win):
    scheduler = _scheduler_seleccionado(win)

    test_idx = win._hist_combo_test.get_selected()
    if not 0 <= test_idx < len(_TEST_TYPES):
        test_idx = 0
    test_type = _TEST_TYPES[test_idx][0] or None

    fecha_idx = win._hist_combo_fecha.get_selected()
    if not 0 <= fecha_idx < len(_DATE_RANGES):
        fecha_idx = 1
    days = _DATE_RANGES[fecha_idx][0]

    switch = getattr(win, "_hist_switch_comparables", None)
    get_active = getattr(switch, "get_active", None)
    solo_comparables = bool(get_active()) if callable(get_active) else True
    versiones = getattr(win, "versiones", {})
    kernel_actual = (
        versiones.get("kernel") if isinstance(versiones, Mapping) else None
    )
    return preparar_filtros_historial(
        scheduler,
        test_type,
        days,
        solo_comparables=solo_comparables,
        kernel_actual=kernel_actual,
    )


def refrescar_historial_ui(win):
    """Reconstruye filtros, contador, lista y gráfico conservando selección."""
    if not hasattr(win, "_hist_combo_sched"):
        return False
    if getattr(win, "_hist_refreshing", False):
        return False

    seleccion_previa = _scheduler_seleccionado(win)
    win._hist_refreshing = True
    try:
        _reconstruir_modelo_schedulers(win, seleccion_previa)
        _refrescar_resultados_historial(win)
    finally:
        win._hist_refreshing = False
    return True


def refrescar_historial(win):
    """Alias compatible para productores que ya buscan esta API pública."""
    return refrescar_historial_ui(win)


def _al_cambiar_filtros(_combo, _pspec, win):
    if not getattr(win, "_hist_refreshing", False):
        _refrescar_resultados_historial(win)


def _vaciar_contenedor(contenedor):
    while (child := contenedor.get_first_child()):
        contenedor.remove(child)


def formatear_contador_historial(
    mostrados,
    total_filtrado,
    filtros,
    *,
    limit=_HISTORY_ROW_LIMIT,
):
    """Explica el alcance del filtro y el límite de la consulta resumida."""
    if isinstance(mostrados, bool) or not isinstance(mostrados, int) or mostrados < 0:
        mostrados = 0
    if (
        isinstance(total_filtrado, bool)
        or not isinstance(total_filtrado, int)
        or total_filtrado < 0
    ):
        total_filtrado = 0
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError("limit debe ser un entero positivo")

    filtros = filtros if isinstance(filtros, Mapping) else {}
    if filtros.get("solo_comparables") is True:
        kernel = _texto_metadata(
            filtros.get("kernel_version"),
            predeterminado="no disponible",
        )
        alcance = f"filtro: completados · kernel {kernel}"
    else:
        alcance = "filtro: todos los estados, modos y kernels"
    return (
        f"Mostrando {mostrados} de {total_filtrado} resultado(s) filtrado(s) · "
        f"{alcance} · límite de carga: {limit}"
    )


def _actualizar_contador(win, mostrados, total, filtros):
    win._hist_lbl_contador.set_label(
        formatear_contador_historial(mostrados, total, filtros)
    )


def _refrescar_resultados_historial(win):
    filtros = _filtros_desde_ui(win)
    if filtros["solo_comparables"] and not filtros["kernel_version"]:
        consultados = []
        total_filtrado = 0
    else:
        argumentos = {
            "scheduler": filtros["scheduler"],
            "test_type": filtros["test_type"],
            "date_from": filtros["date_from"],
            "kernel_version": filtros["kernel_version"],
            "status": filtros["status"],
        }
        consultados = consultar_historial(
            **argumentos,
            limit=_HISTORY_ROW_LIMIT,
            include_payload=False,
        )
        total_filtrado = contar_historial(**argumentos)
    resultados = filtrar_resultados_historial(
        consultados,
        solo_comparables=filtros["solo_comparables"],
        kernel_actual=filtros["kernel_version"],
    )
    _actualizar_contador(
        win,
        len(resultados),
        total_filtrado,
        filtros,
    )

    _vaciar_contenedor(win._hist_box_resultados)
    for resultado in resultados:
        scheduler = _texto_no_vacio(
            resultado.get("scheduler_name"),
            "Scheduler desconocido",
        )
        test_type = _texto_no_vacio(resultado.get("test_type"))
        tipo_nombre = _TEST_NAMES.get(test_type, test_type or "Prueba desconocida")
        fecha = formatear_timestamp_historial(resultado.get("timestamp"))
        valor = formatear_resultado_historial(resultado)
        metadata = preparar_metadata_historial(resultado)
        procedencia = (
            f"Estado: {metadata['status']} · Modo: {metadata['modo']} · "
            f"Kernel: {metadata['kernel_version']}"
        )

        fila = Adw.ActionRow(
            title=f"{scheduler} — {tipo_nombre}",
            subtitle=f"{fecha}  •  {valor}  •  {procedencia}",
        )
        badge = Gtk.Label(
            label=metadata["run_type"].upper(),
            css_classes=["caption", "dim-label"],
        )
        fila.add_suffix(badge)
        win._hist_box_resultados.append(fila)

    _actualizar_datos_grafico(
        win,
        resultados,
        filtros["test_type"],
        scheduler=filtros["scheduler"],
    )


def _actualizar_datos_grafico(win, resultados, test_type, *, scheduler=None):
    estado = preparar_datos_tendencia(
        resultados,
        test_type,
        scheduler=scheduler,
    )
    win._hist_chart_state = estado
    win._hist_chart_data = estado["datos"]
    win._hist_chart_scheds = {serie["key"] for serie in estado["series"]}
    win._hist_chart_hover = None
    win._hist_grupo_tendencia.set_description(
        estado["etiqueta_eje"] or estado["mensaje"]
    )

    _vaciar_contenedor(win._hist_box_leyenda)
    for serie in estado["series"]:
        color = _generar_color_hash(serie["label"])
        chip = Gtk.Box(spacing=6, css_classes=["card", "pill"])
        dot = Gtk.DrawingArea()
        dot.set_content_width(10)
        dot.set_content_height(10)
        dot.set_valign(Gtk.Align.CENTER)
        dot.set_margin_start(6)

        def dibujar_punto(_area, cr, width, height, rgb):
            cr.set_source_rgb(*rgb)
            cr.arc(width / 2, height / 2, 4, 0, 2 * math.pi)
            cr.fill()

        dot.set_draw_func(dibujar_punto, color)
        label = Gtk.Label(label=serie["label"], css_classes=["caption"])
        label.set_margin_end(6)
        label.set_margin_top(2)
        label.set_margin_bottom(2)
        chip.append(dot)
        chip.append(label)
        win._hist_box_leyenda.append(chip)

    win._hist_chart.queue_draw()


def _dibujar_mensaje(cr, width, height, texto):
    cr.set_source_rgba(0.6, 0.6, 0.6, 0.45)
    cr.set_font_size(13)
    ext = cr.text_extents(texto)
    cr.move_to(max(12, width / 2 - ext.width / 2), height / 2)
    cr.show_text(texto)


def _formatear_eje(valor):
    absoluto = abs(valor)
    if absoluto >= 1_000_000:
        return f"{valor / 1_000_000:.1f}M"
    if absoluto >= 1000:
        return f"{valor / 1000:.1f}k"
    if absoluto < 10 and valor != 0:
        return f"{valor:.1f}"
    return f"{valor:.0f}"


def _dibujar_tendencia(area, cr, width, height, win):
    estado = win._hist_chart_state
    datos = estado["datos"]
    series = estado["series"]
    if not datos or not series:
        _dibujar_mensaje(
            cr,
            width,
            height,
            estado["mensaje"] or "Sin datos suficientes para mostrar tendencia",
        )
        return

    tr, tg, tb = 0.6, 0.6, 0.6
    ml, mr, mt, mb = 60, 30, 42, 50
    chart_width = width - ml - mr
    chart_height = height - mt - mb
    if chart_width <= 0 or chart_height <= 0:
        return

    timestamps = [dato["timestamp"] for dato in datos]
    valores = [dato["valor"] for dato in datos]
    t_min = min(timestamps)
    t_max_original = max(timestamps)
    un_solo_instante = t_max_original == t_min
    t_max = t_min + 1.0 if un_solo_instante else t_max_original
    v_min, v_max = calcular_limites_tendencia(valores)

    cr.set_source_rgba(tr, tg, tb, 0.55)
    cr.set_font_size(10)
    cr.move_to(ml, 20)
    cr.show_text(estado["etiqueta_eje"])

    for indice in range(5):
        y = mt + chart_height * (1 - indice / 4)
        valor_eje = v_min + (v_max - v_min) * (indice / 4)
        cr.set_line_width(0.5)
        cr.set_source_rgba(tr, tg, tb, 0.08)
        cr.move_to(ml, y)
        cr.line_to(ml + chart_width, y)
        cr.stroke()
        cr.set_source_rgba(tr, tg, tb, 0.4)
        cr.set_font_size(10)
        etiqueta = _formatear_eje(valor_eje)
        ext = cr.text_extents(etiqueta)
        cr.move_to(ml - ext.width - 8, y + ext.height / 3)
        cr.show_text(etiqueta)

    instantes = len(set(timestamps))
    num_labels = min(6, instantes)
    if num_labels == 1:
        posiciones = [(ml + chart_width / 2, t_min)]
    elif num_labels > 1:
        posiciones = [
            (
                ml + chart_width * (indice / (num_labels - 1)),
                t_min + (t_max_original - t_min) * (indice / (num_labels - 1)),
            )
            for indice in range(num_labels)
        ]
    else:
        posiciones = []

    for x, timestamp in posiciones:
        cr.set_source_rgba(tr, tg, tb, 0.08)
        cr.move_to(x, mt)
        cr.line_to(x, mt + chart_height)
        cr.stroke()
        fecha = datetime.fromtimestamp(timestamp).strftime("%d/%m")
        cr.set_source_rgba(tr, tg, tb, 0.4)
        cr.set_font_size(9)
        ext = cr.text_extents(fecha)
        cr.move_to(x - ext.width / 2, mt + chart_height + 20)
        cr.show_text(fecha)

    def proyectar(timestamp, valor):
        if un_solo_instante:
            x = ml + chart_width / 2
        else:
            x = ml + chart_width * ((timestamp - t_min) / (t_max - t_min))
        y = mt + chart_height * (1 - (valor - v_min) / (v_max - v_min))
        return x, y

    for serie in series:
        puntos = [
            dato for dato in datos if dato["series_key"] == serie["key"]
        ]
        if not puntos:
            continue
        red, green, blue = _generar_color_hash(serie["label"])
        cr.set_source_rgba(red, green, blue, 0.8)
        cr.set_line_width(2.0)
        cr.set_line_cap(1)
        cr.set_line_join(1)

        for indice, punto in enumerate(puntos):
            x, y = proyectar(punto["timestamp"], punto["valor"])
            if indice == 0:
                cr.move_to(x, y)
            else:
                cr.line_to(x, y)
        cr.stroke()

        for punto in puntos:
            x, y = proyectar(punto["timestamp"], punto["valor"])
            cr.set_source_rgba(red, green, blue, 0.3)
            cr.arc(x, y, 6, 0, 2 * math.pi)
            cr.fill()
            cr.set_source_rgba(red, green, blue, 1.0)
            cr.arc(x, y, 3, 0, 2 * math.pi)
            cr.fill()

    if not win._hist_chart_hover:
        return

    hover_x, hover_y = win._hist_chart_hover
    cercano = None
    distancia_cercana = 15.0
    for punto in datos:
        x, y = proyectar(punto["timestamp"], punto["valor"])
        distancia = math.hypot(hover_x - x, hover_y - y)
        if distancia < distancia_cercana:
            cercano = (punto, x, y)
            distancia_cercana = distancia
    if cercano is None:
        return

    punto, x, y = cercano
    red, green, blue = _generar_color_hash(punto["series_label"])
    cr.set_source_rgba(red, green, blue, 1.0)
    cr.arc(x, y, 6, 0, 2 * math.pi)
    cr.fill()

    fecha = datetime.fromtimestamp(punto["timestamp"]).strftime("%d/%m %H:%M")
    tooltip = f"{punto['series_label']}: {punto['detalle']} · {fecha}"
    if len(tooltip) > 100:
        tooltip = f"{tooltip[:97]}..."
    cr.set_font_size(10)
    ext = cr.text_extents(tooltip)
    text_x = min(max(8, x + 10), max(8, width - ext.width - 10))
    text_y = max(y - 20, ext.height + 8)
    cr.set_source_rgba(0, 0, 0, 0.8)
    cr.rectangle(
        text_x - 4,
        text_y - ext.height - 4,
        ext.width + 8,
        ext.height + 8,
    )
    cr.fill()
    cr.set_source_rgba(1, 1, 1, 0.95)
    cr.move_to(text_x, text_y)
    cr.show_text(tooltip)


def _on_chart_hover(_controller, x, y, win):
    win._hist_chart_hover = (x, y)
    win._hist_chart.queue_draw()


def _on_chart_leave(_controller, win):
    win._hist_chart_hover = None
    win._hist_chart.queue_draw()


def invalidar_historial_automatico(win):
    """Invalida resultados y navegación automáticos sin cambiar el scheduler."""
    api_invocada = False
    for nombre in (
        "invalidar_historial_automatico",
        "refrescar_historial_automatico",
        "refrescar_historial_auto",
    ):
        callback = getattr(win, nombre, None)
        if callable(callback):
            callback()
            api_invocada = True
            break

    if not api_invocada and not getattr(win, "en_proceso_auto", False):
        try:
            from ui import automatizacion
        except (ImportError, ValueError):
            automatizacion = None
        limpiar = getattr(automatizacion, "limpiar_ranking_auto", None)
        if callable(limpiar):
            limpiar(win)
            api_invocada = True

    if hasattr(win, "_historial_runs"):
        win._historial_runs = []
    if hasattr(win, "_indice_historial"):
        win._indice_historial = -1
    if not getattr(win, "en_proceso_auto", False):
        for atributo, valor in (
            ("_brutos_finales", {}),
            ("_scores_finales", {}),
            ("ganador_final", None),
            ("_auto_permitir_aplicar", False),
        ):
            if hasattr(win, atributo):
                setattr(win, atributo, valor)

    etiqueta_nav = getattr(win, "lbl_nav", None)
    set_label = getattr(etiqueta_nav, "set_label", None)
    if callable(set_label):
        set_label("")
    for nombre in ("btn_nav_prev", "btn_nav_next"):
        boton = getattr(win, nombre, None)
        set_sensitive = getattr(boton, "set_sensitive", None)
        if callable(set_sensitive):
            set_sensitive(False)
    boton_aplicar = getattr(win, "btn_aplicar_recomendado", None)
    set_visible = getattr(boton_aplicar, "set_visible", None)
    if callable(set_visible) and not getattr(win, "en_proceso_auto", False):
        set_visible(False)
    set_sensitive = getattr(boton_aplicar, "set_sensitive", None)
    if callable(set_sensitive) and not getattr(win, "en_proceso_auto", False):
        set_sensitive(False)
    return api_invocada


def _al_borrar_historial(_button, win):
    dialog = Adw.AlertDialog(
        heading="Borrar Historial",
        body="Esta acción eliminará todos los resultados guardados permanentemente.",
    )
    dialog.add_response("cancel", "Cancelar")
    dialog.add_response("delete", "Borrar Todo")
    dialog.set_default_response("cancel")
    dialog.set_close_response("cancel")
    dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
    dialog.connect("response", _confirmar_borrar, win)
    dialog.present(win)


def _confirmar_borrar(_dialog, response, win):
    if response != "delete":
        return False
    eliminar_historial()
    invalidar_historial_automatico(win)
    refrescar_historial_ui(win)
    return True
