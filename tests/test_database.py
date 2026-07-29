"""Tests para core/database.py — operaciones con DB temporal."""
import sys
import os
import time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.database import (
    activar_db_temporal, desactivar_db_temporal,
    guardar_run, guardar_resultado, guardar_resultados_batch,
    consultar_historial, consultar_tendencia, obtener_schedulers_historial,
    contar_resultados, consultar_runs_auto, cargar_resultados_de_run,
    eliminar_historial, detectar_cambio_version,
    guardar_compatibilidad, cargar_compatibilidad,
)


class TestDBTemporal:
    @classmethod
    def setup_class(cls):
        activar_db_temporal()

    @classmethod
    def teardown_class(cls):
        desactivar_db_temporal()

    def test_guardar_y_consultar_run(self):
        eliminar_historial()
        versiones = {"kernel": "6.1.0", "scxctl": "1.0", "stressng": "0.17", "hyperfine": "1.18"}
        run_id = guardar_run(versiones, run_type="manual")
        assert run_id is not None
        assert run_id > 0

    def test_guardar_y_cargar_resultados(self):
        eliminar_historial()
        versiones = {"kernel": "6.1.0"}
        run_id = guardar_run(versiones)
        result = {
            "sched": "scx_rusty", "tipo": "cpu", "valor": 100.0,
            "p95": 5.0, "waste": 0.1, "modo": "auto",
            "timestamp": time.time()
        }
        guardar_resultado(run_id, result)
        assert contar_resultados() == 1

    def test_batch_insert(self):
        eliminar_historial()
        versiones = {"kernel": "6.1.0"}
        run_id = guardar_run(versiones)
        results = [
            {"sched": "scx_rusty", "tipo": "cpu", "valor": 100.0, "p95": 5.0, "waste": 0.1, "timestamp": time.time()},
            {"sched": "scx_lavd", "tipo": "cpu", "valor": 150.0, "p95": 3.0, "waste": 0.2, "timestamp": time.time()},
        ]
        guardar_resultados_batch(run_id, results)
        assert contar_resultados() == 2

    def test_consultar_historial_filtro(self):
        eliminar_historial()
        versiones = {"kernel": "6.1.0"}
        run_id = guardar_run(versiones)
        guardar_resultado(run_id, {"sched": "scx_rusty", "tipo": "cpu", "valor": 100, "p95": 5.0, "waste": 0.1, "timestamp": time.time()})
        guardar_resultado(run_id, {"sched": "scx_lavd", "tipo": "threads", "valor": 200, "p95": 8.0, "waste": 0.2, "timestamp": time.time()})

        rusty = consultar_historial(scheduler="scx_rusty")
        assert len(rusty) == 1
        assert rusty[0]["scheduler_name"] == "scx_rusty"

        cpu_only = consultar_historial(test_type="cpu")
        assert len(cpu_only) == 1

    def test_consultar_tendencia(self):
        eliminar_historial()
        versiones = {"kernel": "6.1.0"}
        run_id = guardar_run(versiones)
        guardar_resultado(run_id, {"sched": "scx_rusty", "tipo": "cpu", "valor": 100, "p95": 5.0, "waste": 0.1, "timestamp": time.time()})

        tendencia = consultar_tendencia("cpu", days=30)
        assert len(tendencia) == 1

    def test_obtener_schedulers_historial(self):
        eliminar_historial()
        versiones = {"kernel": "6.1.0"}
        run_id = guardar_run(versiones)
        guardar_resultado(run_id, {"sched": "scx_rusty", "tipo": "cpu", "valor": 100, "p95": 5.0, "waste": 0.1, "timestamp": time.time()})
        guardar_resultado(run_id, {"sched": "scx_lavd", "tipo": "cpu", "valor": 150, "p95": 3.0, "waste": 0.2, "timestamp": time.time()})

        scheds = obtener_schedulers_historial()
        assert "scx_rusty" in scheds
        assert "scx_lavd" in scheds

    def test_eliminar_historial(self):
        eliminar_historial()
        assert contar_resultados() == 0

    def test_runs_auto(self):
        eliminar_historial()
        versiones = {"kernel": "6.1.0"}
        run_id = guardar_run(versiones, run_type="auto")
        guardar_resultado(run_id, {"sched": "scx_rusty", "tipo": "cpu", "valor": 100, "p95": 5.0, "waste": 0.1, "timestamp": time.time()})

        runs = consultar_runs_auto()
        assert len(runs) >= 1
        assert runs[0]["run_type"] == "auto"

    def test_cargar_resultados_de_run(self):
        eliminar_historial()
        versiones = {"kernel": "6.1.0"}
        run_id = guardar_run(versiones)
        guardar_resultado(run_id, {"sched": "scx_rusty", "tipo": "cpu", "valor": 100, "p95": 5.0, "waste": 0.1, "timestamp": time.time()})

        resultados = cargar_resultados_de_run(run_id)
        assert len(resultados) == 1
        assert resultados[0]["scheduler_name"] == "scx_rusty"

    def test_compatibilidad(self):
        guardar_compatibilidad("scx_rusty", "6.1.0", True, "OK")
        compat = cargar_compatibilidad("6.1.0")
        assert "scx_rusty" in compat
        assert compat["scx_rusty"][0] is True

    def test_detectar_cambio_version(self):
        eliminar_historial()
        versiones = {"kernel": "6.1.0", "stressng": "0.17", "hyperfine": "1.18"}
        guardar_run(versiones)

        hay_cambio, cambios = detectar_cambio_version(versiones)
        assert hay_cambio is False

        hay_cambio, cambios = detectar_cambio_version({"kernel": "6.2.0", "stressng": "0.17", "hyperfine": "1.18"})
        assert hay_cambio is True
        assert "kernel" in cambios
