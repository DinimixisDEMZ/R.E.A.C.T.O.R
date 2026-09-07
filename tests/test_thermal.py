"""Tests para core/thermal.py — elección de sensor y umbrales derivados del hardware."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch

from core import thermal
from core.thermal import (
    TJMAX_POR_DEFECTO,
    SensorTermico,
    _es_excluido,
    _prioridad_de,
)


def _sin_sensores(monkeypatch=None):
    """Contexto que simula un equipo sin ningún sensor (VM, hardware viejo)."""
    return patch.object(thermal, "glob", autospec=True)


class TestExclusionDeSensores:
    """El filtro descarta periféricos sin descartar el sensor bueno.

    Regresión: una comparación por subcadena hacía que "ac" coincidiera dentro
    de "Package id 0" y descartara justo el mejor sensor del equipo.
    """

    def test_no_descarta_el_paquete_del_cpu(self):
        assert not _es_excluido("coretemp/Package id 0")
        assert not _es_excluido("k10temp/Tctl")
        assert not _es_excluido("x86_pkg_temp")
        assert not _es_excluido("acpitz")

    def test_descarta_perifericos_y_otros_componentes(self):
        for nombre in ("nvme/Composite", "spd5118", "ACAD", "BAT1",
                       "hidpp_battery_0", "r8169_0_3e00:00", "iwlwifi_1",
                       "amdgpu", "nouveau"):
            assert _es_excluido(nombre), nombre


class TestPrioridadDeSensores:
    def test_el_paquete_gana_al_nucleo_suelto(self):
        paquete = _prioridad_de("coretemp", "Package id 0")
        nucleo = _prioridad_de("coretemp", "Core 8")
        assert paquete is not None and nucleo is not None
        assert paquete < nucleo

    def test_el_cpu_gana_a_la_zona_acpi_del_chasis(self):
        assert _prioridad_de("coretemp", "Package id 0") < _prioridad_de("acpitz", "")

    def test_amd_tctl_tiene_maxima_prioridad(self):
        assert _prioridad_de("k10temp", "Tctl") == 1

    def test_los_perifericos_no_puntuan(self):
        assert _prioridad_de("nvme", "Composite") is None
        assert _prioridad_de("spd5118", "") is None


class TestSeleccionDeSensor:
    def test_elige_el_de_mayor_prioridad_no_el_primero(self):
        """El orden de aparición no debe decidir: gana la prioridad."""
        sensor = SensorTermico()
        candidatos = [
            {"ruta": "/fake/acpitz", "nombre": "acpitz", "prioridad": 9,
             "critico": None, "maximo": None, "zona": "/fake/zone0"},
            {"ruta": "/fake/pkg", "nombre": "coretemp/Package id 0", "prioridad": 1,
             "critico": "/fake/pkg_crit", "maximo": None},
        ]
        with patch.object(thermal, "_candidatos_hwmon", return_value=candidatos), \
             patch.object(thermal, "_candidatos_thermal", return_value=[]), \
             patch.object(thermal, "leer_entero", return_value=55000):
            assert sensor.nombre_sensor == "coretemp/Package id 0"

    def test_sin_sensores_devuelve_cero_y_nivel_desconocido(self):
        sensor = SensorTermico()
        with patch.object(thermal, "_candidatos_hwmon", return_value=[]), \
             patch.object(thermal, "_candidatos_thermal", return_value=[]):
            assert sensor.obtener_temp() == 0.0
            assert not sensor.hay_sensor()
            assert sensor.nivel() == "desconocido"

    def test_descarta_lecturas_fisicamente_imposibles(self):
        """sysfs publica valores basura; no deben elegirse ni mostrarse."""
        sensor = SensorTermico()
        candidatos = [{"ruta": "/fake/roto", "nombre": "coretemp", "prioridad": 2,
                       "critico": None, "maximo": None}]
        with patch.object(thermal, "_candidatos_hwmon", return_value=candidatos), \
             patch.object(thermal, "_candidatos_thermal", return_value=[]), \
             patch.object(thermal, "leer_entero", return_value=65261850):
            assert not sensor.hay_sensor()
            assert sensor.obtener_temp() == 0.0


class TestUmbralesDerivados:
    def _sensor_con_tjmax(self, milesimas_crit):
        sensor = SensorTermico()
        candidatos = [{"ruta": "/fake/pkg", "nombre": "coretemp/Package id 0",
                       "prioridad": 1, "critico": "/fake/crit", "maximo": None}]

        def _leer(ruta, defecto=None):
            return milesimas_crit if ruta == "/fake/crit" else 55000

        contexto = (
            patch.object(thermal, "_candidatos_hwmon", return_value=candidatos),
            patch.object(thermal, "_candidatos_thermal", return_value=[]),
            patch.object(thermal, "leer_entero", side_effect=_leer),
        )
        return sensor, contexto

    def test_lee_el_tjmax_publicado_por_el_hardware(self):
        sensor, ctx = self._sensor_con_tjmax(100000)
        with ctx[0], ctx[1], ctx[2]:
            assert sensor.tjmax() == 100.0
            elevado, critico = sensor.umbrales()
            assert elevado == 75.0 and critico == 90.0

    def test_un_portatil_amd_a_95_grados_no_se_declara_critico_a_75(self):
        """Regresión: con umbrales fijos 60/75 este equipo mentía en reposo."""
        sensor, ctx = self._sensor_con_tjmax(95000)
        with ctx[0], ctx[1], ctx[2]:
            assert sensor.tjmax() == 95.0
            elevado, _ = sensor.umbrales()
            assert elevado > 70.0
            assert sensor.nivel() == "estable"  # 55 °C sobre un Tjmax de 95

    def test_tjmax_absurdo_cae_al_valor_conservador(self):
        sensor, ctx = self._sensor_con_tjmax(65261850)
        with ctx[0], ctx[1], ctx[2]:
            assert sensor.tjmax() == TJMAX_POR_DEFECTO

    def test_margen_es_relativo_al_limite_del_equipo(self):
        sensor, ctx = self._sensor_con_tjmax(100000)
        with ctx[0], ctx[1], ctx[2]:
            assert abs(sensor.margen() - 0.55) < 0.01


class TestThrottling:
    def test_detecta_que_el_hardware_freno_por_temperatura(self):
        sensor = SensorTermico()
        with patch.object(thermal, "throttling_termico",
                          return_value={"disponible": True, "nucleo": 10, "paquete": 20}):
            sensor.marcar_inicio_throttling()
        with patch.object(thermal, "throttling_termico",
                          return_value={"disponible": True, "nucleo": 15, "paquete": 20}):
            assert sensor.hubo_throttling()

    def test_sin_cambio_no_reporta_throttling(self):
        sensor = SensorTermico()
        contadores = {"disponible": True, "nucleo": 10, "paquete": 20}
        with patch.object(thermal, "throttling_termico", return_value=contadores):
            sensor.marcar_inicio_throttling()
            assert not sensor.hubo_throttling()

    def test_equipo_sin_contadores_no_reporta_falsos_positivos(self):
        sensor = SensorTermico()
        with patch.object(thermal, "throttling_termico",
                          return_value={"disponible": False, "nucleo": 0, "paquete": 0}):
            sensor.marcar_inicio_throttling()
            assert not sensor.hubo_throttling()


class TestCalibracion:
    def test_calibrar_promedia_las_muestras(self):
        sensor = SensorTermico()
        with patch.object(sensor, "obtener_temp", side_effect=[40.0, 42.0, 44.0]), \
             patch.object(thermal.time, "sleep"):
            assert abs(sensor.calibrar(muestras=3, intervalo=0.1) - 42.0) < 0.01
            assert sensor.temp_base == 42.0

    def test_esperar_estabilizacion_sin_sensor_no_bloquea(self):
        sensor = SensorTermico()
        with patch.object(thermal, "_candidatos_hwmon", return_value=[]), \
             patch.object(thermal, "_candidatos_thermal", return_value=[]):
            resultado = sensor.esperar_estabilizacion()
            assert not resultado["estabilizado"]
            assert resultado["motivo"] == "sin_sensor"

    def test_esperar_estabilizacion_detecta_temperatura_estable(self):
        sensor = SensorTermico()
        with patch.object(sensor, "hay_sensor", return_value=True), \
             patch.object(sensor, "obtener_temp", side_effect=[70.0, 69.5, 69.2, 69.0]), \
             patch.object(thermal.time, "sleep"):
            resultado = sensor.esperar_estabilizacion(tolerancia=1.5, ventana=4)
            assert resultado["estabilizado"]
            assert resultado["motivo"] == "estable"
