"""
Motor de verificación del sistema.
Ejecuta chequeos de herramientas, comandos y dependencias al arrancar.
En modo AppImage solo usa herramientas internas (bundleadas).
"""

import os
import json
import shutil
import sys
import subprocess
from typing import Callable
from dataclasses import dataclass
from pathlib import Path



@dataclass
class Resultado:
    exito: bool
    mensaje: str = ""
    detalles: str = ""
    sugerencia: str = ""


@dataclass
class Verificacion:
    id: str
    nombre: str
    ejecutar: Callable[[], Resultado]
    sugerencia: str = ""
    critico: bool = True


_RESULTADOS: list[tuple[Verificacion, Resultado]] = []


# ─── Modo de ejecución ───


def _modo_appimage() -> bool:
    return "APPDIR" in os.environ and bool(os.environ.get("APPDIR"))


# ─── Chequeos individuales ───


def _check_binario(nombre_bin: str, nombre_mostrar: str = "") -> Resultado:
    nombre = nombre_mostrar or nombre_bin
    if _modo_appimage():
        from utils.helpers import ruta_bundleada
        bundle = ruta_bundleada(f"usr/bin/{nombre_bin}")
        if bundle:
            return Resultado(True, f"{bundle} (bundle)")
        return Resultado(False, f"{nombre} no incluido en el AppImage",
                         sugerencia="El AppImage se construyó sin este binario. Revisá los logs del CI.")
    ruta = shutil.which(nombre_bin)
    if ruta:
        return Resultado(True, f"{ruta}")
    return Resultado(False, "No encontrado en el sistema",
                     sugerencia=_sugerir_instalacion(nombre_bin))


def _check_version(nombre_bin: str, args: list[str] | None = None) -> Resultado:
    try:
        r = subprocess.run([nombre_bin] + (args or ["--version"]), capture_output=True, text=True, timeout=10)
        salida = (r.stdout or r.stderr or "").strip()
        if salida:
            linea = salida.split("\n")[0]
            resto = "\n".join(salida.split("\n")[1:5]) if len(salida.split("\n")) > 1 else ""
            return Resultado(True, linea, detalles=resto)
        return Resultado(False, "No devolvió versión")
    except FileNotFoundError:
        ruta = shutil.which(nombre_bin) or "(no encontrado en PATH)"
        return Resultado(False, f"Binario no encontrado",
                         detalles=f"ruta buscada: {ruta}",
                         sugerencia=f"Asegurate de que {nombre_bin} esté instalado, tenga el loader correcto y esté en PATH")
    except subprocess.TimeoutExpired:
        return Resultado(False, f"Tiempo de espera agotado para {nombre_bin} --version",
                         sugerencia="El binario puede estar corrupto o colgado")
    except (OSError, subprocess.SubprocessError) as e:
        ruta = shutil.which(nombre_bin) or "?"
        return Resultado(False, str(e)[:80], detalles=f"ruta: {ruta}\n{type(e).__name__}: {e}")


def _check_comando(cmd: list[str], parser: Callable | None = None, desc: str = "") -> Resultado:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip()[:200]
            return Resultado(False, f"Código {r.returncode}: {err}")
        salida = r.stdout.strip()
        if parser:
            if parser(salida):
                return Resultado(True, "Formato correcto")
            return Resultado(False, f"Formato inesperado:\n{salida[:300]}")
        return Resultado(True, salida[:200])
    except FileNotFoundError:
        return Resultado(False, "Comando no encontrado")
    except subprocess.TimeoutExpired:
        return Resultado(False, "Tiempo de espera agotado (15s)")
    except (OSError, subprocess.SubprocessError) as e:
        return Resultado(False, str(e))


def _detectar_distribucion() -> str:
    """Identificador de la distribución, para dar una sugerencia útil de instalación."""
    datos = {}
    try:
        with open("/etc/os-release", errors="replace") as f:
            for linea in f:
                if "=" in linea:
                    clave, valor = linea.split("=", 1)
                    datos[clave.strip()] = valor.strip().strip('"')
    except OSError:
        return ""
    return (datos.get("ID") or "").lower()


