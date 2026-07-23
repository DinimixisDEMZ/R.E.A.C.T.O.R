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
from utils.helpers import generar_color_hash
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


def _crear_tarjeta_resumen(icon, texto):
    card = Gtk.Box(spacing=4, css_classes=["card", "pill"], valign=Gtk.Align.CENTER)
    card.set_margin_top(2)
    card.set_margin_bottom(2)
    img = Gtk.Image(icon_name=icon, pixel_size=12)
    img.set_valign(Gtk.Align.CENTER)
    img.set_margin_start(8)
    card.append(img)
    lbl = Gtk.Label(label=texto, css_classes=["caption-heading"])
    lbl.set_margin_end(10)
    lbl.set_margin_top(4)
    lbl.set_margin_bottom(4)
    card.append(lbl)
    return card


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

    grupo = Adw.PreferencesGroup(title="Tendencia de Rendimiento")
    pagina.add(grupo)

    # ── Filtros ──
    filtro_fecha_fila = Adw.ActionRow(title="Rango de Fechas")
    modelo = Gtk.StringList()
    for _, nombre in _RANGOS_FECHA:
        modelo.append(nombre)
    win._hist_trend_combo = Gtk.DropDown(model=modelo, css_classes=["flat"], valign=Gtk.Align.CENTER)
    win._hist_trend_combo.set_selected(1)
    win._hist_trend_combo.connect("notify::selected", lambda *a: _refrescar_tendencia(win))
    filtro_fecha_fila.add_suffix(win._hist_trend_combo)
    grupo.add(filtro_fecha_fila)

    filtro_test_fila = Adw.ActionRow(title="Tipo de Benchmark")
    modelo_test = Gtk.StringList()
    for _, nombre in _TIPOS_PRUEBA:
        modelo_test.append(nombre)
    win._hist_trend_test_type = Gtk.DropDown(model=modelo_test, css_classes=["flat"], valign=Gtk.Align.CENTER)
    win._hist_trend_test_type.set_selected(0)
    win._hist_trend_test_type.connect("notify::selected", lambda *a: _refrescar_tendencia(win))
    filtro_test_fila.add_suffix(win._hist_trend_test_type)
    grupo.add(filtro_test_fila)

    # ── Resumen arriba del gráfico ──
    win._hist_trend_summary_box = Gtk.Box(spacing=8, margin_top=6, margin_bottom=4)
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


