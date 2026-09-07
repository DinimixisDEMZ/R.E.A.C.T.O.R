# Copyright (C) 2026 Dinimixis
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""
REACTOR - Herramienta de gestión y benchmarking para schedulers sched-ext (SCX)
Entry Point: Verificación de dependencias y arranque de la aplicación.
"""

import sys
import shutil
import platform

from utils.i18n import configurar_idioma, traducir
configurar_idioma()

from core.constantes import VERSION

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw


_CRITICO_SIN_LINUX = traducir(
    "Sistema operativo incompatible: {}\n"
    "Esta herramienta solo funciona en Linux con soporte para SCX schedulers."
)


def _verificar_entorno():
    """Decide si la aplicación puede arrancar y con qué limitaciones.

    Antes `scxctl` figuraba como dependencia crítica y su ausencia terminaba en
    `sys.exit(1)`. Eso dejaba la aplicación inservible en cualquier distribución
    que empaquete los planificadores por separado del loader —que son casi
    todas— aunque el kernel tuviera sched_ext y hubiera dieciséis planificadores
    instalados.

    El único requisito real para arrancar es Linux con GTK. Todo lo demás
    limita funciones concretas, y eso se comunica sin bloquear: una aplicación
    que se niega a abrir no le sirve a nadie.
    """
    criticos = []
    advertencias = []

    if platform.system() != "Linux":
        criticos.append(_CRITICO_SIN_LINUX.format(platform.system()))
        return criticos, advertencias

    from core import sched_ext

    if not sched_ext.hay_soporte():
        advertencias.append(traducir(
            "Este kernel no tiene sched_ext (CONFIG_SCHED_CLASS_EXT). No se "
            "pueden aplicar planificadores scx, pero sí medir el planificador "
            "actual del sistema y consultar el historial."))
    else:
        instalados = sched_ext.schedulers_instalados()
        if not instalados:
            advertencias.append(traducir(
                "El kernel soporta sched_ext pero no hay ningún planificador scx "
                "instalado. Instalá el paquete de tu distribución (en Arch: "
                "scx-scheds; en Fedora: kernel-scx-scheds)."))
        elif not shutil.which("scxctl"):
            advertencias.append(traducir(
                "No se encontró scxctl ni el servicio scx_loader. Se usará la "
                "ejecución directa de los planificadores. Para el control "
                "completo instalá el paquete del loader (en Arch: scx-tools)."))

    if not any(shutil.which(h) for h in ("pkexec", "run0", "sudo")):
        advertencias.append(traducir(
            "No se encontró pkexec, run0 ni sudo. Las operaciones que requieren "
            "privilegios no estarán disponibles."))

    for binario, proposito in (
        ("stress-ng", traducir("Benchmarks de estrés")),
        ("hyperfine", traducir("Benchmarks de precisión")),
    ):
        if not shutil.which(binario):
            advertencias.append(
                traducir("{} no encontrado. Necesario para: {}.").format(binario, proposito))

    return criticos, advertencias


def _verificar_importaciones():
    advertencias = []

    try:
        gi.require_version("Vte", "3.91")
        from gi.repository import Vte
    except (ImportError, ValueError):
        advertencias.append(traducir(
            "Vte no encontrado. La pestaña scxtop no estará disponible. "
            "Instale el paquete gir1.2-vte-3.91 o similar."
        ))

    try:
        import cairo
    except ImportError:
        advertencias.append(traducir(
            "Cairo no encontrado. Los gráficos radar no se mostrarán."
        ))

    return advertencias


def main():
    criticos, advertencias = _verificar_entorno()
    advertencias.extend(_verificar_importaciones())

    if criticos:
        _mostrar_verificacion(criticos, advertencias, bloqueante=True)
        return 1

    from app import VentanaSimple

    class MiApp(Adw.Application):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.connect("activate", self._on_activate)

        def _on_activate(self, app):
            from utils.iconos import registrar_ruta_iconos, establecer_iconos_idk
            registrar_ruta_iconos()
            establecer_iconos_idk(True)
            win = VentanaSimple(app)
            win._usar_idk = True
            # Las advertencias ya no abren un diálogo modal antes de arrancar:
            # se muestran dentro de la aplicación, que sigue siendo utilizable.
            win.mostrar_advertencias_arranque(advertencias)
            win.present()

    app = MiApp(application_id="com.dinimixis.reactor")
    return app.run(sys.argv)


def _mostrar_verificacion(mensajes, advertencias=None, bloqueante=False):
    titulo = traducir("Dependencias faltantes") if bloqueante else traducir("Advertencias")
    desc = (
        traducir("No se cumplen los requisitos necesarios para ejecutar todas las "
        "funcionalidades del gestor.")
        if bloqueante else
        traducir("La aplicación puede iniciarse, pero algunas funcionalidades estarán limitadas.")
    )
    icono = "dialog-error-symbolic" if bloqueante else "dialog-warning-symbolic"

    def activate(app):
        win = Adw.Window(application=app, title=titulo)
        win.set_default_size(500, 570)

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
                label=traducir("Continuar de todas formas"),
                halign=Gtk.Align.CENTER,
                css_classes=["suggested-action", "pill"],
            )
            btn_cont.set_margin_top(12)
            btn_cont.connect("clicked", lambda b: (win.close(), app.quit()))
            caja.append(btn_cont)

        btn_salir = Gtk.Button(
            label=traducir("Cerrar Aplicación") if bloqueante else traducir("Salir"),
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
