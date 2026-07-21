import builtins
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

import main as main_module
from main import (
    _activar_ventana_principal,
    _cargar_gtk,
    _detectar_binarios,
    _ejecutando_como_root,
    comprobar_entorno,
)


def _binarios_completos():
    return {
        "scxctl": "/usr/bin/scxctl",
        "stress-ng": "/usr/bin/stress-ng",
        "hyperfine": "/usr/bin/hyperfine",
        "cc": "/usr/bin/cc",
        "gcc": None,
        "clang": None,
        "sudo": "/usr/bin/sudo",
        "run0": "/usr/bin/run0",
    }


def _binarios_sin_backend():
    binarios = _binarios_completos()
    binarios["sudo"] = None
    binarios["run0"] = None
    return binarios


def _adw_stub(major, minor, micro=0):
    return SimpleNamespace(
        Application=type("FakeAdwApplication", (), {}),
        get_major_version=lambda: major,
        get_minor_version=lambda: minor,
        get_micro_version=lambda: micro,
    )


def _cargar_gtk_con_adw(adw):
    fake_gi = ModuleType("gi")
    fake_gi.__path__ = []
    fake_gi.require_version = lambda *_args: None
    fake_repository = ModuleType("gi.repository")
    fake_repository.Gtk = object()
    fake_repository.Adw = adw
    fake_gi.repository = fake_repository

    with patch.dict(
        sys.modules,
        {"gi": fake_gi, "gi.repository": fake_repository},
    ):
        return _cargar_gtk()


def test_linux_con_dependencias_completas():
    resultado = comprobar_entorno("Linux", _binarios_completos(), True)

    assert resultado.correcto
    assert resultado.errores_criticos == ()
    assert resultado.avisos_benchmark == ()


def test_sistema_no_linux_es_error_critico():
    resultado = comprobar_entorno("Windows", _binarios_completos(), True)

    assert not resultado.correcto
    assert any("Windows" in error for error in resultado.errores_criticos)


def test_binarios_ausentes_se_clasifican_por_severidad():
    resultado = comprobar_entorno(
        "Linux",
        {"scxctl": None, "stress-ng": None, "hyperfine": None},
        True,
    )

    assert any("scxctl no encontrado" in error for error in resultado.errores_criticos)
    assert any("stress-ng no encontrado" in aviso for aviso in resultado.avisos_benchmark)
    assert any("hyperfine no encontrado" in aviso for aviso in resultado.avisos_benchmark)


def test_binarios_admiten_coleccion_de_nombres():
    resultado = comprobar_entorno(
        "Linux",
        {"scxctl", "stress-ng", "hyperfine", "cc", "run0"},
        True,
    )

    assert resultado.correcto
    assert resultado.avisos_benchmark == ()
    assert resultado.avisos_entorno == ()


def test_binarios_none_se_trata_como_todos_ausentes():
    resultado = comprobar_entorno("Linux", None, True)

    assert not resultado.correcto
    assert any("scxctl no encontrado" in error for error in resultado.errores_criticos)
    assert len(resultado.avisos_benchmark) == 3


def test_backend_ausente_avisa_sin_bloquear_la_interfaz():
    resultado = comprobar_entorno(
        "Linux",
        _binarios_sin_backend(),
        True,
        es_root=False,
    )

    assert resultado.correcto
    assert len(resultado.avisos_entorno) == 1
    assert "controles" in resultado.avisos_entorno[0]
    assert "automatización" in resultado.avisos_entorno[0]
    assert "no estarán disponibles" in resultado.avisos_entorno[0]


def test_run0_evitar_aviso_de_entorno():
    binarios = _binarios_sin_backend()
    binarios["run0"] = "/usr/bin/run0"

    resultado = comprobar_entorno("Linux", binarios, True, es_root=False)

    assert resultado.correcto
    assert resultado.avisos_entorno == ()


def test_sudo_evitar_aviso_de_entorno():
    binarios = _binarios_sin_backend()
    binarios["sudo"] = "/usr/bin/sudo"

    resultado = comprobar_entorno("Linux", binarios, True, es_root=False)

    assert resultado.correcto
    assert resultado.avisos_entorno == ()


def test_root_evitar_aviso_de_entorno_sin_backend():
    resultado = comprobar_entorno(
        "Linux",
        _binarios_sin_backend(),
        True,
        es_root=True,
    )

    assert resultado.correcto
    assert resultado.avisos_entorno == ()


def test_deteccion_de_binarios_incluye_privilegios_y_compiladores():
    with patch(
        "main.shutil.which",
        side_effect=lambda nombre: f"/usr/bin/{nombre}",
    ) as which:
        binarios = _detectar_binarios()

    assert binarios["sudo"] == "/usr/bin/sudo"
    assert binarios["run0"] == "/usr/bin/run0"
    assert binarios["cc"] == "/usr/bin/cc"
    which.assert_any_call("sudo")
    which.assert_any_call("run0")
    which.assert_any_call("cc")
    which.assert_any_call("gcc")
    which.assert_any_call("clang")


def test_compilador_c_ausente_es_aviso_no_critico():
    binarios = _binarios_completos()
    for nombre in ("cc", "gcc", "clang"):
        binarios[nombre] = None

    resultado = comprobar_entorno("Linux", binarios, True)

    assert resultado.correcto
    assert len(resultado.avisos_benchmark) == 1
    assert "compilador C" in resultado.avisos_benchmark[0]
    assert "compilación" in resultado.avisos_benchmark[0]


