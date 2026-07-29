"""
Funciones de dibujado Cairo para el gráfico de tendencia del historial.
Incluye hover, tooltips y animaciones del chart.
"""

import math
from datetime import datetime

import cairo
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib

from utils.helpers import generar_color_hash
from core.constantes import INTERVALO_FRAME_MS


def _al_pasar_raton(controlador, x, y, win):
    win._hist_chart_hover = (x, y)
    datos = getattr(win, "_hist_chart_data", [])
    planif_hover = None
    if datos:
        valores_x = [d["timestamp"] for d in datos]
        x_min, x_max = min(valores_x), max(valores_x)
        valores_y = [d["valor"] for d in datos]
        y_min, y_max = min(valores_y), max(valores_y)
        if y_max == y_min:
            y_max = y_min + 1
        w = win._hist_chart.get_width()
        h = win._hist_chart.get_height()
        margen = (55, 25, 25, 40)
        def tx(t): return margen[0] + (t - x_min) / (x_max - x_min) * (w - margen[0] - margen[2])
        def ty(v): return margen[1] + h - margen[1] - margen[3] - (v - y_min) / (y_max - y_min) * (h - margen[1] - margen[3])
        mejor_dist = 40
        for d in datos:
            if d["scheduler_name"] in getattr(win, "_hist_chart_ocultos", set()):
                continue
            sx = tx(d["timestamp"])
            sy = ty(d["valor"])
            dist = math.hypot(x - sx, y - sy)
            if dist < mejor_dist:
                mejor_dist = dist
                planif_hover = d["scheduler_name"]
    anterior = getattr(win, "_hist_chart_hover_sched", None)
    win._hist_chart_hover_sched = planif_hover
    if planif_hover is not None:
        win._hist_chart_hover_anim = 1.0
        timer = getattr(win, "_hist_chart_hover_timer", None)
        if timer:
            GLib.source_remove(timer)
            win._hist_chart_hover_timer = None
    elif planif_hover is None and anterior is not None:
        _iniciar_anim_hover(win)
    win._hist_chart.queue_draw()


def _al_salir_raton(controlador, win):
    win._hist_chart_last_hover = win._hist_chart_hover
    win._hist_chart_hover_sched = None
    _iniciar_anim_hover(win)
    win._hist_chart.queue_draw()


def _iniciar_anim_hover(win):
    timer = getattr(win, "_hist_chart_hover_timer", None)
    if timer:
        GLib.source_remove(timer)
    win._hist_chart_hover_timer = GLib.timeout_add(INTERVALO_FRAME_MS, _tick_anim_hover, win)


def _tick_anim_hover(win):
    progreso = getattr(win, "_hist_chart_hover_anim", 0.0)
    objetivo = 1.0 if getattr(win, "_hist_chart_hover_sched", None) is not None else 0.0
    if abs(progreso - objetivo) < 0.01:
        win._hist_chart_hover_anim = objetivo
        win._hist_chart.queue_draw()
        win._hist_chart_hover_timer = None
        if objetivo == 0.0:
            win._hist_chart_hover = None
            win._hist_chart_last_hover = None
        return False
    paso = 0.08
    win._hist_chart_hover_anim = min(1.0, max(0.0, progreso + (paso if objetivo > progreso else -paso)))
    win._hist_chart.queue_draw()
    return True


