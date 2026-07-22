"""
Pestaña de Historial — Muestra benchmarks anteriores guardados en SQLite.
Incluye: versiones del entorno, filtros, tabla de resultados y gráfico de tendencia.
"""

import math
import time
from datetime import datetime

import cairo
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gdk

from core.database import (
    consultar_historial, consultar_tendencia, obtener_schedulers_historial,
    contar_resultados, eliminar_historial,
)
from utils.helpers import generar_color_hash

_TEST_TYPES = [
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


def setup_historial_ui(win):
    header = Adw.HeaderBar()

    stack = Adw.ViewStack()

    pag_resultados = _crear_pagina_resultados(win)
    stack.add_titled_with_icon(
        pag_resultados, "resultados",
        "Resultados", "view-list-bullet-symbolic",
    )

    pag_tendencia = _crear_pagina_tendencia(win)
    stack.add_titled_with_icon(
        pag_tendencia, "tendencia",
        "Tendencia", "view-continuous-symbolic",
    )

    pag_entorno = _crear_pagina_entorno(win)
    stack.add_titled_with_icon(
        pag_entorno, "entorno",
        "Entorno", "computer-symbolic",
    )

    tab_buttons = {}
    tab_data = [
        ("resultados", "view-list-bullet-symbolic", "Resultados"),
        ("tendencia", "view-continuous-symbolic", "Tendencia"),
        ("entorno", "computer-symbolic", "Entorno"),
    ]

    tab_box = Gtk.Box(spacing=0, css_classes=["linked"])
    for name, icon, label in tab_data:
        btn = Gtk.ToggleButton(css_classes=["flat"])
        box = Gtk.Box(spacing=6)
        box.append(Gtk.Image(icon_name=icon))
        box.append(Gtk.Label(label=label))
        btn.set_child(box)
        btn.connect("toggled", lambda b, n=name, tb=tab_buttons: _cambiar_tab(stack, b, n, tb))
        tab_buttons[name] = btn
        tab_box.append(btn)
    header.set_title_widget(tab_box)

    tab_buttons["resultados"].set_active(True)
    stack.set_visible_child_name("resultados")

    btn_borrar = Gtk.Button(
        icon_name="user-trash-symbolic",
        tooltip_text="Borrar todo el historial",
        css_classes=["destructive-action"],
    )
    btn_borrar.connect("clicked", _al_borrar_historial, win)
    header.pack_start(btn_borrar)

    tab_buttons["resultados"].set_active(True)
    stack.set_visible_child_name("resultados")

    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    content.append(stack)

    view = Adw.ToolbarView(content=content)
    view.add_top_bar(header)
    win.pag_historial.set_child(view)

    _rebuild_sched_chips(win)
    _refrescar_historial(win)


def _cambiar_tab(stack, btn, name, tab_buttons):
    if btn.get_active():
        stack.set_visible_child_name(name)
        for n, b in tab_buttons.items():
            if b is not btn:
                b.set_active(False)


def _crear_pagina_resultados(win):
    page = Adw.PreferencesPage()

    grupo_chips = Adw.PreferencesGroup(title="Planificadores")
    win._hist_chips_box = Gtk.FlowBox(
        selection_mode=Gtk.SelectionMode.NONE,
        row_spacing=4, column_spacing=4,
        max_children_per_line=10,
        margin_start=6, margin_end=6, margin_top=6, margin_bottom=6,
    )
    grupo_chips.add(win._hist_chips_box)

    fecha_row = Adw.ActionRow(title="Rango de Fechas")
    modelo_fechas = Gtk.StringList()
    for _, nombre in _DATE_RANGES:
        modelo_fechas.append(nombre)
    win._hist_combo_fecha = Gtk.DropDown(model=modelo_fechas, css_classes=["flat"], valign=Gtk.Align.CENTER)
    win._hist_combo_fecha.set_selected(1)
    win._hist_combo_fecha.connect("notify::selected", lambda *a: _refrescar_historial(win))
    fecha_row.add_suffix(win._hist_combo_fecha)
    grupo_chips.add(fecha_row)

    page.add(grupo_chips)

    grupo_resultados = Adw.PreferencesGroup(title="Resultados Históricos")
    win._hist_box_resultados = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL, spacing=12,
    )
    grupo_resultados.add(win._hist_box_resultados)
    page.add(grupo_resultados)

    return page


