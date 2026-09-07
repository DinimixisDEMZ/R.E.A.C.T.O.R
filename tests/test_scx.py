"""Tests para core/scx.py — backends de control y clasificación de errores."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import subprocess
from unittest.mock import MagicMock, patch

from core import scx as modulo_scx
from core.scx import (
    MODOS,
    MODOS_POR_NOMBRE,
    MODOS_POR_VALOR,
    BackendDirecto,
    BackendScxctl,
    BackendSimulado,
    GestorScx,
    Resultado,
    _clasificar_error_dbus,
)


class TestModos:
    def test_los_valores_coinciden_con_el_enum_del_loader(self):
        """El orden importa: es el entero que viaja por D-Bus.

        La interfaz ofrecía ["auto","powersave","gaming","lowlatency","server"],
        cuyo orden NO coincide con el del loader, así que elegir "powersave"
        habría pedido "gaming".
        """
        assert MODOS_POR_NOMBRE == {
            "auto": 0, "gaming": 1, "powersave": 2, "lowlatency": 3, "server": 4}

    def test_el_mapa_inverso_es_consistente(self):
        for nombre, valor in MODOS:
            assert MODOS_POR_VALOR[valor] == nombre


class TestResultado:
    def test_es_veraz_segun_el_exito(self):
        assert bool(Resultado(True))
        assert not bool(Resultado(False, "algo falló"))

    def test_conserva_el_codigo(self):
        assert Resultado(False, "x", codigo="no_autorizado").codigo == "no_autorizado"


class TestClasificacionDeErrores:
    """La interfaz necesita distinguir causas que exigen respuestas opuestas."""

    def test_servicio_ausente(self):
        assert _clasificar_error_dbus(
            Exception("org.freedesktop.DBus.Error.ServiceUnknown")) == "sin_servicio"

    def test_no_autorizado(self):
        assert _clasificar_error_dbus(Exception("NotAuthorized")) == "no_autorizado"
        assert _clasificar_error_dbus(Exception("AccessDenied")) == "no_autorizado"

    def test_cancelado_por_el_usuario(self):
        assert _clasificar_error_dbus(Exception("Dismissed by user")) == "cancelado"

    def test_desconocido_cae_en_fallo_generico(self):
        assert _clasificar_error_dbus(Exception("vaya usted a saber")) == "fallo"


class TestBackendSimulado:
    def test_arranca_con_un_planificador(self):
        backend = BackendSimulado()
        assert backend.disponible()
        nombre, modo = backend.estado()
        assert nombre.startswith("scx_") and modo == "auto"

    def test_aplicar_cambia_el_estado(self):
        backend = BackendSimulado()
        assert backend.aplicar("lavd", "gaming").ok
        assert backend.estado() == ("scx_lavd", "gaming")

    def test_detener_lo_deja_vacio(self):
        backend = BackendSimulado()
        backend.detener()
        assert backend.estado() == (None, None)

    def test_la_lista_es_canonica(self):
        assert all(n.startswith("scx_") for n in BackendSimulado().lista())


class TestBackendScxctl:
    def _backend(self):
        backend = BackendScxctl(ruta="/usr/bin/scxctl")
        return backend

    def test_parsea_la_lista_del_cliente(self):
        backend = self._backend()
        salida = 'supported schedulers: ["scx_bpfland", "scx_rusty"]'
        with patch.object(backend, "_ejecutar", return_value=Resultado(True, salida)):
            assert backend.lista() == ["scx_bpfland", "scx_rusty"]

    def test_lista_vacia_si_el_cliente_falla(self):
        backend = self._backend()
        with patch.object(backend, "_ejecutar", return_value=Resultado(False, "boom")):
            assert backend.lista() == []

    def test_conmuta_en_vez_de_arrancar_si_ya_hay_uno(self):
        """`start` con un planificador activo lo rechaza el loader."""
        backend = self._backend()
        llamadas = []
        with patch.object(backend, "estado", return_value=("scx_lavd", "auto")), \
             patch.object(backend, "_ejecutar",
                          side_effect=lambda a, **k: llamadas.append(a) or Resultado(True)):
            backend.aplicar("scx_rusty", "auto")
        assert llamadas[0][0] == "switch"

    def test_arranca_si_no_hay_ninguno(self):
        backend = self._backend()
        llamadas = []
        with patch.object(backend, "estado", return_value=(None, None)), \
             patch.object(backend, "_ejecutar",
                          side_effect=lambda a, **k: llamadas.append(a) or Resultado(True)):
            backend.aplicar("scx_rusty", "auto")
        assert llamadas[0][0] == "start"

    def test_sin_binario_informa_falta_de_servicio(self):
        backend = BackendScxctl(ruta=None)
        resultado = backend._ejecutar(["get"])
        assert not resultado.ok and resultado.codigo == "sin_servicio"

    def test_tiempo_agotado_se_clasifica(self):
        backend = self._backend()
        with patch.object(subprocess, "run",
                          side_effect=subprocess.TimeoutExpired("scxctl", 5)):
            resultado = backend._ejecutar(["get"])
        assert resultado.codigo == "tiempo_agotado"


class TestBackendDirecto:
    def test_sin_elevacion_no_finge_poder(self):
        backend = BackendDirecto.__new__(BackendDirecto)
        backend._instalados = {"scx_lavd": "/usr/bin/scx_lavd"}
        backend._elevador = None
        resultado = backend._ejecutar_elevado(["systemctl", "stop", "x"])
        assert not resultado.ok and resultado.codigo == "sin_elevacion"

    def test_planificador_no_instalado(self):
        backend = BackendDirecto.__new__(BackendDirecto)
        backend._instalados = {}
        backend._elevador = ["/usr/bin/pkexec"]
        resultado = backend.aplicar("scx_inexistente")
        assert not resultado.ok and resultado.codigo == "no_instalado"

    def test_detener_sin_planificador_activo_es_exito(self):
        backend = BackendDirecto.__new__(BackendDirecto)
        backend._instalados = {}
        backend._elevador = ["/usr/bin/pkexec"]
        with patch.object(modulo_scx.sched_ext, "estado",
                          return_value={"activo": False, "scheduler": None}):
            resultado = backend.detener()
        assert resultado.ok and resultado.codigo == "sin_scheduler"

    def test_solo_ofrece_el_modo_por_defecto(self):
        """Los modos pertenecen al loader; sin él ofrecerlos sería mentir."""
        backend = BackendDirecto.__new__(BackendDirecto)
        backend._instalados = {}
        backend._elevador = None
        assert backend.modos("scx_lavd") == ["auto"]

    def test_pkexec_cancelado_no_se_confunde_con_fallo(self):
        backend = BackendDirecto.__new__(BackendDirecto)
        backend._elevador = ["/usr/bin/pkexec"]
        proceso = MagicMock(returncode=126, stdout="", stderr="")
        with patch.object(subprocess, "run", return_value=proceso):
            assert backend._ejecutar_elevado(["x"]).codigo == "cancelado"


class TestGestorScx:
    def test_el_modo_simulacion_usa_el_backend_simulado(self):
        gestor = GestorScx(modo_desarrollador=True)
        assert gestor.nombre_backend == "simulado"
        assert gestor.aplicar("scx_lavd", "gaming").ok
        assert gestor.obtener_estado() == ("scx_lavd", "gaming")

    def test_cambiar_de_modo_reelige_backend(self):
        gestor = GestorScx(modo_desarrollador=True)
        assert gestor.nombre_backend == "simulado"
        gestor.modo_desarrollador = False
        assert gestor.nombre_backend != "simulado"

    def test_sin_backend_las_operaciones_fallan_con_claridad(self):
        gestor = GestorScx()
        with patch.object(GestorScx, "backend", property(lambda self: None)):
            operaciones = [
                lambda: gestor.aplicar("scx_lavd"),
                gestor.detener,
                gestor.reiniciar,
                gestor.restaurar_predeterminado,
            ]
            for operacion in operaciones:
                resultado = operacion()
                assert not resultado.ok
                assert resultado.codigo == "sin_backend"

    def test_la_lista_combina_backend_y_binarios_instalados(self):
        """El loader lleva una lista estática que deja fuera lo que sí está."""
        gestor = GestorScx()
        backend_falso = MagicMock()
        backend_falso.lista.return_value = ["scx_rusty"]
        with patch.object(GestorScx, "backend", property(lambda self: backend_falso)), \
             patch.object(modulo_scx.sched_ext, "schedulers_instalados",
                          return_value={"scx_cake": "/usr/bin/scx_cake"}):
            assert gestor.obtener_lista() == ["scx_cake", "scx_rusty"]

    def test_el_filtro_de_compatibles_normaliza_nombres(self):
        gestor = GestorScx()
        backend_falso = MagicMock()
        backend_falso.lista.return_value = ["scx_rusty", "scx_cake"]
        with patch.object(GestorScx, "backend", property(lambda self: backend_falso)), \
             patch.object(modulo_scx.sched_ext, "schedulers_instalados", return_value={}):
            # Se pasa el nombre corto: debe canonizarse antes de comparar.
            assert gestor.obtener_lista(compatibles=["rusty"]) == ["scx_rusty"]

    def test_el_kernel_manda_sobre_el_backend(self):
        """Si el planificador lo arrancó otro cliente, el loader puede no saberlo."""
        gestor = GestorScx()
        backend_falso = MagicMock()
        backend_falso.estado.return_value = ("scx_viejo", "gaming")
        with patch.object(GestorScx, "backend", property(lambda self: backend_falso)), \
             patch.object(modulo_scx.sched_ext, "estado",
                          return_value={"activo": True, "scheduler": "scx_cake"}):
            assert gestor.obtener_estado() == ("scx_cake", "gaming")

    def test_sin_planificador_activo_devuelve_nada(self):
        gestor = GestorScx()
        with patch.object(modulo_scx.sched_ext, "estado",
                          return_value={"activo": False, "scheduler": None}):
            assert gestor.obtener_estado() == (None, None)

    def test_el_diagnostico_describe_el_entorno(self):
        datos = GestorScx().diagnostico()
        for clave in ("soporte_kernel", "servicio_loader", "backend", "capacidades"):
            assert clave in datos, clave

    def test_puede_refleja_las_capacidades_del_backend(self):
        gestor = GestorScx(modo_desarrollador=True)
        assert gestor.puede("aplicar")
        assert not gestor.puede("operacion_inventada")
