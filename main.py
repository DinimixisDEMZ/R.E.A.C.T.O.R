"""
REACTOR - Herramienta de gestión y benchmarking para schedulers sched-ext (SCX)
Versión 0.7.0 | © 2026 UNHARMET

Entry Point: Verificación de dependencias y arranque de la aplicación.
"""

import os
import platform
import shutil
import sys
from collections.abc import Collection, Mapping
from dataclasses import dataclass


_BINARIOS_BENCHMARK = ("stress-ng", "hyperfine")
_COMPILADORES_C = ("cc", "gcc", "clang")
_BINARIOS_PRIVILEGIOS = ("sudo", "run0")
_BINARIOS_PREFLIGHT = (
    "scxctl",
    *_BINARIOS_BENCHMARK,
    *_COMPILADORES_C,
    *_BINARIOS_PRIVILEGIOS,
)
_ADW_VERSION_MINIMA = (1, 7, 0)


@dataclass(frozen=True)
class ResultadoPreflight:
    """Resultado de una comprobacion sin efectos secundarios."""

    errores_criticos: tuple[str, ...]
    avisos_benchmark: tuple[str, ...]
    avisos_entorno: tuple[str, ...] = ()

    @property
    def correcto(self):
        return not self.errores_criticos


def comprobar_entorno(
    sistema,
    binarios,
    gtk_disponible,
    es_root=False,
    gtk_error=None,
):
    """Clasifica dependencias usando solo los datos recibidos.

    ``binarios`` puede ser un mapping nombre -> ruta, una coleccion de nombres
    disponibles o ``None``. La funcion no consulta el sistema ni ejecuta
    comandos, lo que permite probarla sin GTK ni herramientas externas.
    """
    if isinstance(binarios, Mapping):
        binarios = {
            nombre: bool(binarios.get(nombre)) for nombre in _BINARIOS_PREFLIGHT
        }
    elif isinstance(binarios, Collection):
        binarios = {
            nombre: nombre in binarios for nombre in _BINARIOS_PREFLIGHT
        }
    else:
        binarios = {nombre: False for nombre in _BINARIOS_PREFLIGHT}

    errores_criticos = []
    avisos_benchmark = []
    avisos_entorno = []

    if sistema != "Linux":
        errores_criticos.append(
            f"Sistema operativo incompatible: {sistema}\n"
            "Esta herramienta solo funciona en Linux con soporte para SCX schedulers."
        )

    if not binarios.get("scxctl"):
        errores_criticos.append(
            "scxctl no encontrado. Es necesario para gestionar los schedulers del sistema."
        )

    if not gtk_disponible:
        errores_criticos.append(
            gtk_error
            or "GTK4/Libadwaita >= 1.7 no disponibles: falta PyGObject "
            "(modulo gi) o una biblioteca GTK del sistema."
        )

    if not binarios.get("stress-ng"):
        avisos_benchmark.append(
            "stress-ng no encontrado. Es requerido para ejecutar benchmarks de estres."
        )

    if not binarios.get("hyperfine"):
        avisos_benchmark.append(
            "hyperfine no encontrado. Es requerido para ejecutar benchmarks de latencia."
        )

    if not any(binarios.get(nombre) for nombre in _COMPILADORES_C):
        avisos_benchmark.append(
            "No se encontró un compilador C (cc, gcc o clang). "
            "El benchmark de compilación no estará disponible."
        )

    if (
        not errores_criticos
        and not es_root
        and not any(binarios[nombre] for nombre in _BINARIOS_PRIVILEGIOS)
    ):
        avisos_entorno.append(
            "El proceso no se ejecuta como root y no se encontraron sudo ni run0. "
            "Los controles y la automatización privilegiados no estarán disponibles."
        )

    return ResultadoPreflight(
        tuple(errores_criticos),
        tuple(avisos_benchmark),
        tuple(avisos_entorno),
    )


def _ejecutando_como_root():
    """Detecta root en plataformas que exponen ``os.geteuid``."""
    geteuid = getattr(os, "geteuid", None)
    if not callable(geteuid):
        return False
    try:
        return geteuid() == 0
    except (OSError, TypeError):
        return False