def _rebuild_sched_chips(win):
    """Construye los chips de filtro por scheduler."""
    box = getattr(win, "_hist_chips_box", None)
    if box is None:
        return

    while (c := box.get_first_child()):
        box.remove(c)

    win._hist_chips_active = set()
    scheds = sorted(obtener_schedulers_historial())

    for sched in scheds:
        r, g, b = generar_color_hash(sched)

        chip = Gtk.Box(spacing=6, css_classes=["card", "pill"])
        chip.set_cursor(Gdk.Cursor.new_from_name("pointer", None))

        dot = Gtk.DrawingArea()
        dot.set_content_width(10)
        dot.set_content_height(10)
        dot.set_valign(Gtk.Align.CENTER)
        dot.set_margin_start(6)
        dot.set_draw_func(lambda a, cr, w, h, cr_r=r, cr_g=g, cr_b=b: (
            cr.set_source_rgb(cr_r, cr_g, cr_b),
            cr.arc(w / 2, h / 2, 4, 0, 2 * math.pi),
            cr.fill(),
            cr.set_source_rgba(1, 1, 1, 0.15),
            cr.arc(w / 2, h / 2, 4, 0, 2 * math.pi),
            cr.set_line_width(1),
            cr.stroke(),
        ))
        chip.append(dot)

        label = Gtk.Label(label=sched, css_classes=["caption"])
        label.set_margin_top(3)
        label.set_margin_bottom(3)
        label.set_margin_end(8)
        chip.append(label)

        chip.set_opacity(0.5)
        dot.set_opacity(0.4)

        def _toggle(gesture, n, x, y, ch=chip, d=dot, s=sched):
            if s in win._hist_chips_active:
                win._hist_chips_active.discard(s)
                ch.set_opacity(0.5)
                d.set_opacity(0.4)
            else:
                win._hist_chips_active.add(s)
                ch.set_opacity(1.0)
                d.set_opacity(1.0)
            _refrescar_historial(win)

        click = Gtk.GestureClick()
        click.connect("pressed", _toggle)
        chip.add_controller(click)

        box.append(chip)


def _crear_summary_card(icon, text):
    card = Gtk.Box(spacing=4, css_classes=["card", "pill"])
    card.set_margin_top(2)
    card.set_margin_bottom(2)
    img = Gtk.Image(icon_name=icon, pixel_size=12)
    img.set_valign(Gtk.Align.CENTER)
    img.set_margin_start(8)
    card.append(img)
    lbl = Gtk.Label(label=text, css_classes=["caption-heading"])
    lbl.set_margin_end(10)
    lbl.set_margin_top(4)
    lbl.set_margin_bottom(4)
    card.append(lbl)
    return card


def _calc_stddev(vals):
    avg = sum(vals) / len(vals)
    return math.sqrt(sum((x - avg) ** 2 for x in vals) / len(vals))


def _crear_pagina_tendencia(win):
    page = Adw.PreferencesPage()

    grupo = Adw.PreferencesGroup(title="Tendencia de Rendimiento")
    page.add(grupo)

    filtro_row = Adw.ActionRow(title="Filtros")
    filtro_box = Gtk.Box(spacing=8)
    modelo = Gtk.StringList()
    for _, nombre in _DATE_RANGES:
        modelo.append(nombre)
    win._hist_trend_combo = Gtk.DropDown(model=modelo, css_classes=["flat"], valign=Gtk.Align.CENTER)
    win._hist_trend_combo.set_selected(1)
    win._hist_trend_combo.connect("notify::selected", lambda *a: _refrescar_trend(win))
    filtro_box.append(win._hist_trend_combo)

    modelo_test = Gtk.StringList()
    for _, nombre in _TEST_TYPES:
        modelo_test.append(nombre)
    win._hist_trend_test_type = Gtk.DropDown(model=modelo_test, css_classes=["flat"], valign=Gtk.Align.CENTER)
    win._hist_trend_test_type.set_selected(0)
    win._hist_trend_test_type.connect("notify::selected", lambda *a: _refrescar_trend(win))
    filtro_box.append(win._hist_trend_test_type)

    filtro_row.add_suffix(filtro_box)
    grupo.add(filtro_row)

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
    frame_chart = Gtk.Frame(css_classes=["card"])
    frame_chart.set_child(win._hist_chart)
    win._hist_chart_data = []
    win._hist_chart_scheds = set()
    win._hist_chart_hover = None

    motion = Gtk.EventControllerMotion()
    motion.connect("motion", _on_chart_hover, win)
    motion.connect("leave", _on_chart_leave, win)
    win._hist_chart.add_controller(motion)
    win._hist_chart.set_draw_func(_dibujar_tendencia, win)
    caja_visual.append(frame_chart)

    win._hist_box_leyenda = Gtk.FlowBox(
        valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER,
        column_spacing=6, row_spacing=4,
        selection_mode=Gtk.SelectionMode.NONE,
        margin_top=4,
    )
    caja_visual.append(win._hist_box_leyenda)

    grupo.add(caja_visual)

    # ── Tabla comparativa debajo ──
    win._hist_trend_page = page
    win._hist_trend_stat_groups = []

    _refrescar_trend(win)
    return page


