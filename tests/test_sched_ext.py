"""Tests para core/sched_ext.py — observación del kernel sin privilegios."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch

from core import sched_ext
from core.sched_ext import canonizar, nombre_corto


class TestNombres:
    """El kernel publica "lavd" y el loader "scx_lavd".

    Comparar las dos formas sin normalizar hacía que la interfaz mostrara
    "ninguno activo" con un planificador corriendo, y que el combo duplicara
    la entrada del que estaba en uso.
    """

    def test_canonizar_anade_el_prefijo(self):
        assert canonizar("lavd") == "scx_lavd"

    def test_canonizar_es_idempotente(self):
        assert canonizar("scx_lavd") == "scx_lavd"
        assert canonizar(canonizar("bpfland")) == "scx_bpfland"

    def test_canonizar_tolera_vacios(self):
        assert canonizar("") is None
        assert canonizar(None) is None
        assert canonizar("   ") is None

    def test_nombre_corto_quita_el_prefijo(self):
        assert nombre_corto("scx_lavd") == "lavd"
        assert nombre_corto("lavd") == "lavd"
        assert nombre_corto(None) is None

    def test_ida_y_vuelta(self):
        for nombre in ("lavd", "scx_lavd", "bpfland"):
            assert canonizar(nombre_corto(canonizar(nombre))) == canonizar(nombre)


class TestEstado:
    def test_sin_soporte_del_kernel_no_lanza(self):
        with patch.object(sched_ext.os.path, "isdir", return_value=False):
            info = sched_ext.estado()
        assert info["soportado"] is False
        assert info["activo"] is False
        assert info["scheduler"] is None

    def test_planificador_activo(self):
        def _texto(ruta, defecto=None):
            if ruta.endswith("/state"):
                return "enabled"
            if ruta.endswith("/ops"):
                return "bpfland"
            return defecto

        with patch.object(sched_ext.os.path, "isdir", return_value=True), \
             patch.object(sched_ext, "leer_texto", side_effect=_texto), \
             patch.object(sched_ext, "leer_entero", return_value=1), \
             patch.object(sched_ext.glob, "glob",
                          return_value=["/sys/kernel/sched_ext/root/ops"]):
            info = sched_ext.estado()
        assert info["activo"] is True
        # El kernel lo publica sin prefijo; se devuelve canónico.
        assert info["scheduler"] == "scx_bpfland"
        assert info["parcial"] is False

    def test_modo_parcial(self):
        """switch_all=0 significa que sólo gobierna las tareas SCHED_EXT."""
        def _texto(ruta, defecto=None):
            return "enabled" if ruta.endswith("/state") else ("lavd" if ruta.endswith("/ops") else defecto)

        with patch.object(sched_ext.os.path, "isdir", return_value=True), \
             patch.object(sched_ext, "leer_texto", side_effect=_texto), \
             patch.object(sched_ext, "leer_entero", return_value=0), \
             patch.object(sched_ext.glob, "glob",
                          return_value=["/sys/kernel/sched_ext/root/ops"]):
            assert sched_ext.estado()["parcial"] is True

    def test_los_estados_de_transicion_no_son_activo(self):
        """Tratarlos como activo o inactivo hace parpadear la interfaz."""
        for transitorio in ("enabling", "disabling"):
            with patch.object(sched_ext.os.path, "isdir", return_value=True), \
                 patch.object(sched_ext, "leer_texto", return_value=transitorio), \
                 patch.object(sched_ext, "leer_entero", return_value=0), \
                 patch.object(sched_ext.glob, "glob", return_value=[]):
                info = sched_ext.estado()
            assert info["activo"] is False, transitorio
            assert info["en_transicion"] is True, transitorio

    def test_desactivado(self):
        with patch.object(sched_ext.os.path, "isdir", return_value=True), \
             patch.object(sched_ext, "leer_texto", return_value="disabled"), \
             patch.object(sched_ext, "leer_entero", return_value=0), \
             patch.object(sched_ext.glob, "glob", return_value=[]):
            info = sched_ext.estado()
        assert info["activo"] is False
        assert info["en_transicion"] is False
        assert info["scheduler"] is None


class TestEventos:
    def test_sin_archivo_devuelve_vacio(self):
        with patch.object(sched_ext.glob, "glob", return_value=[]):
            assert sched_ext.eventos() == {}

    def test_parsea_ambos_formatos(self):
        with patch.object(sched_ext.glob, "glob", return_value=["/fake/events"]), \
             patch.object(sched_ext, "leer_texto",
                          return_value="SCX_EV_ENQ_SKIP 12\nSCX_EV_BYPASS : 34\n\nbasura"):
            assert sched_ext.eventos() == {"SCX_EV_ENQ_SKIP": 12, "SCX_EV_BYPASS": 34}

    def test_contenido_ilegible_no_lanza(self):
        with patch.object(sched_ext.glob, "glob", return_value=["/fake/events"]), \
             patch.object(sched_ext, "leer_texto", return_value=None):
            assert sched_ext.eventos() == {}


class TestSchedulersInstalados:
    def test_busca_en_el_path_no_solo_en_usr_bin(self):
        """Hay distribuciones que instalan en /usr/local/bin, y Nix en otra parte."""
        with patch.dict(os.environ, {"PATH": "/opt/otro/bin"}), \
             patch.object(sched_ext.os.path, "isdir", lambda r: r == "/opt/otro/bin"), \
             patch.object(sched_ext.os, "listdir", return_value=["scx_lavd", "otra_cosa"]), \
             patch.object(sched_ext.os.path, "isfile", return_value=True), \
             patch.object(sched_ext.os, "access", return_value=True):
            encontrados = sched_ext.schedulers_instalados()
        assert encontrados == {"scx_lavd": "/opt/otro/bin/scx_lavd"}

    def test_ignora_lo_que_no_es_ejecutable(self):
        with patch.dict(os.environ, {"PATH": "/opt/otro/bin"}), \
             patch.object(sched_ext.os.path, "isdir", lambda r: r == "/opt/otro/bin"), \
             patch.object(sched_ext.os, "listdir", return_value=["scx_lavd"]), \
             patch.object(sched_ext.os.path, "isfile", return_value=True), \
             patch.object(sched_ext.os, "access", return_value=False):
            assert sched_ext.schedulers_instalados() == {}

    def test_directorio_ilegible_no_lanza(self):
        with patch.dict(os.environ, {"PATH": "/opt/prohibido"}), \
             patch.object(sched_ext.os.path, "isdir", return_value=True), \
             patch.object(sched_ext.os, "listdir", side_effect=PermissionError):
            assert sched_ext.schedulers_instalados() == {}


class TestContraElEquipoReal:
    def test_las_funciones_no_lanzan_en_este_equipo(self):
        info = sched_ext.estado()
        assert set(info) >= {"soportado", "estado", "activo", "scheduler"}
        assert isinstance(sched_ext.contadores(), dict)
        assert isinstance(sched_ext.eventos(), dict)
        assert isinstance(sched_ext.schedulers_instalados(), dict)
        assert isinstance(sched_ext.tarea_en_sched_ext(), bool)
