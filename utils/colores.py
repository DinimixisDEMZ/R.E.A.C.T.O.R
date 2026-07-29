"""
Utilidades de color y dibujo Cairo.
"""

import math
import zlib

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk


def hsl_to_rgb(h, s, l):
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
    r, g, b = generar_color_hash(name)
    return f"rgb({int(r*255)}, {int(g*255)}, {int(b*255)})"


def _linear_to_srgb(v):
    if v <= 0.0031308:
        return v * 12.92
    return 1.055 * (v ** (1.0 / 2.4)) - 0.055


def obtener_color_tema(nombre):
    label = Gtk.Label()
    ctx = label.get_style_context()
    found, rgba = ctx.lookup_color(nombre)
    if not found:
        return None
    r = min(1.0, max(0.0, _linear_to_srgb(rgba.red)))
    g = min(1.0, max(0.0, _linear_to_srgb(rgba.green)))
    b = min(1.0, max(0.0, _linear_to_srgb(rgba.blue)))
    return (r, g, b)


def dibujar_dot(cr, w, h, r, g, b, radius=3.5):
    cr.set_source_rgb(r, g, b)
    cr.arc(w / 2, h / 2, radius, 0, 2 * math.pi)
    cr.fill()