def _refrescar_tendencia(win):
    """Actualiza el gráfico de tendencia con el rango y tipo seleccionado."""
    indice = win._hist_trend_combo.get_selected()
    days, _ = _RANGOS_FECHA[indice]
    indice_prueba = win._hist_trend_test_type.get_selected()
    tipo_prueba, _ = _TIPOS_PRUEBA[indice_prueba]

    datos = consultar_tendencia(tipo_prueba, days=days if days > 0 else 365)

    planificadores = set()
    for d in datos:
        planificadores.add(d["scheduler_name"])

    # ── Animación: crossfade ──
    anterior = getattr(win, "_hist_chart_data", [])
    win._hist_chart_data = datos
    win._hist_chart_scheds = planificadores
    if anterior and anterior is not datos:
        win._hist_chart_anim_data = anterior
        win._hist_chart_anim_progress = 0.0
        if hasattr(win, "_hist_chart_anim_timer") and win._hist_chart_anim_timer:
            GLib.source_remove(win._hist_chart_anim_timer)
        _tick_anim(win)

    estadisticas = {}
    for planif in planificadores:
        valores = [d["valor"] for d in datos if d["scheduler_name"] == planif]
        if valores:
            estadisticas[planif] = {
                "avg": sum(valores) / len(valores),
                "min": min(valores),
                "max": max(valores),
                "last": valores[-1],
                "count": len(valores),
            }
    win._hist_chart_stats = estadisticas

    if not hasattr(win, "_hist_chart_ocultos"):
        win._hist_chart_ocultos = set()
    win._hist_chart_ocultos &= planificadores

    while (c := win._hist_box_leyenda.get_first_child()):
        win._hist_box_leyenda.remove(c)

    for planif in sorted(planificadores):
        r, g, b = generar_color_hash(planif)
        est = estadisticas.get(planif, {})
        es_visible = planif not in win._hist_chart_ocultos
        chip = Gtk.Box(spacing=10, css_classes=["card", "pill"], valign=Gtk.Align.CENTER)
        chip.set_cursor(Gdk.Cursor.new_from_name("pointer", None))
        chip.set_has_tooltip(True)
        chip.set_tooltip_text(
            f"Promedio: {est.get('avg', 0):,.1f}\n"
            f"Mínimo:   {est.get('min', 0):,.1f}\n"
            f"Máximo:   {est.get('max', 0):,.1f}\n"
            f"Último:   {est.get('last', 0):,.1f}\n"
            f"Tests:    {est.get('count', 0)}"
        )

        punto = Gtk.DrawingArea()
        punto.set_content_width(12)
        punto.set_content_height(12)
        punto.set_valign(Gtk.Align.CENTER)
        punto.set_margin_start(8)
        punto.set_draw_func(lambda a, cr, w, h, rr=r, gg=g, bb=b: (
            cr.set_source_rgb(rr, gg, bb),
            cr.arc(w / 2, h / 2, 5, 0, 2 * math.pi),
            cr.fill(),
            cr.set_source_rgba(1, 1, 1, 0.2),
            cr.arc(w / 2, h / 2, 5, 0, 2 * math.pi),
            cr.set_line_width(1),
            cr.stroke()
        ))
        chip.append(punto)
        lbl = Gtk.Label(label=planif, css_classes=["caption-heading"])
        lbl.set_margin_end(10)
        lbl.set_margin_top(4)
        lbl.set_margin_bottom(4)
        chip.append(lbl)

        chip.set_opacity(1.0 if es_visible else 0.4)
        punto.set_opacity(1.0 if es_visible else 0.4)

        def _alternar(gesture, n, x, y, s=planif, ch=chip, d=punto):
            if s in win._hist_chart_ocultos:
                win._hist_chart_ocultos.discard(s)
                ch.set_opacity(1.0)
                d.set_opacity(1.0)
            else:
                win._hist_chart_ocultos.add(s)
                ch.set_opacity(0.4)
                d.set_opacity(0.4)
            win._hist_chart.queue_draw()

        clic = Gtk.GestureClick()
        clic.connect("pressed", _alternar)
        chip.add_controller(clic)

        win._hist_box_leyenda.append(chip)

    win._hist_chart.queue_draw()

    # ── Resumen arriba del gráfico ──
    while (c := win._hist_trend_summary_box.get_first_child()):
        win._hist_trend_summary_box.remove(c)

    total_pruebas = sum(s["count"] for s in estadisticas.values())
    n_planificadores = len(planificadores)

    if datos:
        ts_todos = [d["timestamp"] for d in datos]
        fecha_desde = datetime.fromtimestamp(min(ts_todos)).strftime("%d/%m")
        fecha_hasta = datetime.fromtimestamp(max(ts_todos)).strftime("%d/%m")
        rango_fechas = f"{fecha_desde} - {fecha_hasta}"

        es_latencia = "latencia" in tipo_prueba
        planificadores_ordenados = sorted(estadisticas.items(), key=lambda x: x[1]["avg"])
        if not es_latencia and tipo_prueba != "memory":
            planificadores_ordenados.reverse()
        mejor_planif = planificadores_ordenados[0][0] if planificadores_ordenados else "—"
        peor_planif = planificadores_ordenados[-1][0] if planificadores_ordenados else "—"
    else:
        rango_fechas = "—"
        mejor_planif = "—"
        peor_planif = "—"

    for icon, texto in [
        ("view-list-symbolic", f"{total_pruebas} tests"),
        ("system-run-symbolic", f"{n_planificadores} planificadores"),
        ("x-office-calendar-symbolic", rango_fechas),
        ("starred-symbolic", f"Mejor: {mejor_planif}"),
        ("dialog-warning-symbolic", f"Peor: {peor_planif}"),
    ]:
        card = _crear_tarjeta_resumen(icon, texto)
        win._hist_trend_summary_box.append(card)

    # ── Tabla comparativa (ColumnView nativo) ──
    pagina = getattr(win, "_hist_trend_page", None)
    for g in getattr(win, "_hist_trend_stat_groups", []):
        if pagina:
            pagina.remove(g)
    win._hist_trend_stat_groups = []

    if datos and planificadores:
        es_latencia = "latencia" in tipo_prueba
        etiquetas = ["Promedio", "Mínimo", "Máximo", "Último", "σ"]
        attrs = ["avg", "minimo", "maximo", "ultimo", "std"]

        estadisticas_planif = {}
        for planif in sorted(planificadores):
            s = estadisticas.get(planif, {})
            valores = [d["valor"] for d in datos if d["scheduler_name"] == planif]
            r, g, b = generar_color_hash(planif)
            estadisticas_planif[planif] = {
                "avg": s.get("avg", 0), "min": s.get("min", 0),
                "max": s.get("max", 0), "last": s.get("last", 0),
                "std": _calcular_desv(valores) if len(valores) > 1 else 0,
                "r": r, "g": g, "b": b,
            }

        # Modelo de datos
        modelo = Gio.ListStore(item_type=_FilaTabla)
        for planif in sorted(planificadores):
            est = estadisticas_planif[planif]
            modelo.append(_FilaTabla(
                planif=planif, r=est["r"], g=est["g"], b=est["b"],
                avg=est["avg"], minimo=est["min"], maximo=est["max"],
                ultimo=est["last"], std=est["std"],
            ))

        columna_vista = Gtk.ColumnView(show_row_separators=True)
        columna_vista.set_show_column_separators(True)

        sort_model = Gtk.SortListModel(model=modelo, sorter=columna_vista.get_sorter())
        sel = Gtk.MultiSelection(model=sort_model)
        columna_vista.set_model(sel)

        # ── Columna: Planificador (sin ordenamiento) ──
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

        col_nombre = Gtk.ColumnViewColumn(title="Planificador", factory=fab_nombre)
        col_nombre.set_fixed_width(160)
        columna_vista.append_column(col_nombre)

        # ── Columnas de métricas ──
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
                lbl = li.get_child().get_first_child()
                lbl.remove_css_class("accent")

            fab.connect("setup", _on_setup)
            fab.connect("bind", _on_bind)
            fab.connect("unbind", _on_unbind)

            col = Gtk.ColumnViewColumn(title=etiqueta, factory=fab)
            col.set_fixed_width(100)
            _attr = attr

            sorter = Gtk.CustomSorter()
            sorter.set_sort_func(_crear_funcion_comparar(_attr))
            col.set_sorter(sorter)
            columna_vista.append_column(col)

        grupo = Adw.PreferencesGroup(title="Comparativa de Planificadores")
        frame = Gtk.Frame(css_classes=["card"])
        frame.set_margin_start(6)
        frame.set_margin_end(6)
        frame.set_margin_top(2)
        frame.set_margin_bottom(2)

        scroll = Gtk.ScrolledWindow(vscrollbar_policy=Gtk.PolicyType.NEVER, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_child(columna_vista)
        frame.set_child(scroll)
        grupo.add(frame)

        if pagina:
            pagina.add(grupo)
        win._hist_trend_stat_groups.append(grupo)

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
    win._hist_chart_anim_timer = GLib.timeout_add(16, lambda: _tick_anim(win))
    return False
