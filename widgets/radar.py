"""
Gráfico radar para visualización de capacidades de CPU.
"""

import math

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib

try:
    import cairo as _cairo_mod
    _HAS_CAIRO = True
except ImportError:
    _HAS_CAIRO = False

from utils.helpers import obtener_color_tema

_EJES = [
    ("CPUs",   32,    ""),
    ("Hilos",  4,     ""),
    ("GHz",    6000,  "GHz"),
    ("L3",     64,    "M"),
    ("L2",     16,    "M"),
    ("Cores",  16,    ""),
]


class RadarChart(Gtk.DrawingArea):
    SIZE = 340
    _PULSE_SPEED  = 0.010
    _PULSE_RINGS  = 3

    def __init__(self):
        super().__init__()
        self.set_content_height(320)
        self.set_hexpand(True)
        self.set_margin_top(8)
        self.set_margin_bottom(8)
        self._values: list[float] = []
        self._labels: list[str]   = []
        self._axis_names: list[str] = []
        self._raw_values: list    = []
        self._progress  = 0.0
        self._pulse_t   = 0.0
        self._anim_id   = None
        self._pulse_id  = None
        self._hover_idx = -1
        self._mode      = "radar"
        self._num_axes  = 0
        self._trans_alpha = 1.0
        self.set_draw_func(self._draw)

        ev_motion = Gtk.EventControllerMotion()
        ev_motion.connect("motion", self._on_motion)
        ev_motion.connect("leave", self._on_leave)
        self.add_controller(ev_motion)

    def _on_motion(self, controller, x, y):
        n = self._num_axes
        if n < 3 or self._progress < 0.5 or self._mode != "radar":
            return
        w, h = self.get_width(), self.get_height()
        cx, cy = w / 2, h / 2
        R = min(w, h) / 2 - 52
        step = 2 * math.pi / n
        a0 = -math.pi / 2
        closest = -1
        closest_dist = 30.0
        for i in range(n):
            a = a0 + i * step
            px = cx + R * math.cos(a)
            py = cy + R * math.sin(a)
            dist = math.hypot(x - px, y - py)
            if dist < closest_dist:
                closest_dist = dist
                closest = i
        if closest != self._hover_idx:
            self._hover_idx = closest
            self.queue_draw()

    def _on_leave(self, controller):
        if self._hover_idx != -1:
            self._hover_idx = -1
            self.queue_draw()

    def _get_theme_colors(self):
        is_dark = Adw.StyleManager.get_default().get_dark()
        accent = obtener_color_tema("accent_color")
        if is_dark:
            return {
                "accent": accent or (0.30, 0.60, 0.95),
                "grid": (0.28, 0.32, 0.40),
                "bg": (0.11, 0.12, 0.15),
                "text": (0.95, 0.96, 0.98),
                "subtext": (0.55, 0.60, 0.70)
            }
        else:
            return {
                "accent": accent or (0.15, 0.45, 0.80),
                "grid": (0.75, 0.78, 0.83),
                "bg": (0.95, 0.96, 0.97),
                "text": (0.10, 0.12, 0.15),
                "subtext": (0.45, 0.50, 0.55)
            }

    def set_data(self, values: list[float], labels: list[str], raw_values: list = None, axis_names: list[str] = None):
        if axis_names is None:
            axis_names = [eje[0] for eje in _EJES]

        paired = []
        for i in range(len(values)):
            v = values[i]
            l = labels[i] if i < len(labels) else "?"
            raw = raw_values[i] if (raw_values and i < len(raw_values)) else None
            name = axis_names[i] if i < len(axis_names) else f"Eje {i+1}"
            paired.append((v, l, raw, name))

        valid = [p for p in paired if p[0] > 0]
        new_vals = [p[0] for p in valid]
        new_labels = [p[1] for p in valid]
        new_raw = [p[2] for p in valid] if raw_values else []
        new_names = [p[3] for p in valid]

        new_mode = "radar" if len(new_vals) >= 3 else "bars"
        axes_changed = (len(new_vals) != self._num_axes) or (new_mode != self._mode)

        self._values = new_vals
        self._labels = new_labels
        self._raw_values = new_raw
        self._axis_names = new_names
        self._num_axes = len(new_vals)
        self._mode = new_mode

        if axes_changed:
            self._progress = 0.0
            self._trans_alpha = 0.0
        else:
            self._progress = max(self._progress, 0.0)

        self._pulse_t = 0.0
        if self._pulse_id:
            GLib.source_remove(self._pulse_id)
            self._pulse_id = None
        if self._anim_id:
            GLib.source_remove(self._anim_id)
        self._anim_id = GLib.timeout_add(14, self._tick_entry)

    def _tick_entry(self) -> bool:
        self._progress = min(1.0, self._progress + 0.030)
        self._trans_alpha = min(1.0, self._trans_alpha + 0.05)
        self.queue_draw()
        if self._progress >= 1.0:
            self._anim_id = None
            self._pulse_id = GLib.timeout_add(33, self._tick_pulse)
            return False
        return True

    def _tick_pulse(self) -> bool:
        if self.get_visible():
            self._pulse_t = (self._pulse_t + self._PULSE_SPEED) % 1.0
            self.queue_draw()
        return True

    @staticmethod
    def _ease(t: float) -> float:
        return 1 - (1 - t) ** 3

    def _draw(self, _area, cr, w, h):
        colors = self._get_theme_colors()
        n = self._num_axes
        prog = self._ease(self._progress)
        ta = self._trans_alpha

        cr.set_antialias(1)

        if n == 0:
            cr.set_source_rgba(*colors["subtext"], 0.5 * ta)
            cr.set_font_size(12)
            ext = cr.text_extents("Sin datos disponibles")
            cr.move_to(w / 2 - ext.width / 2, h / 2 + ext.height / 3)
            cr.show_text("Sin datos disponibles")
            return

        if self._mode == "bars":
            self._draw_bars(cr, w, h, colors, prog, ta)
        else:
            self._draw_radar(cr, w, h, colors, n, prog, ta)

    def _draw_bars(self, cr, w, h, colors, prog, ta):
        n = self._num_axes
        margin_l, margin_r, margin_t, margin_b = 50, 20, 30, 55
        cw = w - margin_l - margin_r
        ch = h - margin_t - margin_b
        if cw <= 0 or ch <= 0: return

        bar_w = max(20, min(50, cw // (n * 2)))
        total_bars_w = n * bar_w + (n - 1) * (bar_w // 2)
        start_x = margin_l + (cw - total_bars_w) / 2
        max_val = max(self._values) if self._values else 1.0
        if max_val <= 0: max_val = 1.0

        cr.set_line_width(0.5)
        for i in range(5):
            y = margin_t + ch * (1 - i / 4)
            cr.set_source_rgba(*colors["grid"], 0.15 * ta)
            cr.move_to(margin_l, y)
            cr.line_to(margin_l + cw, y)
            cr.stroke()

        hi = self._hover_idx
        for i in range(n):
            val = self._values[i] if i < len(self._values) else 0
            bar_h = ch * (val / max_val) * prog
            x = start_x + i * (bar_w + bar_w // 2)
            y = margin_t + ch - bar_h
            is_hl = (i == hi)
            bar_alpha = 1.0 if is_hl else 0.85

            grad = _cairo_mod.LinearGradient(x, y, x, margin_t + ch)
            r, g, b = colors["accent"]
            grad.add_color_stop_rgba(0, r, g, b, 0.9 * bar_alpha * ta)
            grad.add_color_stop_rgba(1, r, g, b, 0.3 * bar_alpha * ta)
            cr.set_source(grad)
            cr.new_sub_path()
            bar_rr = min(4, bar_w / 4)
            cr.arc(x + bar_w - bar_rr, y + bar_rr, bar_rr, -math.pi/2, 0)
            cr.arc(x + bar_w - bar_rr, margin_t + ch - bar_rr, bar_rr, 0, math.pi/2)
            cr.arc(x + bar_rr, margin_t + ch - bar_rr, bar_rr, math.pi/2, math.pi)
            cr.arc(x + bar_rr, y + bar_rr, bar_rr, math.pi, 3*math.pi/2)
            cr.close_path()
            cr.fill()

            if is_hl:
                cr.set_source_rgba(*colors["accent"], 0.15 * ta)
                cr.rectangle(x - 3, y - 3, bar_w + 6, bar_h + 6)
                cr.fill()

            label = self._axis_names[i] if i < len(self._axis_names) else "?"
            cr.select_font_face("Sans", 0, 0)
            cr.set_font_size(9.0)
            ext = cr.text_extents(label)
            cr.set_source_rgba(*colors["accent"] if is_hl else colors["subtext"], 0.9 * ta)
            cr.move_to(x + bar_w / 2 - ext.width / 2, margin_t + ch + 16)
            cr.show_text(label)

            val_str = self._labels[i] if i < len(self._labels) else ""
            cr.set_font_size(9.0)
            ext_v = cr.text_extents(val_str)
            cr.set_source_rgba(*colors["text"], 0.8 * ta * prog)
            cr.move_to(x + bar_w / 2 - ext_v.width / 2, y - 6)
            cr.show_text(val_str)

    def _draw_radar(self, cr, w, h, colors, n, prog, ta):
        cx, cy = w / 2, h / 2
        R = min(w, h) / 2 - 52
        step = 2 * math.pi / n
        a0 = -math.pi / 2

        for ring in range(1, 5):
            r_ring = R * ring / 4
            cr.set_source_rgba(*colors["grid"], 0.25 * ta if ring < 4 else 0.45 * ta)
            cr.set_line_width(0.8 if ring < 4 else 1.4)
            for i in range(n):
                a = a0 + i * step
                x = cx + r_ring * math.cos(a)
                y = cy + r_ring * math.sin(a)
                cr.move_to(x, y) if i == 0 else cr.line_to(x, y)
            cr.close_path()
            cr.stroke()

        cr.set_line_width(0.7)
        for ring in range(1, 4):
            r_mark = R * ring / 4
            cr.set_source_rgba(*colors["grid"], 0.35 * ta)
            for i in range(n):
                a = a0 + i * step
                px = cx + r_mark * math.cos(a)
                py = cy + r_mark * math.sin(a)
                nx, ny = -math.sin(a) * 4.0, math.cos(a) * 4.0
                cr.move_to(px - nx, py - ny)
                cr.line_to(px + nx, py + ny)
                cr.stroke()

        cr.set_line_width(0.9)
        cr.set_source_rgba(*colors["grid"], 0.40 * ta)
        for i in range(n):
            a = a0 + i * step
            cr.move_to(cx, cy)
            cr.line_to(cx + R * math.cos(a), cy + R * math.sin(a))
            cr.stroke()

        if self._progress >= 1.0:
            for ring_idx in range(self._PULSE_RINGS):
                phase = (self._pulse_t + ring_idx / self._PULSE_RINGS) % 1.0
                ring_r = R * (0.05 + 0.95 * phase)
                alpha = (1.0 - phase) * 0.25
                if alpha < 0.01: continue
                cr.set_line_width(1.4)
                cr.set_source_rgba(*colors["accent"], alpha * ta)
                for i in range(n):
                    a = a0 + i * step
                    x = cx + ring_r * math.cos(a)
                    y = cy + ring_r * math.sin(a)
                    cr.move_to(x, y) if i == 0 else cr.line_to(x, y)
                cr.close_path()
                cr.stroke()

        pts = []
        for i in range(n):
            a = a0 + i * step
            val = self._values[i] if i < len(self._values) else 0
            r = R * val * prog
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
        if not pts: return

        def _build_path(cr_ctx):
            cr_ctx.move_to(*pts[0])
            for px, py in pts[1:]:
                cr_ctx.line_to(px, py)
            cr_ctx.close_path()

        for gw, ga in [(16, 0.06), (9, 0.10), (4, 0.16)]:
            cr.new_path()
            _build_path(cr)
            cr.set_line_width(gw)
            cr.set_source_rgba(*colors["accent"], ga * prog * ta)
            cr.stroke()

        cr.new_path()
        _build_path(cr)
        cr.set_source_rgba(*colors["accent"], 0.20 * prog * ta)
        cr.fill_preserve()

        cr.set_line_width(2.0)
        cr.set_source_rgba(*colors["accent"], 0.95 * prog * ta)
        cr.stroke()

        hi = self._hover_idx
        for i, (px, py) in enumerate(pts):
            is_hl = (i == hi)
            halo_r = 12 if is_hl else 9
            dot_r = 5 if is_hl else 3.5
            halo_a = 0.40 if is_hl else 0.25
            cr.set_source_rgba(*colors["accent"], halo_a * prog * ta)
            cr.arc(px, py, halo_r, 0, 2 * math.pi)
            cr.fill()
            cr.set_source_rgba(*colors["accent"], 1.0 * prog * ta)
            cr.arc(px, py, dot_r, 0, 2 * math.pi)
            cr.fill()

        OFFSET = 30
        val_alpha = max(0.0, (prog - 0.5) / 0.5)
        LINE_H = 14

        for i in range(n):
            a = a0 + i * step
            lx = cx + (R + OFFSET) * math.cos(a)
            ly = cy + (R + OFFSET) * math.sin(a)
            name = self._axis_names[i] if i < len(self._axis_names) else "?"
            val = self._labels[i] if i < len(self._labels) else "?"
            is_hl = (i == hi)

            cr.select_font_face("Sans", 0, 0)
            cr.set_font_size(9.0)
            ext_n = cr.text_extents(name)
            total_h = ext_n.height + LINE_H
            base_y = ly - total_h / 2

            name_alpha = 1.0 if is_hl else 0.85
            cr.set_source_rgba(*colors["accent"] if is_hl else colors["subtext"], name_alpha * ta)
            cr.move_to(lx - ext_n.width / 2, base_y + ext_n.height)
            cr.show_text(name)

            cr.select_font_face("Sans", 0, 1)
            cr.set_font_size(11.5)
            ext_v = cr.text_extents(val)
            cr.set_source_rgba(*colors["accent"] if is_hl else colors["text"], val_alpha * ta)
            cr.move_to(lx - ext_v.width / 2, base_y + LINE_H + ext_v.height)
            cr.show_text(val)

            if is_hl and i < len(self._values) and self._values[i] > 0:
                pct = self._values[i] * 100
                desc = f"{name}: {val} ({pct:.0f}%)"
                cr.set_font_size(9.0)
                ext_t = cr.text_extents(desc)
                tx = min(max(lx - ext_t.width / 2, 4), w - ext_t.width - 4)
                ty = max(base_y - ext_t.height - 8, 4)
                cr.set_source_rgba(0, 0, 0, 0.80)
                cr.new_sub_path()
                cr.arc(tx + ext_t.width / 2 + 4, ty + ext_t.height / 2 + 2, 4, 0, 2 * math.pi)
                cr.rectangle(tx - 2, ty - 2, ext_t.width + 12, ext_t.height + 8)
                cr.fill()
                cr.set_source_rgba(1, 1, 1, 0.95)
                cr.move_to(tx + 4, ty + ext_t.height)
                cr.show_text(desc)