#: Cómo se instala cada cosa en cada distribución. Antes se sugería siempre
#: `sudo eopkg install`, que sólo funciona en Solus — la distribución del autor.
#: Nótese que `scxctl` y `scx_loader` viajan en un paquete distinto al de los
#: planificadores en casi todas, y que en varias directamente no se empaquetan.
_PAQUETES = {
    "arch":         {"gestor": "sudo pacman -S {}", "scx": "scx-scheds", "loader": "scx-tools"},
    "endeavouros":  {"gestor": "sudo pacman -S {}", "scx": "scx-scheds", "loader": "scx-tools"},
    "cachyos":      {"gestor": "sudo pacman -S {}", "scx": "scx-scheds", "loader": "scx-scheds"},
    "manjaro":      {"gestor": "sudo pacman -S {}", "scx": "scx-scheds", "loader": "scx-tools"},
    "fedora":       {"gestor": "sudo dnf install {}", "scx": "kernel-scx-scheds", "loader": None},
    "solus":        {"gestor": "sudo eopkg install {}", "scx": "scx-scheds", "loader": "scx-scheds"},
    "opensuse-tumbleweed": {"gestor": "sudo zypper install {}", "scx": "scx-scheds", "loader": None},
    "ubuntu":       {"gestor": "sudo apt install {}", "scx": "scx", "loader": None},
    "debian":       {"gestor": "sudo apt install {}", "scx": "scx", "loader": None},
    "gentoo":       {"gestor": "sudo emerge {}", "scx": "sys-kernel/scx", "loader": "sys-kernel/scx"},
    "nixos":        {"gestor": "nix-env -iA nixpkgs.{}", "scx": "scx", "loader": "scx"},
}


def _receta_distribucion() -> dict:
    return _PAQUETES.get(_detectar_distribucion(), {})


def _sugerir_instalacion(binario: str) -> str:
    """Sugerencia de instalación adaptada a la distribución en uso."""
    receta = _receta_distribucion()
    if not receta:
        return f"Instalá {binario} con el gestor de paquetes de tu distribución."
    return f"Instalá {binario}: {receta['gestor'].format(binario)}"


# ─── Chequeos del entorno sched_ext ───


def _check_sched_ext() -> Resultado:
    """El único requisito de kernel real: CONFIG_SCHED_CLASS_EXT.

    Se comprueba leyendo sysfs, no preguntándole a `scxctl`: el binario puede
    faltar en un equipo perfectamente capaz, y su presencia tampoco garantiza
    que se pueda controlar nada.
    """
    from core import sched_ext
    if not sched_ext.hay_soporte():
        return Resultado(
            False, "El kernel no expone /sys/kernel/sched_ext",
            detalles="La aplicación funciona igual, pero no puede aplicar planificadores scx.",
            sugerencia="Necesitás un kernel compilado con CONFIG_SCHED_CLASS_EXT=y "
                       "(6.12 o posterior). En Arch sirven linux-cachyos o linux-zen.")
    info = sched_ext.estado()
    detalle = f"estado: {info['estado']}"
    if info["scheduler"]:
        detalle += f" — activo: {info['scheduler']}"
        if info["parcial"]:
            detalle += " (modo parcial)"
    return Resultado(True, "/sys/kernel/sched_ext disponible", detalles=detalle)


def _check_schedulers_instalados() -> Resultado:
    from core import sched_ext
    instalados = sched_ext.schedulers_instalados()
    if not instalados:
        receta = _receta_distribucion()
        return Resultado(
            False, "No se encontró ningún planificador scx en el PATH",
            sugerencia=_sugerir_instalacion(receta.get("scx") or "scx-scheds"))
    nombres = ", ".join(sorted(n.removeprefix("scx_") for n in instalados))
    return Resultado(True, f"{len(instalados)} planificadores encontrados", detalles=nombres)


def _check_servicio_loader() -> Resultado:
    """Estado del servicio D-Bus que gestiona los planificadores.

    No es crítico: sin él se usa la ejecución directa. Pero distinguir entre
    "no está instalado", "está y se activa solo" y "está corriendo" cambia por
    completo qué conviene sugerirle al usuario.
    """
    from core.scx import BackendDbus
    estado_servicio = BackendDbus.estado_del_servicio()
    if estado_servicio == "activo":
        return Resultado(True, "org.scx.Loader activo en el bus del sistema")
    if estado_servicio == "activable":
        return Resultado(True, "org.scx.Loader instalado (se activa bajo demanda)")
    receta = _receta_distribucion()
    paquete = receta.get("loader")
    if paquete and receta.get("gestor"):
        sugerencia = f"Para el control completo instalá el loader: {receta['gestor'].format(paquete)}"
    else:
        sugerencia = ("Tu distribución no empaqueta scx_loader. Se usará la "
                      "ejecución directa de los binarios.")
    return Resultado(False, "scx_loader no está disponible",
                     detalles="Se usará la ejecución directa de los planificadores.",
                     sugerencia=sugerencia)


