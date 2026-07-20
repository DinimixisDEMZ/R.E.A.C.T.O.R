"""
Pestaña de Historial — Muestra benchmarks anteriores guardados en SQLite.
Incluye: versiones del entorno, filtros, tabla de resultados y gráfico de tendencia.
"""

import math
import time
from datetime import datetime

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib

from core.database import (
    consultar_historial, consultar_tendencia, obtener_schedulers_historial,
    contar_resultados, eliminar_historial,
)
from utils.helpers import generar_color_hash

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


def setup_historial_ui(win):
    page = Adw.PreferencesPage(title="Historial")

    header = Adw.HeaderBar()
    view = Adw.ToolbarView(content=page)
    view.add_top_bar(header)
    win.pag_historial.set_child(view)

    # ── Entorno del Sistema ──
    grupo_env = Adw.PreferencesGroup(title="Entorno del Sistema")
    page.add(grupo_env)

    versiones = getattr(win, 'versiones', {})
    for titulo, valor in [
        ("Kernel", versiones.get("kernel", "—")),
        ("scxctl", versiones.get("scxctl", "—")),
        ("stress-ng", versiones.get("stressng", "—")),
        ("hyperfine", versiones.get("hyperfine", "—")),
    ]:
        fila = Adw.ActionRow(title=titulo, subtitle=valor or "—")
        fila.add_css_class("property")
        grupo_env.add(fila)

    # ── Filtros ──
    grupo_filtros = Adw.PreferencesGroup(title="Filtros")
    page.add(grupo_filtros)

    # Scheduler
    scheds = ["Todos"] + obtener_schedulers_historial()
    modelo_scheds = Gtk.StringList()
    for s in scheds:
        modelo_scheds.append(s)
    win._hist_combo_sched = Adw.ComboRow(title="Scheduler", model=modelo_scheds)
    grupo_filtros.add(win._hist_combo_sched)

    # Tipo de prueba
    modelo_tests = Gtk.StringList()
    for _, nombre in _TEST_TYPES:
        modelo_tests.append(nombre)
    win._hist_combo_test = Adw.ComboRow(title="Tipo de Prueba", model=modelo_tests)
    grupo_filtros.add(win._hist_combo_test)

    # Rango de fechas
    modelo_fechas = Gtk.StringList()
    for _, nombre in _DATE_RANGES:
        modelo_fechas.append(nombre)
    win._hist_combo_fecha = Adw.ComboRow(title="Rango de Fechas", model=modelo_fechas)
    win._hist_combo_fecha.set_selected(1)
    grupo_filtros.add(win._hist_combo_fecha)

    # ── Contador ──
    win._hist_lbl_contador = Gtk.Label(
        label=f"{contar_resultados()} resultado(s) registrado(s)",
        css_classes=["dim-label", "caption"]
    )
    win._hist_lbl_contador.set_xalign(0)
    grupo_filtros.add(win._hist_lbl_contador)

    # ── Resultados Históricos ──
    grupo_resultados = Adw.PreferencesGroup(title="Resultados Históricos")
    page.add(grupo_resultados)

    win._hist_box_resultados = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    win._hist_box_resultados.add_css_class("boxed-list")
    scroller = Gtk.ScrolledWindow()
    scroller.set_child(win._hist_box_resultados)
    scroller.set_min_content_height(200)
    scroller.set_max_content_height(400)
    scroller.set_hexpand(True)
    grupo_resultados.add(scroller)

    # ── Gráfico de Tendencia ──
    grupo_tendencia = Adw.PreferencesGroup(title="Tendencia de Rendimiento")
    page.add(grupo_tendencia)

    win._hist_chart = Gtk.DrawingArea()
    win._hist_chart.set_content_height(280)
    win._hist_chart.set_hexpand(True)
    win._hist_chart.set_vexpand(True)
    win._hist_chart.add_css_class("card")
    win._hist_chart_data = []
    win._hist_chart_scheds = set()
    win._hist_chart_hover = None

    motion = Gtk.EventControllerMotion()
    motion.connect("motion", _on_chart_hover, win)
    win._hist_chart.add_controller(motion)

    win._hist_chart.set_draw_func(_dibujar_tendencia, win)
    grupo_tendencia.add(win._hist_chart)

    win._hist_box_leyenda = Gtk.FlowBox()
    win._hist_box_leyenda.set_valign(Gtk.Align.CENTER)
    win._hist_box_leyenda.set_halign(Gtk.Align.START)
    win._hist_box_leyenda.set_column_spacing(8)
    win._hist_box_leyenda.set_row_spacing(4)
    grupo_tendencia.add(win._hist_box_leyenda)

    # ── Acciones ──
    grupo_acciones = Adw.PreferencesGroup(title="Acciones")
    page.add(grupo_acciones)

    btn_borrar = Gtk.Button(label="Borrar Historial", css_classes=["destructive-action"])
    btn_borrar.set_halign(Gtk.Align.START)
    btn_borrar.connect("clicked", _al_borrar_historial, win)
    grupo_acciones.add(btn_borrar)

    # ── Conectar filtros ──
    win._hist_combo_sched.connect("notify::selected", lambda *a: _refrescar_historial(win))
    win._hist_combo_test.connect("notify::selected", lambda *a: _refrescar_historial(win))
    win._hist_combo_fecha.connect("notify::selected", lambda *a: _refrescar_historial(win))

    # Carga inicial
    _refrescar_historial(win)