def _refrescar_trend(win):
    """Actualiza el gráfico de tendencia con el rango y tipo seleccionado."""
    idx = win._hist_trend_combo.get_selected()
    days, _ = _DATE_RANGES[idx]
    test_idx = win._hist_trend_test_type.get_selected()
    test_type, _ = _TEST_TYPES[test_idx]

    datos = consultar_tendencia(test_type, days=days if days > 0 else 365)

    scheds = set()
    for d in datos:
        scheds.add(d["scheduler_name"])

    # ── Animación: crossfade ──
    old = getattr(win, "_hist_chart_data", [])
    win._hist_chart_data = datos
    win._hist_chart_scheds = scheds
    if old and old is not datos:
        win._hist_chart_anim_data = old
        win._hist_chart_anim_progress = 0.0
        if hasattr(win, "_hist_chart_anim_timer") and win._hist_chart_anim_timer:
            GLib.source_remove(win._hist_chart_anim_timer)
        _tick_anim(win)

    stats = {}
    for sched in scheds:
        vals = [d["valor"] for d in datos if d["scheduler_name"] == sched]
        if vals:
            stats[sched] = {
                "avg": sum(vals) / len(vals),
                "min": min(vals),
                "max": max(vals),
                "last": vals[-1],
                "count": len(vals),
            }
    win._hist_chart_stats = stats

    if not hasattr(win, "_hist_chart_ocultos"):
        win._hist_chart_ocultos = set()
    win._hist_chart_ocultos &= scheds

    while (c := win._hist_box_leyenda.get_first_child()):
        win._hist_box_leyenda.remove(c)

    for sched in sorted(scheds):
        r, g, b = generar_color_hash(sched)
        st = stats.get(sched, {})
        is_visible = sched not in win._hist_chart_ocultos
        chip = Gtk.Box(spacing=10, css_classes=["card", "pill"])
        chip.set_cursor(Gdk.Cursor.new_from_name("pointer", None))
        chip.set_has_tooltip(True)
        chip.set_tooltip_text(
            f"Promedio: {st.get('avg', 0):,.1f}\n"
            f"Mínimo:   {st.get('min', 0):,.1f}\n"
            f"Máximo:   {st.get('max', 0):,.1f}\n"
            f"Último:   {st.get('last', 0):,.1f}\n"
            f"Tests:    {st.get('count', 0)}"
        )

        dot = Gtk.DrawingArea()
        dot.set_content_width(12)
        dot.set_content_height(12)
        dot.set_valign(Gtk.Align.CENTER)
        dot.set_margin_start(8)
        dot.set_draw_func(lambda a, cr, w, h, rr=r, gg=g, bb=b: (
            cr.set_source_rgb(rr, gg, bb),
            cr.arc(w / 2, h / 2, 5, 0, 2 * math.pi),
            cr.fill(),
            cr.set_source_rgba(1, 1, 1, 0.2),
            cr.arc(w / 2, h / 2, 5, 0, 2 * math.pi),
            cr.set_line_width(1),
            cr.stroke()
        ))
        chip.append(dot)
        lbl = Gtk.Label(label=sched, css_classes=["caption-heading"])
        lbl.set_margin_end(10)
        lbl.set_margin_top(4)
        lbl.set_margin_bottom(4)
        chip.append(lbl)

        chip.set_opacity(1.0 if is_visible else 0.4)
        dot.set_opacity(1.0 if is_visible else 0.4)

        def _toggle(gesture, n, x, y, s=sched, ch=chip, d=dot):
            if s in win._hist_chart_ocultos:
                win._hist_chart_ocultos.discard(s)
                ch.set_opacity(1.0)
                d.set_opacity(1.0)
            else:
                win._hist_chart_ocultos.add(s)
                ch.set_opacity(0.4)
                d.set_opacity(0.4)
            win._hist_chart.queue_draw()

        click = Gtk.GestureClick()
        click.connect("pressed", _toggle)
        chip.add_controller(click)

        win._hist_box_leyenda.append(chip)

    win._hist_chart.queue_draw()

    # ── Resumen arriba del gráfico ──
    while (c := win._hist_trend_summary_box.get_first_child()):
        win._hist_trend_summary_box.remove(c)

    total_tests = sum(s["count"] for s in stats.values())
    n_scheds = len(scheds)

    if datos:
        ts_all = [d["timestamp"] for d in datos]
        date_from = datetime.fromtimestamp(min(ts_all)).strftime("%d/%m")
        date_to = datetime.fromtimestamp(max(ts_all)).strftime("%d/%m")
        date_range = f"{date_from} - {date_to}"

        is_latency = "latencia" in test_type
        sorted_scheds = sorted(stats.items(), key=lambda x: x[1]["avg"])
        if not is_latency and test_type != "memory":
            sorted_scheds.reverse()
        best_sched = sorted_scheds[0][0] if sorted_scheds else "—"
        worst_sched = sorted_scheds[-1][0] if sorted_scheds else "—"
    else:
        date_range = "—"
        best_sched = "—"
        worst_sched = "—"

    for icon, text in [
        ("view-list-symbolic", f"{total_tests} tests"),
        ("system-run-symbolic", f"{n_scheds} planificadores"),
        ("x-office-calendar-symbolic", date_range),
        ("starred-symbolic", f"Mejor: {best_sched}"),
        ("dialog-warning-symbolic", f"Peor: {worst_sched}"),
    ]:
        card = _crear_summary_card(icon, text)
        win._hist_trend_summary_box.append(card)

    # ── Tabla comparativa ──
    page = getattr(win, "_hist_trend_page", None)
    for g in getattr(win, "_hist_trend_stat_groups", []):
        if page:
            page.remove(g)
    win._hist_trend_stat_groups = []

    if datos and scheds:
        is_latency = "latencia" in test_type
        col_keys = ["avg", "min", "max", "last", "std"]
        col_labels = ["Promedio", "Mínimo", "Máximo", "Último", "σ"]

        sched_stats = {}
        for sched in sorted(scheds):
            s = stats.get(sched, {})
            vals = [d["valor"] for d in datos if d["scheduler_name"] == sched]
            r, g, b = generar_color_hash(sched)
            sched_stats[sched] = {
                "avg": s.get("avg", 0),
                "min": s.get("min", 0),
                "max": s.get("max", 0),
                "last": s.get("last", 0),
                "std": _calc_stddev(vals) if len(vals) > 1 else 0,
                "r": r, "g": g, "b": b,
            }

        grupo = Adw.PreferencesGroup(title="Comparativa de Planificadores")
        frame = Gtk.Frame(css_classes=["card"])
        frame.set_margin_start(6)
        frame.set_margin_end(6)
        frame.set_margin_top(2)
        frame.set_margin_bottom(2)
        grid = Gtk.Grid(
            column_spacing=16, row_spacing=4,
            margin_start=12, margin_end=12,
            margin_top=8, margin_bottom=8,
        )
        frame.set_child(grid)

        # Header
        grid.attach(
            Gtk.Label(label="Planificador", css_classes=["dim-label", "caption-heading"],
                      halign=Gtk.Align.START),
            0, 0, 1, 1,
        )
        for ci, name in enumerate(col_labels, start=1):
            grid.attach(
                Gtk.Label(label=name, css_classes=["dim-label", "caption-heading"],
                          halign=Gtk.Align.CENTER, hexpand=True),
                ci, 0, 1, 1,
            )

        # Separator row
        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        grid.attach(sep, 0, 1, 6, 1)

        # Determine best avg
        best_sched = min(sched_stats.items(), key=lambda x: x[1]["avg"]) if is_latency else max(sched_stats.items(), key=lambda x: x[1]["avg"])
        best_name = best_sched[0]

        for ri, sched in enumerate(sorted(scheds), start=2):
            st = sched_stats[sched]

            # Name cell
            name_box = Gtk.Box(spacing=6)
            dot = Gtk.DrawingArea()
            dot.set_content_width(8)
            dot.set_content_height(8)
            dot.set_valign(Gtk.Align.CENTER)
            dot.set_draw_func(lambda a, cr, w, h, rr=st["r"], gg=st["g"], bb=st["b"]: (
                cr.set_source_rgb(rr, gg, bb),
                cr.arc(w / 2, h / 2, 3.5, 0, 2 * math.pi),
                cr.fill(),
            ))
            name_box.append(dot)
            name_box.append(Gtk.Label(label=sched, css_classes=["caption-heading"]))
            grid.attach(name_box, 0, ri, 1, 1)

            # Stat cells
            for ci, col in enumerate(col_keys, start=1):
                val = st[col]
                is_best = col == "avg" and sched == best_name
                grid.attach(
                    Gtk.Label(
                        label=f"{val:,.0f}",
                        css_classes=["accent", "caption-heading"] if is_best else ["caption-heading"],
                        halign=Gtk.Align.CENTER, hexpand=True,
                    ),
                    ci, ri, 1, 1,
                )

        grupo.add(frame)

        if page:
            page.add(grupo)
        win._hist_trend_stat_groups.append(grupo)

    win._hist_chart.queue_draw()


