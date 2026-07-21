"""
Pestaña de Diagnóstico: información detallada de la CPU y topología.
Muestra un panel en tiempo real de uso de CPU, RAM, temperatura y métricas de planificación,
además de un gráfico de radar hexagonal de capacidades y detalles del hardware agrupados.
"""

import json
import math
import subprocess
import time

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gdk

try:
    import cairo as _cairo_mod
    _HAS_CAIRO = True
except ImportError:
    _HAS_CAIRO = False

from utils.helpers import obtener_color_tema
from widgets.circular_meter import CircularMeter, _color_para_temperatura


# ── Helpers de Formateo y Parsing ─────────────────────────────────────────────

def parse_numeric(s: str) -> float:
    """Extrae el primer número de una cadena, tolerando diferentes locales."""
    if not s:
        return 0.0
    try:
        # Extraemos la primera palabra y quitamos caracteres no numéricos excepto los decimales/separadores
        token = s.split()[0]
        cleaned = ""
        for char in token:
            if char.isdigit() or char in '.,-':
                cleaned += char
        
        # Si hay puntos y comas, asumimos formato internacional o español
        if '.' in cleaned and ',' in cleaned:
            dot_idx = cleaned.rfind('.')
            comma_idx = cleaned.rfind(',')
            if dot_idx > comma_idx:
                cleaned = cleaned.replace(',', '')
            else:
                cleaned = cleaned.replace('.', '').replace(',', '.')
        elif ',' in cleaned:
            parts = cleaned.split(',')
            if len(parts) == 2:
                cleaned = parts[0] + '.' + parts[1]
            else:
                cleaned = cleaned.replace(',', '')
        return float(cleaned)
    except Exception:
        return 0.0


def parse_cache_to_mib(s: str) -> float:
    """Convierte cadenas de caché (ej: '16 MiB (1 instancia)' o '256 KiB') a float en MiB."""
    if not s:
        return 0.0
    s_clean = s.split('(')[0].strip()
    parts = s_clean.split()
    if not parts:
        return 0.0
    try:
        val = parse_numeric(parts[0])
        if len(parts) > 1:
            unit = parts[1].upper()
            if "KIB" in unit or "KB" in unit:
                val /= 1024.0
            elif "GIB" in unit or "GB" in unit:
                val *= 1024.0
        return val
    except Exception:
        return 0.0


# ── Lectura de datos del Sistema (/proc) ──────────────────────────────────────

def obtener_uso_cpu_general():
    try:
        with open("/proc/stat", "r") as f:
            line = f.readline()
        if line.startswith("cpu "):
            parts = line.split()
            idle = float(parts[4]) + float(parts[5])  # idle + iowait
            non_idle = sum(float(x) for x in [parts[1], parts[2], parts[3], parts[6], parts[7], parts[8]])
            return idle + non_idle, idle
    except Exception:
        pass
    return 0.0, 0.0


def obtener_uso_cores():
    cores = {}
    try:
        with open("/proc/stat", "r") as f:
            for line in f:
                if line.startswith("cpu") and line[3].isdigit():
                    parts = line.split()
                    name = parts[0]
                    idle = float(parts[4]) + float(parts[5])
                    non_idle = sum(float(x) for x in [parts[1], parts[2], parts[3], parts[6], parts[7], parts[8]])
                    cores[name] = (idle + non_idle, idle)
    except Exception:
        pass
    return cores


def obtener_uso_memoria():
    try:
        with open("/proc/meminfo", "r") as f:
            lines = f.readlines()
        mem = {}
        for line in lines:
            parts = line.split(":")
            if len(parts) == 2:
                mem[parts[0].strip()] = int(parts[1].replace("kB", "").strip())
        
        total = mem.get("MemTotal", 0)
        available = mem.get("MemAvailable", 0)
        if total > 0:
            used = total - available
            fraction = used / total
            return total / 1024 / 1024, used / 1024 / 1024, fraction
    except Exception:
        pass
    return 0.0, 0.0, 0.0


def obtener_loadavg():
    try:
        with open("/proc/loadavg", "r") as f:
            parts = f.read().split()
        return parts[0], parts[1], parts[2]
    except Exception:
        return "0.00", "0.00", "0.00"


def obtener_planif_stats():
    ctxt = 0
    running = 0
    blocked = 0
    try:
        with open("/proc/stat", "r") as f:
            for line in f:
                if line.startswith("ctxt"):
                    ctxt = int(line.split()[1])
                elif line.startswith("procs_running"):
                    running = int(line.split()[1])
                elif line.startswith("procs_blocked"):
                    blocked = int(line.split()[1])
    except Exception:
        pass
    return ctxt, running, blocked