def _refrescar_historial(win):
    sched_idx = win._hist_combo_sched.get_selected()
    test_idx = win._hist_combo_test.get_selected()
    fecha_idx = win._hist_combo_fecha.get_selected()

    sched = None if sched_idx == 0 else win._hist_combo_sched.get_model().get_string(sched_idx)
    test = None if test_idx == 0 else _TEST_TYPES[test_idx][0]
    days, _ = _DATE_RANGES[fecha_idx]

    date_from = time.time() - (days * 86400) if days > 0 else None

    resultados = consultar_historial(scheduler=sched, test_type=test, date_from=date_from)

    # Actualizar contador
    win._hist_lbl_contador.set_label(f"{len(resultados)} resultado(s) encontrado(s)")

    # Limpiar lista
    while (c := win._hist_box_resultados.get_first_child()):
        win._hist_box_resultados.remove(c)

    # Llenar lista
    for r in resultados:
        ts = datetime.fromtimestamp(r["timestamp"]).strftime("%Y-%m-%d %H:%M")
        tipo_nombre = dict(_TEST_TYPES).get(r["test_type"], r["test_type"])
        val = r["valor"]
        p95 = r.get("p95")

        if "latencia" in r["test_type"]:
            val_str = f"{val:,.1f} µs"
            if p95:
                val_str += f" (p95: {p95:,.1f})"
        elif r["test_type"] == "threads":
            val_str = f"{val:,.1f} ops/s"
        else:
            val_str = f"{val:,.1f} pts"

        fila = Adw.ActionRow(
            title=f"{r['scheduler_name']} — {tipo_nombre}",
            subtitle=f"{ts}  •  {val_str}"
        )

        badge = Gtk.Label(
            label=r.get("run_type", "manual").upper(),
            css_classes=["caption", "dim-label"]
        )
        fila.add_suffix(badge)
        win._hist_box_resultados.append(fila)

    # Actualizar gráfico
    _actualizar_datos_grafico(win, test, days)


def _actualizar_datos_grafico(win, test_type, days):
    if not test_type:
        test_type = "cpu"

    datos = consultar_tendencia(test_type, days=days if days > 0 else 365)

    scheds = set()
    for d in datos:
        scheds.add(d["scheduler_name"])

    win._hist_chart_data = datos
    win._hist_chart_scheds = scheds

    # Reconstruir leyenda
    while (c := win._hist_box_leyenda.get_first_child()):
        win._hist_box_leyenda.remove(c)

    for sched in sorted(scheds):
        r, g, b = generar_color_hash(sched)
        chip = Gtk.Box(spacing=6, css_classes=["card", "pill"])
        dot = Gtk.DrawingArea()
        dot.set_content_width(10)
        dot.set_content_height(10)
        dot.set_valign(Gtk.Align.CENTER)
        dot.set_margin_start(6)
        dot.set_draw_func(lambda a, cr, w, h, rgb: (
            cr.set_source_rgb(*rgb),
            cr.arc(w / 2, h / 2, 4, 0, 2 * math.pi),
            cr.fill(),
        ), (r, g, b))
        lbl = Gtk.Label(label=sched, css_classes=["caption"])
        lbl.set_margin_end(6)
        lbl.set_margin_top(2)
        lbl.set_margin_bottom(2)
        chip.append(dot)
        chip.append(lbl)
        win._hist_box_leyenda.append(chip)

    win._hist_chart.queue_draw()


