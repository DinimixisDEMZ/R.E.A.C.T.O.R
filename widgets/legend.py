"""
Widget de leyenda interactiva para la gráfica de detección.
Chips clickeables que permiten ocultar/mostrar schedulers.
"""

import math

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Gdk


def crear_chip_leyenda(nombre, grafico, box_leyenda):
    """Crea un chip de leyenda interactivo para un scheduler.
    
    Args:
        nombre: Nombre del scheduler
        grafico: Instancia de GraficoComparativo
        box_leyenda: Contenedor WrapBox donde añadir el chip
    """
    r, g, b = grafico.colores.get(nombre.lower(), (0.5, 0.5, 0.5))

    chip = Gtk.Box(spacing=10, css_classes=["card", "pill"], valign=Gtk.Align.CENTER)
    chip.set_cursor(Gdk.Cursor.new_from_name("pointer", None))

    # Indicador de color
    dot = Gtk.DrawingArea()
    dot.set_content_width(12)
    dot.set_content_height(12)
    dot.set_valign(Gtk.Align.CENTER)
    dot.set_margin_start(8)
    dot.set_draw_func(lambda a, cr, w, h, x: (
        cr.set_source_rgb(r, g, b),
        cr.arc(w / 2, h / 2, 5, 0, 2 * math.pi),
        cr.fill(),
        cr.set_source_rgba(1, 1, 1, 0.2),
        cr.arc(w / 2, h / 2, 5, 0, 2 * math.pi),
        cr.set_line_width(1),
        cr.stroke()
    ), None)

    label = Gtk.Label(label=nombre)
    label.set_margin_end(10)
    label.set_margin_top(4)
    label.set_margin_bottom(4)
    label.add_css_class("caption-heading")

    chip.append(dot)
    chip.append(label)

    # Toggle de visibilidad al clickear
    def al_clickear(gesture, n_press, x, y, name):
        if name in grafico.ocultos:
            grafico.ocultos.remove(name)
            chip.set_opacity(1.0)
            dot.set_opacity(1.0)
        else:
            grafico.ocultos.add(name)
            chip.set_opacity(0.4)
            dot.set_opacity(0.3)
        grafico.queue_draw()

    click = Gtk.GestureClick()
    click.connect("pressed", al_clickear, nombre)
    chip.add_controller(click)

    box_leyenda.append(chip)
