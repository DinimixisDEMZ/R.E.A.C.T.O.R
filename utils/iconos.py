import os
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gio", "2.0")
from gi.repository import Gtk, Gdk, Gio

DIR_ICONOS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "icons")
_RUTA_GREOURCE = os.path.join(DIR_ICONOS, "reactor.gresource")


def _display():
    d = Gdk.Display.get_default()
    if d is None:
        d = Gdk.Display.open(None)
    return d


def registrar_ruta_iconos():
    tema = Gtk.IconTheme.get_for_display(_display())
    res_path = "/reactor/icons"
    if os.path.isfile(_RUTA_GREOURCE):
        try:
            res = Gio.Resource.load(_RUTA_GREOURCE)
            Gio.resources_register(res)
            tema.add_resource_path(res_path)
        except Exception:
            pass


def establecer_iconos_idk(usar_idk):
    tema = Gtk.IconTheme.get_for_display(_display())
    paths = list(tema.get_search_path())
    paths = [p for p in paths if p != DIR_ICONOS]
    if usar_idk:
        tema.set_search_path([DIR_ICONOS] + paths)
    else:
        tema.set_search_path(paths + [DIR_ICONOS])


# ── Sidebar ──
CONTROLES = "preferences-system-symbolic"
RENDIMIENTO = "power-profile-performance-symbolic"
AUTOMATIZACION = "network-server-symbolic"
DISPONIBILIDAD = "dialog-information-symbolic"
DIAGNOSTICO = "sonar-symbolic"
HISTORIAL = "document-open-recent-symbolic"

# ── Estado / feedback ──
ERROR = "dialog-error-symbolic"
ADVERTENCIA = "dialog-warning-symbolic"
INFORMACION = "dialog-information-symbolic"
OK = "emblem-ok-symbolic"
PREGUNTA = "dialog-question-symbolic"
PASSWORD = "dialog-password-symbolic"
SINCRONIZANDO = "emblem-synchronizing-symbolic"

# ── Térmico ──
TEMPERATURA = "temperature-symbolic"
TEMPERATURA_CRITICA = "fire-symbolic"

# ── Benchmarks ──
CPU = "input-mouse-symbolic"
HILOS = "system-run-symbolic"
MEMORIA = "network-server-symbolic"
FORK = "preferences-other-symbolic"
COMPILACION = "utilities-terminal-symbolic"
LATENCIA = "weather-clear-night-symbolic"

# ── Acciones ──
REFRESCAR = "view-refresh-symbolic"
ELIMINAR = "user-trash-symbolic"
TERMINAL = "utilities-terminal-symbolic"
ACERCA_DE = "help-about-symbolic"
APP = "application-x-firmware"

# ── Presets ──
BALANCEADO = "object-select-symbolic"
POTENCIA = "power-profile-performance-symbolic"
RESPUESTA = "click-symbolic"
FLUIDEZ = "wind-symbolic"

# ── Hardware / sistema ──
COMPUTADORA = "computer-symbolic"
INGENIERIA = "applications-engineering-symbolic"
DISTRIBUCION = "start-here-symbolic"
HOST = "avatar-default-symbolic"
KERNEL = "system-run-symbolic"
CALCULADORA = "accessories-calculator-symbolic"
EJECUTABLE = "application-x-executable-symbolic"
UTILIDADES = "applications-utilities-symbolic"
NAVEGADOR = "web-browser-symbolic"
FIRMWARE = "application-x-firmware"
DISCO = "drive-harddisk-symbolic"

# ── Historial ──
CALENDARIO = "x-office-calendar-symbolic"
LISTA = "view-list-symbolic"
LISTA_BALAS = "view-list-bullet-symbolic"
CONTINUO = "view-continuous-symbolic"
DESTACADO = "starred-symbolic"
RECIENTES = "document-open-recent-symbolic"

# ── Diagnóstico ──
MONITOR = "accessories-calculator-symbolic"
ENTORNO = "computer-symbolic"

# ── Media ──
REPRODUCIR = "media-playback-start-symbolic"
DETENER = "media-playback-stop-symbolic"
