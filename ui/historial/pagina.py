"""
Página principal del módulo historial — Setup y borrado de historial.
"""

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw

from core.database import eliminar_historial
from .resultados import _crear_pagina_resultados, _reconstruir_chips, _refrescar_historial
from .tendencia import _crear_pagina_tendencia


def configurar_ui_historial(win):
    encabezado = Adw.HeaderBar()

    pila = Adw.ViewStack()

    pag_resultados = _crear_pagina_resultados(win)
    pila.add_titled_with_icon(
        pag_resultados, "resultados",
        "Resultados", "view-list-bullet-symbolic",
    )

    pag_tendencia = _crear_pagina_tendencia(win)
    pila.add_titled_with_icon(
        pag_tendencia, "tendencia",
        "Tendencia", "view-continuous-symbolic",
    )

    cambiador = Adw.InlineViewSwitcher()
    cambiador.add_css_class("round")
    cambiador.set_stack(pila)
    cambiador.set_display_mode(Adw.InlineViewSwitcherDisplayMode.BOTH)
    encabezado.set_title_widget(cambiador)
    pila.set_visible_child_name("resultados")

    btn_borrar = Gtk.Button(
        icon_name="user-trash-symbolic",
        tooltip_text="Borrar todo el historial",
        css_classes=["destructive-action"],
    )
    btn_borrar.connect("clicked", _al_borrar_historial, win)
    encabezado.pack_start(btn_borrar)

    contenido = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    contenido.append(pila)

    vista = Adw.ToolbarView(content=contenido)
    vista.add_top_bar(encabezado)
    win.pag_historial.set_child(vista)

    _reconstruir_chips(win)
    _refrescar_historial(win)


def _al_borrar_historial(btn, win):
    def _on_confirm(response):
        if response == "confirm":
            eliminar_historial()
            _refrescar_historial(win)

    dialog = Adw.AlertDialog(
        heading="¿Borrar todo el historial?",
        body="Esta acción eliminará permanentemente todos los resultados guardados.",
    )
    dialog.add_response("cancel", "Cancelar")
    dialog.add_response("confirm", "Borrar")
    dialog.set_response_appearance("confirm", Adw.ResponseAppearance.DESTRUCTIVE)
    dialog.connect("response", lambda d, r: _on_confirm(r))
    dialog.present(win)
