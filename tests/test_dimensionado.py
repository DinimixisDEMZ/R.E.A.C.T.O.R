"""Tests para core/dimensionado.py — parámetros de prueba según el equipo."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock

from core.dimensionado import (
    DURACION_MAXIMA,
    DURACION_MINIMA,
    Dimensionado,
    obtener_dimensionado,
)


def _perfil(hilos=8, virtual=False, cuota=None):
    perfil = MagicMock()
    perfil.hilos = hilos
    perfil.hilos_para_carga = min(hilos, cuota) if cuota else hilos
    perfil.es_virtual = virtual
    perfil.ram_disponible_kib = 8 * 1024 * 1024
    return perfil


class TestTrabajadores:
    def test_usa_los_hilos_realmente_utilizables(self):
        """Dentro de un contenedor limitado, cpu_count() sigue mintiendo.

        Con 16 hilos visibles y cuota de 2, lanzar 16 trabajadores convertía la
        prueba en una medición del solapamiento, no del planificador.
        """
        dim = Dimensionado(perfil=_perfil(hilos=16, cuota=2))
        assert dim.trabajadores == 2

    def test_la_contencion_sobresuscribe_a_proposito(self):
        dim = Dimensionado(perfil=_perfil(hilos=8))
        assert dim.trabajadores_contencion == 16

    def test_nunca_baja_de_un_trabajador(self):
        dim = Dimensionado(perfil=_perfil(hilos=1))
        assert dim.trabajadores >= 1
        assert dim.trabajadores_contencion >= 2


class TestDuracion:
    def test_equipo_normal_usa_la_duracion_pedida(self):
        assert Dimensionado(perfil=_perfil(hilos=16), duracion_solicitada=10).duracion == 10

    def test_equipo_pequeno_mide_mas_tiempo(self):
        """Con pocos núcleos hay menos paralelismo que promediar y más varianza."""
        pequeno = Dimensionado(perfil=_perfil(hilos=2), duracion_solicitada=8).duracion
        grande = Dimensionado(perfil=_perfil(hilos=16), duracion_solicitada=8).duracion
        assert pequeno > grande

    def test_maquina_virtual_mide_mas_tiempo(self):
        virtual = Dimensionado(perfil=_perfil(hilos=8, virtual=True), duracion_solicitada=8).duracion
        real = Dimensionado(perfil=_perfil(hilos=8), duracion_solicitada=8).duracion
        assert virtual > real

    def test_se_respetan_los_limites(self):
        assert Dimensionado(perfil=_perfil(hilos=2), duracion_solicitada=1).duracion >= DURACION_MINIMA
        assert Dimensionado(perfil=_perfil(hilos=2), duracion_solicitada=999).duracion <= DURACION_MAXIMA


class TestEstimacion:
    def test_crece_con_el_numero_de_planificadores(self):
        dim = Dimensionado(perfil=_perfil(hilos=8))
        assert dim.duracion_total_estimada(10) > dim.duracion_total_estimada(2)

    def test_un_equipo_lento_tarda_mas(self):
        lento = Dimensionado(perfil=_perfil(hilos=2)).duracion_total_estimada(10)
        rapido = Dimensionado(perfil=_perfil(hilos=16)).duracion_total_estimada(10)
        assert lento > rapido

    def test_nunca_estima_cero(self):
        assert Dimensionado(perfil=_perfil(hilos=8)).duracion_total_estimada(0) > 0


class TestContexto:
    def test_el_diccionario_permite_saber_si_dos_medidas_son_comparables(self):
        datos = Dimensionado(perfil=_perfil(hilos=8)).como_diccionario()
        assert set(datos) == {"trabajadores", "trabajadores_contencion", "duracion_s"}

    def test_el_resumen_es_una_linea(self):
        texto = Dimensionado(perfil=_perfil(hilos=8)).resumen()
        assert texto and "\n" not in texto


class TestContraElEquipoReal:
    def test_se_construye_sin_lanzar(self):
        dim = obtener_dimensionado(recargar=True)
        assert dim.trabajadores >= 1
        assert DURACION_MINIMA <= dim.duracion <= DURACION_MAXIMA
