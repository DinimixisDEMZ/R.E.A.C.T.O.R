"""
Widget de leyenda interactiva para la gráfica de detección.
Controles enfocables que permiten ocultar/mostrar schedulers.
"""

import math

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Gdk


def calcular_estado_leyenda(nombre, ocultos, visible=None):
    """Calcula el estado visual y la copia actualizada de ocultos.

    La colección recibida nunca se modifica. Si ``visible`` no se proporciona,
    se infiere a partir de ``nombre`` y ``ocultos``; al cambiarlo se devuelve la
    transición completa que necesita el chip.
    """
    ocultos_nuevos = set(ocultos)
    visible = nombre not in ocultos_nuevos if visible is None else bool(visible)

    if visible:
        ocultos_nuevos.discard(nombre)
        opacidad = 1.0
        opacidad_indicador = 1.0
    else:
        ocultos_nuevos.add(nombre)
        opacidad = 0.4
        opacidad_indicador = 0.3

    return visible, opacidad, opacidad_indicador, ocultos_nuevos


def _actualizar_nombre_accesible(widget, texto):
    """Actualiza el nombre accesible con la API disponible de GTK4."""
    setter = getattr(widget, "set_accessible_name", None)
    if callable(setter):
        setter(texto)
        return

    update_property = getattr(widget, "update_property", None)
    accessible_property = getattr(Gtk, "AccessibleProperty", None)
    label_property = getattr(accessible_property, "LABEL", None)
    if not callable(update_property) or label_property is None:
        return

    try:
        update_property([label_property], [texto])
    except TypeError:
        # Permite stubs y bindings que exponen la variante escalar.
        update_property(label_property, texto)


def _aplicar_estado(chip, indicador, nombre, estado):
    visible, opacidad, opacidad_indicador, _ = estado
    chip.set_active(visible)
    chip.set_opacity(opacidad)
    indicador.set_opacity(opacidad_indicador)

    accion = "Ocultar" if visible else "Mostrar"
    texto = f"{accion} scheduler {nombre}"
    chip.set_tooltip_text(texto)
    _actualizar_nombre_accesible(chip, texto)


def crear_chip_leyenda(nombre, grafico, box_leyenda):
    """Crea un control de leyenda accesible para un scheduler.
    
    Args:
        nombre: Nombre del scheduler
        grafico: Instancia de GraficoComparativo
        box_leyenda: Contenedor WrapBox donde añadir el chip
    """
    r, g, b = grafico.colores.get(nombre.lower(), (0.5, 0.5, 0.5))

    chip = Gtk.ToggleButton(css_classes=["card", "pill"])
    chip.set_cursor(Gdk.Cursor.new_from_name("pointer", None))

    contenido = Gtk.Box(spacing=10)

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

    contenido.append(dot)
    contenido.append(label)
    chip.set_child(contenido)

    estado = calcular_estado_leyenda(nombre, grafico.ocultos)
    _aplicar_estado(chip, dot, nombre, estado)

    def al_togglear(button, *_args):
        estado = calcular_estado_leyenda(
            nombre,
            grafico.ocultos,
            button.get_active(),
        )
        grafico.ocultos.clear()
        grafico.ocultos.update(estado[3])
        _aplicar_estado(button, dot, nombre, estado)
        grafico.queue_draw()

    chip.connect("toggled", al_togglear)

    box_leyenda.append(chip)
    return chip
