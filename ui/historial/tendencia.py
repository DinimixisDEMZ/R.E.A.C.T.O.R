"""
Página de tendencia del historial — Gráfico de tendencia y comparativa de schedulers.
"""

import math
from datetime import datetime

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gdk, Gio, GObject

from core.database import consultar_tendencia
from core.constantes import INTERVALO_FRAME_MS
from utils.colores import generar_color_hash, dibujar_dot
from utils.helpers import vaciar_contenedor
from utils.i18n import traducir
from widgets.legend import crear_chip_leyenda, crear_chip_informativo
from .constantes import _TIPOS_PRUEBA, _RANGOS_FECHA


class _FilaTabla(GObject.GObject):
    __gtype_name__ = "_FilaTabla"
    planif = GObject.Property(type=str, default="")
    r = GObject.Property(type=float, default=0.0)
    g = GObject.Property(type=float, default=0.0)
    b = GObject.Property(type=float, default=0.0)
    avg = GObject.Property(type=float, default=0.0)
    minimo = GObject.Property(type=float, default=0.0)
    maximo = GObject.Property(type=float, default=0.0)
    ultimo = GObject.Property(type=float, default=0.0)
    std = GObject.Property(type=float, default=0.0)


def _crear_funcion_comparar(attr):
    def _comparar(a, b, *args):
        va = getattr(a, attr)
        vb = getattr(b, attr)
        return (va > vb) - (va < vb)
    return _comparar


def _calcular_desv(valores):
    n = len(valores)
    if n < 2:
        return 0.0
    prom = sum(valores) / n
    return math.sqrt(sum((x - prom) ** 2 for x in valores) / (n - 1))


def _crear_pagina_tendencia(win):
    pagina = Adw.PreferencesPage()

    grupo = Adw.PreferencesGroup(title=traducir("Tendencia de Rendimiento"))
    pagina.add(grupo)

    # ── Filtros ──
    filtro_fecha_fila = Adw.ActionRow(title=traducir("Rango de Fechas"))
    modelo = Gtk.StringList()
    for _, nombre in _RANGOS_FECHA:
        modelo.append(traducir(nombre))
    win._hist_trend_combo = Gtk.DropDown(model=modelo, css_classes=["flat"], valign=Gtk.Align.CENTER)
    win._hist_trend_combo.set_selected(1)
    win._hist_trend_combo.connect("notify::selected", lambda *a: _refrescar_tendencia(win))
    filtro_fecha_fila.add_suffix(win._hist_trend_combo)
    grupo.add(filtro_fecha_fila)

    filtro_test_fila = Adw.ActionRow(title=traducir("Tipo de Benchmark"))
    modelo_test = Gtk.StringList()
    for _, nombre in _TIPOS_PRUEBA:
        modelo_test.append(traducir(nombre))
    win._hist_trend_test_type = Gtk.DropDown(model=modelo_test, css_classes=["flat"], valign=Gtk.Align.CENTER)
    win._hist_trend_test_type.set_selected(0)
    win._hist_trend_test_type.connect("notify::selected", lambda *a: _refrescar_tendencia(win))
    filtro_test_fila.add_suffix(win._hist_trend_test_type)
    grupo.add(filtro_test_fila)

    # ── Resumen arriba del gráfico ──
    win._hist_trend_summary_box = Gtk.Box(spacing=8, margin_top=6, margin_bottom=4, halign=Gtk.Align.CENTER)
    grupo.add(win._hist_trend_summary_box)

    caja_visual = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=6, margin_bottom=6)

    win._hist_chart = Gtk.DrawingArea()
    win._hist_chart.set_content_height(320)
    win._hist_chart.set_hexpand(True)
    win._hist_chart.set_margin_top(16)
    win._hist_chart.set_margin_bottom(16)
    win._hist_chart.set_margin_start(16)
    win._hist_chart.set_margin_end(16)
    marco_grafico = Gtk.Frame(css_classes=["card"])
    marco_grafico.set_child(win._hist_chart)
    win._hist_chart_data = []
    win._hist_chart_scheds = set()
    win._hist_chart_hover = None

    from .dibujo import _al_pasar_raton, _al_salir_raton, _dibujar_tendencia
    motion = Gtk.EventControllerMotion()
    motion.connect("motion", _al_pasar_raton, win)
    motion.connect("leave", _al_salir_raton, win)
    win._hist_chart.add_controller(motion)
    win._hist_chart.set_draw_func(_dibujar_tendencia, win)
    caja_visual.append(marco_grafico)

    win._hist_box_leyenda = Gtk.FlowBox(
        valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER,
        column_spacing=6, row_spacing=4,
        selection_mode=Gtk.SelectionMode.NONE,
        margin_top=4,
    )
    caja_visual.append(win._hist_box_leyenda)

    grupo.add(caja_visual)

    # ── Tabla comparativa debajo ──
    win._hist_trend_page = pagina
    win._hist_trend_stat_groups = []

    _refrescar_tendencia(win)
    return pagina