# ── Ejecución de Comandos Externos ───────────────────────────────────────────

def _ejecutar_lscpu_json():
    try:
        res = subprocess.run(["lscpu", "-J"], capture_output=True, text=True, timeout=2)
        if res.returncode == 0 and res.stdout:
            return json.loads(res.stdout)
    except Exception:
        return None
    return None


def _leer_proc_cpuinfo():
    info = []
    try:
        with open('/proc/cpuinfo', 'r') as f:
            contenido = f.read()
        for b in contenido.strip().split('\n\n'):
            props = {}
            for l in b.splitlines():
                if ':' in l:
                    k, v = l.split(':', 1)
                    props[k.strip()] = v.strip()
            if props:
                info.append(props)
    except Exception:
        pass
    return info


def _clear_listbox(lb: Gtk.ListBox):
    while True:
        row = lb.get_row_at_index(0)
        if row is None:
            break
        lb.remove(row)


def _make_finder(flat_map: dict):
    def find(*keys):
        for key in keys:
            k = key.lower()
            if k in flat_map:
                return flat_map[k]
        for key in keys:
            sk = key.lower()
            for lk, v in flat_map.items():
                if sk in lk:
                    return v
        return None
    return find


# ── Configuración de Ejes de Radar ─────────────────────────────────────────────

_EJES = [
    ("CPUs",   32,    ""),
    ("Hilos",  4,     ""),
    ("GHz",    6000,  "GHz"),
    ("L3",     64,    "M"),
    ("L2",     16,    "M"),
    ("Cores",  16,    ""),
]


# ── Radar Chart (Cairo) ────────────────────────────────────────────────────────

