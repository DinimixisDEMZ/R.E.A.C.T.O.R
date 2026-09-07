"""Tests para core/verificacion.py — qué bloquea el arranque y qué no."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch

from core import verificacion
from core.verificacion import (
    VERIFICACIONES,
    Resultado,
    Verificacion,
    _sugerir_instalacion,
    ejecutar_verificaciones,
    resumen_critico,
    todo_critico_ok,
)


class TestQueEsCritico:
    """El motivo por el que el proyecto quedó inservible.

    Había tres chequeos de `scxctl` marcados como críticos, y basta con que
    falte ese binario —que ni siquiera es necesario— para que el diálogo
    deshabilite el botón de continuar. En una distribución que empaqueta los
    planificadores aparte del loader, la aplicación se declaraba rota estando
    perfectamente utilizable.
    """

    def test_scxctl_ya_no_es_critico(self):
        criticos = {v.id for v in VERIFICACIONES if v.critico}
        assert "scxctl" not in criticos
        assert not any("scxctl" in v.id for v in VERIFICACIONES if v.critico)

    def test_solo_el_kernel_y_python_son_criticos(self):
        criticos = {v.id for v in VERIFICACIONES if v.critico}
        assert criticos <= {"sched_ext", "python_deps", "appimage_integridad"}

    def test_las_herramientas_de_medicion_son_opcionales(self):
        for identificador in ("stressng_bin", "hyperfine_bin", "rt_tests"):
            coincidencias = [v for v in VERIFICACIONES if v.id == identificador]
            assert coincidencias, identificador
            assert not coincidencias[0].critico, identificador

    def test_todos_los_ids_son_unicos(self):
        ids = [v.id for v in VERIFICACIONES]
        assert len(ids) == len(set(ids))


class TestSugerenciasPorDistribucion:
    """Antes se sugería siempre `sudo eopkg install`, que sólo sirve en Solus."""

    def test_arch(self):
        with patch.object(verificacion, "_detectar_distribucion", return_value="arch"):
            assert "pacman" in _sugerir_instalacion("stress-ng")

    def test_fedora(self):
        with patch.object(verificacion, "_detectar_distribucion", return_value="fedora"):
            assert "dnf" in _sugerir_instalacion("stress-ng")

    def test_debian(self):
        with patch.object(verificacion, "_detectar_distribucion", return_value="debian"):
            assert "apt" in _sugerir_instalacion("stress-ng")

    def test_distribucion_desconocida_no_inventa_un_gestor(self):
        with patch.object(verificacion, "_detectar_distribucion", return_value="loquesea"):
            texto = _sugerir_instalacion("stress-ng")
        assert "gestor de paquetes" in texto
        for gestor in ("pacman", "apt", "dnf", "eopkg"):
            assert gestor not in texto


class TestEjecucion:
    def test_una_excepcion_no_tumba_la_verificacion(self):
        def _explota():
            raise RuntimeError("boom")

        rota = Verificacion("prueba_rota", "Chequeo que falla", _explota, critico=False)
        with patch.object(verificacion, "VERIFICACIONES", [rota]):
            resultados = ejecutar_verificaciones()
        assert len(resultados) == 1
        assert not resultados[0][1].exito
        assert "boom" in resultados[0][1].mensaje

    def test_filtrado_por_id(self):
        resultados = ejecutar_verificaciones(solo_ids=["sched_ext"])
        assert len(resultados) == 1
        assert resultados[0][0].id == "sched_ext"

    def test_resumen_critico_tiene_el_formato_esperado(self):
        resultados = ejecutar_verificaciones()
        ok, total = resumen_critico(resultados).split("/")
        assert int(ok) <= int(total)

    def test_todo_critico_ok_ignora_los_opcionales(self):
        criticos_bien = [
            (Verificacion("a", "A", lambda: Resultado(True), critico=True), Resultado(True)),
            (Verificacion("b", "B", lambda: Resultado(False), critico=False), Resultado(False)),
        ]
        assert todo_critico_ok(criticos_bien)

    def test_un_critico_fallido_lo_marca(self):
        con_fallo = [
            (Verificacion("a", "A", lambda: Resultado(False), critico=True), Resultado(False)),
        ]
        assert not todo_critico_ok(con_fallo)


class TestChequeosContraElEquipoReal:
    def test_ninguno_lanza_en_este_equipo(self):
        for v in VERIFICACIONES:
            resultado = v.ejecutar()
            assert isinstance(resultado, Resultado), v.id

    def test_el_kernel_de_este_equipo_soporta_sched_ext(self):
        """Verdad de terreno: aquí sí hay sched_ext, y debe detectarse."""
        from core import sched_ext
        resultado = verificacion._check_sched_ext()
        assert resultado.exito == sched_ext.hay_soporte()

    def test_el_chequeo_de_python_importa_de_verdad(self):
        """Miraba `sys.modules`, lo que daba falsos negativos fuera de la GUI."""
        assert verificacion._check_dependencias_python().exito
