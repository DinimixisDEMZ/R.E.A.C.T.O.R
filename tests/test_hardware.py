"""Tests para core/hardware.py — detección de equipo y degradación segura."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch

from core import hardware
from core.hardware import (
    PerfilMaquina,
    _tamano_a_kib,
    cpus_utilizables,
    cuota_cgroup,
    expandir_lista_cpus,
    leer_entero,
    leer_texto,
    obtener_perfil,
)


class TestLectoresBase:
    """Ninguna lectura puede lanzar: el hardware ausente es lo normal."""

    def test_ruta_inexistente_devuelve_el_defecto(self):
        assert leer_texto("/sys/no/existe/en/ninguna/parte") is None
        assert leer_texto("/sys/no/existe", "reserva") == "reserva"
        assert leer_entero("/sys/no/existe", 42) == 42

    def test_directorio_en_vez_de_archivo_no_lanza(self):
        assert leer_texto("/sys") is None

    def test_entero_acepta_hexadecimal(self):
        with patch.object(hardware, "leer_texto", return_value="0xff"):
            assert leer_entero("/lo/que/sea") == 255

    def test_entero_ignora_basura(self):
        with patch.object(hardware, "leer_texto", return_value="no-es-un-numero"):
            assert leer_entero("/lo/que/sea", 7) == 7


class TestExpandirListaCpus:
    def test_rangos_y_sueltos(self):
        assert expandir_lista_cpus("0-3,8,10-11") == [0, 1, 2, 3, 8, 10, 11]

    def test_cpu_unica(self):
        assert expandir_lista_cpus("0") == [0]

    def test_vacio_y_nulo(self):
        assert expandir_lista_cpus("") == []
        assert expandir_lista_cpus(None) == []

    def test_entrada_malformada_no_lanza(self):
        assert expandir_lista_cpus("a-b,,3") == [3]

    def test_deduplica_y_ordena(self):
        assert expandir_lista_cpus("3,1,1,0-1") == [0, 1, 3]


class TestTamanoCache:
    def test_unidades(self):
        assert _tamano_a_kib("48K") == 48
        assert _tamano_a_kib("24576K") == 24576
        assert _tamano_a_kib("32M") == 32768
        assert _tamano_a_kib("1G") == 1048576

    def test_sin_unidad_asume_kib(self):
        assert _tamano_a_kib("512") == 512

    def test_vacio_o_invalido(self):
        assert _tamano_a_kib("") == 0
        assert _tamano_a_kib(None) == 0
        assert _tamano_a_kib("desconocido") == 0


class TestCpusUtilizables:
    def test_respeta_la_afinidad_del_proceso(self):
        """Dentro de un cpuset, os.cpu_count() miente y la afinidad no."""
        with patch.object(os, "sched_getaffinity", return_value={0, 1}):
            assert cpus_utilizables() == [0, 1]

    def test_sin_soporte_de_afinidad_cae_a_las_cpus_en_linea(self):
        with patch.object(os, "sched_getaffinity", side_effect=OSError):
            assert len(cpus_utilizables()) >= 1


class TestCuotaCgroup:
    def test_sin_limite(self):
        with patch.object(hardware, "leer_texto", return_value="max 100000"):
            assert cuota_cgroup() is None

    def test_con_limite(self):
        with patch.object(hardware, "leer_texto", return_value="200000 100000"):
            assert cuota_cgroup() == 2

    def test_archivo_ausente(self):
        with patch.object(hardware, "leer_texto", return_value=None):
            assert cuota_cgroup() is None

    def test_contenido_corrupto(self):
        with patch.object(hardware, "leer_texto", return_value="basura"):
            assert cuota_cgroup() is None


class TestNucleosHibridos:
    def test_intel_hibrido_se_detecta_por_los_pmu_de_perf(self):
        """En x86 `core_type` no existe y `cpu_capacity` vale 1024 en todos.

        Los dos discriminantes "obvios" no sirven; lo que funciona es que perf
        registre un PMU por tipo de núcleo.
        """
        def _leer(ruta, defecto=None):
            if ruta == "/sys/devices/cpu_core/cpus":
                return "0-11"
            if ruta == "/sys/devices/cpu_atom/cpus":
                return "12-15"
            return defecto

        with patch.object(hardware, "leer_texto", side_effect=_leer):
            rendimiento, eficiencia = hardware.nucleos_hibridos()
        assert rendimiento == list(range(12))
        assert eficiencia == [12, 13, 14, 15]

    def test_cpu_homogenea_no_reporta_hibridez(self):
        with patch.object(hardware, "leer_texto", return_value=None), \
             patch.object(hardware, "leer_entero", return_value=1024), \
             patch.object(hardware, "cpus_online", return_value=[0, 1, 2, 3]):
            assert hardware.nucleos_hibridos() == ([], [])

    def test_arm_se_detecta_por_cpu_capacity(self):
        capacidades = {0: 1024, 1: 1024, 2: 512, 3: 512}

        def _entero(ruta, defecto=None):
            for cpu, valor in capacidades.items():
                if f"cpu{cpu}/cpu_capacity" in ruta:
                    return valor
            return defecto

        with patch.object(hardware, "leer_texto", return_value=None), \
             patch.object(hardware, "leer_entero", side_effect=_entero), \
             patch.object(hardware, "cpus_online", return_value=[0, 1, 2, 3]):
            rendimiento, eficiencia = hardware.nucleos_hibridos()
        assert rendimiento == [0, 1]
        assert eficiencia == [2, 3]


class TestBateria:
    def test_un_periferico_no_cuenta_como_bateria_del_equipo(self):
        """Un ratón inalámbrico aparece como type=Battery.

        Se distingue por `scope`: los periféricos declaran scope=Device.
        Sin ese filtro, un equipo de escritorio con ratón Bluetooth se
        clasificaba como portátil.
        """
        def _leer(ruta, defecto=None):
            if ruta.endswith("hidpp_battery_0/type"):
                return "Battery"
            if ruta.endswith("hidpp_battery_0/scope"):
                return "Device"
            return defecto

        with patch.object(hardware.glob, "glob",
                          return_value=["/sys/class/power_supply/hidpp_battery_0"]), \
             patch.object(hardware, "leer_texto", side_effect=_leer):
            assert hardware.baterias_del_sistema() == []

    def test_la_bateria_del_equipo_si_cuenta(self):
        def _leer(ruta, defecto=None):
            if ruta.endswith("BAT1/type"):
                return "Battery"
            return defecto

        with patch.object(hardware.glob, "glob",
                          return_value=["/sys/class/power_supply/BAT1"]), \
             patch.object(hardware, "leer_texto", side_effect=_leer):
            assert hardware.baterias_del_sistema() == ["/sys/class/power_supply/BAT1"]

    def test_sin_fuentes_de_energia_no_esta_en_bateria(self):
        with patch.object(hardware.glob, "glob", return_value=[]):
            assert hardware.en_bateria() is False


class TestPerfilReal:
    """Se ejecuta contra el equipo de verdad: debe ser coherente en cualquiera."""

    def test_el_perfil_se_construye_sin_lanzar(self):
        perfil = PerfilMaquina()
        assert perfil.hilos >= 1
        assert perfil.nucleos_fisicos >= 1
        assert perfil.hilos_por_nucleo >= 1
        assert perfil.hilos_para_carga >= 1
        assert perfil.grupos_llc >= 1
        assert perfil.nodos_numa >= 1

    def test_los_hilos_nunca_superan_a_las_cpus_utilizables(self):
        perfil = PerfilMaquina()
        assert perfil.hilos == len(perfil.cpus_utilizables)

    def test_hibrida_implica_ambas_listas(self):
        perfil = PerfilMaquina()
        if perfil.es_hibrida:
            assert perfil.cpus_rendimiento and perfil.cpus_eficiencia
            assert perfil.nucleos_rendimiento >= 1
            assert perfil.nucleos_eficiencia >= 1

    def test_el_diccionario_trae_lo_necesario_para_comparar(self):
        datos = PerfilMaquina().como_diccionario()
        for clave in ("cpu_modelo", "hilos", "nucleos_fisicos", "kernel",
                      "tipo_equipo", "governor", "en_bateria", "ram_total_kib"):
            assert clave in datos, clave

    def test_descripcion_corta_es_una_linea(self):
        texto = PerfilMaquina().descripcion_corta()
        assert texto and "\n" not in texto

    def test_el_perfil_se_cachea(self):
        assert obtener_perfil() is obtener_perfil()
        assert obtener_perfil(recargar=True) is obtener_perfil()

    def test_tipo_de_equipo_es_uno_de_los_previstos(self):
        assert PerfilMaquina().tipo_equipo in (
            "portatil", "escritorio", "virtual", "desconocido")