class RadarChart(Gtk.DrawingArea):
    SIZE = 340
    _PULSE_SPEED  = 0.010
    _PULSE_RINGS  = 3

    def __init__(self):
        super().__init__()
        self.set_size_request(self.SIZE, self.SIZE)
        self.set_hexpand(True)
        self.set_halign(Gtk.Align.CENTER)
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

        # ── Fondo Redondeado de la Tarjeta ───────────────────────────────────
        rr, bx, by = 16, 4, 4
        bw, bh = w - 8, h - 8
        cr.new_sub_path()
        cr.arc(bx + bw - rr, by + rr,      rr, -math.pi/2, 0)
        cr.arc(bx + bw - rr, by + bh - rr, rr,  0,          math.pi/2)
        cr.arc(bx + rr,      by + bh - rr, rr,  math.pi/2,  math.pi)
        cr.arc(bx + rr,      by + rr,      rr,  math.pi,    3*math.pi/2)
        cr.close_path()
        cr.set_source_rgba(*colors["bg"], 0.75 * ta)
        cr.fill_preserve()
        cr.set_source_rgba(*colors["grid"], 0.25 * ta)
        cr.set_line_width(1)
        cr.stroke()

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

        if cw <= 0 or ch <= 0:
            return

        bar_w = max(20, min(50, cw // (n * 2)))
        total_bars_w = n * bar_w + (n - 1) * (bar_w // 2)
        start_x = margin_l + (cw - total_bars_w) / 2

        max_val = max(self._values) if self._values else 1.0
        if max_val <= 0:
            max_val = 1.0

        # Grid horizontal
        cr.set_line_width(0.5)
        for i in range(5):
            y = margin_t + ch * (1 - i / 4)
            cr.set_source_rgba(*colors["grid"], 0.15 * ta)
            cr.move_to(margin_l, y)
            cr.line_to(margin_l + cw, y)
            cr.stroke()

        # Barras
        hi = self._hover_idx
        for i in range(n):
            val = self._values[i] if i < len(self._values) else 0
            bar_h = ch * (val / max_val) * prog
            x = start_x + i * (bar_w + bar_w // 2)
            y = margin_t + ch - bar_h

            is_hl = (i == hi)
            bar_alpha = 1.0 if is_hl else 0.85

            # Gradiente de barra
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

            # Glow en hover
            if is_hl:
                cr.set_source_rgba(*colors["accent"], 0.15 * ta)
                cr.rectangle(x - 3, y - 3, bar_w + 6, bar_h + 6)
                cr.fill()

            # Label nombre (abajo)
            label = self._axis_names[i] if i < len(self._axis_names) else "?"
            cr.select_font_face("Sans", 0, 0)
            cr.set_font_size(9.0)
            ext = cr.text_extents(label)
            cr.set_source_rgba(*colors["accent"] if is_hl else colors["subtext"], 0.9 * ta)
            cr.move_to(x + bar_w / 2 - ext.width / 2, margin_t + ch + 16)
            cr.show_text(label)

            # Valor (arriba de la barra)
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

        # ── Cuadrícula ──────────────────────────────────────────────────────
        rings = 4
        for ring in range(1, rings + 1):
            r_ring = R * ring / rings
            cr.set_source_rgba(*colors["grid"], 0.25 * ta if ring < rings else 0.45 * ta)
            cr.set_line_width(0.8 if ring < rings else 1.4)
            for i in range(n):
                a = a0 + i * step
                x = cx + r_ring * math.cos(a)
                y = cy + r_ring * math.sin(a)
                cr.move_to(x, y) if i == 0 else cr.line_to(x, y)
            cr.close_path()
            cr.stroke()

        # Marcas de escala
        cr.set_line_width(0.7)
        for ring in range(1, rings):
            r_mark = R * ring / rings
            cr.set_source_rgba(*colors["grid"], 0.35 * ta)
            for i in range(n):
                a = a0 + i * step
                px = cx + r_mark * math.cos(a)
                py = cy + r_mark * math.sin(a)
                nx, ny = -math.sin(a) * 4.0, math.cos(a) * 4.0
                cr.move_to(px - nx, py - ny)
                cr.line_to(px + nx, py + ny)
                cr.stroke()

        # Ejes radiales
        cr.set_line_width(0.9)
        cr.set_source_rgba(*colors["grid"], 0.40 * ta)
        for i in range(n):
            a = a0 + i * step
            cr.move_to(cx, cy)
            cr.line_to(cx + R * math.cos(a), cy + R * math.sin(a))
            cr.stroke()

        # ── Animación Pulse ──────────────────────────────────────────────────
        if self._progress >= 1.0:
            for ring_idx in range(self._PULSE_RINGS):
                phase = (self._pulse_t + ring_idx / self._PULSE_RINGS) % 1.0
                ring_r = R * (0.05 + 0.95 * phase)
                alpha = (1.0 - phase) * 0.25
                if alpha < 0.01:
                    continue
                cr.set_line_width(1.4)
                cr.set_source_rgba(*colors["accent"], alpha * ta)
                for i in range(n):
                    a = a0 + i * step
                    x = cx + ring_r * math.cos(a)
                    y = cy + ring_r * math.sin(a)
                    cr.move_to(x, y) if i == 0 else cr.line_to(x, y)
                cr.close_path()
                cr.stroke()

        # ── Polígono de Capacidad ────────────────────────────────────────────
        pts = []
        for i in range(n):
            a = a0 + i * step
            val = self._values[i] if i < len(self._values) else 0
            r = R * val * prog
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))

        if not pts:
            return

        def _build_path(cr_ctx):
            cr_ctx.move_to(*pts[0])
            for px, py in pts[1:]:
                cr_ctx.line_to(px, py)
            cr_ctx.close_path()

        # Glow
        for gw, ga in [(16, 0.06), (9, 0.10), (4, 0.16)]:
            cr.new_path()
            _build_path(cr)
            cr.set_line_width(gw)
            cr.set_source_rgba(*colors["accent"], ga * prog * ta)
            cr.stroke()

        # Relleno
        cr.new_path()
        _build_path(cr)
        cr.set_source_rgba(*colors["accent"], 0.20 * prog * ta)
        cr.fill_preserve()

        # Borde
        cr.set_line_width(2.0)
        cr.set_source_rgba(*colors["accent"], 0.95 * prog * ta)
        cr.stroke()

        # Vértices
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

        # ── Etiquetas ────────────────────────────────────────────────────────
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

            # Tooltip
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


# ── Monitoreo en Tiempo Real - Callback de Actualización ──────────────────────

def actualizar_diagnostico_tiempo_real(win, widgets):
    """Callback periódico para actualizar métricas en vivo."""
    try:
        # Detener actualización si la ventana ya no está visible o fue destruida
        if not win or not win.get_visible():
            return False
    except Exception:
        return False

    # Evitar consumo innecesario si no estamos en la pestaña Diagnóstico
    if win.split_view.get_content() != win.pag_diagnostico:
        return True

    # 1. Carga General de CPU
    t_tot, t_idl = obtener_uso_cpu_general()
    if win._prev_cpu_total is not None:
        d_tot = t_tot - win._prev_cpu_total
        d_idl = t_idl - win._prev_cpu_idle
        if d_tot > 0:
            cpu_usage = (d_tot - d_idl) / d_tot
            widgets["cpu_meter"].update(cpu_usage, f"{cpu_usage * 100:.1f}%")
    win._prev_cpu_total = t_tot
    win._prev_cpu_idle = t_idl

    # 2. Uso de Memoria
    m_tot, m_usd, m_frac = obtener_uso_memoria()
    if m_tot > 0:
        widgets["mem_meter"].update(m_frac, f"{m_usd:.1f} GB")

    # 3. Temperatura
    t_temp = win.sensor.obtener_temp()
    if t_temp > 0:
        widgets["temp_meter"].update(t_temp / 100.0, f"{t_temp:.1f} °C", color=_color_para_temperatura(t_temp))
    else:
        widgets["temp_meter"].update(0.0, "N/D")

    # 4. Planificador Activo
    sc_name, sc_mode = win.scx.obtener_estado()
    if sc_name:
        widgets["sched_val_lbl"].set_label(f"{sc_name} [{sc_mode}]")
    else:
        widgets["sched_val_lbl"].set_label("Sistema Base (Default)")

    # 5. Carga de Cores Individuales
    cores_stats = obtener_uso_cores()
    for name, (c_tot, c_idl) in cores_stats.items():
        if name in win._prev_cores:
            prev_tot, prev_idl = win._prev_cores[name]
            d_tot = c_tot - prev_tot
            d_idl = c_idl - prev_idl
            if d_tot > 0:
                core_usage = (d_tot - d_idl) / d_tot
                if name in widgets["core_bars"]:
                    widgets["core_bars"][name].set_fraction(core_usage)
                    widgets["core_labels"][name].set_label(f"{int(core_usage * 100)}%")
        win._prev_cores[name] = (c_tot, c_idl)

    # 6. Estadísticas de Planificación
    ctxt, running, blocked = obtener_planif_stats()
    now_t = time.time()
    if win._prev_ctxt is not None and win._prev_ctxt_time is not None:
        dt = now_t - win._prev_ctxt_time
        if dt > 0:
            ctxt_rate = (ctxt - win._prev_ctxt) / dt
            # Mostrar con formato local (puntos de miles)
            widgets["ctxt_rate_lbl"].set_label(f"{int(ctxt_rate):,}".replace(",", ".") + " ctxt/s")
    win._prev_ctxt = ctxt
    win._prev_ctxt_time = now_t

    widgets["ctxt_total_lbl"].set_label(f"{ctxt:,}".replace(",", "."))
    widgets["procs_running_lbl"].set_label(str(running))
    widgets["procs_blocked_lbl"].set_label(str(blocked))

    # Resaltar en rojo si hay procesos bloqueados por I/O
    widgets["procs_blocked_lbl"].remove_css_class("error-label")
    widgets["procs_blocked_lbl"].remove_css_class("success-label")
    if blocked > 0:
        widgets["procs_blocked_lbl"].add_css_class("error-label")
    else:
        widgets["procs_blocked_lbl"].add_css_class("success-label")

    # 7. Carga Media (Load Average)
    la1, la5, la15 = obtener_loadavg()
    widgets["loadavg_lbl"].set_label(f"{la1}  •  {la5}  •  {la15}")

    return True


# ── Construcción de la Interfaz Diagnóstico ────────────────────────────────────

def setup_diagnostico_ui(win):
    pref_page = Adw.PreferencesPage()

    # ── CSS Personalizado para la Rejilla ──
    css_provider = Gtk.CssProvider()
    css_provider.load_from_data("""
        .core-card {
            background-color: alpha(@window_bg_color, 0.45);
            border: 1px solid alpha(@border_color, 0.35);
            border-radius: 8px;
            padding: 6px 10px;
        }
        .core-label {
            font-size: 8.5pt;
            font-weight: bold;
            color: alpha(@window_fg_color, 0.85);
            min-width: 45px;
        }
        .core-pct-label {
            font-size: 8pt;
            color: alpha(@window_fg_color, 0.7);
            min-width: 32px;
        }
        .success-label {
            color: #26a269;
            font-weight: bold;
        }
        .warning-label {
            color: #e5a50a;
            font-weight: bold;
        }
        .error-label {
            color: #c01c28;
            font-weight: bold;
        }
    """, -1)
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(),
        css_provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )

    # ── Estado de Monitoreo en el Objeto Window ──
    win._prev_cpu_total = None
    win._prev_cpu_idle = None
    win._prev_cores = {}
    win._prev_ctxt = None
    win._prev_ctxt_time = None

    # ── 1. Medidores en Tiempo Real (Grandes, al principio) ──
    meters_group = Adw.PreferencesGroup()
    cpu_meter = CircularMeter("cpu-symbolic", "CPU", size=90)
    mem_meter = CircularMeter("drive-harddisk-symbolic", "RAM", size=90)
    temp_meter = CircularMeter("temperature-symbolic", "Temp", size=90)

    box_meters = Gtk.Box(spacing=24, halign=Gtk.Align.CENTER, margin_top=12, margin_bottom=12)
    box_meters.append(cpu_meter)
    box_meters.append(mem_meter)
    box_meters.append(temp_meter)
    meters_group.add(box_meters)
    pref_page.add(meters_group)

    # ── 2. Encabezado de la Página ──
    banner_group = Adw.PreferencesGroup()
    cpu_title_row = Adw.ActionRow()
    cpu_title_row.set_icon_name("cpu-symbolic")
    banner_group.add(cpu_title_row)
    pref_page.add(banner_group)
    win.cpu_title_row = cpu_title_row  # Guardamos referencia para poblar el modelo de CPU

    # ── 3. Monitoreo en Tiempo Real ──
    rt_group = Adw.PreferencesGroup(
        title="Monitoreo en Tiempo Real",
        description="Estado, carga del sistema e integridad térmica."
    )

    sched_row = Adw.ActionRow(title="Planificador Activo")
    sched_val_lbl = Gtk.Label(label="Cargando...", valign=Gtk.Align.CENTER)
    sched_row.add_suffix(sched_val_lbl)
    rt_group.add(sched_row)

    # Rejilla de carga por núcleo lógica
    cores_init = obtener_uso_cores()
    core_names = sorted(cores_init.keys(), key=lambda x: int(x[3:]) if x[3:].isdigit() else 0)

    flowbox = Gtk.FlowBox(
        valign=Gtk.Align.START,
        max_children_per_line=8,
        min_children_per_line=2,
        selection_mode=Gtk.SelectionMode.NONE,
        homogeneous=True,
        row_spacing=6,
        column_spacing=6
    )

    core_bars = {}
    core_labels = {}

    for name in core_names:
        card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        card.add_css_class("core-card")
        
        lbl_num = Gtk.Label(label=f"CPU {name[3:]}")
        lbl_num.add_css_class("core-label")
        lbl_num.set_halign(Gtk.Align.START)
        
        pb = Gtk.ProgressBar(valign=Gtk.Align.CENTER, hexpand=True)
        pb.add_css_class("core-progress")
        
        lbl_pct = Gtk.Label(label="0%")
        lbl_pct.add_css_class("core-pct-label")
        lbl_pct.set_halign(Gtk.Align.END)
        
        card.append(lbl_num)
        card.append(pb)
        card.append(lbl_pct)
        flowbox.append(card)

        core_bars[name] = pb
        core_labels[name] = lbl_pct

    if core_names:
        core_expander = Adw.ExpanderRow(
            title="Carga por Núcleo de Procesamiento",
            subtitle="Uso en tiempo real de cada CPU lógica"
        )
        flowbox_container = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            margin_top=10, margin_bottom=10, margin_start=12, margin_end=12
        )
        flowbox_container.append(flowbox)
        core_expander.add_row(flowbox_container)
        rt_group.add(core_expander)

    pref_page.add(rt_group)

    # ── 3. Métricas de Planificación ──
    sched_group = Adw.PreferencesGroup(
        title="Métricas de Planificación",
        description="Agilidad y comportamiento de la cola de tareas del Kernel Linux."
    )
    
    loadavg_row = Adw.ActionRow(title="Carga Media (Load Average)")
    loadavg_lbl = Gtk.Label(label="Cargando...", valign=Gtk.Align.CENTER)
    loadavg_row.add_suffix(loadavg_lbl)
    sched_group.add(loadavg_row)

    ctxt_rate_row = Adw.ActionRow(title="Cambios de Contexto (Context Switches)")
    ctxt_rate_lbl = Gtk.Label(label="Calculando...", valign=Gtk.Align.CENTER)
    ctxt_rate_row.add_suffix(ctxt_rate_lbl)
    sched_group.add(ctxt_rate_row)

    ctxt_total_row = Adw.ActionRow(title="Cambios de Contexto Totales (desde arranque)")
    ctxt_total_lbl = Gtk.Label(label="Cargando...", valign=Gtk.Align.CENTER)
    ctxt_total_row.add_suffix(ctxt_total_lbl)
    sched_group.add(ctxt_total_row)

    procs_running_row = Adw.ActionRow(title="Tareas en Ejecución Activa")
    procs_running_lbl = Gtk.Label(label="Cargando...", valign=Gtk.Align.CENTER)
    procs_running_row.add_suffix(procs_running_lbl)
    sched_group.add(procs_running_row)

    procs_blocked_row = Adw.ActionRow(
        title="Tareas Bloqueadas (Esperando I/O)",
        subtitle="Un valor elevado indica cuellos de botella en el disco o red"
    )
    procs_blocked_lbl = Gtk.Label(label="Cargando...", valign=Gtk.Align.CENTER)
    procs_blocked_row.add_suffix(procs_blocked_lbl)
    sched_group.add(procs_blocked_row)

    pref_page.add(sched_group)

    # ── Diccionario de Widgets para el Monitoreo en Vivo ──
    widgets = {
        "cpu_meter": cpu_meter,
        "mem_meter": mem_meter,
        "temp_meter": temp_meter,
        "sched_val_lbl": sched_val_lbl,
        "core_bars": core_bars,
        "core_labels": core_labels,
        "ctxt_rate_lbl": ctxt_rate_lbl,
        "ctxt_total_lbl": ctxt_total_lbl,
        "procs_running_lbl": procs_running_lbl,
        "procs_blocked_lbl": procs_blocked_lbl,
        "loadavg_lbl": loadavg_lbl,
    }

    # ── 4. Gráfico de Radar de Hardware ──
    radar_group = Adw.PreferencesGroup(
        title="Capacidades y Topología de Hardware",
        description="Visualización comparativa de las capacidades de la CPU."
    )
    pref_page.add(radar_group)

    if _HAS_CAIRO:
        radar = RadarChart()
        radar_group.add(radar)
    else:
        radar = None
        radar_group.add(Gtk.Label(
            label="Cairo no disponible — instala python3-cairo",
            css_classes=["dim-label"],
            margin_top=12, margin_bottom=12
        ))

    # ── 5. Información Avanzada Agrupada ──
    detalle_group = Adw.PreferencesGroup(
        title="Especificaciones Avanzadas de CPU",
        description="Jerarquía técnica completa agrupada de lscpu."
    )
    pref_page.add(detalle_group)

    exp_general = Adw.ExpanderRow(title="General y Arquitectura", subtitle="Modos, tamaños de dirección, orden de bytes")
    lb_general = Gtk.ListBox(css_classes=["flat"])
    lb_general.set_selection_mode(Gtk.SelectionMode.NONE)
    exp_general.add_row(lb_general)
    detalle_group.add(exp_general)

    exp_topologia = Adw.ExpanderRow(title="Topología y Distribución", subtitle="Hilos por núcleo, núcleos por socket, sockets, NUMA")
    lb_topologia = Gtk.ListBox(css_classes=["flat"])
    lb_topologia.set_selection_mode(Gtk.SelectionMode.NONE)
    exp_topologia.add_row(lb_topologia)
    detalle_group.add(exp_topologia)

    exp_caches = Adw.ExpanderRow(title="Cachés de CPU", subtitle="Jerarquía de memorias caché L1d, L1i, L2 y L3")
    lb_caches = Gtk.ListBox(css_classes=["flat"])
    lb_caches.set_selection_mode(Gtk.SelectionMode.NONE)
    exp_caches.add_row(lb_caches)
    detalle_group.add(exp_caches)

    exp_frecuencias = Adw.ExpanderRow(title="Frecuencias y Escalado", subtitle="Frecuencias mín/máx, BogoMIPS y factor de escala")
    lb_frecuencias = Gtk.ListBox(css_classes=["flat"])
    lb_frecuencias.set_selection_mode(Gtk.SelectionMode.NONE)
    exp_frecuencias.add_row(lb_frecuencias)
    detalle_group.add(exp_frecuencias)

    exp_virtualizacion = Adw.ExpanderRow(title="Virtualización e Hipervisor", subtitle="Soporte y tipo de virtualización por hardware")
    lb_virtualizacion = Gtk.ListBox(css_classes=["flat"])
    lb_virtualizacion.set_selection_mode(Gtk.SelectionMode.NONE)
    exp_virtualizacion.add_row(lb_virtualizacion)
    detalle_group.add(exp_virtualizacion)

    exp_seguridad = Adw.ExpanderRow(title="Mitigaciones de Seguridad", subtitle="Vulnerabilidades de CPU y su estado de mitigación")
    lb_seguridad = Gtk.ListBox(css_classes=["flat"])
    lb_seguridad.set_selection_mode(Gtk.SelectionMode.NONE)
    exp_seguridad.add_row(lb_seguridad)
    detalle_group.add(exp_seguridad)

    # ── Función de Carga de Datos ──
    def poblar():
        _clear_listbox(lb_general)
        _clear_listbox(lb_topologia)
        _clear_listbox(lb_caches)
        _clear_listbox(lb_frecuencias)
        _clear_listbox(lb_virtualizacion)
        _clear_listbox(lb_seguridad)

        lscpu_raw = _ejecutar_lscpu_json()
        cpuinfo   = _leer_proc_cpuinfo()
        first_cpu = cpuinfo[0] if cpuinfo else {}

        fields_list = []
        flat_map = {}

        if lscpu_raw and 'lscpu' in lscpu_raw:
            # Función recursiva para aplanar lscpu -J
            def traverse(entries):
                for entry in entries:
                    field = entry.get("field", "").rstrip(":")
                    data = entry.get("data")
                    if data is not None:
                        fields_list.append((field, data))
                        flat_map[field.lower()] = data
                    children = entry.get("children")
                    if children:
                        traverse(children)
            
            traverse(lscpu_raw['lscpu'])

            # Población clasificada en Expanders
            for field, data in fields_list:
                field_low = field.lower()
                row = Adw.ActionRow(title=field, subtitle=str(data))

                if any(k in field_low for k in ["vulnerabilidad", "vulnerability", "mitigación", "mitigation", "gather data", "ghostwrite", "speculative"]):
                    lb_seguridad.append(row)
                elif any(k in field_low for k in ["l1", "l2", "l3", "l1d", "l1i", "caché", "cache"]):
                    lb_caches.append(row)
                elif any(k in field_low for k in ["mhz", "ghz", "frecuencia", "frequency", "bogomips", "escala", "scaling", "driver", "aumento"]):
                    lb_frecuencias.append(row)
                elif any(k in field_low for k in ["virtual", "hiper", "hyper", "kvm"]):
                    lb_virtualizacion.append(row)
                elif any(k in field_low for k in ["socket", "nodo", "numa", "hilo", "núcleo", "core", "thread", "siblings", "cpu(s)"]):
                    lb_topologia.append(row)
                else:
                    lb_general.append(row)

            # Buscador inteligente sobre mapa plano
            find = _make_finder(flat_map)

            model_name = find('nombre del modelo', 'model name') or first_cpu.get('model name') or "Procesador Desconocido"
            win.cpu_title_row.set_title(f"<b>{model_name}</b>")
            win.cpu_title_row.set_subtitle("Información y Diagnóstico de la CPU")
            win.cpu_title_row.set_use_markup(True)

            # ── Extraer Valores para el Radar Chart ──
            cores_val = find('cpu(s)', 'logical cpu(s)', 'cpus')
            threads_val = find(
                'hilo(s) de procesamiento por núcleo',
                'thread(s) per core',
                'hilo(s) por núcleo',
                'hilo'
            )
            freq_val = find(
                'cpu mhz máx',
                'cpu max mhz',
                'velocidad máxima de cpu',
                'max cpu mhz'
            )
            if not freq_val:
                freq_val = find('cpu(s) factor de escala mhz', 'cpu mhz', 'mhz')
            if not freq_val and first_cpu:
                freq_val = first_cpu.get('cpu MHz')

            l3_val = find('l3', 'l3 cache', 'caché l3')
            l2_val = find('l2', 'l2 cache', 'caché l2')
            cps_val = find('núcleo(s) por', 'core(s) per socket', 'core(s)', 'núcleos por', 'nucleo')

            if not cps_val:
                sockets_val = find('«socket(s)»', 'socket(s)', 'sockets') or '1'
                if cores_val and sockets_val:
                    try:
                        cps_derived = int(parse_numeric(cores_val)) // max(1, int(parse_numeric(sockets_val)))
                        if threads_val:
                            cps_derived //= max(1, int(parse_numeric(threads_val)))
                        cps_val = str(cps_derived) if cps_derived > 0 else None
                    except Exception:
                        pass

            raw   = [cores_val, threads_val, freq_val, l3_val, l2_val, cps_val]
            units = [eje[2] for eje in _EJES]

            def to_f(val_s, idx):
                if not val_s:
                    return 0.0
                return parse_cache_to_mib(val_s) if idx in (3, 4) else parse_numeric(val_s)

            raw_nums = [to_f(v, i) for i, v in enumerate(raw)]
            defaults = [16.0, 4.0, 4000.0, 32.0, 8.0, 8.0]
            dynamic_maxes = [max(v * 1.5, d) for v, d in zip(raw_nums, defaults)]

            norms, labels = [], []
            for idx, (fv, mx) in enumerate(zip(raw_nums, dynamic_maxes)):
                norms.append(min(1.0, fv / mx) if mx else 0.0)
                unit = units[idx]
                if idx == 2 and fv:
                    labels.append(f"{fv/1000:.1f}{unit}")
                elif idx in (3, 4) and fv:
                    labels.append(f"{fv:.1f}{unit}" if fv < 10 else f"{int(fv)}{unit}")
                else:
                    labels.append(f"{int(fv)}{unit}" if fv else "?")

            if radar:
                radar.set_data(norms, labels, raw_values=raw_nums)

        else:
            # Fallback a lscpu plano
            try:
                res = subprocess.run(["lscpu"], capture_output=True, text=True, timeout=2)
                for linea in res.stdout.splitlines():
                    if ':' in linea:
                        k, v = linea.split(':', 1)
                        lb_general.append(Adw.ActionRow(title=k.strip(), subtitle=v.strip()))
            except Exception:
                lb_general.append(Adw.ActionRow(title="Error al obtener lscpu"))

        # Completar desde /proc/cpuinfo
        if cpuinfo:
            if not win.cpu_title_row.get_title():
                win.cpu_title_row.set_title(first_cpu.get('model name', 'Procesador'))
                win.cpu_title_row.set_subtitle("Información de CPU")

            for k in ['model name', 'cpu MHz', 'cache size', 'flags', 'siblings', 'cpu cores']:
                if k in first_cpu:
                    row = Adw.ActionRow(title=k.title(), subtitle=str(first_cpu[k]))
                    if 'cache' in k or 'size' in k:
                        lb_caches.append(row)
                    elif 'mhz' in k.lower():
                        lb_frecuencias.append(row)
                    elif 'cores' in k.lower() or 'siblings' in k.lower():
                        lb_topologia.append(row)
                    else:
                        lb_general.append(row)

            if radar and not lscpu_raw:
                cores_f = float(len(cpuinfo))
                mhz_f   = parse_numeric(first_cpu.get('cpu MHz', '0'))
                cores_c = parse_numeric(first_cpu.get('cpu cores', '0')) or (cores_f / 2)
                radar.set_data(
                    [min(1.0, cores_f/32), 0.0, min(1.0, mhz_f/6000), 0.0, 0.0, min(1.0, cores_c/16)],
                    [str(int(cores_f)), "?", f"{mhz_f/1000:.1f}GHz", "?", "?", str(int(cores_c))]
                )

        # Ocultar automáticamente secciones vacías
        exp_seguridad.set_visible(lb_seguridad.get_row_at_index(0) is not None)
        exp_caches.set_visible(lb_caches.get_row_at_index(0) is not None)
        exp_frecuencias.set_visible(lb_frecuencias.get_row_at_index(0) is not None)
        exp_virtualizacion.set_visible(lb_virtualizacion.get_row_at_index(0) is not None)
        exp_topologia.set_visible(lb_topologia.get_row_at_index(0) is not None)
        exp_general.set_visible(lb_general.get_row_at_index(0) is not None)

        return False

    # ── Barra de Herramientas / Cabecera ──
    header = Adw.HeaderBar()
    btn_actualizar = Gtk.Button(
        icon_name="view-refresh-symbolic",
        tooltip_text="Actualizar especificaciones",
        css_classes=["flat"]
    )
    btn_actualizar.connect('clicked', lambda _b: GLib.idle_add(poblar))
    header.pack_end(btn_actualizar)

    view = Adw.ToolbarView(content=pref_page)
    view.add_top_bar(header)

    win.pag_diagnostico = Adw.NavigationPage(title="Diagnóstico", tag="page_e")
    win.pag_diagnostico.set_child(view)

    # Lanzar la carga inicial de especificaciones
    GLib.idle_add(poblar)

    # Iniciar temporizador periódico para actualizar métricas en vivo
    GLib.timeout_add(1500, actualizar_diagnostico_tiempo_real, win, widgets)

    return win.pag_diagnostico