def _tick_anim(win):
    """Timer tick para animación de crossfade del gráfico de tendencia."""
    progress = getattr(win, "_hist_chart_anim_progress", 1.0)
    progress += 0.06
    if progress >= 1.0:
        win._hist_chart_anim_progress = 1.0
        win._hist_chart.queue_draw()
        win._hist_chart_anim_timer = 0
        if hasattr(win, "_hist_chart_anim_data"):
            del win._hist_chart_anim_data
        return False
    win._hist_chart_anim_progress = progress
    win._hist_chart.queue_draw()
    win._hist_chart_anim_timer = GLib.timeout_add(16, lambda: _tick_anim(win))
    return False


def _crear_pagina_entorno(win):
    page = Adw.PreferencesPage()

    grupo = Adw.PreferencesGroup(title="Entorno del Sistema")
    page.add(grupo)

    versiones = getattr(win, 'versiones', {})
    for icono, titulo, valor in [
        ("system-run-symbolic", "Kernel", versiones.get("kernel", "—")),
        ("application-x-executable-symbolic", "scxctl", versiones.get("scxctl", "—")),
        ("utilities-terminal-symbolic", "stress-ng", versiones.get("stressng", "—")),
        ("utilities-system-monitor-symbolic", "hyperfine", versiones.get("hyperfine", "—")),
    ]:
        fila = Adw.ActionRow(title=titulo, subtitle=valor or "—")
        fila.add_css_class("property")
        fila.set_icon_name(icono)
        grupo.add(fila)

    return page