def _obtener_datos_tendencia(win):
    indice = win._hist_trend_combo.get_selected()
    days, _ = _RANGOS_FECHA[indice]
    indice_prueba = win._hist_trend_test_type.get_selected()
    tipo_prueba, _ = _TIPOS_PRUEBA[indice_prueba]
    datos = consultar_tendencia(tipo_prueba, days=days if days > 0 else 365)
    planificadores = {d["scheduler_name"] for d in datos}
    return tipo_prueba, datos, planificadores


def _iniciar_animacion_crossfade(win, datos, planificadores):
    anterior = getattr(win, "_hist_chart_data", [])
    win._hist_chart_data = datos
    win._hist_chart_scheds = planificadores
    if anterior and anterior is not datos:
        win._hist_chart_anim_data = anterior
        win._hist_chart_anim_progress = 0.0
        if hasattr(win, "_hist_chart_anim_timer") and win._hist_chart_anim_timer:
            GLib.source_remove(win._hist_chart_anim_timer)
        _tick_anim(win)


def _calcular_estadisticas(datos, planificadores):
    est = {}
    for planif in planificadores:
        valores = [d["valor"] for d in datos if d["scheduler_name"] == planif]
        if valores:
            est[planif] = {
                "avg": sum(valores) / len(valores),
                "min": min(valores),
                "max": max(valores),
                "last": valores[-1],
                "count": len(valores),
            }
    return est


def _construir_leyenda(win, planificadores, estadisticas):
    if not hasattr(win, "_hist_chart_ocultos"):
        win._hist_chart_ocultos = set()
    win._hist_chart_ocultos &= planificadores

    vaciar_contenedor(win._hist_box_leyenda)

    for planif in sorted(planificadores):
        est = estadisticas.get(planif, {})
        tooltip = (
            f"{traducir('Promedio')}: {est.get('avg', 0):,.1f}\n"
            f"{traducir('Mínimo')}:   {est.get('min', 0):,.1f}\n"
            f"{traducir('Máximo')}:   {est.get('max', 0):,.1f}\n"
            f"{traducir('Último')}:   {est.get('last', 0):,.1f}\n"
            f"{traducir('Tests')}:    {est.get('count', 0)}"
        ) if est else None

        def _toggle(name, visible):
            win._hist_chart.queue_draw()

        crear_chip_leyenda(
            planif, color_func=generar_color_hash,
            on_toggle=_toggle, tooltip_text=tooltip,
            grafico=getattr(win, "_hist_chart", None),
            box_leyenda=win._hist_box_leyenda
        )
    win._hist_chart.queue_draw()