def _check_backend_control() -> Resultado:
    """¿Puede la aplicación aplicar un planificador en este equipo?"""
    from core.scx import GestorScx
    gestor = GestorScx()
    if gestor.backend is None:
        return Resultado(
            False, "No hay ningún backend de control disponible",
            detalles="Se pueden ejecutar mediciones y consultar el historial, "
                     "pero no cambiar el planificador.",
            sugerencia="Instalá scx_loader, o asegurate de tener pkexec o run0.")
    etiquetas = {
        "dbus": "D-Bus (scx_loader), autorizado por polkit",
        "scxctl": "binario scxctl",
        "directo": "ejecución directa con systemd-run",
        "simulado": "modo simulación",
    }
    return Resultado(True, etiquetas.get(gestor.nombre_backend, gestor.nombre_backend),
                     detalles="operaciones: " + ", ".join(sorted(gestor.backend.capacidades)))


def _check_privilegios() -> Resultado:
    """Cómo se elevan privilegios en este equipo.

    La aplicación ya no pide la contraseña: la solicita el agente del escritorio
    a través de polkit, en el momento exacto de la operación.
    """
    disponibles = [h for h in ("pkexec", "run0") if shutil.which(h)]
    if disponibles:
        return Resultado(True, " y ".join(disponibles) + " disponibles",
                         detalles="La autenticación la muestra el agente polkit del escritorio.")
    if shutil.which("sudo"):
        return Resultado(True, "sólo sudo",
                         detalles="Se usa únicamente si ya hay una sesión activa; "
                                  "la aplicación nunca pide la contraseña.",
                         sugerencia="Instalá polkit (pkexec) para el flujo habitual del escritorio.")
    return Resultado(False, "No hay forma de elevar privilegios",
                     sugerencia="Instalá polkit (pkexec), o usá un sistema con systemd (run0).")


def _check_scxctl() -> Resultado:
    """`scxctl` ya no es obligatorio: es un cliente más del loader.

    Viaja en un paquete distinto al de los planificadores en casi todas las
    distribuciones, y su ausencia no impide observar ni controlar nada.
    """
    ruta = shutil.which("scxctl")
    if ruta:
        return Resultado(True, ruta)
    return Resultado(False, "No instalado",
                     detalles="No hace falta: la aplicación habla con el loader por "
                              "D-Bus, o ejecuta los binarios directamente.")


def _check_sensor_termico() -> Resultado:
    from core.thermal import SensorTermico
    sensor = SensorTermico()
    if not sensor.hay_sensor():
        return Resultado(False, "No se encontró un sensor de temperatura del CPU",
                         detalles="Las mediciones se ejecutan igual, pero no se puede "
                                  "descartar la influencia del calor en los resultados.")
    return Resultado(True, f"{sensor.nombre_sensor} — {sensor.obtener_temp():.1f} °C",
                     detalles=f"límite declarado por el hardware: {sensor.tjmax():.0f} °C")


def _check_hardware() -> Resultado:
    from core.hardware import obtener_perfil
    perfil = obtener_perfil()
    detalles = [f"tipo: {perfil.tipo_equipo}",
                f"gobernador: {perfil.energia.get('governor')}",
                f"driver: {perfil.energia.get('driver')}"]
    if perfil.es_hibrida:
        detalles.append(f"híbrida: {perfil.nucleos_rendimiento}P + {perfil.nucleos_eficiencia}E")
    if perfil.es_virtual:
        detalles.append(f"virtualización: {perfil.virtualizacion}")
    if perfil.energia.get("en_bateria"):
        detalles.append("funcionando con batería")
    return Resultado(True, perfil.descripcion_corta(), detalles=" · ".join(detalles))