def _dibujar_tendencia(area, cr, w, h, win):
    datos = getattr(win, "_hist_chart_data", []) or []
    hover = getattr(win, "_hist_chart_hover", None)
    es_oscuro = Adw.StyleManager.get_default().get_dark()
    prog_anim = getattr(win, "_hist_chart_anim_progress", 1.0)
    old_datos = getattr(win, "_hist_chart_anim_data", None)
    activos = datos or old_datos

    if not activos:
        cr.select_font_face("Sans", 0, 0)
        cr.set_font_size(14)
        cr.set_source_rgba(0.6, 0.6, 0.6, 0.6)
        ext = cr.text_extents("Sin datos de tendencia")
        cr.move_to(w / 2 - ext.width / 2, h / 2 + ext.height / 2)
        cr.show_text("Sin datos de tendencia")
        return

    # Compute plot area from active data
    valores = [d["valor"] for d in activos]
    marcas_temp = [d["timestamp"] for d in activos]
    v_min, v_max = min(valores), max(valores)
    if v_max == v_min:
        v_max = v_min + 1
    t_min, t_max = min(marcas_temp), max(marcas_temp)
    if t_max == t_min:
        t_max = t_min + 1
    v_prom = sum(valores) / len(valores)

    margen = 55, 25, 25, 40
    ancho_trazo = w - margen[0] - margen[2]
    alto_trazo = h - margen[1] - margen[3]
    if ancho_trazo <= 0 or alto_trazo <= 0:
        return

    def tx(t):
        return margen[0] + (t - t_min) / (t_max - t_min) * ancho_trazo

    def ty(v):
        return margen[1] + alto_trazo - (v - v_min) / (v_max - v_min) * alto_trazo

    y_inferior = margen[1] + alto_trazo

    # ── Cuadrícula, etiquetas y promedio ──
    color_cuadricula = (0.5, 0.5, 0.5, 0.15 if es_oscuro else 0.12)
    cr.set_line_width(1)
    cr.set_source_rgba(*color_cuadricula)
    for i in range(5):
        y = margen[1] + alto_trazo * i / 4
        cr.move_to(margen[0], y)
        cr.line_to(w - margen[2], y)
        cr.stroke()

    # ── Cuadrícula vertical (fechas) ──
    n_etiquetas_x = max(3, min(8, int(ancho_trazo / 80)))
    for i in range(n_etiquetas_x):
        fraccion = i / (n_etiquetas_x - 1) if n_etiquetas_x > 1 else 0.5
        x = margen[0] + ancho_trazo * fraccion
        cr.set_line_width(1)
        cr.set_source_rgba(*color_cuadricula)
        cr.move_to(x, margen[1])
        cr.line_to(x, margen[1] + alto_trazo)
        cr.stroke()

    y_prom = ty(v_prom)
    cr.set_line_width(1.2)
    cr.set_source_rgba(0.7, 0.7, 0.7, 0.5 if es_oscuro else 0.35)
    cr.set_dash([4, 3], 0)
    cr.move_to(margen[0], y_prom)
    cr.line_to(w - margen[2], y_prom)
    cr.stroke()
    cr.set_dash([], 0)

    cr.select_font_face("Sans", 0, 0)
    cr.set_font_size(9)
    color_etiqueta = (0.55, 0.55, 0.55, 0.7 if es_oscuro else 0.6)
    cr.set_source_rgba(*color_etiqueta)
    for i, val in enumerate([v_min, v_prom, v_max]):
        y = margen[1] + alto_trazo * (1 - (val - v_min) / (v_max - v_min))
        etiqueta = [f"▼ {val:,.0f}", f"⌀ {val:,.0f}", f"▲ {val:,.0f}"][i]
        ext = cr.text_extents(etiqueta)
        cr.move_to(margen[0] - ext.width - 5, y + ext.height / 3)
        cr.show_text(etiqueta)

    cr.select_font_face("Sans", 0, 0)
    cr.set_font_size(8.5)
    cr.set_source_rgba(*color_etiqueta)
    for i in range(n_etiquetas_x):
        fraccion = i / (n_etiquetas_x - 1) if n_etiquetas_x > 1 else 0.5
        t = t_min + fraccion * (t_max - t_min)
        x = tx(t)
        etiqueta = datetime.fromtimestamp(t).strftime("%d/%m")
        ext = cr.text_extents(etiqueta)
        cr.move_to(x - ext.width / 2, y_inferior + 14)
        cr.show_text(etiqueta)

    # ── Dibujar líneas de datos con fade ──
    planif_hover = getattr(win, "_hist_chart_hover_sched", None)
    if old_datos and prog_anim < 1.0:
        _dibujar_lineas_tendencia(cr, win, old_datos, tx, ty, 1.0 - prog_anim, es_oscuro, planif_hover, margen, alto_trazo)
    if datos:
        _dibujar_lineas_tendencia(cr, win, datos, tx, ty, prog_anim, es_oscuro, planif_hover, margen, alto_trazo)

    # ── Tooltip hover (fade out when leaving) ──
    alfa_hover = getattr(win, "_hist_chart_hover_anim", 0.0)
    if datos and prog_anim >= 1.0:
        pos_tooltip = hover or getattr(win, "_hist_chart_last_hover", None)
        if pos_tooltip:
            _dibujar_tooltip(cr, win, datos, tx, ty, w, margen, es_oscuro, pos_tooltip, alfa_hover)


