import os
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Gdk

DIR_ICONOS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "icons")
_PATHS_ORIGINALES = None


def _display():
    d = Gdk.Display.get_default()
    if d is None:
        d = Gdk.Display.open(None)
    return d

def registrar_ruta_iconos():
    global _PATHS_ORIGINALES
    tema = Gtk.IconTheme.get_for_display(_display())
    _PATHS_ORIGINALES = list(tema.get_search_path())


def establecer_iconos_idk(usar_idk):
    tema = Gtk.IconTheme.get_for_display(_display())
    paths = list(tema.get_search_path())
    if usar_idk:
        if DIR_ICONOS not in paths:
            tema.set_search_path([DIR_ICONOS] + paths)
    else:
        if DIR_ICONOS in paths:
            paths.remove(DIR_ICONOS)
            tema.set_search_path(paths)


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
TEMPERATURA_CRITICA = "software-update-urgent-symbolic"

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
RESPUESTA = "preferences-system-time-symbolic"
FLUIDEZ = "weather-windy-symbolic"

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