def _construir_resumen(win, datos, estadisticas, planificadores, tipo_prueba):
    vaciar_contenedor(win._hist_trend_summary_box)

    total_pruebas = sum(s["count"] for s in estadisticas.values())
    n_planif = len(planificadores)

    if datos:
        ts_todos = [d["timestamp"] for d in datos]
        fecha_desde = datetime.fromtimestamp(min(ts_todos)).strftime("%d/%m")
        fecha_hasta = datetime.fromtimestamp(max(ts_todos)).strftime("%d/%m")
        rango_fechas = f"{fecha_desde} - {fecha_hasta}"
        es_latencia = "latencia" in tipo_prueba
        ordenados = sorted(estadisticas.items(), key=lambda x: x[1]["avg"], reverse=not (es_latencia or tipo_prueba == "memory"))
        mejor = ordenados[0][0] if ordenados else "—"
        peor = ordenados[-1][0] if ordenados else "—"
    else:
        rango_fechas = "—"
        mejor = "—"
        peor = "—"

    for icon, texto in [
        ("view-list-symbolic", f"{total_pruebas} {traducir('tests')}"),
        ("system-run-symbolic", f"{n_planif} {traducir('planificadores')}"),
        ("x-office-calendar-symbolic", rango_fechas),
        ("starred-symbolic", f"{traducir('Mejor')}: {mejor}"),
        ("dialog-warning-symbolic", f"{traducir('Peor')}: {peor}"),
    ]:
        win._hist_trend_summary_box.append(crear_chip_informativo(icon, texto))


