"""
Motor de verificación del sistema.
Ejecuta chequeos de herramientas, comandos y dependencias al arrancar.
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


# ─── Chequeos individuales ───

def _check_binario(nombre_bin: str, nombre_mostrar: str = "") -> Resultado:
    nombre = nombre_mostrar or nombre_bin
    ruta = shutil.which(nombre_bin)
    if ruta:
        return Resultado(True, f"{ruta}")
    from utils.helpers import ruta_bundleada
    bundle = ruta_bundleada(f"usr/bin/{nombre_bin}")
    if bundle:
        return Resultado(True, f"{bundle} (bundle)")
    return Resultado(False, "No encontrado")


def _check_version(nombre_bin: str, args: list[str] | None = None) -> Resultado:
    try:
        r = subprocess.run([nombre_bin] + (args or ["--version"]), capture_output=True, text=True, timeout=10)
        salida = (r.stdout or r.stderr or "").strip()
        if salida:
            return Resultado(True, salida.split("\n")[0])
        return Resultado(False, "No devolvió versión")
    except FileNotFoundError:
        return Resultado(False, "Binario no encontrado")
    except subprocess.TimeoutExpired:
        return Resultado(False, "Tiempo de espera agotado")
    except (OSError, subprocess.SubprocessError) as e:
        return Resultado(False, str(e))


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
    # Acepta cualquier salida no vacía (RUNNING, IDLE, etc.)
    # Lo importante es que el comando se ejecute sin error
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
    return Resultado(False, "No se encontró soporte sched_ext en sysfs", sugerencia="Asegurate de estar usando un kernel con CONFIG_SCHED_CLASS_EXT activado")


def _check_rt_tests() -> Resultado:
    """Verifica cyclictest: PATH, bundle, /tmp/rt-tests."""
    ruta = shutil.which("cyclictest")
    if ruta:
        return Resultado(True, f"{ruta}")
    from utils.helpers import ruta_bundleada
    bundle = ruta_bundleada("usr/bin/cyclictest")
    if bundle:
        return Resultado(True, f"{bundle} (bundle)")
    makefile = Path("/tmp/rt-tests/Makefile")
    if makefile.is_file():
        cyclictest = Path("/tmp/rt-tests/cyclictest")
        if cyclictest.is_file():
            return Resultado(True, str(cyclictest))
        return Resultado(True, "Clonado en /tmp/rt-tests (sin compilar)")
    bundle_src = ruta_bundleada("usr/share/reactor/rt-tests/Makefile")
    if bundle_src:
        return Resultado(True, "Source bundleado en AppImage")
    return Resultado(False, "No encontrado (ni en PATH ni bundleado)")


def _check_gresource() -> Resultado:
    ruta = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "icons", "reactor.gresource")
    if os.path.isfile(ruta):
        return Resultado(True, f"{ruta}")
    return Resultado(False, "reactor.gresource no encontrado",
                     sugerencia="Ejecutá: glib-compile-resources data/icons/reactor.gresource.xml")


def _check_dependencias_python() -> Resultado:
    """Verifica que los módulos críticos de Python estén cargados.
    
    Usa sys.modules porque la app ya importó todo al arrancar.
    find_spec falla con gi.repository.* e import_module
    no es thread-safe en threads secundarios.
    """
    modulos = [
        "gi", "gi.repository.Gtk", "gi.repository.Adw", "gi.repository.GLib",
        "gi.repository.Gio", "gi.repository.Gdk", "gi.repository.cairo",
    ]
    fallos = [m for m in modulos if m not in sys.modules]
    if fallos:
        return Resultado(False, f"Faltan: {', '.join(fallos)}")
    return Resultado(True, f"{len(modulos)} módulos OK")


# ─── Registro maestro ───

VERIFICACIONES: list[Verificacion] = [
    # ── Críticos ──
    Verificacion("scxctl_bin", "scxctl instalado", lambda: _check_binario("scxctl"), critico=True),
    Verificacion("scxctl_list", "scxctl list (formato JSON)", lambda: _check_comando(["scxctl", "list"], parser=_parse_scxctl_list), critico=True),
    Verificacion("scxctl_get", "scxctl get (parseable)", lambda: _check_comando(["scxctl", "get"], parser=_parse_scxctl_get), critico=True),
    Verificacion("sched_ext", "Soporte sched_ext en kernel", _check_sched_ext_sysfs, critico=True),
    Verificacion("python_deps", "Dependencias Python", _check_dependencias_python, critico=True),
    Verificacion("gresource", "Iconos empaquetados (GResource)", _check_gresource, critico=False),

    # ── No críticos ──
    Verificacion("scxctl_version", "Versión de scxctl", lambda: _check_version("scxctl", ["--version"]), critico=False),
    Verificacion("stressng_bin", "stress-ng instalado", lambda: _check_binario("stress-ng"), critico=False),
    Verificacion("stressng_version", "Versión de stress-ng", lambda: _check_version("stress-ng", ["--version"]), critico=False),
    Verificacion("hyperfine_bin", "hyperfine instalado", lambda: _check_binario("hyperfine"), critico=False),
    Verificacion("hyperfine_version", "Versión de hyperfine", lambda: _check_version("hyperfine", ["--version"]), critico=False),
    Verificacion("sudo_session", "Acceso a sudo", _check_sudo, critico=False),
    Verificacion("rt_tests", "rt-tests (benchmark de compilación)", _check_rt_tests, critico=False),
]


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