def _check_sistema(nombre_bin: str) -> Resultado:
    """Verifica un binario en el sistema ignorando modo AppImage."""
    ruta = shutil.which(nombre_bin)
    if ruta:
        return Resultado(True, f"{ruta}")
    return Resultado(False, "No encontrado en el sistema",
                     sugerencia=_sugerir_instalacion(nombre_bin))


def _check_rt_tests() -> Resultado:
    if _modo_appimage():
        from utils.helpers import ruta_bundleada
        bundle = ruta_bundleada("usr/share/reactor/rt-tests")
        if bundle:
            return Resultado(True, "rt-tests source (bundle)")
        binario = ruta_bundleada("usr/bin/cyclictest")
        if binario:
            return Resultado(True, f"cyclictest (bundle, sin source para compile)")
        return Resultado(False, "rt-tests no incluido en el AppImage")
    if os.path.isfile("/tmp/rt-tests/Makefile"):
        return Resultado(True, "rt-tests source en /tmp/rt-tests")
    ruta = shutil.which("cyclictest")
    if ruta:
        return Resultado(True, f"{ruta}")
    return Resultado(False, "No encontrado (ni source ni binario)")


def _check_gresource() -> Resultado:
    ruta = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "icons", "reactor.gresource")
    if os.path.isfile(ruta):
        return Resultado(True, f"{ruta}")
    return Resultado(False, "reactor.gresource no encontrado",
                     sugerencia="Ejecutá: glib-compile-resources data/icons/reactor.gresource.xml")


def _check_dependencias_python() -> Resultado:
    """Comprueba que las librerías gráficas estén realmente disponibles.

    Antes se miraba si los módulos figuraban en `sys.modules`, lo que daba un
    falso negativo en cuanto se ejecutaba la verificación fuera de la interfaz
    y un falso positivo para lo que aún no se hubiera importado. Ahora se
    intenta importar de verdad, que es la única comprobación que significa algo.
    """
    faltantes = []
    detalles = []
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gdk, Gio, GLib, Gtk
        detalles.append(f"GTK {Gtk.MAJOR_VERSION}.{Gtk.MINOR_VERSION}.{Gtk.MICRO_VERSION}")
        detalles.append(f"Libadwaita {Adw.MAJOR_VERSION}.{Adw.MINOR_VERSION}.{Adw.MICRO_VERSION}")
    except (ImportError, ValueError) as e:
        faltantes.append(f"GTK4/Libadwaita ({e})")

    # Opcionales: limitan una función concreta, no impiden usar la aplicación.
    opcionales = []
    try:
        import cairo  # noqa: F401
    except ImportError:
        opcionales.append("cairo (sin gráficos radar ni medidores)")
    try:
        import gi
        gi.require_version("Vte", "3.91")
        from gi.repository import Vte  # noqa: F401
    except (ImportError, ValueError):
        opcionales.append("Vte (sin terminal scxtop embebida)")

    if faltantes:
        return Resultado(False, "Faltan: " + ", ".join(faltantes),
                         sugerencia="Instalá python-gobject, gtk4 y libadwaita.")
    if opcionales:
        detalles.append("opcionales ausentes: " + ", ".join(opcionales))
    return Resultado(True, " · ".join(detalles[:2]), detalles=" · ".join(detalles[2:]))


def _check_appimage_integridad() -> Resultado:
    appdir = os.environ.get("APPDIR", "")
    if not appdir or not os.path.isdir(appdir):
        return Resultado(False, "$APPDIR no apunta a un directorio válido")
    required = [
        ("usr/bin/hyperfine", "hyperfine"),
        ("usr/bin/cyclictest", "cyclictest"),
        ("usr/share/reactor/rt-tests/Makefile", "rt-tests source"),
        ("usr/share/reactor/main.py", "entrada principal"),
    ]
    fallos = [desc for subpath, desc in required if not os.path.isfile(os.path.join(appdir, subpath))]
    if fallos:
        return Resultado(False, f"Faltan: {', '.join(fallos)}",
                         sugerencia="El AppImage está corrupto o mal construido")
    return Resultado(True, "Estructura AppImage intacta")


# ─── Registro maestro ───