def test_deteccion_root_usa_geteuid_cuando_existe():
    with patch("main.os.geteuid", return_value=0, create=True):
        assert _ejecutando_como_root()


def test_activar_dos_veces_reutiliza_la_ventana_principal():
    class FakeWindow:
        def __init__(self):
            self.presentaciones = 0
            self.inicializaciones = 0

        def present(self):
            self.presentaciones += 1

        def iniciar_inicializacion(self):
            self.inicializaciones += 1

    class FakeApplication:
        def __init__(self):
            self.ventana_activa = None

        def get_active_window(self):
            return self.ventana_activa

    app = FakeApplication()
    creadas = []

    def crear_ventana(application):
        ventana = FakeWindow()
        application.ventana_activa = ventana
        creadas.append(ventana)
        return ventana

    primera = _activar_ventana_principal(app, crear_ventana)
    segunda = _activar_ventana_principal(app, crear_ventana)

    assert primera is segunda
    assert len(creadas) == 1
    assert primera.presentaciones == 2
    assert primera.inicializaciones == 1


def test_activar_presenta_ventana_existente_sin_crearla():
    class FakeWindow:
        def __init__(self):
            self.presentaciones = 0

        def present(self):
            self.presentaciones += 1

    existente = FakeWindow()
    app = SimpleNamespace(get_active_window=lambda: existente)
    crear_ventana = Mock()

    resultado = _activar_ventana_principal(app, crear_ventana)

    assert resultado is existente
    assert existente.presentaciones == 1
    crear_ventana.assert_not_called()


def test_activar_presenta_shell_antes_de_iniciar_migraciones():
    eventos = []

    class FakeWindow:
        def present(self):
            eventos.append("present")

        def iniciar_inicializacion(self):
            eventos.append("init")

    app = SimpleNamespace(get_active_window=lambda: None)

    _activar_ventana_principal(app, lambda _app: FakeWindow())

    assert eventos == ["present", "init"]


def test_adw_1_6_es_rechazado_por_el_preflight():
    gtk_modules, error = _cargar_gtk_con_adw(_adw_stub(1, 6))

    assert gtk_modules is None
    assert error is not None
    assert "Libadwaita 1.6.0" in error
    assert ">= 1.7.0" in error
    assert "Adw.WrapBox" in error


def test_adw_1_7_es_aceptado_por_el_preflight():
    adw = _adw_stub(1, 7)

    gtk_modules, error = _cargar_gtk_con_adw(adw)

    assert gtk_modules is not None
    assert gtk_modules[1] is adw
    assert error is None


def test_stub_adw_sin_getters_se_rechaza_de_forma_explicita():
    adw = SimpleNamespace(Application=type("FakeAdwApplication", (), {}))

    gtk_modules, error = _cargar_gtk_con_adw(adw)

    assert gtk_modules is None
    assert error is not None
    assert "API no expone" in error


def test_getter_adw_que_falla_se_rechaza_sin_romper_preflight():
    def fallo():
        raise RuntimeError("versión ilegible")

    adw = _adw_stub(1, 7)
    adw.get_minor_version = fallo

    gtk_modules, error = _cargar_gtk_con_adw(adw)

    assert gtk_modules is None
    assert "versión ilegible" in error


def test_gtk_ausente_es_error_critico_sin_importar_herramientas():
    resultado = comprobar_entorno("Linux", _binarios_completos(), False)

    assert not resultado.correcto
    assert any("GTK4/Libadwaita" in error for error in resultado.errores_criticos)


def test_import_gi_ausente_devuelve_diagnostico():
    real_import = builtins.__import__

    def import_without_gi(name, *args, **kwargs):
        if name == "gi":
            raise ModuleNotFoundError("No module named 'gi'")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=import_without_gi):
        gtk_modules, error = _cargar_gtk()

    assert gtk_modules is None
    assert error is not None
    assert "PyGObject" in error
    assert "GTK4" in error


def test_main_abre_la_app_y_avisa_si_no_hay_backend_privilegiado():
    aplicaciones = []

    class FakeApplication:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.callbacks = {}
            aplicaciones.append(self)

        def connect(self, signal, callback):
            self.callbacks[signal] = callback

        def run(self, argv):
            assert argv is main_module.sys.argv
            return 23

    fake_adw = SimpleNamespace(Application=FakeApplication)
    fake_app = SimpleNamespace(VentanaSimple=lambda _application: None)

    with (
        patch("main.platform.system", return_value="Linux"),
        patch("main._detectar_binarios", return_value=_binarios_sin_backend()),
        patch("main._cargar_gtk", return_value=((object(), fake_adw), None)),
        patch("main._ejecutando_como_root", return_value=False) as detectar_root,
        patch("main._mostrar_avisos_entorno") as mostrar_entorno,
        patch("main._mostrar_avisos_benchmark") as mostrar_benchmark,
        patch.dict(sys.modules, {"app": fake_app}),
    ):
        exit_code = main_module.main()

    assert exit_code == 23
    assert aplicaciones[0].kwargs == {
        "application_id": "com.dinimixis.reactor"
    }
    detectar_root.assert_called_once_with()
    mostrar_entorno.assert_called_once()
    assert "no estarán disponibles" in mostrar_entorno.call_args.args[0][0]
    mostrar_benchmark.assert_not_called()