def _refrescar_historial(win):
    fecha_idx = win._hist_combo_fecha.get_selected()
    days, _ = _DATE_RANGES[fecha_idx]
    date_from = time.time() - (days * 86400) if days > 0 else None

    active = getattr(win, "_hist_chips_active", set())
    sched = list(active)[0] if len(active) == 1 else None
    test = None

    resultados = consultar_historial(
        scheduler=sched, test_type=None, date_from=date_from,
    )

    if active:
        resultados = [r for r in resultados if r["scheduler_name"] in active]

    while (c := win._hist_box_resultados.get_first_child()):
        win._hist_box_resultados.remove(c)

    contador = 0
    groups = {}
    for r in resultados:
        groups.setdefault(r["scheduler_name"], []).append(r)

    for sched, items in groups.items():
        r_sched, g_sched, b_sched = generar_color_hash(sched)
        grupo = Adw.PreferencesGroup(title=sched)

        header_box = Gtk.Box(spacing=6, margin_start=6, margin_bottom=4)
        dot = Gtk.DrawingArea()
        dot.set_content_width(8)
        dot.set_content_height(8)
        dot.set_valign(Gtk.Align.CENTER)
        dot.set_draw_func(lambda a, cr, w, h, cr_r=r_sched, cr_g=g_sched, cr_b=b_sched: (
            cr.set_source_rgb(cr_r, cr_g, cr_b),
            cr.arc(w / 2, h / 2, 3.5, 0, 2 * math.pi),
            cr.fill(),
        ))
        header_box.append(dot)
        grupo.set_header_suffix(header_box)

        for r in items:
            ts = datetime.fromtimestamp(r["timestamp"]).strftime("%d/%m %H:%M")
            tipo_nombre = dict(_TEST_TYPES).get(r["test_type"], r["test_type"])
            val = r["valor"]
            p95 = r.get("p95")

            if "latencia" in r["test_type"]:
                val_str = f"{val:,.1f} µs"
                if p95:
                    val_str += f"  (p95: {p95:,.1f})"
            elif r["test_type"] == "threads":
                val_str = f"{val:,.1f} ops/s"
            else:
                val_str = f"{val:,.1f} pts"

            row = Adw.ActionRow(title=tipo_nombre, subtitle=ts)
            row.add_suffix(Gtk.Label(label=val_str, valign=Gtk.Align.CENTER))

            run_type = r.get("run_type", "manual")
            badge_icon = "emblem-synchronizing-symbolic" if run_type == "auto" else "applications-engineering-symbolic"
            badge_tip = "Detección automática" if run_type == "auto" else "Benchmark manual"
            badge = Gtk.Image(
                icon_name=badge_icon, tooltip_text=badge_tip,
                pixel_size=14, css_classes=["dim-label"],
                valign=Gtk.Align.CENTER,
            )
            badge.set_margin_start(8)
            row.add_suffix(badge)

            grupo.add(row)
            contador += 1

        win._hist_box_resultados.append(grupo)

    grupo = win._hist_box_resultados.get_parent()
    while grupo and not isinstance(grupo, Adw.PreferencesGroup):
        grupo = grupo.get_parent()
    if grupo:
        grupo.set_title(f"Resultados Históricos — {contador} encontrado(s)")