def _detectar_binarios():
    """Obtiene las rutas de las herramientas sin ejecutarlas."""
    return {nombre: shutil.which(nombre) for nombre in _BINARIOS_PREFLIGHT}


def _activar_ventana_principal(app, ventana_cls):
    """Presenta la ventana existente o crea la unica ventana principal."""
    ventana = app.get_active_window()
    creada = ventana is None
    if ventana is None:
        ventana = ventana_cls(app)
    ventana.present()
    if creada:
        iniciar = getattr(ventana, "iniciar_inicializacion", None)
        if callable(iniciar):
            iniciar()
    return ventana


def _validar_version_adw(Adw):
    """Comprueba la version de Libadwaita sin aceptar un valor por defecto."""
    nombres_getters = (
        "get_major_version",
        "get_minor_version",
        "get_micro_version",
    )
    getters = [getattr(Adw, nombre, None) for nombre in nombres_getters]
    if not all(callable(getter) for getter in getters):
        return (
            "No se pudo comprobar la version de Libadwaita: la API no expone "
            "get_major_version(), get_minor_version() y get_micro_version()."
        )

    try:
        version = tuple(int(getter()) for getter in getters)
    except Exception as exc:
        return f"No se pudo comprobar la version de Libadwaita: {exc}"

    if version < _ADW_VERSION_MINIMA:
        version_text = ".".join(str(part) for part in version)
        return (
            f"Libadwaita {version_text} no es compatible: se requiere "
            "Libadwaita >= 1.7.0 porque la interfaz utiliza Adw.WrapBox."
        )

    return None


def _cargar_gtk():
    """Carga GTK solo despues de poder capturar los errores de importacion."""
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Gtk, Adw
        version_error = _validar_version_adw(Adw)
        if version_error:
            return None, version_error
    except (ImportError, OSError, ValueError) as exc:
        return None, f"No se pudo cargar PyGObject/GTK4/Libadwaita: {exc}"

    return (Gtk, Adw), None


def main():
    sistema = platform.system()
    binarios = _detectar_binarios()
    gtk_modules, gtk_error = _cargar_gtk()
    resultado = comprobar_entorno(
        sistema,
        binarios,
        gtk_modules is not None,
        es_root=_ejecutando_como_root(),
        gtk_error=gtk_error,
    )

    if resultado.avisos_entorno:
        _mostrar_avisos_entorno(resultado.avisos_entorno)

    if resultado.avisos_benchmark:
        _mostrar_avisos_benchmark(resultado.avisos_benchmark)

    if not resultado.correcto:
        if gtk_modules is None:
            _mostrar_error_sin_gtk(resultado.errores_criticos, gtk_error)
        else:
            _mostrar_error_critico(resultado.errores_criticos, gtk_modules)
        return 1

    # ── Arranque ──
    _, Adw = gtk_modules
    from app import VentanaSimple

    def activar_ventana(app):
        _activar_ventana_principal(app, VentanaSimple)

    class MiApp(Adw.Application):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.connect("activate", activar_ventana)

    app = MiApp(application_id="com.dinimixis.reactor")
    return app.run(sys.argv)


def _mostrar_avisos_benchmark(avisos):
    """Informa de herramientas opcionales para el arranque de la UI."""
    for aviso in avisos:
        print(f"Advertencia de benchmark: {aviso}", file=sys.stderr)


def _mostrar_avisos_entorno(avisos):
    """Informa de capacidades del entorno que no bloquean la UI."""
    for aviso in avisos:
        print(f"Advertencia de entorno: {aviso}", file=sys.stderr)


def _mostrar_error_sin_gtk(errores, detalle=None):
    """Emite un diagnostico util cuando no se puede crear el dialogo GTK."""
    print(
        "No se puede iniciar R.E.A.C.T.O.R porque GTK/PyGObject no esta disponible.",
        file=sys.stderr,
    )
    for error in errores:
        print(f"- {error}", file=sys.stderr)
    if detalle and not any(detalle in error for error in errores):
        print(f"Detalle de carga de GTK: {detalle}", file=sys.stderr)


def _mostrar_error_critico(errores, gtk_modules):
    """Muestra una ventana de error para dependencias faltantes."""
    Gtk, Adw = gtk_modules

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
