"""
Funciones de utilidad compartidas por toda la aplicación.
Incluye: Regex precompilados, logging, limpieza de texto, colores CSS.
"""

import os
import re
from datetime import datetime

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib, Gdk

# ─── Regex Precompilados ───

RE_RUNNING = re.compile(r"running\s+([\w\.-]+)(?:.*(?:in\s+|\[)([\w-]+)(?:\]|\s+mode)?)?", re.IGNORECASE)
RE_JSON_ARRAY = re.compile(r'\[[\s\S]*\]')
RE_ANSI = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')


# ─── Logging ───


def log(tv, texto, nivel="info"):
    """Añade texto con formato al TextView de logs desde cualquier hilo.
    
    nivel: "info", "title", "success", "warning", "error"
    """
    GLib.idle_add(_append_log, tv, texto, nivel)


def _color_adwaita(tv, nombre):
    """Resuelve un color del tema Adwaita a string hex."""
    ctx = tv.get_style_context()
    found, rgba = ctx.lookup_color(nombre)
    if found:
        r = max(0, min(255, int(round(rgba.red * 255))))
        g = max(0, min(255, int(round(rgba.green * 255))))
        b = max(0, min(255, int(round(rgba.blue * 255))))
        return f"#{r:02x}{g:02x}{b:02x}"
    return None


def _etiqueta_log(buf, nombre, color=None, negrita=False):
    tag_table = buf.get_tag_table()
    tag = tag_table.lookup(nombre)
    if tag is not None:
        return tag
    tag = buf.create_tag(nombre)
    if color:
        tag.set_property("foreground", color)
    if negrita:
        tag.set_property("weight", 700)
    return tag


def _append_log(tv, texto, nivel):
    buf = tv.get_buffer()
    ts = datetime.now().strftime("%H:%M:%S")
    inicio = buf.get_end_iter()

    if nivel == "error":
        format_text = f"[{ts}] ERROR: {texto}\n"
        buf.insert(inicio, format_text)
        fin = buf.get_end_iter()
        color = _color_adwaita(tv, "error_color")
        et = _etiqueta_log(buf, "log-err", color=color, negrita=True)
        buf.apply_tag(et, buf.get_iter_at_offset(inicio.get_offset()), fin)
    elif nivel == "warning":
        format_text = f"[{ts}] {texto}\n"
        buf.insert(inicio, format_text)
        fin = buf.get_end_iter()
        color = _color_adwaita(tv, "warning_color")
        et = _etiqueta_log(buf, "log-warn", color=color)
        buf.apply_tag(et, buf.get_iter_at_offset(inicio.get_offset()), fin)
    elif nivel == "success":
        format_text = f"[{ts}] {texto}\n"
        buf.insert(inicio, format_text)
        fin = buf.get_end_iter()
        color = _color_adwaita(tv, "success_color")
        et = _etiqueta_log(buf, "log-ok", color=color, negrita=True)
        buf.apply_tag(et, buf.get_iter_at_offset(inicio.get_offset()), fin)
    elif nivel == "title":
        format_text = f"\n{'─'*35}\n[{ts}] {texto}\n{'─'*35}\n"
        buf.insert(inicio, format_text)
        fin = buf.get_end_iter()
        tt = _etiqueta_log(buf, "log-title", negrita=True)
        buf.apply_tag(tt, buf.get_iter_at_offset(inicio.get_offset()), fin)
    else:
        format_text = f"[{ts}] {texto}\n"
        buf.insert(inicio, format_text)

    adj = tv.get_vadjustment()
    if nivel == "title" or (adj.get_upper() - adj.get_value() - adj.get_page_size()) < 50:
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


def _linear_to_srgb(v):
    """Convierte un valor de color de espacio lineal a sRGB."""
    if v <= 0.0031308:
        return v * 12.92
    return 1.055 * (v ** (1.0 / 2.4)) - 0.055


def obtener_color_tema(nombre):
    """Obtiene un color del tema Adwaita activo como tupla (r, g, b) en sRGB.
    
    Acepta: accent_color, success_color, error_color, warning_color, etc.
    Retorna None si el color no existe en el tema.
    """
    from gi.repository import Gtk
    label = Gtk.Label()
    ctx = label.get_style_context()
    found, rgba = ctx.lookup_color(nombre)
    if not found:
        return None
    r = min(1.0, max(0.0, _linear_to_srgb(rgba.red)))
    g = min(1.0, max(0.0, _linear_to_srgb(rgba.green)))
    b = min(1.0, max(0.0, _linear_to_srgb(rgba.blue)))
    return (r, g, b)


# ─── Parseo de lscpu ───

def parse_lscpu_numeric(s):
    """Extrae un valor numérico de un string de lscpu (ej. '4', '2.5 GHz')."""
    if not s:
        return 0.0
    try:
        token = s.split()[0]
        cleaned = "".join(c for c in token if c.isdigit() or c in '.,-')
        if '.' in cleaned and ',' in cleaned:
            if cleaned.rfind('.') > cleaned.rfind(','):
                cleaned = cleaned.replace(',', '')
            else:
                cleaned = cleaned.replace('.', '').replace(',', '.')
        elif ',' in cleaned:
            parts = cleaned.split(',')
            cleaned = parts[0] + '.' + parts[1] if len(parts) == 2 else cleaned.replace(',', '')
        return float(cleaned)
    except (ValueError, TypeError):
        return 0.0


def parse_lscpu_cache(s):
    """Convierte un string de caché de lscpu (ej. '8 MiB (1 instancia)') a MiB."""
    if not s:
        return 0.0
    try:
        parts = s.split('(')[0].strip().split()
        if not parts:
            return 0.0
        val = parse_lscpu_numeric(parts[0])
        if len(parts) > 1:
            u = parts[1].upper()
            if "KIB" in u or "KB" in u:
                val /= 1024.0
            elif "GIB" in u or "GB" in u:
                val *= 1024.0
        return val
    except (ValueError, TypeError):
        return 0.0


def make_lscpu_finder(flat_map):
    """Crea una función find(*keys) para buscar claves en un flat_map de lscpu."""
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


# ─── AppImage / Bundle Detection ───


def ruta_bundleada(subpath: str) -> str | None:
    """Devuelve la ruta completa a un recurso dentro del AppImage, o None."""
    appdir = os.environ.get("APPDIR")
    if not appdir:
        return None
    ruta = os.path.join(appdir, subpath)
    return ruta if os.path.exists(ruta) else None
