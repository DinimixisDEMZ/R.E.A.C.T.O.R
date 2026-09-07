"""
Widget de medidor circular para métricas en tiempo real.
Dibuja un arco de progreso con icono y valor numérico.
"""

import math

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Gdk, GLib, Adw

try:
    import cairo
    _HAY_CAIRO = True
except ImportError:  # pragma: no cover - depende del sistema
    cairo = None
    _HAY_CAIRO = False

from core.constantes import INTERVALO_FRAME_MS

_COLOR_VERDE = (0.18, 0.76, 0.49)
_COLOR_AMARILLO = (0.96, 0.76, 0.07)
_COLOR_ROJO = (0.90, 0.33, 0.30)
_COLOR_FONDO_LIGHT = (0.0, 0.0, 0.0, 0.08)
_COLOR_FONDO_DARK = (1.0, 1.0, 1.0, 0.10)

_ANGULO_INICIO = 3 * math.pi / 4
_ARCO_TOTAL = 2 * math.pi - (3 * math.pi / 4 - (-math.pi / 4))


def _color_para_fraccion(fraction):
    if fraction < 0.50:
        return _COLOR_VERDE
    if fraction < 0.75:
        return _COLOR_AMARILLO
    return _COLOR_ROJO


def _color_para_temperatura(temp_c, limite=None):
    """Color del medidor según lo cerca que esté la temperatura de su límite.

    El límite lo declara el propio hardware (Tjmax). Comparar contra grados
    fijos pintaba de rojo a un portátil que trabajaba con total normalidad: un
    CPU con Tjmax de 100 °C a 78 °C está holgado, y uno de 85 °C no.
    """
    from core.thermal import FRACCION_CRITICA, FRACCION_ELEVADA, TJMAX_POR_DEFECTO
    limite = limite or TJMAX_POR_DEFECTO
    if limite <= 0:
        return _COLOR_VERDE
    fraccion = temp_c / limite
    if fraccion < FRACCION_ELEVADA:
        return _COLOR_VERDE
    if fraccion < FRACCION_CRITICA:
        return _COLOR_AMARILLO
    return _COLOR_ROJO


class CircularMeter(Gtk.Overlay):

    def __init__(self, icon_name, label_text, size=90):
        super().__init__()
        self._size = size
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)

        self._icon_name = icon_name
        self._label_text = label_text
        self._fraction = 0.0
        self._target_fraction = 0.0
        self._value_text = "—"
        self._color = _COLOR_VERDE
        self._anim_id = None
        # Si el widget muere a mitad de una animación, el temporizador seguiría
        # vivo con una referencia a un objeto ya destruido.
        self.connect("destroy", lambda *_: self._detener_animacion())

        # Base de dibujo para el anillo de progreso circular
        self.drawing_area = Gtk.DrawingArea()
        self.drawing_area.set_content_width(size)
        if size < 50:
            self.drawing_area.set_content_height(size)
        else:
            self.drawing_area.set_content_height(int(size * 1.22))
        self.drawing_area.set_draw_func(self._draw)
        self.set_child(self.drawing_area)

        # Icono nativo superpuesto para el modo mini
        self.icon_widget = Gtk.Image(icon_name=icon_name)
        self.icon_widget.set_halign(Gtk.Align.CENTER)
        self.icon_widget.set_valign(Gtk.Align.CENTER)

        if size < 50:
            # Tamaño del icono proporcional (aprox. la mitad del tamaño total)
            self.icon_widget.set_pixel_size(int(size * 0.5))
            self.icon_widget.set_visible(True)
        else:
            self.icon_widget.set_visible(False)

        self.add_overlay(self.icon_widget)

    def update(self, fraction, value_text, color=None):
        self._target_fraction = max(0.0, min(1.0, fraction))
        self._value_text = value_text
        if color is not None:
            self._color = color
        else:
            self._color = _color_para_fraccion(fraction)
        
        if self._size < 50:
            self.set_tooltip_text(f"{self._label_text}: {value_text}")
            
        self._start_animation()

    def _detener_animacion(self):
        if self._anim_id is not None:
            GLib.source_remove(self._anim_id)
            self._anim_id = None

    def _start_animation(self):
        if self._anim_id is not None:
            return
        ajustes = Gtk.Settings.get_default()
        if ajustes is not None and not ajustes.get_property("gtk-enable-animations"):
            self._fraction = self._target_fraction
            self.drawing_area.queue_draw()
            return
        self._anim_id = GLib.timeout_add(INTERVALO_FRAME_MS, self._anim_tick)

    def _anim_tick(self):
        diff = self._target_fraction - self._fraction
        if abs(diff) < 0.005:
            self._fraction = self._target_fraction
            self._anim_id = None
            self.drawing_area.queue_draw()
            return False
        self._fraction += diff * 0.18
        self.drawing_area.queue_draw()
        return True

    def _draw(self, area, cr, w, h):
        style_manager = Adw.StyleManager.get_default()
        is_dark = style_manager.get_dark()

        cx = w / 2
        cy = h / 2 if self._size < 50 else (h - 20) / 2
        radius = (min(w, cy * 2) / 2) - 3.5 if self._size < 50 else (min(w, cy * 2) / 2) - 5
        line_width = 3.0 if self._size < 50 else 6.0

        cr.set_line_width(line_width)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)

        if is_dark:
            cr.set_source_rgba(*_COLOR_FONDO_DARK)
        else:
            cr.set_source_rgba(*_COLOR_FONDO_LIGHT)
        cr.arc(cx, cy, radius, _ANGULO_INICIO, _ANGULO_INICIO + _ARCO_TOTAL)
        cr.stroke()

        if self._fraction > 0.001:
            progreso = _ARCO_TOTAL * self._fraction
            r, g, b = self._color
            cr.set_source_rgb(r, g, b)
            cr.arc(cx, cy, radius, _ANGULO_INICIO, _ANGULO_INICIO + progreso)
            cr.stroke()

        r, g, b = self._color
        cr.set_source_rgb(r, g, b)
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)

        if self._size < 50:
            # Modo mini: Anillo de progreso puro, sin iniciales para un diseño extremadamente limpio.
            pass
        else:
            # Usar siglas de 3 caracteres de forma elegante (p. ej. CPU, RAM, TMP)
            label_text_lower = self._label_text.lower()
            if label_text_lower == "temp":
                initials = "TMP"
            elif len(self._label_text) > 3:
                initials = self._label_text[:3].upper()
            else:
                initials = self._label_text.upper()

            if len(initials) >= 3:
                cr.set_font_size(15)
            else:
                cr.set_font_size(19)

            ext = cr.text_extents(initials)
            cr.move_to(cx - ext.width / 2, cy + ext.height / 2)
            cr.show_text(initials)

            cr.set_source_rgb(r, g, b)
            cr.set_font_size(13)
            ext = cr.text_extents(self._value_text)
            cr.move_to(cx - ext.width / 2, h - 18)
            cr.show_text(self._value_text)

            if is_dark:
                cr.set_source_rgba(1.0, 1.0, 1.0, 0.5)
            else:
                cr.set_source_rgba(0.0, 0.0, 0.0, 0.5)
            cr.set_font_size(10)
            ext = cr.text_extents(self._label_text)
            cr.move_to(cx - ext.width / 2, h - 4)
            cr.show_text(self._label_text)
