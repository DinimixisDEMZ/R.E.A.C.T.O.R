"""
SCXCTL - Herramienta de gestión y benchmarking para schedulers sched-ext (SCX)
Versión 5.0.0 | © 2026 UNHARMET

Entry Point: Verificación de dependencias y arranque de la aplicación.
"""

import sys
import shutil
import platform

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw


def main():
    # ── Verificación de Dependencias ──
    errores_criticos = []
    advertencias = []

    # 1. Verificar Linux
    if platform.system() != "Linux":
        errores_criticos.append(
            f"Sistema operativo incompatible: {platform.system()}\n"
            "Esta herramienta solo funciona en Linux con soporte para SCX schedulers."
        )

    # 2. Verificar scxctl
    if not shutil.which("scxctl"):
        errores_criticos.append("scxctl no encontrado. Es necesario para gestionar los planificadores del sistema.")

    # Si hay errores críticos, mostrar diálogo de error y salir
    if errores_criticos:
        _mostrar_error_critico(errores_criticos)
        return 1

    # 3. Verificar stress-ng (advertencia, no crítico)
    if not shutil.which("stress-ng"):
        advertencias.append("stress-ng no encontrado. Los benchmarks no funcionarán. Instálalo con tu gestor de paquetes.")

    for adv in advertencias:
        print(f"⚠️  Advertencia: {adv}")

    # ── Arranque ──
    from app import VentanaSimple

    class MiApp(Adw.Application):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.connect("activate", lambda a: VentanaSimple(a).present())

    app = MiApp(application_id="com.dinimixis.scheduler")
    return app.run(sys.argv)


def _mostrar_error_critico(errores):
    """Muestra una ventana de error para dependencias faltantes."""
    def error_activate(app):
        win = Adw.Window(application=app, title="Error de Sistema")
        win.set_default_size(500, 500)

        status_page = Adw.StatusPage(
            icon_name="dialog-error-symbolic",
            title="Incompatible",
            description="No se cumplen los requisitos necesarios para ejecutar el gestor."
        )

        caja_detalles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)

        lista_errores = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        for err in errores:
            row = Adw.ActionRow(title=err)
            lista_errores.append(row)
        caja_detalles.append(lista_errores)

        btn_salir = Gtk.Button(label="Cerrar Aplicación", halign=Gtk.Align.CENTER)
        btn_salir.add_css_class("destructive-action")
        btn_salir.add_css_class("pill")
        btn_salir.set_margin_top(12)
        btn_salir.connect("clicked", lambda b: app.quit())
        caja_detalles.append(btn_salir)

        clamp = Adw.Clamp(maximum_size=450, tightening_threshold=300)
        clamp.set_child(caja_detalles)
        status_page.set_child(clamp)

        view = Adw.ToolbarView(content=status_page)
        win.set_content(view)
        win.present()

    app_err = Adw.Application(application_id="com.dinimixis.scheduler.error")
    app_err.connect("activate", error_activate)
    app_err.run([])
    sys.exit(1)


if __name__ == "__main__":
    sys.exit(main() or 0)