def _dibujar_tendencia(area, cr, w, h, win):
    datos = win._hist_chart_data
    scheds = win._hist_chart_scheds

    tr, tg, tb = 0.6, 0.6, 0.6

    if not datos or not scheds:
        cr.set_source_rgba(tr, tg, tb, 0.3)
        cr.set_font_size(13)
        ext = cr.text_extents("Sin datos suficientes para mostrar tendencia")
        cr.move_to(w / 2 - ext.width / 2, h / 2)
        cr.show_text("Sin datos suficientes para mostrar tendencia")
        return

    ml, mr, mt, mb = 60, 30, 30, 50
    cw = w - ml - mr
    ch = h - mt - mb

    if cw <= 0 or ch <= 0:
        return

    timestamps = [d["timestamp"] for d in datos]
    valores = [d["valor"] for d in datos]
    t_min = min(timestamps)
    t_max = max(timestamps)
    v_min = min(valores) * 0.9
    v_max = max(valores) * 1.1
    if v_max == v_min:
        v_max = v_min + 1
    if t_max == t_min:
        t_max = t_min + 1

    # Grid horizontal
    cr.set_line_width(0.5)
    for i in range(5):
        y = mt + ch * (1 - i / 4)
        val = v_min + (v_max - v_min) * (i / 4)
        cr.set_source_rgba(tr, tg, tb, 0.08)
        cr.move_to(ml, y)
        cr.line_to(ml + cw, y)
        cr.stroke()
        cr.set_source_rgba(tr, tg, tb, 0.4)
        cr.set_font_size(10)
        if val >= 1000:
            lbl = f"{val/1000:.1f}k"
        else:
            lbl = f"{val:.0f}"
        ext = cr.text_extents(lbl)
        cr.move_to(ml - ext.width - 8, y + ext.height / 3)
        cr.show_text(lbl)

    # Eje X — fechas
    num_labels = min(6, len(set(timestamps)))
    if num_labels > 1:
        for i in range(num_labels):
            t = t_min + (t_max - t_min) * (i / (num_labels - 1))
            x = ml + cw * (i / (num_labels - 1))
            cr.set_source_rgba(tr, tg, tb, 0.08)
            cr.move_to(x, mt)
            cr.line_to(x, mt + ch)
            cr.stroke()
            cr.set_source_rgba(tr, tg, tb, 0.4)
            cr.set_font_size(9)
            fecha = datetime.fromtimestamp(t).strftime("%d/%m")
            ext = cr.text_extents(fecha)
            cr.move_to(x - ext.width / 2, mt + ch + 20)
            cr.show_text(fecha)

    # Líneas por scheduler
    for sched in sorted(scheds):
        pts_sched = [(d["timestamp"], d["valor"]) for d in datos if d["scheduler_name"] == sched]
        if len(pts_sched) < 1:
            continue

        r, g, b = generar_color_hash(sched)
        cr.set_source_rgba(r, g, b, 0.8)
        cr.set_line_width(2.0)
        cr.set_line_cap(1)
        cr.set_line_join(1)

        first = True
        for ts, val in pts_sched:
            x = ml + cw * ((ts - t_min) / (t_max - t_min))
            y = mt + ch * (1 - (val - v_min) / (v_max - v_min))
            if first:
                cr.move_to(x, y)
                first = False
            else:
                cr.line_to(x, y)
        cr.stroke()

        # Puntos
        for ts, val in pts_sched:
            x = ml + cw * ((ts - t_min) / (t_max - t_min))
            y = mt + ch * (1 - (val - v_min) / (v_max - v_min))
            cr.set_source_rgba(r, g, b, 0.3)
            cr.arc(x, y, 6, 0, 2 * math.pi)
            cr.fill()
            cr.set_source_rgba(r, g, b, 1.0)
            cr.arc(x, y, 3, 0, 2 * math.pi)
            cr.fill()

    # Hover
    if win._hist_chart_hover:
        hx, hy = win._hist_chart_hover
        for sched in sorted(scheds):
            pts_sched = [(d["timestamp"], d["valor"]) for d in datos if d["scheduler_name"] == sched]
            r, g, b = generar_color_hash(sched)
            for ts, val in pts_sched:
                x = ml + cw * ((ts - t_min) / (t_max - t_min))
                y = mt + ch * (1 - (val - v_min) / (v_max - v_min))
                dist = math.hypot(hx - x, hy - y)
                if dist < 15:
                    cr.set_source_rgba(r, g, b, 1.0)
                    cr.arc(x, y, 6, 0, 2 * math.pi)
                    cr.fill()

                    val_str = f"{val:,.1f}"
                    fecha_str = datetime.fromtimestamp(ts).strftime("%d/%m %H:%M")
                    tooltip = f"{sched}: {val_str} — {fecha_str}"
                    cr.set_source_rgba(0, 0, 0, 0.8)
                    cr.set_font_size(10)
                    ext = cr.text_extents(tooltip)
                    tx = min(x + 10, w - ext.width - 10)
                    ty = max(y - 20, 10)
                    cr.rectangle(tx - 4, ty - ext.height - 4, ext.width + 8, ext.height + 8)
                    cr.fill()
                    cr.set_source_rgba(1, 1, 1, 0.95)
                    cr.move_to(tx, ty)
                    cr.show_text(tooltip)
                    return


def _on_chart_hover(controller, x, y, win):
    win._hist_chart_hover = (x, y)
    win._hist_chart.queue_draw()


def _al_borrar_historial(btn, win):
    dialog = Adw.AlertDialog(
        title="Borrar Historial",
        body="Esta acción eliminará todos los resultados guardados permanentemente.",
    )
    dialog.add_response("cancel", "Cancelar")
    dialog.add_response("delete", "Borrar Todo")
    dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
    dialog.connect("response", _confirmar_borrar, win)
    dialog.present(win)


def _confirmar_borrar(dialog, response, win):
    if response == "delete":
        eliminar_historial()
        _refrescar_historial(win)
