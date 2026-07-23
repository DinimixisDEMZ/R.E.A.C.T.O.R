"""
REACTOR - Herramienta de gestión y benchmarking para schedulers sched-ext (SCX)
Versión 0.7.0 | © 2026 UNHARMET

Entry Point: Verificación de dependencias y arranque de la aplicación.
"""

import sys
import shutil
import platform

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw


_HERRAMIENTAS = [
    ("scxctl", "scxctl", True, "Gestión de planificadores SCX"),
    ("stress-ng", "stress-ng", False, "Benchmarks de estrés"),
    ("hyperfine", "hyperfine", False, "Benchmarks de precisión"),
]

_CRITICO_SIN_LINUX = (
    "Sistema operativo incompatible: {}\n"
    "Esta herramienta solo funciona en Linux con soporte para SCX schedulers."
)


def _verificar_herramientas():
    criticos = []
    advertencias = []

    if platform.system() != "Linux":
        criticos.append(_CRITICO_SIN_LINUX.format(platform.system()))

    for nombre, binario, critico, proposito in _HERRAMIENTAS:
        if not shutil.which(binario):
            msg = f"{nombre} no encontrado. Necesario para: {proposito}."
            if critico:
                criticos.append(msg)
            else:
                advertencias.append(msg)

    # Verificar run0 o sudo
    if not shutil.which("run0") and not shutil.which("sudo"):
        advertencias.append(
            "No se encontró run0 ni sudo. Las operaciones que requieran "
            "privilegios no estarán disponibles."
        )

    return criticos, advertencias


def main():
    criticos, advertencias = _verificar_herramientas()

    if criticos:
        _mostrar_verificacion(criticos, advertencias, bloqueante=True)
        return 1

    if advertencias:
        _mostrar_verificacion(advertencias, bloqueante=False)

    from app import VentanaSimple

    class MiApp(Adw.Application):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.connect("activate", lambda a: VentanaSimple(a).present())

    app = MiApp(application_id="com.dinimixis.reactor")
    return app.run(sys.argv)


def _mostrar_verificacion(mensajes, advertencias=None, bloqueante=False):
    titulo = "Dependencias faltantes" if bloqueante else "Advertencias"
    desc = (
        "No se cumplen los requisitos necesarios para ejecutar todas las "
        "funcionalidades del gestor."
        if bloqueante else
        "La aplicación puede iniciarse, pero algunas funcionalidades estarán limitadas."
    )
    icono = "dialog-error-symbolic" if bloqueante else "dialog-warning-symbolic"

    def activate(app):
        win = Adw.Window(application=app, title=titulo)
        win.set_default_size(500, 450)

        status = Adw.StatusPage(icon_name=icono, title=titulo, description=desc)
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        lista = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        for msg in mensajes:
            row = Adw.ActionRow(title=msg, subtitle="")
            lista.append(row)
        caja.append(lista)

        if advertencias:
            adv_lista = Gtk.ListBox(
                css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE
            )
            for msg in advertencias:
                row = Adw.ActionRow(title=msg, subtitle="")
                adv_lista.append(row)
            caja.append(adv_lista)

        if not bloqueante:
            btn_cont = Gtk.Button(
                label="Continuar de todas formas",
                halign=Gtk.Align.CENTER,
                css_classes=["suggested-action", "pill"],
            )
            btn_cont.set_margin_top(12)
            btn_cont.connect("clicked", lambda b: (win.close(), app.quit()))
            caja.append(btn_cont)

        btn_salir = Gtk.Button(
            label="Cerrar Aplicación" if bloqueante else "Salir",
            halign=Gtk.Align.CENTER,
            css_classes=["destructive-action", "pill"] if bloqueante else ["pill"],
        )
        btn_salir.set_margin_top(6)
        btn_salir.connect("clicked", lambda b: app.quit())
        caja.append(btn_salir)

        clamp = Adw.Clamp(maximum_size=450, tightening_threshold=300)
        clamp.set_child(caja)
        status.set_child(clamp)

        view = Adw.ToolbarView(content=status)
        win.set_content(view)
        win.present()

    app_id = (
        "com.dinimixis.reactor.error"
        if bloqueante else
        "com.dinimixis.reactor.warning"
    )
    app = Adw.Application(application_id=app_id)
    app.connect("activate", activate)
    app.run([])

    if bloqueante:
        sys.exit(1)


if __name__ == "__main__":
    sys.exit(main() or 0)
