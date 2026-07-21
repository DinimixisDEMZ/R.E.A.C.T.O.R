"""
Pestaña de Diagnóstico: información detallada de la CPU y topología.
Muestra un panel en tiempo real de uso de CPU, RAM, temperatura y métricas de planificación,
además de un gráfico de radar hexagonal de capacidades y detalles del hardware agrupados.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import threading
import time

try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Gtk, Adw, GLib, Gdk
    _GTK_AVAILABLE = True
except (ImportError, ValueError, AttributeError):
    _GTK_AVAILABLE = False

    class _UnavailableDrawingArea:
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("GTK4/Libadwaita no está disponible.")

    class _UnavailableGtk:
        DrawingArea = _UnavailableDrawingArea

    Gtk = _UnavailableGtk()
    Adw = GLib = Gdk = None

try:
    import cairo as _cairo_mod
    _HAS_CAIRO = True
except (ImportError, OSError):
    _HAS_CAIRO = False

if _GTK_AVAILABLE:
    from utils.helpers import obtener_color_tema
else:
    def obtener_color_tema(_name):
        return None


# ── Helpers de Formateo y Parsing ─────────────────────────────────────────────

_NUMBER_RE = re.compile(
    r"[+-]?(?:(?:\d[\d\s\u00a0.,]*\d)|\d|(?:[.,]\d+))"
    r"(?:[eE][+-]?\d+)?"
)


def _finite_nonnegative(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if not math.isfinite(number) or number < 0:
        return 0.0
    return number


def _normalize_number_token(token: str) -> str:
    token = token.replace("\u00a0", "").replace(" ", "")
    exponent = ""
    exponent_match = re.search(r"[eE][+-]?\d+$", token)
    if exponent_match:
        exponent = exponent_match.group(0)
        token = token[:exponent_match.start()]

    if "." in token and "," in token:
        decimal = "." if token.rfind(".") > token.rfind(",") else ","
        thousands = "," if decimal == "." else "."
        token = token.replace(thousands, "")
        if decimal == ",":
            token = token.replace(",", ".")
    elif token.count(",") == 1:
        token = token.replace(",", ".")
    elif token.count(",") > 1:
        groups = token.split(",")
        token = (
            "".join(groups)
            if all(len(group) == 3 for group in groups[1:])
            else "".join(groups[:-1]) + "." + groups[-1]
        )
    elif token.count(".") > 1:
        groups = token.split(".")
        token = (
            "".join(groups)
            if all(len(group) == 3 for group in groups[1:])
            else "".join(groups[:-1]) + "." + groups[-1]
        )
    return token + exponent


def parse_numeric(value) -> float:
    """Extrae un número finito, tolerando separadores locales y ausencias."""
    if isinstance(value, (int, float)):
        return _finite_nonnegative(value)
    if value is None:
        return 0.0
    match = _NUMBER_RE.search(str(value))
    if match is None:
        return 0.0
    try:
        parsed = float(_normalize_number_token(match.group(0)))
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return _finite_nonnegative(parsed)


def parse_cache_to_mib(value) -> float:
    """Convierte cadenas de caché (ej: '16 MiB (1 instancia)' o '256 KiB') a float en MiB."""
    if value is None:
        return 0.0
    text = str(value).split("(", 1)[0].strip()
    number = parse_numeric(text)
    unit_match = re.search(r"\b(KIB|KB|MIB|MB|GIB|GB|B)\b", text, re.IGNORECASE)
    unit = unit_match.group(1).upper() if unit_match else "MIB"
    if unit in ("KIB", "KB"):
        number /= 1024.0
    elif unit in ("GIB", "GB"):
        number *= 1024.0
    elif unit == "B":
        number /= 1024.0 * 1024.0
    return _finite_nonnegative(number)


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
        environment = {**os.environ, "LC_ALL": "C", "LANG": "C"}
        res = subprocess.run(
            ["lscpu", "-J"],
            capture_output=True,
            text=True,
            timeout=2,
            env=environment,
        )
        if res.returncode == 0 and res.stdout:
            parsed = json.loads(res.stdout)
            if isinstance(parsed, dict) and isinstance(parsed.get("lscpu"), list):
                return parsed
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, TypeError):
        return None
    return None


def _ejecutar_lscpu_texto():
    try:
        environment = {**os.environ, "LC_ALL": "C", "LANG": "C"}
        result = subprocess.run(
            ["lscpu"],
            capture_output=True,
            text=True,
            timeout=2,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout if result.returncode == 0 else ""


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


def _flatten_lscpu(data):
    """Aplana una respuesta de ``lscpu -J`` ignorando nodos malformados."""
    if not isinstance(data, dict) or not isinstance(data.get("lscpu"), list):
        return [], {}

    fields = []
    flat_map = {}

    def traverse(entries):
        if not isinstance(entries, list):
            return
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            field = str(entry.get("field") or "").strip().rstrip(":")
            value = entry.get("data")
            if field and value is not None and not isinstance(value, (dict, list)):
                text = str(value).strip()
                if text:
                    fields.append((field, text))
                    flat_map[field.lower()] = text
            traverse(entry.get("children"))

    traverse(data["lscpu"])
    return fields, flat_map


def _parse_lscpu_text(text):
    fields = []
    for line in str(text or "").splitlines():
        if ":" not in line:
            continue
        field, value = line.split(":", 1)
        field = field.strip()
        value = value.strip()
        if field and value:
            fields.append((field, value))
    return fields


def _recoger_snapshot_cpu():
    """Recoge especificaciones fuera del hilo GTK."""
    lscpu_raw = _ejecutar_lscpu_json()
    fields, _flat_map = _flatten_lscpu(lscpu_raw)
    return {
        "lscpu": lscpu_raw,
        "lscpu_text": "" if fields else _ejecutar_lscpu_texto(),
        "cpuinfo": _leer_proc_cpuinfo(),
    }


def _recoger_metricas_tiempo_real(win):
    """Recoge E/S y subprocess en worker; no toca widgets GTK."""
    scheduler = (None, None)
    scheduler_available = False
    if not _diagnostico_esta_ocupado(win):
        try:
            scheduler = win.scx.obtener_estado()
            scheduler_available = True
        except Exception:
            pass
    return {
        "cpu": obtener_uso_cpu_general(),
        "memory": obtener_uso_memoria(),
        "temperature": win.sensor.obtener_temp(),
        "scheduler": scheduler,
        "scheduler_available": scheduler_available,
        "cores": obtener_uso_cores(),
        "planning": obtener_planif_stats(),
        "loadavg": obtener_loadavg(),
        "timestamp": time.monotonic(),
    }


def _diagnostico_esta_ocupado(win):
    operations = getattr(win, "operaciones", None)
    if operations is not None:
        try:
            busy = getattr(operations, "is_busy", False)
            if bool(busy() if callable(busy) else busy):
                return True
        except Exception:
            pass
    return bool(
        getattr(win, "en_proceso_auto", False)
        or getattr(win, "en_proceso_bench", False)
    )


def _intervalo_sondeo_diagnostico(ocupado, pagina_activa=True):
    if ocupado:
        return 10_000
    return 1_500 if pagina_activa else 5_000


def _invalidar_baselines_diagnostico(win):
    """Descarta referencias de tasas sin repetir escrituras si ya están vacías."""
    if win is None:
        return False

    missing = object()
    changed = False
    for attr in (
        "_prev_cpu_total",
        "_prev_cpu_idle",
        "_prev_ctxt",
        "_prev_ctxt_time",
    ):
        previous = getattr(win, attr, missing)
        if previous is missing:
            setattr(win, attr, None)
        elif previous is not None:
            setattr(win, attr, None)
            changed = True

    previous_cores = getattr(win, "_prev_cores", missing)
    if isinstance(previous_cores, dict):
        if previous_cores:
            previous_cores.clear()
            changed = True
    else:
        setattr(win, "_prev_cores", {})
        changed = changed or previous_cores not in (missing, None)
    return changed


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


def normalizar_radar(raw_values, axes=_EJES):
    """Normaliza valores contra los máximos declarados, preservando cada eje."""
    values = list(raw_values or ())
    normalized = []
    for index, (_name, maximum, _unit) in enumerate(axes):
        value = _finite_nonnegative(values[index] if index < len(values) else 0.0)
        max_value = _finite_nonnegative(maximum)
        normalized.append(min(1.0, value / max_value) if max_value > 0 else 0.0)
    return normalized


def _formatear_valor_radar(value, index):
    value = _finite_nonnegative(value)
    if value == 0:
        return "?"
    if index == 2:
        return f"{value / 1000.0:.1f} GHz"
    if index in (3, 4):
        return f"{value:.1f} MiB" if value < 10 else f"{value:.0f} MiB"
    return f"{value:.0f}"


def preparar_datos_radar(raw_values, axes=_EJES):
    """Devuelve valores normalizados, nombres, crudos y textos alineados."""
    values = list(raw_values or ())
    raw = [
        _finite_nonnegative(values[index] if index < len(values) else 0.0)
        for index in range(len(axes))
    ]
    names = [str(axis[0]) for axis in axes]
    display = [_formatear_valor_radar(value, index) for index, value in enumerate(raw)]
    return normalizar_radar(raw, axes), names, raw, display


def _parse_frequency_mhz(value):
    frequency = parse_numeric(value)
    if "ghz" in str(value or "").lower():
        frequency *= 1000.0
    return _finite_nonnegative(frequency)


def _extraer_valores_radar(flat_map, cpuinfo):
    """Extrae seis valores alineados desde lscpu y, si falta, /proc/cpuinfo."""
    flat_map = flat_map if isinstance(flat_map, dict) else {}
    cpuinfo = cpuinfo if isinstance(cpuinfo, list) else []
    first_cpu = cpuinfo[0] if cpuinfo and isinstance(cpuinfo[0], dict) else {}
    find = _make_finder(flat_map)

    logical_value = find("cpu(s)", "logical cpu(s)", "cpus")
    if logical_value is None and cpuinfo:
        logical_value = len(cpuinfo)
    threads_value = find(
        "hilo(s) de procesamiento por núcleo",
        "thread(s) per core",
        "hilo(s) por núcleo",
    )
    frequency_value = find(
        "cpu mhz máx",
        "cpu max mhz",
        "velocidad máxima de cpu",
        "max cpu mhz",
    )
    if frequency_value is None:
        frequency_value = find("cpu mhz", "mhz")
    if frequency_value is None:
        frequency_value = first_cpu.get("cpu MHz")

    l3_value = find("l3 cache", "caché l3", "l3")
    l2_value = find("l2 cache", "caché l2", "l2")
    cores_value = find(
        "núcleo(s) por socket",
        "core(s) per socket",
        "core(s)",
        "núcleos por socket",
    )
    if cores_value is None:
        cores_value = first_cpu.get("cpu cores")

    logical = parse_numeric(logical_value)
    threads = parse_numeric(threads_value)
    cores = parse_numeric(cores_value)
    if cores <= 0 and logical > 0:
        sockets = max(1.0, parse_numeric(find("socket(s)", "sockets")) or 1.0)
        divisor = sockets * max(1.0, threads or 1.0)
        cores = logical / divisor
    if threads <= 0 and first_cpu:
        siblings = parse_numeric(first_cpu.get("siblings"))
        proc_cores = parse_numeric(first_cpu.get("cpu cores"))
        if siblings > 0 and proc_cores > 0:
            threads = siblings / proc_cores

    return [
        logical,
        threads,
        _parse_frequency_mhz(frequency_value),
        parse_cache_to_mib(l3_value),
        parse_cache_to_mib(l2_value),
        cores,
    ]


# ── Radar Chart (Cairo) ────────────────────────────────────────────────────────

class RadarChart(Gtk.DrawingArea):
    SIZE = 340
    _PULSE_SPEED  = 0.010
    _PULSE_RINGS  = 3

    def __init__(self, busy_check=None):
        super().__init__()
        self._busy_check = busy_check or (lambda: False)
        self.set_size_request(self.SIZE, self.SIZE)
        self.set_hexpand(True)
        self.set_halign(Gtk.Align.CENTER)
        self._values: list[float] = []
        self._labels: list[str]   = []
        self._raw_values: list    = []
        self._display_values: list[str] = []
        self._progress  = 0.0
        self._pulse_t   = 0.0
        self._anim_id   = None
        self._pulse_id  = None
        self._hover_idx = -1
        self._mode      = "radar"
        self._num_axes  = 0
        self._trans_alpha = 1.0
        self.set_draw_func(self._draw)
        self.connect("unrealize", self._on_unrealize)

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

    def _is_busy(self):
        try:
            return bool(self._busy_check())
        except Exception:
            return False

    def _is_rooted(self):
        try:
            return self.get_root() is not None
        except Exception:
            return False

    def _stop_animations(self):
        if self._pulse_id:
            GLib.source_remove(self._pulse_id)
            self._pulse_id = None
        if self._anim_id:
            GLib.source_remove(self._anim_id)
            self._anim_id = None

    def _on_unrealize(self, *_args):
        self._stop_animations()

    def dispose_sources(self):
        self._stop_animations()

    def _schedule_pulse(self):
        if self._pulse_id or not self._is_rooted():
            return
        interval = 1_000 if self._is_busy() else 66
        self._pulse_id = GLib.timeout_add(interval, self._tick_pulse)

    def set_data(
        self,
        values: list[float],
        labels: list[str],
        raw_values: list = None,
        display_values: list[str] = None,
    ):
        values = list(values or ())
        labels = list(labels or ())
        raw_values = list(raw_values or ())
        display_values = list(display_values or ())
        axis_count = max(
            len(values), len(labels), len(raw_values), len(display_values)
        )
        new_vals = [
            min(1.0, _finite_nonnegative(values[index] if index < len(values) else 0.0))
            for index in range(axis_count)
        ]
        new_labels = [
            str(labels[index]) if index < len(labels) and labels[index] else f"Eje {index + 1}"
            for index in range(axis_count)
        ]
        new_raw = [
            _finite_nonnegative(
                raw_values[index] if index < len(raw_values) else 0.0
            )
            for index in range(axis_count)
        ]
        new_display = [
            str(display_values[index])
            if index < len(display_values) and display_values[index]
            else (f"{new_raw[index]:g}" if new_raw[index] else "?")
            for index in range(axis_count)
        ]

        new_mode = "radar" if len(new_vals) >= 3 else "bars"
        axes_changed = (len(new_vals) != self._num_axes) or (new_mode != self._mode)

        self._values = new_vals
        self._labels = new_labels
        self._raw_values = new_raw
        self._display_values = new_display
        self._num_axes = len(new_vals)
        self._mode = new_mode

        if axes_changed:
            self._progress = 0.0
            self._trans_alpha = 0.0
        else:
            self._progress = max(self._progress, 0.0)

        self._pulse_t = 0.0
        self._stop_animations()
        if not self._is_rooted():
            self._progress = 1.0
            self._trans_alpha = 1.0
            return
        if self._is_busy():
            self._progress = 1.0
            self._trans_alpha = 1.0
            self.queue_draw()
            self._schedule_pulse()
            return
        self._anim_id = GLib.timeout_add(16, self._tick_entry)

    def _tick_entry(self) -> bool:
        if not self._is_rooted():
            self._anim_id = None
            return False
        if self._is_busy():
            self._progress = 1.0
            self._trans_alpha = 1.0
            self._anim_id = None
            self.queue_draw()
            self._schedule_pulse()
            return False
        self._progress = min(1.0, self._progress + 0.030)
        self._trans_alpha = min(1.0, self._trans_alpha + 0.05)
        self.queue_draw()
        if self._progress >= 1.0:
            self._anim_id = None
            self._schedule_pulse()
            return False
        return True

    def _tick_pulse(self) -> bool:
        self._pulse_id = None
        if not self._is_rooted():
            return False
        if not self._is_busy() and self.get_visible():
            self._pulse_t = (self._pulse_t + self._PULSE_SPEED) % 1.0
            self.queue_draw()
        self._schedule_pulse()
        return False

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
            bar_h = ch * val * prog
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
            label = self._labels[i] if i < len(self._labels) else "?"
            cr.select_font_face("Sans", 0, 0)
            cr.set_font_size(9.0)
            ext = cr.text_extents(label)
            cr.set_source_rgba(*colors["accent"] if is_hl else colors["subtext"], 0.9 * ta)
            cr.move_to(x + bar_w / 2 - ext.width / 2, margin_t + ch + 16)
            cr.show_text(label)

            # Valor (arriba de la barra)
            val_str = self._display_values[i] if i < len(self._display_values) else "?"
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
            name = self._labels[i] if i < len(self._labels) else "?"
            val = self._display_values[i] if i < len(self._display_values) else "?"
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

def actualizar_diagnostico_tiempo_real(win, widgets, snapshot):
    """Aplica en GTK una instantánea ya recogida por un worker."""
    if not isinstance(snapshot, dict):
        return

    cpu_snapshot = snapshot.get("cpu", (0.0, 0.0))
    if not isinstance(cpu_snapshot, (tuple, list)) or len(cpu_snapshot) != 2:
        cpu_snapshot = (0.0, 0.0)
    t_tot, t_idl = cpu_snapshot
    t_tot = _finite_nonnegative(t_tot)
    t_idl = _finite_nonnegative(t_idl)
    if win._prev_cpu_total is not None:
        d_tot = t_tot - win._prev_cpu_total
        d_idl = t_idl - win._prev_cpu_idle
        if d_tot > 0:
            cpu_usage = min(1.0, max(0.0, (d_tot - d_idl) / d_tot))
            widgets["cpu_progress"].set_fraction(cpu_usage)
            widgets["cpu_val_lbl"].set_label(f"{cpu_usage * 100:.1f}%")
    win._prev_cpu_total = t_tot
    win._prev_cpu_idle = t_idl

    memory_snapshot = snapshot.get("memory", (0.0, 0.0, 0.0))
    if not isinstance(memory_snapshot, (tuple, list)) or len(memory_snapshot) != 3:
        memory_snapshot = (0.0, 0.0, 0.0)
    m_tot, m_usd, m_frac = memory_snapshot
    m_tot = _finite_nonnegative(m_tot)
    m_usd = _finite_nonnegative(m_usd)
    m_frac = min(1.0, _finite_nonnegative(m_frac))
    if m_tot > 0:
        widgets["mem_progress"].set_fraction(m_frac)
        widgets["mem_val_lbl"].set_label(
            f"{m_usd:.1f} GB / {m_tot:.1f} GB ({m_frac * 100:.1f}%)"
        )

    temperature = _finite_nonnegative(snapshot.get("temperature", 0.0))
    temperature_label = widgets["temp_val_lbl"]
    for css_class in ("success-label", "warning-label", "error-label"):
        temperature_label.remove_css_class(css_class)
    if temperature > 0:
        temperature_label.set_label(f"{temperature:.1f} °C")
        if temperature < 60:
            temperature_label.add_css_class("success-label")
        elif temperature < 75:
            temperature_label.add_css_class("warning-label")
        else:
            temperature_label.add_css_class("error-label")
    else:
        temperature_label.set_label("N/D")

    scheduler = snapshot.get("scheduler", (None, None))
    if not isinstance(scheduler, (tuple, list)) or len(scheduler) != 2:
        scheduler = (None, None)
    scheduler_name, scheduler_mode = scheduler
    if not snapshot.get("scheduler_available", False):
        widgets["sched_val_lbl"].set_label("N/D")
    elif scheduler_name:
        widgets["sched_val_lbl"].set_label(
            f"{scheduler_name} [{scheduler_mode or 'auto'}]"
        )
    else:
        widgets["sched_val_lbl"].set_label("Sistema Base (Default)")

    cores_stats = snapshot.get("cores", {})
    if isinstance(cores_stats, dict):
        for name, current in cores_stats.items():
            if not isinstance(current, (tuple, list)) or len(current) != 2:
                continue
            c_tot = _finite_nonnegative(current[0])
            c_idl = _finite_nonnegative(current[1])
            if name in win._prev_cores:
                prev_tot, prev_idl = win._prev_cores[name]
                d_tot = c_tot - prev_tot
                d_idl = c_idl - prev_idl
                if d_tot > 0 and name in widgets["core_bars"]:
                    core_usage = min(1.0, max(0.0, (d_tot - d_idl) / d_tot))
                    widgets["core_bars"][name].set_fraction(core_usage)
                    widgets["core_labels"][name].set_label(
                        f"{int(core_usage * 100)}%"
                    )
            win._prev_cores[name] = (c_tot, c_idl)

    planning = snapshot.get("planning", (0, 0, 0))
    if not isinstance(planning, (tuple, list)) or len(planning) != 3:
        planning = (0, 0, 0)
    ctxt, running, blocked = (int(_finite_nonnegative(value)) for value in planning)
    now_t = _finite_nonnegative(snapshot.get("timestamp", 0.0))
    if win._prev_ctxt is not None and win._prev_ctxt_time is not None:
        dt = now_t - win._prev_ctxt_time
        delta = ctxt - win._prev_ctxt
        if dt > 0 and delta >= 0:
            ctxt_rate = delta / dt
            widgets["ctxt_rate_lbl"].set_label(
                f"{int(ctxt_rate):,}".replace(",", ".") + " ctxt/s"
            )
    win._prev_ctxt = ctxt
    win._prev_ctxt_time = now_t

    widgets["ctxt_total_lbl"].set_label(f"{ctxt:,}".replace(",", "."))
    widgets["procs_running_lbl"].set_label(str(running))
    widgets["procs_blocked_lbl"].set_label(str(blocked))
    widgets["procs_blocked_lbl"].remove_css_class("error-label")
    widgets["procs_blocked_lbl"].remove_css_class("success-label")
    widgets["procs_blocked_lbl"].add_css_class(
        "error-label" if blocked > 0 else "success-label"
    )

    loadavg = snapshot.get("loadavg", (0.0, 0.0, 0.0))
    if not isinstance(loadavg, (tuple, list)) or len(loadavg) != 3:
        loadavg = (0.0, 0.0, 0.0)
    load_values = [parse_numeric(value) for value in loadavg]
    widgets["loadavg_lbl"].set_label(
        "  •  ".join(f"{value:.2f}" for value in load_values)
    )


# ── Construcción de la Interfaz Diagnóstico ────────────────────────────────────

def setup_diagnostico_ui(win):
    if not _GTK_AVAILABLE:
        raise RuntimeError("GTK4/Libadwaita no está disponible.")
    previous_cleanup = getattr(win, "_diagnostico_cleanup", None)
    if callable(previous_cleanup):
        previous_cleanup()

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

    # ── 1. Encabezado de la Página ──
    banner_group = Adw.PreferencesGroup()
    cpu_title_row = Adw.ActionRow()
    cpu_title_row.set_icon_name("cpu-symbolic")
    banner_group.add(cpu_title_row)
    pref_page.add(banner_group)
    win.cpu_title_row = cpu_title_row  # Guardamos referencia para poblar el modelo de CPU

    # ── 2. Monitoreo en Tiempo Real ──
    rt_group = Adw.PreferencesGroup(
        title="Monitoreo en Tiempo Real",
        description="Estado, carga del sistema e integridad térmica."
    )
    
    cpu_row = Adw.ActionRow(title="Carga de CPU")
    cpu_val_lbl = Gtk.Label(label="...", valign=Gtk.Align.CENTER)
    cpu_progress = Gtk.ProgressBar(valign=Gtk.Align.CENTER, hexpand=True, margin_end=12)
    cpu_row.add_suffix(cpu_progress)
    cpu_row.add_suffix(cpu_val_lbl)
    rt_group.add(cpu_row)

    mem_row = Adw.ActionRow(title="Uso de Memoria RAM")
    mem_val_lbl = Gtk.Label(label="...", valign=Gtk.Align.CENTER)
    mem_progress = Gtk.ProgressBar(valign=Gtk.Align.CENTER, hexpand=True, margin_end=12)
    mem_row.add_suffix(mem_progress)
    mem_row.add_suffix(mem_val_lbl)
    rt_group.add(mem_row)

    temp_row = Adw.ActionRow(title="Temperatura de CPU")
    temp_val_lbl = Gtk.Label(label="Cargando...", valign=Gtk.Align.CENTER)
    temp_row.add_suffix(temp_val_lbl)
    rt_group.add(temp_row)

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
        "cpu_progress": cpu_progress,
        "cpu_val_lbl": cpu_val_lbl,
        "mem_progress": mem_progress,
        "mem_val_lbl": mem_val_lbl,
        "temp_val_lbl": temp_val_lbl,
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
        radar = RadarChart(lambda: _diagnostico_esta_ocupado(win))
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

    refresh_state = {
        "running": False,
        "pending": False,
        "generation": 0,
        "disposed": False,
    }
    monitor_state = {
        "source_id": None,
        "worker_running": False,
        "generation": 0,
        "baseline_epoch": 0,
        "paused": False,
    }

    def _is_rooted():
        try:
            return pref_page.get_root() is not None
        except Exception:
            return False

    # ── Aplicación GTK de las especificaciones recogidas por el worker ──
    def poblar(snapshot):
        _clear_listbox(lb_general)
        _clear_listbox(lb_topologia)
        _clear_listbox(lb_caches)
        _clear_listbox(lb_frecuencias)
        _clear_listbox(lb_virtualizacion)
        _clear_listbox(lb_seguridad)

        snapshot = snapshot if isinstance(snapshot, dict) else {}
        cpuinfo = snapshot.get("cpuinfo", [])
        cpuinfo = cpuinfo if isinstance(cpuinfo, list) else []
        first_cpu = cpuinfo[0] if cpuinfo and isinstance(cpuinfo[0], dict) else {}
        fields_list, flat_map = _flatten_lscpu(snapshot.get("lscpu"))
        if not fields_list:
            fields_list = _parse_lscpu_text(snapshot.get("lscpu_text", ""))
            flat_map = {field.lower(): value for field, value in fields_list}

        for field, data in fields_list:
            field_low = field.lower()
            row = Adw.ActionRow(title=field, subtitle=str(data))
            if any(
                key in field_low
                for key in (
                    "vulnerabilidad", "vulnerability", "mitigación", "mitigation",
                    "gather data", "ghostwrite", "speculative",
                )
            ):
                lb_seguridad.append(row)
            elif any(
                key in field_low
                for key in ("l1", "l2", "l3", "l1d", "l1i", "caché", "cache")
            ):
                lb_caches.append(row)
            elif any(
                key in field_low
                for key in (
                    "mhz", "ghz", "frecuencia", "frequency", "bogomips",
                    "escala", "scaling", "driver", "aumento",
                )
            ):
                lb_frecuencias.append(row)
            elif any(
                key in field_low
                for key in ("virtual", "hiper", "hyper", "kvm")
            ):
                lb_virtualizacion.append(row)
            elif any(
                key in field_low
                for key in (
                    "socket", "nodo", "numa", "hilo", "núcleo", "core",
                    "thread", "siblings", "cpu(s)",
                )
            ):
                lb_topologia.append(row)
            else:
                lb_general.append(row)

        find = _make_finder(flat_map)
        model_name = (
            find("nombre del modelo", "model name")
            or first_cpu.get("model name")
            or "Procesador Desconocido"
        )
        escaped_model_name = GLib.markup_escape_text(str(model_name))
        win.cpu_title_row.set_title(f"<b>{escaped_model_name}</b>")
        win.cpu_title_row.set_subtitle("Información y Diagnóstico de la CPU")
        win.cpu_title_row.set_use_markup(True)

        represented_fields = {field.lower() for field, _value in fields_list}
        if cpuinfo:
            for key in (
                "model name", "cpu MHz", "cache size", "flags", "siblings", "cpu cores"
            ):
                if key in first_cpu and key.lower() not in represented_fields:
                    row = Adw.ActionRow(title=key.title(), subtitle=str(first_cpu[key]))
                    if "cache" in key or "size" in key:
                        lb_caches.append(row)
                    elif "mhz" in key.lower():
                        lb_frecuencias.append(row)
                    elif "cores" in key.lower() or "siblings" in key.lower():
                        lb_topologia.append(row)
                    else:
                        lb_general.append(row)

        if not fields_list and not cpuinfo:
            lb_general.append(Adw.ActionRow(title="No se pudo obtener información de CPU"))

        if radar:
            raw_values = _extraer_valores_radar(flat_map, cpuinfo)
            norms, names, raw_values, display = preparar_datos_radar(raw_values)
            radar.set_data(
                norms,
                names,
                raw_values=raw_values,
                display_values=display,
            )

        # Ocultar automáticamente secciones vacías
        exp_seguridad.set_visible(lb_seguridad.get_row_at_index(0) is not None)
        exp_caches.set_visible(lb_caches.get_row_at_index(0) is not None)
        exp_frecuencias.set_visible(lb_frecuencias.get_row_at_index(0) is not None)
        exp_virtualizacion.set_visible(lb_virtualizacion.get_row_at_index(0) is not None)
        exp_topologia.set_visible(lb_topologia.get_row_at_index(0) is not None)
        exp_general.set_visible(lb_general.get_row_at_index(0) is not None)

    def _apply_spec_snapshot(generation, snapshot):
        if generation != refresh_state["generation"]:
            return False
        refresh_state["running"] = False
        if refresh_state["disposed"]:
            return False
        btn_actualizar.set_sensitive(True)
        if not _is_rooted():
            refresh_state["pending"] = True
            return False
        poblar(snapshot)
        if refresh_state["pending"]:
            refresh_state["pending"] = False
            _request_spec_refresh()
        return False

    def _request_spec_refresh(*_args):
        if refresh_state["disposed"]:
            return
        if not _is_rooted():
            refresh_state["pending"] = True
            return
        if _diagnostico_esta_ocupado(win):
            refresh_state["pending"] = True
            return
        if refresh_state["running"]:
            refresh_state["pending"] = True
            return
        refresh_state["running"] = True
        refresh_state["pending"] = False
        refresh_state["generation"] += 1
        generation = refresh_state["generation"]
        btn_actualizar.set_sensitive(False)

        def worker():
            try:
                snapshot = _recoger_snapshot_cpu()
            except Exception:
                snapshot = {"lscpu": None, "lscpu_text": "", "cpuinfo": []}
            GLib.idle_add(_apply_spec_snapshot, generation, snapshot)

        threading.Thread(target=worker, daemon=True).start()

    def _page_is_active():
        try:
            return win.split_view.get_content() == win.pag_diagnostico
        except Exception:
            return False

    def _set_monitor_paused(paused):
        paused = bool(paused)
        if monitor_state["paused"] == paused:
            return
        _invalidar_baselines_diagnostico(win)
        monitor_state["baseline_epoch"] += 1
        monitor_state["paused"] = paused

    def _clear_monitor_source():
        source_id = monitor_state["source_id"]
        if source_id is not None:
            GLib.source_remove(source_id)
            monitor_state["source_id"] = None
            if getattr(win, "_diagnostico_timer_id", None) == source_id:
                win._diagnostico_timer_id = None

    def _schedule_monitor(delay=None):
        if (
            refresh_state["disposed"]
            or not _is_rooted()
            or monitor_state["source_id"] is not None
        ):
            return
        if delay is None:
            delay = _intervalo_sondeo_diagnostico(
                _diagnostico_esta_ocupado(win), _page_is_active()
            )
        source_id = GLib.timeout_add(delay, _monitor_timeout)
        monitor_state["source_id"] = source_id
        win._diagnostico_timer_id = source_id

    def _apply_monitor_snapshot(generation, baseline_epoch, snapshot):
        if generation != monitor_state["generation"]:
            return False
        monitor_state["worker_running"] = False
        if refresh_state["disposed"] or not _is_rooted():
            return False
        if baseline_epoch != monitor_state["baseline_epoch"]:
            _schedule_monitor()
            return False
        try:
            paused = not _page_is_active() or _diagnostico_esta_ocupado(win)
            _set_monitor_paused(paused)
            if snapshot and not paused:
                actualizar_diagnostico_tiempo_real(win, widgets, snapshot)
        finally:
            _schedule_monitor()
        return False

    def _monitor_timeout():
        source_id = monitor_state["source_id"]
        monitor_state["source_id"] = None
        if getattr(win, "_diagnostico_timer_id", None) == source_id:
            win._diagnostico_timer_id = None
        if refresh_state["disposed"] or not _is_rooted():
            return False
        paused = _diagnostico_esta_ocupado(win) or not _page_is_active()
        if paused:
            _set_monitor_paused(True)
            _schedule_monitor()
            return False
        _set_monitor_paused(False)
        if monitor_state["worker_running"]:
            _schedule_monitor()
            return False

        if refresh_state["pending"] and not refresh_state["running"]:
            _request_spec_refresh()
            _schedule_monitor()
            return False

        monitor_state["worker_running"] = True
        monitor_state["generation"] += 1
        generation = monitor_state["generation"]
        baseline_epoch = monitor_state["baseline_epoch"]

        def worker():
            try:
                snapshot = _recoger_metricas_tiempo_real(win)
            except Exception:
                snapshot = {}
            GLib.idle_add(
                _apply_monitor_snapshot, generation, baseline_epoch, snapshot
            )

        threading.Thread(target=worker, daemon=True).start()
        return False

    # ── Barra de Herramientas / Cabecera ──
    header = Adw.HeaderBar()
    btn_actualizar = Gtk.Button(
        icon_name="view-refresh-symbolic",
        tooltip_text="Actualizar especificaciones",
        css_classes=["flat"]
    )
    btn_actualizar.connect("clicked", _request_spec_refresh)
    header.pack_end(btn_actualizar)

    view = Adw.ToolbarView(content=pref_page)
    view.add_top_bar(header)

    win.pag_diagnostico = Adw.NavigationPage(title="Diagnóstico", tag="page_e")
    win.pag_diagnostico.set_child(view)

    def _stop_runtime_sources():
        _clear_monitor_source()
        if radar:
            radar.dispose_sources()

    def _on_realize(*_args):
        if refresh_state["disposed"]:
            return
        _set_monitor_paused(False)
        _request_spec_refresh()
        _schedule_monitor(100)

    def _on_unrealize(*_args):
        _set_monitor_paused(True)
        _stop_runtime_sources()

    def cleanup():
        if refresh_state["disposed"]:
            return
        refresh_state["disposed"] = True
        _stop_runtime_sources()

    win._diagnostico_cleanup = cleanup
    win._diagnostico_timer_id = None
    pref_page.connect("realize", _on_realize)
    pref_page.connect("unrealize", _on_unrealize)

    if _is_rooted():
        _on_realize()

    return win.pag_diagnostico