def _dibujar_lineas_tendencia(cr, win, datos, tx, ty, alfa, es_oscuro, planif_hover=None, margen=None, alto_trazo=0):
    """Dibuja líneas y puntos con opacidad alfa (0=invisible, 1=visible)."""
    if margen is None:
        margen = (55, 25, 25, 40)
    datos_planif = {}
    for d in datos:
        datos_planif.setdefault(d["scheduler_name"], []).append(d)

    scheds = sorted(getattr(win, "_hist_chart_scheds", set()) or datos_planif.keys())
    colores_planif = {}
    for sched in scheds:
        colores_planif[sched] = generar_color_hash(sched)

    for name, sched_datos in datos_planif.items():
        ocultos = getattr(win, "_hist_chart_ocultos", set())
        if name in ocultos:
            continue

        esta_hover = planif_hover is not None and name == planif_hover
        prog_anim = getattr(win, "_hist_chart_hover_anim", 0.0)
        if planif_hover is not None and not esta_hover:
            oscurecer = 0.25 + 0.75 * (1.0 - prog_anim)
        else:
            oscurecer = 1.0
        alfa_linea = alfa * oscurecer
        alfa_punto = alfa * oscurecer
        color = colores_planif.get(name, (0.6, 0.6, 0.6))
        datos_ordenados = sorted(sched_datos, key=lambda d: d["timestamp"])
        r, g, b = color

        # ── Relleno de área bajo la línea (gradiente) ──
        if len(datos_ordenados) >= 2:
            cr.set_line_width(0)
            pts = [(tx(d["timestamp"]), ty(d["valor"])) for d in datos_ordenados]
            y_base_area = margen[1] + alto_trazo
            cr.move_to(pts[0][0], y_base_area)
            for px, py in pts:
                cr.line_to(px, py)
            cr.line_to(pts[-1][0], y_base_area)
            cr.close_path()
            # Gradiente vertical: color arriba → transparente abajo
            pat = cairo.LinearGradient(0, margen[1], 0, y_base_area)
            pat.add_color_stop_rgba(0, r, g, b, 0.25 * alfa_linea)
            pat.add_color_stop_rgba(1, r, g, b, 0.02 * alfa_linea)
            cr.set_source(pat)
            cr.fill()

        # ── Línea de datos ──

        cr.set_source_rgba(r, g, b, 0.85 * alfa_linea)
        cr.set_line_width(3.0 if esta_hover else 2.5)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        for idx, d in enumerate(datos_ordenados):
            x = tx(d["timestamp"])
            y = ty(d["valor"])
            if idx == 0:
                cr.move_to(x, y)
            else:
                cr.line_to(x, y)
        cr.stroke()

        valores = [d["valor"] for d in datos_ordenados]
        s_min, s_max = min(valores), max(valores)
        for d in datos_ordenados:
            x = tx(d["timestamp"])
            y = ty(d["valor"])
            es_max = d["valor"] == s_max
            es_min = d["valor"] == s_min
            radio = 5 if (es_max or es_min) else 3.5

            if es_max or es_min:
                radio_brillo = radio * 2.5
                cr.set_source_rgba(r, g, b, (0.25 if es_max else 0.15) * alfa_punto)
                cr.arc(x, y, radio_brillo, 0, 2 * math.pi)
                cr.fill()

            cr.set_source_rgba(r, g, b, 0.9 * alfa_punto)
            cr.arc(x, y, radio, 0, 2 * math.pi)
            cr.fill()
            cr.set_source_rgba(1, 1, 1, (0.4 if es_oscuro else 0.7) * alfa_punto)
            cr.set_line_width(1.2)
            cr.arc(x, y, radio, 0, 2 * math.pi)
            cr.stroke()


