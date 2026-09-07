"""
Pestaña de scxtop: terminal embebida con el monitor de sched_ext.
"""

import shutil

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, GLib

from utils.i18n import traducir
from utils.logging import registrar_error

try:
    gi.require_version("Vte", "3.91")
    from gi.repository import Vte
    _HAY_VTE = True
except (ImportError, ValueError):
    _HAY_VTE = False
    Vte = None


def _mensaje(texto):
    """Etiqueta centrada para los casos en que no se puede mostrar la terminal."""
    return Gtk.Label(label=texto, css_classes=["dim-label"], wrap=True,
                     justify=Gtk.Justification.CENTER,
                     margin_top=24, margin_bottom=24, margin_start=12,
                     margin_end=12, hexpand=True)


def configurar_pestana_scxtop(win):
    """Crea la página de scxtop (terminal Vte, o un aviso si no se puede)."""
    pagina = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

    if not _HAY_VTE:
        pagina.append(_mensaje(traducir(
            "Vte no está instalado, así que no se puede empotrar la terminal. "
            "Instalá el paquete de introspección de Vte para GTK 4 "
            "(vte4 en Arch, gir1.2-vte-3.91 en Debian y Ubuntu).")))
        return pagina

    ruta = shutil.which("scxtop")
    if not ruta:
        pagina.append(_mensaje(traducir(
            "scxtop no está instalado. Viene con el paquete de planificadores "
            "de tu distribución (scx-scheds en Arch).")))
        return pagina

    terminal = Vte.Terminal(hexpand=True, vexpand=True)

    def _lanzar():
        try:
            # scxtop 1.1.x organiza su funcionalidad en subcomandos (tui, trace,
            # mcp). Se pide "tui" de forma explícita en vez de confiar en el
            # comportamiento por defecto al invocarlo sin argumentos, que ya
            # cambió una vez y puede volver a cambiar.
            terminal.spawn_async(
                Vte.PtyFlags.DEFAULT,
                working_directory=None,
                argv=[ruta, "tui"],
                envv=None,
                spawn_flags=GLib.SpawnFlags.DEFAULT,
                child_setup=None,
                timeout=-1,
            )
        except Exception as e:
            registrar_error("lanzar scxtop", e)
        return False

    pagina.append(terminal)
    win._scxtop_term = terminal
    win._scxtop_path = ruta
    GLib.idle_add(_lanzar)
    return pagina
