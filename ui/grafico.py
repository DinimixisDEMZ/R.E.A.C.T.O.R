"""
Gráfico radar/spider para comparar schedulers.
Visualiza múltiples categorías como un polígono superpuesto.
"""

import math

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib

try:
    import cairo
    _HAS_CAIRO = True
except ImportError:
    _HAS_CAIRO = False

from utils.helpers import generar_color_hash, obtener_color_tema


class GraficoComparativo(Gtk.DrawingArea):

    def __init__(self):
        super().__init__()
        self.set_draw_func(self.dibujar)
        self.num_categorias = 6
        self.categorias = [
            "Context\nSwitch", "Carga\nMixta", "Mutex",
            "Fork", "Compile", "Bajo\nCarga"
        ]
        self.datos_raw = {}
        self.valores_animados = {}
        self.max_por_categoria = [0.0] * self.num_categorias
        self.max_animados = [0.0] * self.num_categorias
        self.ocultos = set()
        self.colores = {}
        self.set_hexpand(True)
        self.set_content_height(320)
        self.set_margin_top(16)
        self.set_margin_bottom(16)
        self.set_margin_start(16)
        self.set_margin_end(16)
        self.highlight_sc = None
        self.focus_animado = {}
        self.anim_tick = 0
        self._hover_x = 0.0
        self._hover_y = 0.0
        self._pulse_active = False

        ev_motion = Gtk.EventControllerMotion()
        ev_motion.connect("motion", self.on_mouse_move)
        ev_motion.connect("leave", self.on_mouse_leave)
        self.add_controller(ev_motion)

        GLib.timeout_add(16, self.tick)

    def registrar_scheduler(self, name):
        if name not in self.datos_raw:
            self.datos_raw[name] = [0.0] * self.num_categorias
            self.valores_animados[name] = [0.0] * self.num_categorias
            self.focus_animado[name] = 0.5
            low = name.lower()
            if low not in self.colores:
                self.colores[low] = generar_color_hash(name)

    def actualizar_dato(self, sched, cat_idx, val_raw):
        if sched in self.datos_raw:
            self.datos_raw[sched][cat_idx] = val_raw
            if val_raw > self.max_por_categoria[cat_idx]:
                self.max_por_categoria[cat_idx] = val_raw

    def tick(self):
        if not self.datos_raw:
            return True

        cambio = False

        if self._pulse_active:
            cambio = True

        for s, puntos in self.datos_raw.items():
            for i in range(self.num_categorias):
                if self.max_por_categoria[i] <= 0:
                    objetivo = 0.0
                else:
                    objetivo = (puntos[i] / self.max_por_categoria[i]) * 100
                diff = objetivo - self.valores_animados[s][i]
                if abs(diff) > 0.5:
                    self.valores_animados[s][i] += diff * 0.08
                    cambio = True
                else:
                    self.valores_animados[s][i] = objetivo

        for i in range(self.num_categorias):
            diff_max = self.max_por_categoria[i] - self.max_animados[i]
            if abs(diff_max) > 0.5:
                self.max_animados[i] += diff_max * 0.05
                cambio = True
            else:
                self.max_animados[i] = self.max_por_categoria[i]

        for s in self.datos_raw:
            if self.highlight_sc is None:
                objetivo_focus = 0.5
            elif s == self.highlight_sc:
                objetivo_focus = 1.0
            else:
                objetivo_focus = 0.0
            diff_f = objetivo_focus - self.focus_animado.get(s, 0.5)
            if abs(diff_f) > 0.01:
                self.focus_animado[s] = self.focus_animado.get(s, 0.5) + diff_f * 0.15
                cambio = True
            else:
                self.focus_animado[s] = objetivo_focus

        if cambio:
            self.anim_tick += 1
            self.queue_draw()
        return True

    def _get_fg(self):
        accent = obtener_color_tema("accent_color")
        if Adw.StyleManager.get_default().get_dark():
            return accent or (1.0, 1.0, 1.0)
        else:
            return accent or (0.1, 0.1, 0.1)

    def iniciar_pulso(self):
        self._pulse_active = True
        self._pulse_t = 0.0

    def detener_pulso(self):
        self._pulse_active = False
        self._pulse_t = 0.0
        self.queue_draw()
        return 0.0, 0.0, 0.0

    def on_mouse_move(self, controller, x, y):
        self._hover_x = x
        self._hover_y = y
        cx, cy = self.get_width() / 2, self.get_height() / 2
        radio = min(cx, cy) - 50
        if radio <= 0:
            return

        mejor_dist = 30.0
        nuevo = None
        for s, pts in self.valores_animados.items():
            if s in self.ocultos:
                continue
            for i in range(self.num_categorias):
                ang = (2 * math.pi * i / self.num_categorias) - math.pi / 2
                val = pts[i] / 100.0
                px = cx + math.cos(ang) * radio * val
                py = cy + math.sin(ang) * radio * val
                dist = math.hypot(x - px, y - py)
                if dist < mejor_dist:
                    mejor_dist = dist
                    nuevo = s

        if nuevo != self.highlight_sc:
            self.highlight_sc = nuevo
            self.queue_draw()

    def on_mouse_leave(self, controller):
        if self.highlight_sc:
            self.highlight_sc = None
            self._hover_x = 0.0
            self._hover_y = 0.0
            self.queue_draw()

    def _dibujar_tooltip(self, cr, w, h, tr, tg, tb, ta):
        sc = self.highlight_sc
        if not sc:
            return
        r, g, b = self.colores.get(sc.lower(), (0.6, 0.6, 0.6))
        texto = f"  {sc}  "
        cr.set_font_size(10)
        ext = cr.text_extents(texto)
        tw = ext.width + 16
        th = ext.height + 10
        tx = self._hover_x + 14
        ty = self._hover_y - th - 6
        if tx + tw > w - 5:
            tx = self._hover_x - tw - 14
        if ty < 5:
            ty = self._hover_y + 14
        rr = 5
        cr.set_source_rgba(0, 0, 0, 0.78 * ta)
        cr.new_sub_path()
        cr.arc(tx + tw - rr, ty + rr, rr, -math.pi/2, 0)
        cr.arc(tx + tw - rr, ty + th - rr, rr, 0, math.pi/2)
        cr.arc(tx + rr, ty + th - rr, rr, math.pi/2, math.pi)
        cr.arc(tx + rr, ty + rr, rr, math.pi, 3*math.pi/2)
        cr.close_path()
        cr.fill()
        cr.set_source_rgba(r, g, b, 0.3 * ta)
        cr.new_sub_path()
        cr.arc(tx + tw - rr, ty + rr, rr, -math.pi/2, 0)
        cr.arc(tx + tw - rr, ty + th - rr, rr, 0, math.pi/2)
        cr.arc(tx + rr, ty + th - rr, rr, math.pi/2, math.pi)
        cr.arc(tx + rr, ty + rr, rr, math.pi, 3*math.pi/2)
        cr.close_path()
        cr.fill()
        text_x = tx + (tw - ext.width) / 2 - ext.x_bearing
        text_y = ty + (th - ext.height) / 2 - ext.y_bearing
        cr.set_source_rgba(1, 1, 1, 0.95 * ta)
        cr.move_to(text_x, text_y)
        cr.show_text(texto)

    def dibujar(self, area, cr, width, height, user_data=None):
        if not _HAS_CAIRO:
            return
        tr, tg, tb = self._get_fg()
        cx, cy = width / 2, height / 2
        radio = min(cx, cy) - 50
        if radio <= 0:
            return
        n = self.num_categorias

        for nivel in [25, 50, 75, 100]:
            cr.set_line_width(0.5)
            cr.set_source_rgba(tr, tg, tb, 0.06 if nivel < 100 else 0.1)
            for i in range(n + 1):
                ang = (2 * math.pi * (i % n) / n) - math.pi / 2
                r = radio * (nivel / 100.0)
                px = cx + math.cos(ang) * r
                py = cy + math.sin(ang) * r
                if i == 0:
                    cr.move_to(px, py)
                else:
                    cr.line_to(px, py)
            cr.close_path()
            cr.stroke()

        cr.set_font_size(11)
        for i in range(n):
            ang = (2 * math.pi * i / n) - math.pi / 2
            px = cx + math.cos(ang) * radio
            py = cy + math.sin(ang) * radio

            cr.set_source_rgba(tr, tg, tb, 0.12)
            cr.set_line_width(0.8)
            cr.move_to(cx, cy)
            cr.line_to(px, py)
            cr.stroke()

            lineas = self.categorias[i].upper().split("\n")
            line_h = cr.text_extents("Ay").height + 2
            exts = [cr.text_extents(l) for l in lineas]
            max_w = max(e.width for e in exts)
            lbl_r = radio + 25
            total_h = line_h * len(lineas)
            base_x = cx + math.cos(ang) * lbl_r - max_w / 2
            base_y = cy + math.sin(ang) * lbl_r - total_h / 2
            cr.set_source_rgba(tr, tg, tb, 0.6)
            for li, linea in enumerate(lineas):
                cr.move_to(base_x + (max_w - exts[li].width) / 2, base_y + li * line_h + line_h)
                cr.show_text(linea)

            val_max = self.max_animados[i] if i < len(self.max_animados) else 0
            val_str = f"{val_max:,.0f}"
            ext_v = cr.text_extents(val_str)
            vx = cx + math.cos(ang) * (lbl_r + 18) - ext_v.width / 2
            vy = cy + math.sin(ang) * (lbl_r + 18) + ext_v.height / 2
            cr.set_source_rgba(tr, tg, tb, 0.35)
            cr.move_to(vx, vy)
            cr.show_text(val_str)

        for s, pts in self.valores_animados.items():
            if s in self.ocultos:
                continue
            r, g, b = self.colores.get(s.lower(), (0.6, 0.6, 0.6))

            f = self.focus_animado.get(s, 0.5)
            if f <= 0.5:
                op_fill = 0.04 + (f / 0.5) * (0.18 - 0.04)
                op_stroke = 0.15 + (f / 0.5) * (0.8 - 0.15)
                sw = 1.5
            else:
                op_fill = 0.18 + ((f - 0.5) / 0.5) * (0.4 - 0.18)
                op_stroke = 0.8 + ((f - 0.5) / 0.5) * (1.0 - 0.8)
                sw = 2.0 + ((f - 0.5) / 0.5) * 1.0

            puntos = []
            for i in range(n):
                ang = (2 * math.pi * i / n) - math.pi / 2
                val = max(0.02, min(0.98, pts[i] / 100.0))
                px = cx + math.cos(ang) * radio * val
                py = cy + math.sin(ang) * radio * val
                puntos.append((px, py))

            cr.move_to(*puntos[0])
            for px, py in puntos[1:]:
                cr.line_to(px, py)
            cr.close_path()

            grad = cairo.RadialGradient(cx, cy, 0, cx, cy, radio)
            grad.add_color_stop_rgba(0, r, g, b, op_fill * 0.3)
            grad.add_color_stop_rgba(1, r, g, b, op_fill)
            cr.set_source(grad)
            cr.fill()

            cr.move_to(*puntos[0])
            for px, py in puntos[1:]:
                cr.line_to(px, py)
            cr.close_path()
            cr.set_source_rgba(r, g, b, op_stroke)
            cr.set_line_width(sw)
            cr.set_line_join(cairo.LINE_JOIN_ROUND)
            cr.stroke()

            for px, py in puntos:
                cr.set_source_rgba(r, g, b, op_fill * 1.5)
                cr.arc(px, py, 5, 0, 2 * math.pi)
                cr.fill()
                cr.set_source_rgba(r, g, b, op_stroke)
                cr.arc(px, py, 2.5, 0, 2 * math.pi)
                cr.fill()

        cr.set_source_rgba(tr, tg, tb, 0.15)
        cr.arc(cx, cy, 3, 0, 2 * math.pi)
        cr.fill()

        if self._pulse_active:
            phase = (self.anim_tick * 0.008) % 1.0
            r = radio * phase

            # glow trail behind the ring
            if phase > 0.02:
                grad = cairo.RadialGradient(cx, cy, 0, cx, cy, r)
                grad.add_color_stop_rgba(0, tr, tg, tb, 0.05 * (1.0 - phase))
                grad.add_color_stop_rgba(1, tr, tg, tb, 0.0)
                cr.save()
                for j in range(n + 1):
                    ang = (2 * math.pi * (j % n) / n) - math.pi / 2
                    px = cx + math.cos(ang) * radio
                    py = cy + math.sin(ang) * radio
                    if j == 0:
                        cr.move_to(px, py)
                    else:
                        cr.line_to(px, py)
                cr.close_path()
                cr.clip()
                cr.set_source(grad)
                cr.paint()
                cr.restore()

            # ping ring at wave front
            if phase > 0.01:
                ring_alpha = 0.35 * (1.0 - phase * 0.8)
                cr.set_line_width(1.5)
                cr.set_source_rgba(tr, tg, tb, ring_alpha)
                for j in range(n + 1):
                    ang = (2 * math.pi * (j % n) / n) - math.pi / 2
                    px = cx + math.cos(ang) * r
                    py = cy + math.sin(ang) * r
                    if j == 0:
                        cr.move_to(px, py)
                    else:
                        cr.line_to(px, py)
                cr.close_path()
                cr.stroke()

        if self.highlight_sc:
            self._dibujar_tooltip(cr, width, height, tr, tg, tb, 1.0)
