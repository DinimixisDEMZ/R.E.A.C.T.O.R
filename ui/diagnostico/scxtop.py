"""
scxtop tab: embedded terminal running the scxtop scheduler visualizer.
"""

import subprocess

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, GLib

try:
    gi.require_version("Vte", "3.91")
    from gi.repository import Vte
    _HAS_VTE = True
except (ImportError, ValueError):
    _HAS_VTE = False
    Vte = None


def configurar_pestana_scxtop(win):
    """Create the scxtop page (Vte.Terminal or fallback label).
    Returns the page widget."""
    pagina_scxtop = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

    if not _HAS_VTE:
        pagina_scxtop.append(Gtk.Label(
            label="Vte no instalado — instale gir1.2-vte-3.91 para scxtop",
            css_classes=["dim-label"],
            margin_top=24, margin_bottom=24, hexpand=True))
        return pagina_scxtop

    ruta_scxtop = subprocess.run(["which", "scxtop"], capture_output=True, text=True).stdout.strip()
    if ruta_scxtop:
        terminal = Vte.Terminal(hexpand=True, vexpand=True)
        def _lanzar_scxtop():
            terminal.spawn_async(
                Vte.PtyFlags.DEFAULT,
                working_directory=None,
                argv=[ruta_scxtop],
                envv=None,
                spawn_flags=GLib.SpawnFlags.DEFAULT,
                child_setup=None,
                timeout=-1,
            )
        pagina_scxtop.append(terminal)
        win._scxtop_term = terminal
        win._scxtop_path = ruta_scxtop
        GLib.idle_add(_lanzar_scxtop)
    else:
        pagina_scxtop.append(Gtk.Label(
            label="scxtop no instalado", css_classes=["dim-label"],
            margin_top=24, margin_bottom=24, hexpand=True))

    return pagina_scxtop