def _on_chart_hover(controller, x, y, win):
    win._hist_chart_hover = (x, y)
    datos = getattr(win, "_hist_chart_data", [])
    hover_sched = None
    if datos:
        xs = [d["timestamp"] for d in datos]
        x_min, x_max = min(xs), max(xs)
        y_vals = [d["valor"] for d in datos]
        y_min, y_max = min(y_vals), max(y_vals)
        if y_max == y_min:
            y_max = y_min + 1
        w = win._hist_chart.get_width()
        h = win._hist_chart.get_height()
        margin = (55, 25, 25, 40)
        def tx(t): return margin[0] + (t - x_min) / (x_max - x_min) * (w - margin[0] - margin[2])
        def ty(v): return margin[1] + h - margin[1] - margin[3] - (v - y_min) / (y_max - y_min) * (h - margin[1] - margin[3])
        best_dist = 40
        for d in datos:
            if d["scheduler_name"] in getattr(win, "_hist_chart_ocultos", set()):
                continue
            sx = tx(d["timestamp"])
            sy = ty(d["valor"])
            dist = math.hypot(x - sx, y - sy)
            if dist < best_dist:
                best_dist = dist
                hover_sched = d["scheduler_name"]
    old = getattr(win, "_hist_chart_hover_sched", None)
    win._hist_chart_hover_sched = hover_sched
    if hover_sched != old:
        _start_hover_anim(win)
    win._hist_chart.queue_draw()


def _on_chart_leave(controller, win):
    win._hist_chart_hover = None
    win._hist_chart_hover_sched = None
    _start_hover_anim(win)
    win._hist_chart.queue_draw()


def _start_hover_anim(win):
    timer = getattr(win, "_hist_chart_hover_timer", None)
    if timer:
        GLib.source_remove(timer)
    win._hist_chart_hover_timer = GLib.timeout_add(16, _tick_hover_anim, win)


def _tick_hover_anim(win):
    prog = getattr(win, "_hist_chart_hover_anim", 0.0)
    target = 1.0 if getattr(win, "_hist_chart_hover_sched", None) is not None else 0.0
    if abs(prog - target) < 0.01:
        win._hist_chart_hover_anim = target
        win._hist_chart.queue_draw()
        return False
    step = 0.08
    win._hist_chart_hover_anim = min(1.0, max(0.0, prog + (step if target > prog else -step)))
    win._hist_chart.queue_draw()
    return True


