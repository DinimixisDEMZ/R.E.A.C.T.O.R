"""
Main Diagnóstico page: assembles Monitor, scxtop, and Entorno tabs
with a ViewStack and InlineViewSwitcher.
"""

import subprocess

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib

from ui.diagnostico.monitoreo import configurar_pestana_monitor, actualizar_diagnostico_tiempo_real
from ui.diagnostico.scxtop import configurar_pestana_scxtop, _HAS_VTE, Vte
from ui.historial.entorno import _crear_pagina_entorno
from utils.i18n import traducir


def configurar_ui_diagnostico(win):
    encabezado = Adw.HeaderBar()

    pagina_pref, controles = configurar_pestana_monitor(win)

    pila = Adw.ViewStack()
    pila.add_titled_with_icon(pagina_pref, "monitor", traducir("Monitor"), "accessories-calculator-symbolic")

    if _HAS_VTE:
        pagina_scxtop = configurar_pestana_scxtop(win)
        pila.add_titled_with_icon(pagina_scxtop, "scxtop", traducir("scxtop"), "utilities-terminal-symbolic")

    pila.add_titled_with_icon(_crear_pagina_entorno(win), "entorno", traducir("Entorno"), "computer-symbolic")

    cambiador = Adw.InlineViewSwitcher()
    cambiador.add_css_class("round")
    cambiador.set_stack(pila)
    cambiador.set_display_mode(Adw.InlineViewSwitcherDisplayMode.BOTH)
    encabezado.set_title_widget(cambiador)
    pila.set_visible_child_name("monitor")

    # scxtop lifecycle on tab switch (only if Vte is available)
    if _HAS_VTE:
        def _al_cambiar_pila(*_):
            nombre = pila.get_visible_child_name()
            ruta_scxtop = getattr(win, "_scxtop_path", None)
            if ruta_scxtop:
                if nombre == "scxtop":
                    terminal = getattr(win, "_scxtop_term", None)
                    if terminal:
                        subprocess.run(["pkill", "-f", "scxtop"], capture_output=True)
                        GLib.timeout_add(300, lambda: (
                            terminal.spawn_async(
                                Vte.PtyFlags.DEFAULT,
                                working_directory=None,
                                argv=[ruta_scxtop],
                                envv=None,
                                spawn_flags=GLib.SpawnFlags.DEFAULT,
                                child_setup=None,
                                timeout=-1,
                            )
                        ))
                else:
                    subprocess.run(["pkill", "-f", "scxtop"], capture_output=True)
        pila.connect("notify::visible-child", _al_cambiar_pila)

    vista = Adw.ToolbarView(content=pila)
    vista.add_top_bar(encabezado)

    win.pag_diagnostico = Adw.NavigationPage(title=traducir("Diagnóstico"), tag="page_e")
    win.pag_diagnostico.set_child(vista)

    GLib.timeout_add(1500, actualizar_diagnostico_tiempo_real, win, controles)

    return win.pag_diagnostico
