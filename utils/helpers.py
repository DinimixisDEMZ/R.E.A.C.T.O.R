"""
Funciones de utilidad compartidas por toda la aplicación.
Incluye: Regex precompilados, logging, limpieza de texto, colores CSS.
"""

import re
from datetime import datetime

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib

# ─── Regex Precompilados ───

RE_RUNNING = re.compile(r"running\s+([\w\.-]+)(?:.*(?:in\s+|\[)([\w-]+)(?:\]|\s+mode)?)?", re.IGNORECASE)
RE_JSON_ARRAY = re.compile(r'\[.*?\]', re.DOTALL)
RE_ANSI = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')


# ─── Logging ───

def log(tv, texto, es_titulo=False, es_error=False):
    """Añade texto con formato al TextView de logs desde cualquier hilo."""
    GLib.idle_add(_append_log, tv, texto, es_titulo, es_error)

def _append_log(tv, texto, es_titulo, es_error):
    buf = tv.get_buffer()
    ts = datetime.now().strftime("%H:%M:%S")
    if es_error:
        format_text = f"[{ts}] ⚠️ ERROR: {texto}\n"
    elif es_titulo:
        format_text = f"\n{'='*30}\n[{ts}] {texto}\n{'='*30}\n"
    else:
        format_text = f"[{ts}] > {texto}\n"
    buf.insert(buf.get_end_iter(), format_text)
    adj = tv.get_vadjustment()
    if es_titulo or (adj.get_upper() - adj.get_value() - adj.get_page_size()) < 50:
        mark = buf.create_mark(None, buf.get_end_iter(), False)
        tv.scroll_to_mark(mark, 0.0, True, 0.5, 1.0)
        buf.delete_mark(mark)


# ─── Limpieza de Texto ───

def limpiar_texto(texto):
    """Elimina códigos ANSI y elementos de TUI de la salida de terminal."""
    if not texto:
        return ""
    limpio = RE_ANSI.sub('', texto)
    lineas = limpio.splitlines()
    filtradas = [l for l in lineas if not any(c in l for c in "│─┐└┘┌├┤┼█▓▒░")]
    muy_filtradas = [l for l in filtradas if len(l.strip()) > 2]
    return "\n".join(muy_filtradas).strip()


# ─── Colores ───

def hsl_to_rgb(h, s, l):
    """Convierte HSL a RGB (0.0-1.0)."""
    def q(p, q, t):
        if t < 0: t += 1
        if t > 1: t -= 1
        if t < 1/6: return p + (q - p) * 6 * t
        if t < 1/2: return q
        if t < 2/3: return p + (q - p) * (2/3 - t) * 6
        return p
    v2 = l * (1 + s) if l <= 0.5 else l + s - (l * s)
    v1 = 2 * l - v2
    return (q(v1, v2, h + 1/3), q(v1, v2, h), q(v1, v2, h - 1/3))

def generar_color_hash(name):
    """Genera un color HSL único basado en el nombre (estética GNOME)."""
    import zlib
    low = name.lower()
    h_val = zlib.crc32(low.encode()) & 0xffffffff
    h_val ^= (h_val >> 16)
    h_val = (h_val * 0x45d9f3b) & 0xffffffff
    h_val ^= (h_val >> 16)
    phi = 0.618033988749895
    hue = (h_val * phi) % 1.0
    sat = 0.65 + (h_val % 10) / 100.0
    lit = 0.50 + (h_val % 10) / 100.0
    return hsl_to_rgb(hue, sat, lit)

def obtener_color_css(name):
    """Devuelve un string CSS 'rgb(...)' basado en el nombre del scheduler."""
    r, g, b = generar_color_hash(name)
    return f"rgb({int(r*255)}, {int(g*255)}, {int(b*255)})"