def _dibujar_tendencia(area, cr, w, h, win):
    datos = getattr(win, "_hist_chart_data", []) or []
    hover = getattr(win, "_hist_chart_hover", None)
    is_dark = Adw.StyleManager.get_default().get_dark()
    anim_prog = getattr(win, "_hist_chart_anim_progress", 1.0)
    old_datos = getattr(win, "_hist_chart_anim_data", None)
    active = datos or old_datos

    if not active:
        cr.select_font_face("Sans", 0, 0)
        cr.set_font_size(14)
        cr.set_source_rgba(0.6, 0.6, 0.6, 0.6)
        ext = cr.text_extents("Sin datos de tendencia")
        cr.move_to(w / 2 - ext.width / 2, h / 2 + ext.height / 2)
        cr.show_text("Sin datos de tendencia")
        return

    # Compute plot area from active data
    vals = [d["valor"] for d in active]
    ts = [d["timestamp"] for d in active]
    v_min, v_max = min(vals), max(vals)
    if v_max == v_min:
        v_max = v_min + 1
    t_min, t_max = min(ts), max(ts)
    if t_max == t_min:
        t_max = t_min + 1
    v_avg = sum(vals) / len(vals)

    margin = 55, 25, 25, 40
    plot_w = w - margin[0] - margin[2]
    plot_h = h - margin[1] - margin[3]
    if plot_w <= 0 or plot_h <= 0:
        return

    def tx(t):
        return margin[0] + (t - t_min) / (t_max - t_min) * plot_w

    def ty(v):
        return margin[1] + plot_h - (v - v_min) / (v_max - v_min) * plot_h

    bottom_y = margin[1] + plot_h

    # ── Cuadrícula, etiquetas y promedio (una sola vez) ──
    grid_color = (0.5, 0.5, 0.5, 0.15 if is_dark else 0.12)
    cr.set_line_width(1)
    cr.set_source_rgba(*grid_color)
    for i in range(5):
        y = margin[1] + plot_h * i / 4
        cr.move_to(margin[0], y)
        cr.line_to(w - margin[2], y)
        cr.stroke()

    avg_y = ty(v_avg)
    cr.set_line_width(1.2)
    cr.set_source_rgba(0.7, 0.7, 0.7, 0.5 if is_dark else 0.35)
    cr.set_dash([4, 3], 0)
    cr.move_to(margin[0], avg_y)
    cr.line_to(w - margin[2], avg_y)
    cr.stroke()
    cr.set_dash([], 0)

    cr.select_font_face("Sans", 0, 0)
    cr.set_font_size(9)
    label_color = (0.55, 0.55, 0.55, 0.7 if is_dark else 0.6)
    cr.set_source_rgba(*label_color)
    for i, val in enumerate([v_min, v_avg, v_max]):
        y = margin[1] + plot_h * (1 - (val - v_min) / (v_max - v_min))
        label = [f"▼ {val:,.0f}", f"⌀ {val:,.0f}", f"▲ {val:,.0f}"][i]
        ext = cr.text_extents(label)
        cr.move_to(margin[0] - ext.width - 5, y + ext.height / 3)
        cr.show_text(label)

    cr.select_font_face("Sans", 0, 0)
    cr.set_font_size(8.5)
    cr.set_source_rgba(*label_color)
    for i, t in enumerate([t_min, (t_min + t_max) / 2, t_max]):
        x = tx(t)
        label = datetime.fromtimestamp(t).strftime("%d/%m")
        ext = cr.text_extents(label)
        cr.move_to(x - ext.width / 2, bottom_y + 14)
        cr.show_text(label)

    # ── Dibujar líneas de datos con fade ──
    hover_sched = getattr(win, "_hist_chart_hover_sched", None)
    if old_datos and anim_prog < 1.0:
        _draw_trend_lines(cr, win, old_datos, tx, ty, 1.0 - anim_prog, is_dark, hover_sched)
    if datos:
        _draw_trend_lines(cr, win, datos, tx, ty, anim_prog, is_dark, hover_sched)

    # ── Tooltip hover ──
    if hover and datos and anim_prog >= 1.0:
        _draw_tooltip(cr, win, datos, tx, ty, w, margin, is_dark, hover)


def _draw_trend_lines(cr, win, datos, tx, ty, alpha, is_dark, hover_sched=None):
    """Dibuja líneas y puntos con opacidad alpha (0=invisible, 1=visible)."""
    scheduler_data = {}
    for d in datos:
        scheduler_data.setdefault(d["scheduler_name"], []).append(d)

    scheds = sorted(getattr(win, "_hist_chart_scheds", set()) or scheduler_data.keys())
    sched_colors = {}
    for sched in scheds:
        sched_colors[sched] = generar_color_hash(sched)

    for name, sched_datos in scheduler_data.items():
        ocultos = getattr(win, "_hist_chart_ocultos", set())
        if name in ocultos:
            continue

        # Hover: dim non-hovered lines with animation
        is_hovered = hover_sched is not None and name == hover_sched
        anim_prog = getattr(win, "_hist_chart_hover_anim", 0.0)
        if hover_sched is not None and not is_hovered:
            dim = 0.25 + 0.75 * (1.0 - anim_prog)
        else:
            dim = 1.0
        line_alpha = alpha * dim
        point_alpha = alpha * dim
        color = sched_colors.get(name, (0.6, 0.6, 0.6))
        sorted_datos = sorted(sched_datos, key=lambda d: d["timestamp"])
        r, g, b = color

        cr.set_source_rgba(r, g, b, 0.85 * line_alpha)
        cr.set_line_width(3.0 if is_hovered else 2.5)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        for idx, d in enumerate(sorted_datos):
            x = tx(d["timestamp"])
            y = ty(d["valor"])
            if idx == 0:
                cr.move_to(x, y)
            else:
                cr.line_to(x, y)
        cr.stroke()

        vals = [d["valor"] for d in sorted_datos]
        s_min, s_max = min(vals), max(vals)
        for d in sorted_datos:
            x = tx(d["timestamp"])
            y = ty(d["valor"])
            is_max = d["valor"] == s_max
            is_min = d["valor"] == s_min
            radius = 5 if (is_max or is_min) else 3.5

            if is_max or is_min:
                glow_r = radius * 2.5
                cr.set_source_rgba(r, g, b, (0.25 if is_max else 0.15) * point_alpha)
                cr.arc(x, y, glow_r, 0, 2 * math.pi)
                cr.fill()

            cr.set_source_rgba(r, g, b, 0.9 * point_alpha)
            cr.arc(x, y, radius, 0, 2 * math.pi)
            cr.fill()
            cr.set_source_rgba(1, 1, 1, (0.4 if is_dark else 0.7) * point_alpha)
            cr.set_line_width(1.2)
            cr.arc(x, y, radius, 0, 2 * math.pi)
            cr.stroke()