def _construir_tabla(win, datos, planificadores, estadisticas, tipo_prueba):
    pagina = getattr(win, "_hist_trend_page", None)
    for g in getattr(win, "_hist_trend_stat_groups", []):
        if pagina:
            pagina.remove(g)
    win._hist_trend_stat_groups = []

    if not datos or not planificadores:
        return

    es_latencia = "latencia" in tipo_prueba
    etiquetas = [traducir("Promedio"), traducir("Mínimo"), traducir("Máximo"), traducir("Último"), "σ"]
    attrs = ["avg", "minimo", "maximo", "ultimo", "std"]

    planif_stats = {}
    for planif in sorted(planificadores):
        s = estadisticas.get(planif, {})
        valores = [d["valor"] for d in datos if d["scheduler_name"] == planif]
        r, g, b = generar_color_hash(planif)
        planif_stats[planif] = {
            "avg": s.get("avg", 0), "min": s.get("min", 0),
            "max": s.get("max", 0), "last": s.get("last", 0),
            "std": _calcular_desv(valores) if len(valores) > 1 else 0,
            "r": r, "g": g, "b": b,
        }

    modelo = Gio.ListStore(item_type=_FilaTabla)
    for planif in sorted(planificadores):
        est = planif_stats[planif]
        modelo.append(_FilaTabla(
            planif=planif, r=est["r"], g=est["g"], b=est["b"],
            avg=est["avg"], minimo=est["min"], maximo=est["max"],
            ultimo=est["last"], std=est["std"],
        ))

    columna_vista = Gtk.ColumnView(show_row_separators=True)
    columna_vista.set_show_column_separators(True)
    sort_model = Gtk.SortListModel(model=modelo, sorter=columna_vista.get_sorter())
    columna_vista.set_model(Gtk.MultiSelection(model=sort_model))

    # Columna: Planificador
    fab_nombre = Gtk.SignalListItemFactory()
    def _on_setup_nombre(f, li):
        box = Gtk.Box(spacing=6)
        dot = Gtk.DrawingArea()
        dot.set_content_width(8)
        dot.set_content_height(8)
        dot.set_valign(Gtk.Align.CENTER)
        lbl = Gtk.Label(halign=Gtk.Align.START, css_classes=["caption-heading"])
        box.append(dot)
        box.append(lbl)
        li.set_child(box)

    def _on_bind_nombre(f, li):
        item = li.get_item()
        box = li.get_child()
        dot = box.get_first_child()
        lbl = dot.get_next_sibling()
        dot.set_draw_func(lambda a, cr, w, h, rr=item.r, gg=item.g, bb=item.b:
            (cr.set_source_rgb(rr, gg, bb), cr.arc(w/2, h/2, 3.5, 0, 2*math.pi), cr.fill()))
        lbl.set_label(item.planif)

    def _on_unbind_nombre(f, li):
        li.get_child().get_first_child().set_draw_func(lambda a, cr, w, h: None)

    fab_nombre.connect("setup", _on_setup_nombre)
    fab_nombre.connect("bind", _on_bind_nombre)
    fab_nombre.connect("unbind", _on_unbind_nombre)

    col_nombre = Gtk.ColumnViewColumn(title=traducir("Planificador"), factory=fab_nombre)
    col_nombre.set_fixed_width(160)
    columna_vista.append_column(col_nombre)

    # Columnas de métricas
    for etiqueta, attr in zip(etiquetas, attrs):
        fab = Gtk.SignalListItemFactory()

        def _on_setup(f, li):
            box = Gtk.Box(spacing=2, halign=Gtk.Align.CENTER)
            lbl = Gtk.Label(halign=Gtk.Align.CENTER, css_classes=["caption-heading"])
            lbl.set_width_chars(8)
            box.append(lbl)
            li.set_child(box)

        def _on_bind(f, li, a=attr):
            item = li.get_item()
            lbl = li.get_child().get_first_child()
            lbl.set_label(f"{getattr(item, a):,.0f}")
            if a in ("avg", "ultimo") and not es_latencia:
                es_mejor = all(getattr(item, a) >= getattr(m, a) for m in modelo)
            elif a == "minimo" and not es_latencia:
                es_mejor = all(getattr(item, a) >= getattr(m, a) for m in modelo)
            else:
                es_mejor = all(getattr(item, a) <= getattr(m, a) for m in modelo)
            if es_mejor:
                lbl.add_css_class("accent")
            else:
                lbl.remove_css_class("accent")

        def _on_unbind(f, li):
            li.get_child().get_first_child().remove_css_class("accent")

        fab.connect("setup", _on_setup)
        fab.connect("bind", _on_bind)
        fab.connect("unbind", _on_unbind)

        col = Gtk.ColumnViewColumn(title=etiqueta, factory=fab)
        col.set_fixed_width(100)
        sorter = Gtk.CustomSorter()
        sorter.set_sort_func(_crear_funcion_comparar(attr))
        col.set_sorter(sorter)
        columna_vista.append_column(col)

    grupo = Adw.PreferencesGroup(title=traducir("Comparativa de Planificadores"))
    frame = Gtk.Frame(css_classes=["card"])
    for m in ("start", "end", "top", "bottom"):
        getattr(frame, f"set_margin_{m}")(6)
    scroll = Gtk.ScrolledWindow(vscrollbar_policy=Gtk.PolicyType.NEVER, hscrollbar_policy=Gtk.PolicyType.NEVER)
    scroll.set_child(columna_vista)
    frame.set_child(scroll)
    grupo.add(frame)
    if pagina:
        pagina.add(grupo)
    win._hist_trend_stat_groups.append(grupo)


def _refrescar_tendencia(win):
    tipo_prueba, datos, planificadores = _obtener_datos_tendencia(win)
    _iniciar_animacion_crossfade(win, datos, planificadores)
    estadisticas = _calcular_estadisticas(datos, planificadores)
    win._hist_chart_stats = estadisticas
    _construir_leyenda(win, planificadores, estadisticas)
    _construir_resumen(win, datos, estadisticas, planificadores, tipo_prueba)
    _construir_tabla(win, datos, planificadores, estadisticas, tipo_prueba)
    win._hist_chart.queue_draw()


def _tick_anim(win):
    """Timer tick para animación de crossfade del gráfico de tendencia."""
    progreso = getattr(win, "_hist_chart_anim_progress", 1.0)
    progreso += 0.06
    if progreso >= 1.0:
        win._hist_chart_anim_progress = 1.0
        win._hist_chart.queue_draw()
        win._hist_chart_anim_timer = 0
        if hasattr(win, "_hist_chart_anim_data"):
            del win._hist_chart_anim_data
        return False
    win._hist_chart_anim_progress = progreso
    win._hist_chart.queue_draw()
    win._hist_chart_anim_timer = GLib.timeout_add(INTERVALO_FRAME_MS, lambda: _tick_anim(win))
    return False
