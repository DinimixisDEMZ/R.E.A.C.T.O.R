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

from utils.helpers import RE_JSON_ARRAY


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
                     sugerencia=f"Instalá {nombre_bin} con tu gestor de paquetes (ej: 'sudo eopkg install {nombre_bin}')")


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


def _parse_scxctl_list(salida: str) -> bool:
    m = RE_JSON_ARRAY.search(salida)
    if not m:
        return False
    try:
        datos = json.loads(m.group())
        return isinstance(datos, list) and len(datos) > 0 and all(isinstance(x, str) for x in datos)
    except (json.JSONDecodeError, TypeError):
        return False


def _parse_scxctl_get(salida: str) -> bool:
    return len(salida.strip()) > 0


def _check_sudo() -> Resultado:
    ruta_sudo = shutil.which("sudo")
    ruta_run0 = shutil.which("run0")
    if not ruta_sudo and not ruta_run0:
        return Resultado(False, "No disponible (se requiere sudo o run0)")
    if ruta_sudo:
        try:
            r = subprocess.run(["sudo", "-n", "true"], capture_output=True, timeout=5)
            if r.returncode == 0:
                return Resultado(True, "sudo — sesión activa")
            return Resultado(True, "sudo — disponible (requiere autenticación)")
        except subprocess.TimeoutExpired:
            return Resultado(False, "sudo — tiempo de espera agotado")
    return Resultado(True, "run0 — disponible")


def _check_sched_ext_sysfs() -> Resultado:
    ruta = Path("/sys/fs/sched_ext")
    if ruta.is_dir():
        return Resultado(True, "/sys/fs/sched_ext existe")
    root = Path("/sys/kernel/sched_ext")
    if root.is_dir():
        return Resultado(True, "/sys/kernel/sched_ext existe")
    return Resultado(False, "No se encontró soporte sched_ext en sysfs",
                     sugerencia="Asegurate de usar un kernel con CONFIG_SCHED_CLASS_EXT activado")


def _check_scxctl() -> Resultado:
    """scxctl siempre se verifica en el sistema (no se bundlea)."""
    ruta = shutil.which("scxctl")
    if ruta:
        return Resultado(True, f"{ruta}")
    return Resultado(False, "No encontrado en el sistema (scxctl es requerido)")


def _check_sistema(nombre_bin: str) -> Resultado:
    """Verifica un binario en el sistema ignorando modo AppImage."""
    ruta = shutil.which(nombre_bin)
    if ruta:
        return Resultado(True, f"{ruta}")
    return Resultado(False, "No encontrado en el sistema",
                     sugerencia=f"Instalá {nombre_bin} con tu gestor de paquetes")


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
    modulos = [
        "gi", "gi.repository.Gtk", "gi.repository.Adw", "gi.repository.GLib",
        "gi.repository.Gio", "gi.repository.Gdk", "gi.repository.cairo",
    ]
    fallos = [m for m in modulos if m not in sys.modules]
    if fallos:
        return Resultado(False, f"Faltan: {', '.join(fallos)}")
    return Resultado(True, f"{len(modulos)} módulos OK")


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
    # ── Críticos (siempre sistema) ──
    Verificacion("scxctl_bin", "scxctl instalado", _check_scxctl, critico=True),
    Verificacion("scxctl_list", "scxctl list (formato JSON)", lambda: _check_comando(["scxctl", "list"], parser=_parse_scxctl_list), critico=True),
    Verificacion("scxctl_get", "scxctl get (parseable)", lambda: _check_comando(["scxctl", "get"], parser=_parse_scxctl_get), critico=True),
    Verificacion("scxctl_version", "Versión de scxctl", lambda: _check_version("scxctl", ["--version"]), critico=False),
    Verificacion("sched_ext", "Soporte sched_ext en kernel", _check_sched_ext_sysfs, critico=True),
    Verificacion("python_deps", "Dependencias Python", _check_dependencias_python, critico=True),
    Verificacion("gresource", "Iconos empaquetados (GResource)", _check_gresource, critico=False),
    Verificacion("sudo_session", "Acceso a sudo", _check_sudo, critico=False),
]

# ── Chequeos específicos de herramientas (modo-aware) ──
_VERIFICACIONES_HERRAMIENTAS: list[Verificacion] = [
    Verificacion("stressng_bin", "stress-ng instalado", lambda: _check_sistema("stress-ng"), critico=False),
    Verificacion("stressng_version", "Versión de stress-ng", lambda: _check_version("stress-ng", ["--version"]), critico=False),
    Verificacion("hyperfine_bin", "hyperfine instalado", lambda: _check_binario("hyperfine"), critico=False),
    Verificacion("hyperfine_version", "Versión de hyperfine", lambda: _check_version("hyperfine", ["--version"]), critico=False),
    Verificacion("rt_tests", "rt-tests (benchmark de compilación)", _check_rt_tests, critico=False),
]

# ── Autodiagnóstico AppImage (solo en modo bundle) ──
if _modo_appimage():
    VERIFICACIONES.append(
        Verificacion("appimage_integridad", "Integridad del AppImage", _check_appimage_integridad, critico=True)
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
