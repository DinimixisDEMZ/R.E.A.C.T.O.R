"""Comprobaciones de integridad del proyecto.

Estas pruebas no ejercitan lógica: verifican que el código esté completo y sea
coherente. Existen porque un import que falta no rompe ninguna prueba unitaria
—rompe la aplicación la primera vez que un usuario pulsa un botón.
"""
import importlib
import os
import pkgutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _modulos_del_proyecto():
    """Todos los módulos importables del proyecto, sin las pruebas."""
    modulos = ["main", "app"]
    for paquete in ("core", "ui", "utils", "widgets"):
        ruta = os.path.join(RAIZ, paquete)
        for info in pkgutil.walk_packages([ruta], prefix=f"{paquete}."):
            modulos.append(info.name)
        modulos.append(paquete)
    return sorted(set(modulos))


@pytest.mark.parametrize("nombre", _modulos_del_proyecto())
def test_todos_los_modulos_importan(nombre):
    """Cada módulo debe importarse sin errores.

    Regresión: `core/benchmark.py` llegó a usar `obtener_dimensionado()` sin
    haberlo importado. Ninguna prueba lo detectaba porque el motor de medición
    no se ejecuta en las pruebas, y la aplicación arrancaba con normalidad: el
    fallo sólo aparecía al lanzar el primer benchmark.
    """
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    importlib.import_module(nombre)


def test_no_quedan_nombres_sin_definir():
    """Barrido estático en busca de nombres usados y nunca definidos.

    Es la comprobación que encontró el import ausente descrito arriba. Se salta
    si ruff no está instalado, para no exigirlo como dependencia de las pruebas.
    """
    import shutil
    import subprocess

    if not shutil.which("ruff"):
        pytest.skip("ruff no está instalado")

    proceso = subprocess.run(
        ["ruff", "check", ".", "--select", "F821,F811,B023", "--output-format=concise"],
        cwd=RAIZ, capture_output=True, text=True)
    assert proceso.returncode == 0, (
        "ruff encontró nombres indefinidos, redefiniciones o closures sobre "
        f"variables de bucle:\n{proceso.stdout}")


def test_la_version_es_unica_y_valida():
    """La versión vive en un solo sitio y es una versión PEP 440 válida."""
    from core.constantes import VERSION

    assert VERSION
    partes = VERSION.replace(".dev", ".").split(".")
    assert all(p.isdigit() for p in partes if p), VERSION


def test_el_catalogo_cubre_lo_que_hay_instalado():
    """Todo planificador instalado debería tener ficha, o al menos no romper."""
    from core.sched_ext import schedulers_instalados
    from core.tipos import obtener_scheduler

    for canonico in schedulers_instalados():
        ficha = obtener_scheduler(canonico)
        # Un planificador nuevo sin ficha es aceptable; lo que no vale es que
        # consultarlo lance.
        if ficha is not None:
            assert ficha.canonico == canonico
            assert ficha.descripcion
            assert ficha.url.startswith("https://")


def test_los_planificadores_peligrosos_estan_excluidos_del_ciclo():
    """scx_chaos existe para provocar fallos; no puede medirse desatendido."""
    from core.tipos import schedulers_para_benchmark

    assert "chaos" not in schedulers_para_benchmark(["chaos", "lavd"])
    assert "lavd" in schedulers_para_benchmark(["chaos", "lavd"])
