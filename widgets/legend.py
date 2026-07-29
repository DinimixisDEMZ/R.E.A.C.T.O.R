"""
Widgets de leyenda interactiva.
Chips cliqueables con dot coloreado para ocultar/mostrar series.
"""

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Gdk

from utils.colores import dibujar_dot
from utils.helpers import vaciar_contenedor


def crear_chip_leyenda(
    nombre, color_func=None, on_toggle=None,
    tooltip_text=None, dot_radius=3.5, dot_size=12,
    opacity_visible=1.0, opacity_hidden=0.4,
    grafico=None, ocultos_set=None, box_leyenda=None
):
    """Crea un chip de leyenda interactivo.

    Args:
        nombre: Texto del label
        color_func: Callable(nombre) -> (r, g, b) o None para gris
        on_toggle: Callable(nombre, visible) al clickear
        tooltip_text: Texto de tooltip multilínea
        dot_radius: Radio del círculo coloreado
        dot_size: Tamaño del DrawingArea del dot
        opacity_visible: Opacidad cuando visible
        opacity_hidden: Opacidad cuando oculto
        grafico: Si se pasa, conecta toggle a grafico.ocultos + queue_draw
        ocultos_set: Set alternativo para toggle (reemplaza grafico.ocultos)
        box_leyenda: Si se pasa, agrega el chip al contenedor
    """
    if color_func:
        r, g, b = color_func(nombre)
    elif grafico:
        r, g, b = grafico.colores.get(nombre.lower(), (0.5, 0.5, 0.5))
    else:
        r, g, b = (0.5, 0.5, 0.5)

    chip = Gtk.Box(spacing=10, css_classes=["card", "pill"], valign=Gtk.Align.CENTER)
    chip.set_cursor(Gdk.Cursor.new_from_name("pointer", None))
    if tooltip_text:
        chip.set_has_tooltip(True)
        chip.set_tooltip_text(tooltip_text)

    dot = Gtk.DrawingArea()
    dot.set_content_width(dot_size)
    dot.set_content_height(dot_size)
    dot.set_valign(Gtk.Align.CENTER)
    dot.set_margin_start(8)
    dot.set_draw_func(lambda a, cr, w, h, rr=r, gg=g, bb=b, rd=dot_radius:
                      dibujar_dot(cr, w, h, rr, gg, bb, rd))

    label = Gtk.Label(label=nombre, css_classes=["caption-heading"])
    label.set_margin_end(10)
    label.set_margin_top(4)
    label.set_margin_bottom(4)

    chip.append(dot)
    chip.append(label)

    def _toggle_set(s, ocultos):
        if s in ocultos:
            ocultos.discard(s)
            chip.set_opacity(opacity_visible)
            dot.set_opacity(opacity_visible)
        else:
            ocultos.add(s)
            chip.set_opacity(opacity_hidden)
            dot.set_opacity(opacity_hidden)

    def al_clickear(gesture, n_press, x, y):
        if ocultos_set is not None:
            _toggle_set(nombre, ocultos_set)
            if on_toggle:
                on_toggle(nombre, nombre not in ocultos_set)
        elif grafico is not None:
            _toggle_set(nombre, grafico.ocultos)
            grafico.queue_draw()
            if on_toggle:
                on_toggle(nombre, nombre not in grafico.ocultos)
        elif on_toggle:
            on_toggle(nombre, True)

    click = Gtk.GestureClick()
    click.connect("pressed", al_clickear)
    chip.add_controller(click)

    if box_leyenda is not None:
        box_leyenda.append(chip)

    return chip


def crear_chip_informativo(icon, texto, tooltip=None):
    """Crea un chip informativo no interactivo (icono + texto)."""
    card = Gtk.Box(spacing=4, css_classes=["card", "pill"], valign=Gtk.Align.CENTER)
    card.set_margin_top(2)
    card.set_margin_bottom(2)
    img = Gtk.Image(icon_name=icon, pixel_size=12)
    img.set_valign(Gtk.Align.CENTER)
    img.set_margin_start(8)
    card.append(img)
    lbl = Gtk.Label(label=texto, css_classes=["caption-heading"])
    lbl.set_margin_end(10)
    lbl.set_margin_top(4)
    lbl.set_margin_bottom(4)
    card.append(lbl)
    if tooltip:
        card.set_has_tooltip(True)
        card.set_tooltip_text(tooltip)
    return card