VERIFICACIONES: list[Verificacion] = [
    # Sólo dos cosas son realmente críticas: que el kernel tenga sched_ext y que
    # GTK esté cargado. Todo lo demás limita funciones concretas y se informa
    # sin bloquear. Antes había tres chequeos de `scxctl` marcados como críticos,
    # y bastaba con que faltara ese binario —que ni siquiera es necesario— para
    # que el diálogo deshabilitara el botón de continuar.
    Verificacion("sched_ext", "Soporte sched_ext en el kernel", _check_sched_ext, critico=True),
    Verificacion("python_deps", "Dependencias Python", _check_dependencias_python, critico=True),

    # Entorno de control, en cascada de degradación.
    Verificacion("schedulers", "Planificadores scx instalados", _check_schedulers_instalados, critico=False),
    Verificacion("loader", "Servicio scx_loader", _check_servicio_loader, critico=False),
    Verificacion("backend", "Backend de control activo", _check_backend_control, critico=False),
    Verificacion("privilegios", "Elevación de privilegios", _check_privilegios, critico=False),
    Verificacion("scxctl", "Cliente scxctl (opcional)", _check_scxctl, critico=False),

    # Entorno del equipo.
    Verificacion("hardware", "Hardware detectado", _check_hardware, critico=False),
    Verificacion("sensor_termico", "Sensor térmico", _check_sensor_termico, critico=False),
    Verificacion("gresource", "Iconos empaquetados (GResource)", _check_gresource, critico=False),
]

# ── Herramientas de medición: opcionales, cada una limita una prueba ──
_VERIFICACIONES_HERRAMIENTAS: list[Verificacion] = [
    Verificacion("stressng_bin", "stress-ng instalado", lambda: _check_sistema("stress-ng"), critico=False),
    Verificacion("stressng_version", "Versión de stress-ng", lambda: _check_version("stress-ng", ["--version"]), critico=False),
    Verificacion("hyperfine_bin", "hyperfine instalado", lambda: _check_binario("hyperfine"), critico=False),
    Verificacion("hyperfine_version", "Versión de hyperfine", lambda: _check_version("hyperfine", ["--version"]), critico=False),
    Verificacion("rt_tests", "rt-tests (benchmark de compilación)", _check_rt_tests, critico=False),
]

# ── Autodiagnóstico del AppImage (sólo dentro del bundle) ──
if _modo_appimage():
    VERIFICACIONES.append(
        Verificacion("appimage_integridad", "Integridad del AppImage",
                     _check_appimage_integridad, critico=True)
    )

VERIFICACIONES.extend(_VERIFICACIONES_HERRAMIENTAS)


def ejecutar_verificaciones(solo_ids: list[str] | None = None) -> list[tuple[Verificacion, Resultado]]:
    global _RESULTADOS
    _RESULTADOS = []
    for v in VERIFICACIONES:
        if solo_ids and v.id not in solo_ids:
            continue
        try:
            res = v.ejecutar()
        except Exception as e:
            res = Resultado(False, f"Excepción: {e}")
        _RESULTADOS.append((v, res))
    return _RESULTADOS


def obtener_resultados() -> list[tuple[Verificacion, Resultado]]:
    return _RESULTADOS


def todo_critico_ok(resultados: list[tuple[Verificacion, Resultado]] | None = None) -> bool:
    if resultados is None:
        resultados = _RESULTADOS
    return all(res.exito for v, res in resultados if v.critico)


def resumen_critico(resultados: list[tuple[Verificacion, Resultado]] | None = None) -> str:
    if resultados is None:
        resultados = _RESULTADOS
    ok = sum(1 for v, res in resultados if v.critico and res.exito)
    total = sum(1 for v, _ in resultados if v.critico)
    return f"{ok}/{total}"


# ─── Persistencia del flag de verificación ───

_RUTA_CONFIG = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
    "reactor", "config.json"
)


def _cargar_config() -> dict:
    try:
        if os.path.isfile(_RUTA_CONFIG):
            with open(_RUTA_CONFIG) as f:
                return json.load(f)
    except (OSError, ValueError):
        pass
    return {}


def _guardar_config(actualizar: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_RUTA_CONFIG), exist_ok=True)
        cfg = _cargar_config()
        cfg.update(actualizar)
        with open(_RUTA_CONFIG, "w") as f:
            json.dump(cfg, f)
    except (OSError, ValueError):
        pass


def verificar_si_primera_vez() -> bool:
    cfg = _cargar_config()
    return not cfg.get("verificacion_hecha", False)


def marcar_verificacion_hecha() -> None:
    _guardar_config({"verificacion_hecha": True})
