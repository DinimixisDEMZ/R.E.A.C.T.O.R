"""Prueba de integración: el ciclo entero en modo simulación.

Recorre lo mismo que hace la detección automática —aplicar un planificador,
medir, puntuar, elegir ganador, aplicarlo y guardar— sin tocar el sistema. Es
la prueba que cubre las costuras entre módulos, donde las pruebas unitarias no
llegan.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch

import pytest

from core import benchmark as modulo_benchmark
from core import hybrid as modulo_hybrid
from core.benchmark import correr_benchmark
from core.database import (
    activar_db_temporal,
    cargar_resultados_de_run,
    desactivar_db_temporal,
    guardar_resultados_batch,
    guardar_run,
)
from core.hybrid import correr_hybrid
from core.scoring import calcular_scores_finales
from core.scx import GestorScx
from core.tipos import claves_hibridas, valor_para_grafico

PLANIFICADORES = ("scx_bpfland", "scx_lavd", "scx_rusty")
PRUEBAS = ("cpu", "threads", "memory", "fork", "compile", "loaded")


@pytest.fixture(autouse=True)
def sin_esperas():
    """El modo simulación duerme para imitar el ritmo real de una medición.

    En las pruebas esas esperas sólo suman minutos, así que se anulan.
    """
    with patch.object(modulo_benchmark.time, "sleep"), \
         patch.object(modulo_hybrid.time, "sleep"):
        yield


@pytest.fixture
def gestor():
    return GestorScx(modo_desarrollador=True)


@pytest.fixture
def base_temporal():
    activar_db_temporal()
    yield
    desactivar_db_temporal()


def _medir_todo(gestor):
    brutos = {}
    for sc in PLANIFICADORES:
        assert gestor.aplicar(sc, "auto").ok
        brutos[sc] = {}
        for tipo in PRUEBAS:
            funcion = correr_hybrid if tipo in claves_hibridas() else correr_benchmark
            resultado = funcion(tipo, gestor, None, logs=False, modo_dev=True)
            if resultado:
                brutos[sc][resultado["tipo"]] = resultado
    return brutos


class TestCicloCompleto:
    def test_aplicar_confirma_el_cambio(self, gestor):
        for sc in PLANIFICADORES:
            assert gestor.aplicar(sc, "auto").ok
            assert gestor.obtener_estado()[0] == sc

    def test_se_mide_cada_prueba_de_cada_planificador(self, gestor):
        brutos = _medir_todo(gestor)
        assert len(brutos) == len(PLANIFICADORES)
        for sc, datos in brutos.items():
            assert len(datos) == len(PRUEBAS), f"{sc}: {sorted(datos)}"

    def test_la_puntuacion_produce_un_ranking_utilizable(self, gestor):
        scores = calcular_scores_finales(_medir_todo(gestor))
        assert len(scores) == len(PLANIFICADORES)
        for datos in scores.values():
            assert 0 < datos["score"] <= 100
            assert datos["cobertura"] == 1.0

    def test_el_ganador_se_aplica_de_verdad(self, gestor):
        """Regresión: el ganador nunca llegaba a aplicarse.

        Se usaba `scxctl start`, que el loader rechaza si ya hay un
        planificador activo — y al terminar el ciclo siempre lo había. El
        usuario veía el nombre del ganador anunciado y su equipo seguía con
        el último probado.
        """
        scores = calcular_scores_finales(_medir_todo(gestor))
        ganador = max(scores, key=lambda s: scores[s]["score"])
        assert gestor.aplicar(ganador, "auto").ok
        assert gestor.obtener_estado()[0] == ganador

    def test_el_contrato_del_grafico_se_cumple_en_todas_las_categorias(self, gestor):
        """Toda categoría devuelve una tasa donde más alto es mejor."""
        brutos = _medir_todo(gestor)
        for tipo in ("cpu", "threads", "memory",
                     "latencia_fork", "latencia_compile", "latencia_loaded"):
            valores = [valor_para_grafico(d[tipo], tipo)
                       for d in brutos.values() if tipo in d]
            assert valores, tipo
            assert all(v >= 0 for v in valores), tipo

    def test_los_resultados_sobreviven_al_guardado(self, gestor, base_temporal):
        brutos = _medir_todo(gestor)
        run_id = guardar_run({"kernel": "7.2.3"}, run_type="auto")
        guardar_resultados_batch(
            run_id, [r for datos in brutos.values() for r in datos.values()])
        filas = cargar_resultados_de_run(run_id)
        assert len(filas) == len(PLANIFICADORES) * len(PRUEBAS)

    def test_detener_deja_el_sistema_sin_planificador(self, gestor):
        gestor.aplicar("scx_lavd", "auto")
        assert gestor.detener().ok
        assert gestor.obtener_estado() == (None, None)