def _dibujar_tooltip(cr, win, datos, tx, ty, w, margen, es_oscuro, hover, alfa_hover=1.0):
    hx, hy = hover
    if not (margen[0] <= hx <= w - margen[2]):
        return

    # ── Encontrar el timestamp más cercano al hover X ──
    ocultos = getattr(win, "_hist_chart_ocultos", set())
    visibles = [d for d in datos if d["scheduler_name"] not in ocultos]
    if not visibles:
        return

    # Buscar timestamp del punto más cercano en X
    mejor_d = None
    mejor_dist_x = 9999
    for d in visibles:
        sx = tx(d["timestamp"])
        dist = abs(hx - sx)
        if dist < mejor_dist_x:
            mejor_dist_x = dist
            mejor_d = d
    if not mejor_d or mejor_dist_x > 60:
        return

    snap_x = tx(mejor_d["timestamp"])
    snap_ts = mejor_d["timestamp"]

    # ── Línea vertical de crosshair ──
    cr.set_line_width(1)
    cr.set_source_rgba(0.6, 0.6, 0.6, (0.4 if es_oscuro else 0.3) * alfa_hover)
    cr.set_dash([3, 3], 0)
    cr.move_to(snap_x, margen[1])
    cr.line_to(snap_x, margen[1] + (w - margen[0] - margen[2]))
    cr.stroke()
    cr.set_dash([], 0)

    # ── Recoger todos los valores visibles en este timestamp ──
    puntos_en_x = []
    for d in visibles:
        if abs(tx(d["timestamp"]) - snap_x) < 2:
            puntos_en_x.append(d)
    puntos_en_x.sort(key=lambda d: d["valor"], reverse=True)

    colores_planif = {}
    for d in puntos_en_x:
        colores_planif[d["scheduler_name"]] = generar_color_hash(d["scheduler_name"])

    # ── Calcular tamaño del tooltip ──
    cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    cr.set_font_size(10)
    alto_linea = 16
    relleno = 12
    alto_encabezado = 28

    ancho_max_etiqueta = 0
    for d in puntos_en_x:
        ext = cr.text_extents(d["scheduler_name"])
        if ext.width > ancho_max_etiqueta:
            ancho_max_etiqueta = ext.width

    cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
    cr.set_font_size(9.5)
    val_text = f"{puntos_en_x[0]['valor']:,.0f}" if puntos_en_x else ""
    val_ext = cr.text_extents(val_text)
    ancho_col_valor = val_ext.width + 16

    fecha_str = datetime.fromtimestamp(snap_ts).strftime("%d/%m %H:%M")
    ext_fecha = cr.text_extents(fecha_str)
    ancho_tooltip = max(ancho_max_etiqueta + ancho_col_valor + relleno * 2, ext_fecha.width + relleno * 2 + 8)
    alto_tooltip = alto_encabezado + len(puntos_en_x) * alto_linea + relleno * 2

    x2_tooltip = snap_x + 14
    y2_tooltip = margen[1] + 10
    if x2_tooltip + ancho_tooltip > w - margen[2]:
        x2_tooltip = snap_x - ancho_tooltip - 14
    if y2_tooltip + alto_tooltip > margen[1] + (w - margen[0] - margen[2]):
        y2_tooltip = margen[1] + (w - margen[0] - margen[2]) - alto_tooltip - 10

    # ── Fondo del tooltip ──
    radio_r = 8
    fondo = (0.1, 0.11, 0.13, 0.94 * alfa_hover) if es_oscuro else (0.96, 0.96, 0.96, 0.96 * alfa_hover)
    cr.set_source_rgba(*fondo)
    cr.new_sub_path()
    cr.arc(x2_tooltip + ancho_tooltip - radio_r, y2_tooltip + radio_r, radio_r, -math.pi / 2, 0)
    cr.arc(x2_tooltip + ancho_tooltip - radio_r, y2_tooltip + alto_tooltip - radio_r, radio_r, 0, math.pi / 2)
    cr.arc(x2_tooltip + radio_r, y2_tooltip + alto_tooltip - radio_r, radio_r, math.pi / 2, math.pi)
    cr.arc(x2_tooltip + radio_r, y2_tooltip + radio_r, radio_r, math.pi, 3 * math.pi / 2)
    cr.close_path()
    cr.fill()

    # ── Borde sutil ──
    cr.set_line_width(1)
    cr.set_source_rgba(0.5, 0.5, 0.5, 0.2 * alfa_hover)
    cr.new_sub_path()
    cr.arc(x2_tooltip + ancho_tooltip - radio_r, y2_tooltip + radio_r, radio_r, -math.pi / 2, 0)
    cr.arc(x2_tooltip + ancho_tooltip - radio_r, y2_tooltip + alto_tooltip - radio_r, radio_r, 0, math.pi / 2)
    cr.arc(x2_tooltip + radio_r, y2_tooltip + alto_tooltip - radio_r, radio_r, math.pi / 2, math.pi)
    cr.arc(x2_tooltip + radio_r, y2_tooltip + radio_r, radio_r, math.pi, 3 * math.pi / 2)
    cr.close_path()
    cr.stroke()

    # ── Fecha ──
    primer_plano = (0.85, 0.85, 0.85, 0.9 * alfa_hover) if es_oscuro else (0.3, 0.3, 0.3, 0.9 * alfa_hover)
    cr.set_source_rgba(*primer_plano)
    cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
    cr.set_font_size(9)
    cr.move_to(x2_tooltip + relleno, y2_tooltip + relleno + 10)
    cr.show_text(fecha_str)

    # ── Separador ──
    y_separador = y2_tooltip + alto_encabezado
    cr.set_line_width(1)
    cr.set_source_rgba(0.5, 0.5, 0.5, 0.2 * alfa_hover)
    cr.move_to(x2_tooltip + relleno, y_separador)
    cr.line_to(x2_tooltip + ancho_tooltip - relleno, y_separador)
    cr.stroke()

    # ── Valores por scheduler ──
    cr.set_font_size(9.5)
    for i, d in enumerate(puntos_en_x):
        y = y_separador + relleno + 6 + i * alto_linea
        color = colores_planif.get(d["scheduler_name"], (0.6, 0.6, 0.6))
        r, g, b = color

        # Dot
        cr.set_source_rgba(r, g, b, 1.0 * alfa_hover)
        cr.arc(x2_tooltip + relleno + 4, y + 4, 3.5, 0, 2 * math.pi)
        cr.fill()

        # Nombre
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        fg2 = (0.9, 0.9, 0.9, 0.95 * alfa_hover) if es_oscuro else (0.15, 0.15, 0.15, 0.95 * alfa_hover)
        cr.set_source_rgba(*fg2)
        cr.move_to(x2_tooltip + relleno + 14, y + 4)
        cr.show_text(d["scheduler_name"])

        # Valor
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_source_rgba(r, g, b, 0.95 * alfa_hover)
        val_str = f"{d['valor']:,.0f}"
        val_ext = cr.text_extents(val_str)
        cr.move_to(x2_tooltip + ancho_tooltip - relleno - val_ext.width, y + 4)
        cr.show_text(val_str)