def _draw_tooltip(cr, win, datos, tx, ty, w, margin, is_dark, hover):
        sched_colors = {}
        for name in set(d["scheduler_name"] for d in datos):
            sched_colors[name] = generar_color_hash(name)
        hx, hy = hover
        if margin[0] <= hx <= w - margin[2]:
            best = None
            best_dist = 30
            ocultos = getattr(win, "_hist_chart_ocultos", set())
            for d in datos:
                if d["scheduler_name"] in ocultos:
                    continue
                sx = tx(d["timestamp"])
                sy = ty(d["valor"])
                dist = math.hypot(hx - sx, hy - sy)
                if dist < best_dist:
                    best_dist = dist
                    best = d
            if best:
                color = sched_colors.get(best["scheduler_name"], (0.6, 0.6, 0.6))
                sx = tx(best["timestamp"])
                sy = ty(best["valor"])
                r, g, b = color

                tipo_nombre = dict(_TEST_TYPES).get(best.get("test_type", ""), best.get("test_type", ""))
                fecha_str = datetime.fromtimestamp(best["timestamp"]).strftime("%d/%m %H:%M")

                title_text = best["scheduler_name"]
                desc_text = f"{tipo_nombre}  •  {best['valor']:,.1f}  •  {fecha_str}"

                cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
                cr.set_font_size(10.5)
                ext_title = cr.text_extents(title_text)

                cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
                cr.set_font_size(9.5)
                ext_desc = cr.text_extents(desc_text)

                tw = max(ext_title.width, ext_desc.width) + 24
                th = 36
                tx2, ty2 = sx + 12, sy - th - 10
                if tx2 + tw > w:
                    tx2 = sx - tw - 12
                if ty2 < 5:
                    ty2 = sy + 14

                rr = 6
                cr.set_source_rgba(0.08, 0.09, 0.11, 0.92)
                cr.new_sub_path()
                cr.arc(tx2 + tw - rr, ty2 + rr, rr, -math.pi / 2, 0)
                cr.arc(tx2 + tw - rr, ty2 + th - rr, rr, 0, math.pi / 2)
                cr.arc(tx2 + rr, ty2 + th - rr, rr, math.pi / 2, math.pi)
                cr.arc(tx2 + rr, ty2 + rr, rr, math.pi, 3 * math.pi / 2)
                cr.close_path()
                cr.fill()

                cr.set_source_rgba(r, g, b, 1.0)
                cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
                cr.set_font_size(10.5)
                cr.move_to(tx2 + 12, ty2 + 14)
                cr.show_text(title_text)

                cr.set_source_rgba(0.9, 0.9, 0.9, 0.95)
                cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
                cr.set_font_size(9.5)
                cr.move_to(tx2 + 12, ty2 + 26)
                cr.show_text(desc_text)


def _al_borrar_historial(btn, win):
    def _on_confirm(response):
        if response == "confirm":
            eliminar_historial()
            _refrescar_historial(win)

    dialog = Adw.AlertDialog(
        heading="¿Borrar todo el historial?",
        body="Esta acción eliminará permanentemente todos los resultados guardados.",
    )
    dialog.add_response("cancel", "Cancelar")
    dialog.add_response("confirm", "Borrar")
    dialog.set_response_appearance("confirm", Adw.ResponseAppearance.DESTRUCTIVE)
    dialog.connect("response", lambda d, r: _on_confirm(r))
    dialog.present(win)
