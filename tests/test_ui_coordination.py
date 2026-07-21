import importlib.util
from pathlib import Path
import subprocess
import sys
import types

import pytest

from core.operations import CancellationToken, OperationCancelled
from core.scx import ScxManager as RealScxManager, ScxState as RealScxState
from main import _activar_ventana_principal


ROOT = Path(__file__).resolve().parents[1]
STRICT_NOT_RUNNING_STATE = RealScxManager.parsear_estado(
    "NOT RUNNING scx_foreign in gaming mode"
)
TEST_TYPES = (
    "cpu",
    "threads",
    "memory",
    "latencia_fork",
    "latencia_compile",
    "latencia_loaded",
)


class DummyWindow:
    pass


class DummyImage:
    pass


@pytest.fixture
def gtk_stubs(monkeypatch):
    gi = types.ModuleType("gi")
    gi.require_version = lambda *_args: None
    repository = types.ModuleType("gi.repository")
    repository.Gtk = types.SimpleNamespace(Image=DummyImage)
    repository.Adw = types.SimpleNamespace(
        ApplicationWindow=DummyWindow,
        Window=DummyWindow,
        Spinner=lambda **_kwargs: object(),
    )
    repository.GLib = types.SimpleNamespace(
        idle_add=lambda callback, *args: callback(*args),
    )
    repository.Gdk = types.SimpleNamespace()
    gi.repository = repository
    monkeypatch.setitem(sys.modules, "gi", gi)
    monkeypatch.setitem(sys.modules, "gi.repository", repository)
    return repository


def _load_module(monkeypatch, module_name, relative_path):
    spec = importlib.util.spec_from_file_location(module_name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def controles(monkeypatch, gtk_stubs):
    database = types.ModuleType("core.database")
    database.activar_db_temporal = lambda: None
    database.desactivar_db_temporal = lambda: None
    disponibilidad = types.ModuleType("ui.disponibilidad")
    disponibilidad.recargar_disponibilidad_ui = (
        lambda _win, _nombres=None: None
    )
    rendimiento = types.ModuleType("ui.rendimiento")

    def invalidar_manual(win):
        win.datos_rendimiento = []
        win._manual_development_mode = None
        win._manual_generation = int(
            getattr(win, "_manual_generation", 0) or 0
        ) + 1

    rendimiento.invalidar_estado_rendimiento = invalidar_manual
    monkeypatch.setitem(sys.modules, "core.database", database)
    monkeypatch.setitem(sys.modules, "ui.disponibilidad", disponibilidad)
    monkeypatch.setitem(sys.modules, "ui.rendimiento", rendimiento)
    return _load_module(
        monkeypatch,
        "_reactor_test_controles",
        "ui/controles.py",
    )


@pytest.fixture
def rendimiento(monkeypatch, gtk_stubs):
    benchmark = types.ModuleType("core.benchmark")
    benchmark.correr_benchmark = lambda *_args, **_kwargs: None
    hybrid = types.ModuleType("core.hybrid")
    hybrid.correr_hybrid = lambda *_args, **_kwargs: None
    scoring = types.ModuleType("core.scoring")
    scoring.calcular_ranking_manual = lambda _results: {}
    scoring.calcular_valor_ranking = lambda result, _tipo: result["valor"]
    scoring.HYBRID_TYPES = {"fork", "compile", "loaded"}
    database = types.ModuleType("core.database")
    database.guardar_run_completo = lambda *_args, **_kwargs: 1
    monkeypatch.setitem(sys.modules, "core.benchmark", benchmark)
    monkeypatch.setitem(sys.modules, "core.hybrid", hybrid)
    monkeypatch.setitem(sys.modules, "core.scoring", scoring)
    monkeypatch.setitem(sys.modules, "core.database", database)
    return _load_module(
        monkeypatch,
        "_reactor_test_rendimiento",
        "ui/rendimiento.py",
    )


@pytest.fixture
def password_dialog(monkeypatch, gtk_stubs):
    return _load_module(
        monkeypatch,
        "_reactor_test_password_dialog",
        "widgets/password_dialog.py",
    )


@pytest.fixture
def app_module(monkeypatch, gtk_stubs):
    scx = types.ModuleType("core.scx")
    scx.ScxManager = type("ScxManager", (), {})
    scx.ScxState = RealScxState
    thermal = types.ModuleType("core.thermal")
    thermal.SensorTermico = type("SensorTermico", (), {})
    grafico = types.ModuleType("ui.grafico")
    grafico.GraficoComparativo = type("GraficoComparativo", (), {})
    controles_stub = types.ModuleType("ui.controles")
    controles_stub.setup_controles_ui = lambda _win: None
    rendimiento_stub = types.ModuleType("ui.rendimiento")
    rendimiento_stub.setup_rendimiento_ui = lambda _win: None
    rendimiento_stub.actualizar_interfaz_ranking = lambda _win: None
    rendimiento_stub.invalidar_estado_rendimiento = lambda _win: None
    automatizacion = types.ModuleType("ui.automatizacion")
    automatizacion.setup_automatizacion_ui = lambda _win: None
    automatizacion._refrescar_auto_schedulers = (
        lambda _win, _nombres=None: None
    )
    disponibilidad = types.ModuleType("ui.disponibilidad")
    disponibilidad.setup_disponibilidad_ui = lambda _win: None
    disponibilidad.recargar_disponibilidad_ui = (
        lambda _win, _nombres=None: None
    )
    diagnostico = types.ModuleType("ui.diagnostico")
    diagnostico.setup_diagnostico_ui = lambda _win: None
    historial = types.ModuleType("ui.historial")
    historial.setup_historial_ui = lambda _win: None
    database = types.ModuleType("core.database")
    database.inicializar_db = lambda: None
    database.obtener_versiones = lambda: {}
    database.detectar_cambio_version = lambda _versions: (False, None)
    database.cargar_compatibilidad = lambda _kernel: {}
    password = types.ModuleType("widgets.password_dialog")
    password.DialogoPassword = type("DialogoPassword", (), {})
    password.backend_no_requiere_password = (
        lambda backend: str(backend or "").casefold() in {"run0", "direct"}
    )

    for name, module in {
        "core.scx": scx,
        "core.thermal": thermal,
        "ui.grafico": grafico,
        "ui.controles": controles_stub,
        "ui.rendimiento": rendimiento_stub,
        "ui.automatizacion": automatizacion,
        "ui.disponibilidad": disponibilidad,
        "ui.diagnostico": diagnostico,
        "ui.historial": historial,
        "core.database": database,
        "widgets.password_dialog": password,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    return _load_module(monkeypatch, "_reactor_test_app", "app.py")


class ImmediateThread:
    def __init__(self, target, daemon=False, name=None, **_kwargs):
        self.target = target
        self.daemon = daemon
        self.name = name

    def start(self):
        self.target()


class DeferredThread(ImmediateThread):
    instances = []

    def __init__(self, target, daemon=False, name=None, **kwargs):
        super().__init__(target, daemon, name=name, **kwargs)
        self.started = False
        self.instances.append(self)

    def start(self):
        self.started = True


class FakeHandle:
    def __init__(self, token=None):
        self.release_count = 0
        self.token = token or CancellationToken()
        self.operation_id = 1

    def check_cancelled(self):
        self.token.raise_if_cancelled()

    def release(self):
        self.release_count += 1
        return self.release_count == 1


class FakeItem:
    def __init__(self, value):
        self.value = value

    def get_string(self):
        return self.value


class FakeCombo:
    def __init__(self, value):
        self.item = FakeItem(value) if value is not None else None

    def get_selected_item(self):
        return self.item


class FakeSensitiveButton:
    def __init__(self, sensitive=False):
        self.sensitive = sensitive

    def set_sensitive(self, sensitive):
        self.sensitive = bool(sensitive)


class FakeButton:
    def __init__(self, icon="benchmark-symbolic"):
        self.icon = icon
        self.child = None
        self.sensitive = True

    def get_icon_name(self):
        return self.icon

    def set_icon_name(self, icon):
        self.icon = icon

    def set_child(self, child):
        self.child = child

    def set_sensitive(self, sensitive):
        self.sensitive = sensitive


class FakeSwitch:
    def __init__(self, active, handler_id):
        self.active = active
        self.handler_id = handler_id
        self.blocked_handlers = set()
        self.signal_events = []
        self.callback = None

    def get_active(self):
        return self.active

    def set_active(self, active):
        changed = self.active != active
        self.active = active
        if (
            changed
            and self.handler_id not in self.blocked_handlers
            and self.callback is not None
        ):
            self.signal_events.append("notify")
            self.callback(self, None)

    def handler_block(self, handler_id):
        self.signal_events.append(("block", handler_id))
        self.blocked_handlers.add(handler_id)

    def handler_unblock(self, handler_id):
        self.signal_events.append(("unblock", handler_id))
        self.blocked_handlers.remove(handler_id)


def test_refresh_only_synchronizes_without_running_restart(controles):
    calls = []
    win = types.SimpleNamespace(sincronizar_sistema=lambda: calls.append("refresh"))

    controles.refrescar_estado(win)

    assert calls == ["refresh"]


@pytest.mark.parametrize(
    ("actual", "requested"),
    ((False, True), (True, False)),
)
def test_busy_developer_toggle_restores_switch_without_touching_database(
    controles,
    monkeypatch,
    actual,
    requested,
):
    database_calls = []
    busy_messages = []
    monkeypatch.setattr(
        controles,
        "activar_db_temporal",
        lambda: database_calls.append("activate"),
    )
    monkeypatch.setattr(
        controles,
        "desactivar_db_temporal",
        lambda: database_calls.append("deactivate"),
    )

    win = types.SimpleNamespace(
        modo_desarrollador=actual,
        operaciones=types.SimpleNamespace(is_busy=True),
        scx=types.SimpleNamespace(modo_desarrollador=actual),
        mostrar_operacion_ocupada=lambda: busy_messages.append("busy"),
    )
    handler_id = 17
    switch = FakeSwitch(requested, handler_id)
    switch.callback = lambda sw, _pspec: controles._alternar_modo_desarrollador(
        win,
        sw,
        handler_id,
    )

    changed = controles._alternar_modo_desarrollador(
        win,
        switch,
        handler_id,
    )

    assert changed is False
    assert switch.active is actual
    assert switch.signal_events == [
        ("block", handler_id),
        ("unblock", handler_id),
    ]
    assert win.modo_desarrollador is actual
    assert win.scx.modo_desarrollador is actual
    assert database_calls == []
    assert busy_messages == ["busy"]


@pytest.mark.parametrize(
    ("actual", "requested", "failed_call", "rollback_call"),
    (
        (False, True, "activate", "deactivate"),
        (True, False, "deactivate", "activate"),
    ),
)
def test_developer_toggle_database_failure_restores_coherent_state(
    controles,
    monkeypatch,
    actual,
    requested,
    failed_call,
    rollback_call,
):
    events = []

    def activate():
        events.append("activate")
        if failed_call == "activate":
            raise RuntimeError("DB temporal bloqueada")

    def deactivate():
        events.append("deactivate")
        if failed_call == "deactivate":
            raise RuntimeError("no se pudo cerrar DB")

    monkeypatch.setattr(controles, "activar_db_temporal", activate)
    monkeypatch.setattr(controles, "desactivar_db_temporal", deactivate)

    toasts = []
    win = types.SimpleNamespace(
        modo_desarrollador=actual,
        operaciones=types.SimpleNamespace(is_busy=False),
        scx=types.SimpleNamespace(modo_desarrollador=actual),
        compatibles=["scx_ok"],
        datos_rendimiento=[{"development_mode": actual}],
        mostrar_toast=lambda message, alta=False: toasts.append(
            (message, alta)
        ),
        sincronizar_sistema=lambda: events.append("sync"),
    )
    switch = FakeSwitch(requested, 19)

    changed = controles._alternar_modo_desarrollador(win, switch, 19)

    assert changed is False
    assert events == [failed_call, rollback_call]
    assert switch.active is actual
    assert win.modo_desarrollador is actual
    assert win.scx.modo_desarrollador is actual
    assert win.compatibles == ["scx_ok"]
    assert win.datos_rendimiento == [{"development_mode": actual}]
    assert toasts and toasts[-1][1] is True


def test_developer_toggle_commits_database_then_invalidates_and_syncs(
    controles,
    monkeypatch,
):
    events = []
    automation = types.ModuleType("ui.automatizacion")

    def invalidate(win):
        assert win.modo_desarrollador is True
        win._scores_finales = {}
        win._auto_development_mode = None
        events.append("invalidate")

    automation.invalidar_estado_automatizacion = invalidate
    monkeypatch.setitem(sys.modules, "ui.automatizacion", automation)
    rendimiento = types.ModuleType("ui.rendimiento")

    def invalidate_manual(win):
        assert win.modo_desarrollador is True
        win.datos_rendimiento = []
        win._manual_development_mode = None
        events.append("invalidate-manual")

    rendimiento.invalidar_estado_rendimiento = invalidate_manual
    monkeypatch.setitem(sys.modules, "ui.rendimiento", rendimiento)

    def activate_database():
        events.append("database")

    monkeypatch.setattr(controles, "activar_db_temporal", activate_database)
    monkeypatch.setattr(
        controles,
        "desactivar_db_temporal",
        lambda: events.append("wrong-database"),
    )

    image = object()
    nav = types.SimpleNamespace(
        remove_css_class=lambda _css_class: None,
        get_child=lambda: types.SimpleNamespace(
            get_first_child=lambda: image,
        ),
    )
    toasts = []
    win = types.SimpleNamespace(
        modo_desarrollador=False,
        operaciones=types.SimpleNamespace(is_busy=False),
        scx=types.SimpleNamespace(modo_desarrollador=False),
        compatibles=["scx_old"],
        nav_disponibilidad=nav,
        _scores_finales={"simulado": {"score": 100}},
        _auto_development_mode=True,
        datos_rendimiento=[{"tipo": "cpu"}],
        _manual_development_mode=False,
        mostrar_toast=lambda message, alta=False: toasts.append(
            (message, alta)
        ),
        sincronizar_sistema=lambda: events.append("sync"),
    )
    switch = FakeSwitch(True, 23)

    changed = controles._alternar_modo_desarrollador(win, switch, 23)

    assert changed is True
    assert events == ["database", "invalidate", "invalidate-manual", "sync"]
    assert win.modo_desarrollador is True
    assert win.scx.modo_desarrollador is True
    assert win.compatibles is None
    assert win.datos_rendimiento == []
    assert win._manual_development_mode is None
    assert toasts == [("Modo Desarrollador: ACTIVADO", False)]


def test_developer_toggle_has_defensive_invalidation_fallback(
    controles,
    monkeypatch,
):
    automation = types.ModuleType("ui.automatizacion")
    monkeypatch.setitem(sys.modules, "ui.automatizacion", automation)
    monkeypatch.setattr(controles, "activar_db_temporal", lambda: None)

    win = types.SimpleNamespace(
        modo_desarrollador=False,
        operaciones=types.SimpleNamespace(is_busy=False),
        scx=types.SimpleNamespace(modo_desarrollador=False),
        compatibles=["scx_old"],
        nav_disponibilidad=types.SimpleNamespace(
            remove_css_class=lambda _css_class: None,
            get_child=lambda: types.SimpleNamespace(
                get_first_child=lambda: object(),
            ),
        ),
        mostrar_toast=lambda *_args, **_kwargs: None,
        sincronizar_sistema=lambda: None,
    )

    assert controles._alternar_modo_desarrollador(
        win,
        FakeSwitch(True, 29),
        29,
    ) is True
    assert win._brutos_finales == {}
    assert win._scores_finales == {}
    assert win.ganador_final is None
    assert win._auto_permitir_aplicar is False
    assert win._auto_contexto_aplicable is False
    assert win._auto_pesos_validos is False
    assert win._auto_development_mode is None


def test_combo_change_only_marks_configuration_pending(controles):
    auth_calls = []
    button = FakeSensitiveButton()
    win = types.SimpleNamespace(
        combo_schedulers=FakeCombo("scx_lavd"),
        combo_modos=FakeCombo("gaming"),
        btn_aplicar_configuracion=button,
        _actualizando_configuracion=False,
        _configuracion_pendiente=False,
        solicitar_sudo_si_necesario=lambda callback: auth_calls.append(callback),
    )

    pending = controles._seleccion_configuracion_cambiada(win)

    assert pending is True
    assert win._configuracion_pendiente is True
    assert button.sensitive is True
    assert auth_calls == []

    win._actualizando_configuracion = True
    win._configuracion_pendiente = False
    button.set_sensitive(False)
    assert controles._seleccion_configuracion_cambiada(win) is False
    assert win._configuracion_pendiente is False
    assert button.sensitive is False


def test_explicit_apply_runs_one_atomic_switch(controles, monkeypatch):
    monkeypatch.setattr(controles.threading, "Thread", ImmediateThread)
    commands = []
    refreshes = []
    handle = FakeHandle()

    class Coordinator:
        def try_acquire(self, name):
            assert name == "cambio de scheduler a scx_lavd"
            return handle

    class Scx:
        def ejecutar_con_sudo(self, command, *, cancel_token=None):
            commands.append((command, cancel_token))
            return subprocess.CompletedProcess(command, 0, "", "")

    button = FakeSensitiveButton(True)
    win = types.SimpleNamespace(
        en_sincronizacion=False,
        combo_schedulers=FakeCombo("scx_lavd"),
        combo_modos=FakeCombo("lowlatency"),
        btn_aplicar_configuracion=button,
        _configuracion_pendiente=True,
        _aplicando_configuracion=False,
        operaciones=Coordinator(),
        scx=Scx(),
        modo_desarrollador=False,
        _mode_generation=0,
        solicitar_sudo_si_necesario=lambda callback: callback(),
        ejecutar_en_ui=lambda callback, *args: callback(*args),
        mostrar_toast=lambda *_args, **_kwargs: None,
        sincronizar_sistema=lambda: refreshes.append("refresh"),
    )

    assert controles.aplicar_cambio_scheduler(win, button) is True

    assert commands == [
        (
            [
            "scxctl",
            "switch",
            "-s",
            "scx_lavd",
            "-m",
            "lowlatency",
            ],
            handle.token,
        )
    ]
    assert refreshes == ["refresh"]
    assert handle.release_count == 1
    assert win._aplicando_configuracion is False
    assert win._configuracion_pendiente is False
    assert button.sensitive is False


def test_apply_authorization_discards_stale_combo_selection(controles):
    callbacks = []
    acquisitions = []
    toasts = []
    button = FakeSensitiveButton(True)

    class Coordinator:
        def try_acquire(self, name):
            acquisitions.append(name)
            return FakeHandle()

    win = types.SimpleNamespace(
        _ui_alive=True,
        en_sincronizacion=False,
        combo_schedulers=FakeCombo("scx_old"),
        combo_modos=FakeCombo("gaming"),
        btn_aplicar_configuracion=button,
        _configuracion_pendiente=True,
        _config_generation=3,
        _mode_generation=0,
        _actualizando_configuracion=False,
        _aplicando_configuracion=False,
        modo_desarrollador=False,
        operaciones=Coordinator(),
        scx=object(),
        solicitar_sudo_si_necesario=lambda callback: callbacks.append(callback),
        mostrar_toast=lambda message, alta=False: toasts.append(
            (message, alta)
        ),
    )

    assert controles.aplicar_cambio_scheduler(win, button) is True
    win.combo_schedulers.item = FakeItem("scx_new")
    controles._seleccion_configuracion_cambiada(win)
    callbacks[0]()

    assert acquisitions == []
    assert win._configuracion_pendiente is True
    assert button.sensitive is True
    assert toasts == [
        ("La selección cambió durante la autorización; aplíquela de nuevo.", False)
    ]


def test_maintenance_captures_combos_before_auth_and_releases_handle(
    controles,
    monkeypatch,
):
    monkeypatch.setattr(controles.threading, "Thread", ImmediateThread)
    callbacks = []
    commands = []
    toasts = []
    handle = FakeHandle()

    class Coordinator:
        def try_acquire(self, name):
            assert name == "mantenimiento SCX (start)"
            return handle

    class Scx:
        def ejecutar_con_sudo(self, command, *, cancel_token=None):
            commands.append((command, cancel_token))
            return subprocess.CompletedProcess(command, 0, "", "")

    win = types.SimpleNamespace(
        combo_schedulers=FakeCombo("scx_old"),
        combo_modos=FakeCombo("gaming"),
        operaciones=Coordinator(),
        scx=Scx(),
        modo_desarrollador=False,
        _mode_generation=0,
        solicitar_sudo_si_necesario=lambda callback: callbacks.append(callback),
        ejecutar_en_ui=lambda callback, *args: callback(*args),
        mostrar_toast=lambda message, alta=False: toasts.append((message, alta)),
        sincronizar_sistema=lambda: commands.append("refresh"),
    )

    controles.ejecutar_mantenimiento(win, None, "start")
    win.combo_schedulers.item = FakeItem("scx_new")
    win.combo_modos.item = FakeItem("powersave")
    callbacks[0]()

    assert commands[0] == (
        ["scxctl", "start", "-s", "scx_old", "-m", "gaming"],
        handle.token,
    )
    assert commands[1] == "refresh"
    assert handle.release_count == 1
    assert toasts == [("SCX iniciado: scx_old [gaming].", False)]


@pytest.mark.parametrize("action", ("start", "stop", "switch"))
def test_nonzero_not_running_result_is_always_an_error(controles, action):
    result = subprocess.CompletedProcess(
        ["scxctl", action],
        1,
        "",
        "scheduler is not running",
    )

    message, is_error = controles._describir_resultado(
        action,
        result,
        "scx_lavd",
        "auto",
    )

    assert is_error is True
    assert message == "Error de SCX: scheduler is not running"


def test_control_worker_releases_handle_when_scx_raises(controles):
    handle = FakeHandle()

    result, error = controles._ejecutar_con_handle(
        handle,
        lambda: (_ for _ in ()).throw(RuntimeError("fallo SCX")),
    )

    assert result is None
    assert error == "fallo SCX"
    assert handle.release_count == 1


@pytest.mark.parametrize("action", ("start", "stop", "switch"))
def test_close_before_control_worker_prevents_every_scx_command(
    controles,
    monkeypatch,
    action,
):
    DeferredThread.instances.clear()
    monkeypatch.setattr(controles.threading, "Thread", DeferredThread)
    handle = FakeHandle()
    commands = []
    callbacks = []

    class Coordinator:
        state = None

        def try_acquire(self, _name):
            return handle

        def cancel_current(self):
            return handle.token.cancel()

    class Scx:
        def ejecutar_con_sudo(self, command, *, cancel_token=None):
            commands.append((command, cancel_token))
            return subprocess.CompletedProcess(command, 0, "", "")

    coordinator = Coordinator()
    win = types.SimpleNamespace(
        _ui_alive=True,
        operaciones=coordinator,
        scx=Scx(),
        ejecutar_en_ui=lambda callback, *args: callbacks.append(
            (callback, args)
        ),
        mostrar_toast=lambda *_args, **_kwargs: None,
    )

    assert controles._lanzar_operacion_scx(
        win,
        f"test {action}",
        action,
        ["scxctl", action],
        "scx_test",
        "auto",
    ) is True
    assert coordinator.cancel_current() is True
    win._ui_alive = False

    DeferredThread.instances[0].target()

    assert commands == []
    assert handle.release_count == 1
    assert len(callbacks) == 1
    assert isinstance(callbacks[0][1][3], OperationCancelled)


def test_control_cancellation_is_presented_without_error_priority(controles):
    toasts = []
    win = types.SimpleNamespace(
        sincronizar_sistema=lambda: (_ for _ in ()).throw(
            AssertionError("una cancelación no debe refrescar SCX")
        ),
        mostrar_toast=lambda message, alta=False: toasts.append(
            (message, alta)
        ),
    )

    controles._finalizar_operacion_scx(
        win,
        "start",
        None,
        OperationCancelled("cerrada"),
        "scx_test",
        "auto",
    )

    assert toasts == [("Operación SCX cancelada.", False)]


def test_manual_benchmark_uses_single_atomic_save_and_preserves_metrics(
    rendimiento,
    monkeypatch,
):
    result = {
        "tipo": "cpu",
        "sched": "scx_test",
        "valor": 1200.0,
        "response": 4.2,
        "response_kind": "mean_context_switch_us",
        "p95": None,
    }
    saves = []

    def legacy_benchmark(_tipo, _scx, _log_view, *, modo_dev=False):
        assert modo_dev is False
        return result

    monkeypatch.setattr(
        rendimiento,
        "correr_benchmark",
        legacy_benchmark,
    )
    monkeypatch.setattr(
        rendimiento,
        "guardar_run_completo",
        lambda versions, results, **kwargs: saves.append(
            (versions, results, kwargs)
        ) or 42,
    )

    persisted, run_id = rendimiento._correr_y_guardar_benchmark(
        "cpu",
        object(),
        None,
        False,
        {"kernel": "test"},
    )

    assert persisted is not result
    assert persisted["development_mode"] is False
    assert persisted["run_id"] == 42
    assert run_id == 42
    assert saves == [
        (
            {"kernel": "test"},
            [{**result, "development_mode": False}],
            {
                "run_type": "manual",
                "metadata": {"development_mode": False},
            },
        )
    ]
    assert saves[0][1][0]["response_kind"] == "mean_context_switch_us"
    assert saves[0][1][0]["p95"] is None


@pytest.mark.parametrize(
    ("tipo", "engine_name"),
    (("cpu", "correr_benchmark"), ("fork", "correr_hybrid")),
)
def test_manual_benchmark_cancellation_propagates_token_without_saving(
    rendimiento,
    monkeypatch,
    tipo,
    engine_name,
):
    monkeypatch.setattr(rendimiento.threading, "Thread", ImmediateThread)
    token = CancellationToken()
    handle = FakeHandle(token)
    button = FakeButton()
    other_button = FakeButton("other-symbolic")
    received_tokens = []
    saves = []
    toasts = []
    ui_errors = []
    result = {
        "tipo": tipo,
        "sched": "scx_test",
        "valor": 1.0,
    }

    def engine(*_args, cancel_token=None, **_kwargs):
        received_tokens.append(cancel_token)
        assert cancel_token.cancel() is True
        return result

    monkeypatch.setattr(rendimiento, engine_name, engine)
    monkeypatch.setattr(
        rendimiento,
        "guardar_run_completo",
        lambda *_args, **_kwargs: saves.append("saved"),
    )

    class Coordinator:
        def try_acquire(self, name):
            assert name == f"benchmark manual ({tipo})"
            return handle

    def run_ui(callback, *args):
        ui_errors.append(args[-1])
        return callback(*args)

    win = types.SimpleNamespace(
        en_proceso_bench=False,
        operaciones=Coordinator(),
        scx=object(),
        text_view_logs=object(),
        modo_desarrollador=False,
        versiones={"kernel": "test"},
        btns_bench=[button, other_button],
        datos_rendimiento=[],
        ejecutar_en_ui=run_ui,
        mostrar_toast=lambda message, alta=False: toasts.append((message, alta)),
    )

    rendimiento.ejecutar_benchmark(win, button, tipo)

    assert received_tokens == [token]
    assert token.cancelled is True
    assert len(ui_errors) == 1
    assert isinstance(ui_errors[0], OperationCancelled)
    assert saves == []
    assert win.datos_rendimiento == []
    assert handle.release_count == 1
    assert win.en_proceso_bench is False
    assert button.child is None
    assert button.icon == "benchmark-symbolic"
    assert button.sensitive is True
    assert other_button.sensitive is True
    assert toasts == [("Benchmark manual cancelado.", False)]


def test_manual_benchmark_exception_restores_buttons_and_releases_operation(
    rendimiento,
    monkeypatch,
):
    monkeypatch.setattr(rendimiento.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(
        rendimiento,
        "_correr_y_guardar_benchmark",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("fallo de SQLite")
        ),
    )
    handle = FakeHandle()
    button = FakeButton()
    other_button = FakeButton("other-symbolic")
    toasts = []

    class Coordinator:
        def try_acquire(self, name):
            assert name == "benchmark manual (cpu)"
            return handle

    win = types.SimpleNamespace(
        en_proceso_bench=False,
        operaciones=Coordinator(),
        scx=object(),
        text_view_logs=object(),
        modo_desarrollador=False,
        versiones={"kernel": "test"},
        btns_bench=[button, other_button],
        ejecutar_en_ui=lambda callback, *args: callback(*args),
        mostrar_toast=lambda message, alta=False: toasts.append((message, alta)),
    )

    rendimiento.ejecutar_benchmark(win, button, "cpu")

    assert handle.release_count == 1
    assert win.en_proceso_bench is False
    assert button.child is None
    assert button.icon == "benchmark-symbolic"
    assert button.sensitive is True
    assert other_button.sensitive is True
    assert toasts == [("Error en el benchmark: fallo de SQLite", True)]


def test_manual_callback_invalidated_after_worker_does_not_restore_old_mode_data(
    rendimiento,
    monkeypatch,
):
    DeferredThread.instances.clear()
    monkeypatch.setattr(rendimiento.threading, "Thread", DeferredThread)
    monkeypatch.setattr(
        rendimiento,
        "correr_benchmark",
        lambda *_args, **_kwargs: {
            "tipo": "cpu",
            "sched": "scx_old",
            "valor": 12.0,
        },
    )
    monkeypatch.setattr(
        rendimiento,
        "guardar_run_completo",
        lambda *_args, **_kwargs: 77,
    )
    handle = FakeHandle()
    button = FakeButton()
    other_button = FakeButton("other-symbolic")
    callbacks = []
    toasts = []

    class Coordinator:
        def try_acquire(self, _name):
            return handle

    win = types.SimpleNamespace(
        en_proceso_bench=False,
        operaciones=Coordinator(),
        scx=object(),
        text_view_logs=object(),
        modo_desarrollador=False,
        versiones={"kernel": "test"},
        btns_bench=[button, other_button],
        datos_rendimiento=[],
        _manual_generation=0,
        ejecutar_en_ui=lambda callback, *args: callbacks.append(
            (callback, args)
        ),
        mostrar_toast=lambda message, alta=False: toasts.append(
            (message, alta)
        ),
    )

    rendimiento.ejecutar_benchmark(win, button, "cpu")
    DeferredThread.instances[0].target()
    assert handle.release_count == 1
    assert len(callbacks) == 1

    rendimiento.invalidar_estado_rendimiento(win)
    win.modo_desarrollador = True
    callbacks[0][0](*callbacks[0][1])

    assert win.datos_rendimiento == []
    assert win._manual_development_mode is None
    assert button.child is None
    assert button.sensitive is True
    assert other_button.sensitive is True
    assert toasts == []


class FakeLabel:
    def __init__(self, text="stale"):
        self.text = text

    def set_text(self, text):
        self.text = text


class FakeRow:
    def __init__(self, title="Ganador residual", subtitle="Detalle residual"):
        self.title = title
        self.subtitle = subtitle
        self.classes = {"success", "accent"}

    def set_title(self, title):
        self.title = title

    def set_subtitle(self, subtitle):
        self.subtitle = subtitle

    def add_css_class(self, css_class):
        self.classes.add(css_class)

    def remove_css_class(self, css_class):
        self.classes.discard(css_class)


class FakeExpander:
    def __init__(self, stale_row):
        self.rows = [stale_row]
        self.subtitle = "Detalle residual"
        self.expanded = True

    def remove(self, row):
        self.rows.remove(row)

    def add_row(self, row):
        self.rows.append(row)

    def set_subtitle(self, subtitle):
        self.subtitle = subtitle

    def set_expanded(self, expanded):
        self.expanded = expanded


class FakeBuffer:
    def __init__(self):
        self.text = "logs residuales"

    def set_text(self, text):
        self.text = text


def test_manual_cleanup_removes_leader_styles_details_and_logs(rendimiento):
    filas = {}
    expanders = {}
    expander_rows = {}
    for test_type in TEST_TYPES:
        row = FakeRow()
        stale = FakeRow()
        filas[test_type] = (row, FakeLabel(), FakeLabel(), "pts")
        expanders[test_type] = FakeExpander(stale)
        expander_rows[test_type] = [stale]

    buffer = FakeBuffer()
    win = types.SimpleNamespace(
        datos_rendimiento=[{"tipo": "cpu", "valor": 1}],
        active_sc=None,
        filas_pruebas=filas,
        expanders=expanders,
        expander_rows=expander_rows,
        fila_lider_manual=FakeRow(),
        text_view_logs=types.SimpleNamespace(get_buffer=lambda: buffer),
    )

    rendimiento.limpiar_ranking(win)

    assert win.datos_rendimiento == []
    assert win.fila_lider_manual.title == "Esperando datos..."
    assert win.fila_lider_manual.subtitle.startswith("Determina el mejor")
    assert not win.fila_lider_manual.classes
    assert buffer.text == ""
    assert all(not expander.rows for expander in expanders.values())
    assert all(expander.subtitle == "Sin datos" for expander in expanders.values())
    assert all(expander.expanded is False for expander in expanders.values())
    assert all("success" not in row[0].classes for row in filas.values())


def test_manual_invalidation_clears_data_graph_and_provenance(rendimiento):
    resets = []
    win = types.SimpleNamespace(
        datos_rendimiento=[{"development_mode": True}],
        _manual_development_mode=True,
        _manual_generation=8,
        grafico=types.SimpleNamespace(reset=lambda: resets.append("reset")),
    )

    generation = rendimiento.invalidar_estado_rendimiento(win)

    assert generation == 9
    assert win.datos_rendimiento == []
    assert win._manual_development_mode is None
    assert resets == ["reset"]


def test_manual_ranking_never_mixes_real_and_development_results(
    rendimiento,
    monkeypatch,
):
    monkeypatch.setattr(
        rendimiento.Adw,
        "ActionRow",
        FakeRow,
        raising=False,
    )
    scoring_inputs = []
    monkeypatch.setattr(
        rendimiento,
        "calcular_ranking_manual",
        lambda results: scoring_inputs.append(list(results)) or {},
    )

    filas = {}
    expanders = {}
    expander_rows = {}
    for test_type in TEST_TYPES:
        row = FakeRow()
        stale = FakeRow()
        filas[test_type] = (row, FakeLabel(), FakeLabel(), "pts")
        expanders[test_type] = FakeExpander(stale)
        expander_rows[test_type] = [stale]

    win = types.SimpleNamespace(
        modo_desarrollador=False,
        datos_rendimiento=[
            {
                "tipo": "cpu",
                "sched": "scx_real",
                "valor": 10.0,
                "development_mode": False,
                "run_id": 10,
            },
            {
                "tipo": "cpu",
                "sched": "scx_dev",
                "valor": 999.0,
                "development_mode": True,
                "run_id": 20,
            },
            {
                "tipo": "cpu",
                "sched": "scx_unknown",
                "valor": 500.0,
            },
        ],
        active_sc=None,
        filas_pruebas=filas,
        expanders=expanders,
        expander_rows=expander_rows,
        fila_lider_manual=FakeRow(),
    )

    rendimiento.actualizar_interfaz_ranking(win)

    assert [item["sched"] for item in scoring_inputs[-1]] == ["scx_real"]
    assert "run_id" not in scoring_inputs[-1][0]
    assert filas["cpu"][2].text == "#1 scx_real"

    win.modo_desarrollador = True
    rendimiento.actualizar_interfaz_ranking(win)

    assert [item["sched"] for item in scoring_inputs[-1]] == ["scx_dev"]
    assert filas["cpu"][2].text == "#1 scx_dev"


def test_lazy_history_refresh_uses_public_window_callback(rendimiento):
    calls = []
    win = types.SimpleNamespace(refrescar_historial=lambda: calls.append("refresh"))

    assert rendimiento._refrescar_historial_publico(win) is True
    assert calls == ["refresh"]


def test_password_callback_contract_and_run0_policy(password_dialog):
    def async_callback(_password, _complete):
        return None

    def legacy_callback(_password):
        return None

    assert password_dialog._callback_acepta_respuesta(async_callback) is True
    assert password_dialog._callback_acepta_respuesta(legacy_callback) is False
    assert password_dialog.backend_no_requiere_password("run0") is True
    assert password_dialog.backend_no_requiere_password("direct") is True
    assert password_dialog.backend_no_requiere_password("sudo") is False


def test_sudo_success_ignores_closed_dialog_and_completes_once(
    app_module,
    password_dialog,
):
    def build_dialog(close_events):
        dialog = object.__new__(password_dialog.DialogoPassword)
        dialog._cerrado = False
        dialog._exito_aceptado = False
        dialog._validando = True
        dialog.entry = types.SimpleNamespace(set_text=lambda _text: None)
        dialog.spinner = types.SimpleNamespace(set_visible=lambda _visible: None)

        def close():
            close_events.append("close")
            password_dialog.DialogoPassword._al_cerrar(dialog)

        dialog.close = close
        return dialog

    cancelled_events = []
    cancelled = build_dialog(cancelled_events)
    password_dialog.DialogoPassword._al_cerrar(cancelled)
    win = types.SimpleNamespace(_dialogo_password=None)

    app_module.VentanaSimple._resolver_validacion_sudo(
        win,
        cancelled,
        lambda *_args: cancelled_events.append("complete"),
        lambda: cancelled_events.append("callback"),
        True,
        None,
    )

    assert cancelled.cerrado is True
    assert cancelled.cancelado is True
    assert cancelled_events == []

    success_events = []
    successful = build_dialog(success_events)
    win._dialogo_password = successful

    def continue_operation():
        assert successful.cerrado is False
        success_events.append("callback")

    for _attempt in range(2):
        app_module.VentanaSimple._resolver_validacion_sudo(
            win,
            successful,
            successful.completar_validacion,
            continue_operation,
            True,
            None,
        )

    assert success_events == ["callback", "close"]
    assert successful.cerrado is True
    assert successful.cancelado is False
    assert win._dialogo_password is None


def test_window_run0_path_never_checks_or_requests_password(app_module):
    calls = []

    class Run0Scx:
        backend_privilegiado = "run0"

        def sudo_disponible(self):
            raise AssertionError("run0 no debe comprobar sudo")

    win = types.SimpleNamespace(
        _ui_alive=True,
        modo_desarrollador=False,
        scx=Run0Scx(),
    )

    app_module.VentanaSimple.solicitar_sudo_si_necesario(
        win,
        lambda: calls.append("continue"),
    )

    assert calls == ["continue"]


def test_window_close_cancels_active_operation_before_invalidating_ui(app_module):
    ui_alive_when_cancelled = []

    class Coordinator:
        def cancel_current(self):
            ui_alive_when_cancelled.append(win._ui_alive)
            return True

    win = types.SimpleNamespace(
        _ui_alive=True,
        en_sincronizacion=True,
        en_proceso_bench=True,
        en_proceso_auto=True,
        _auth_check_en_progreso=True,
        _thermal_timer_id=None,
        _dialogo_password=None,
        operaciones=Coordinator(),
        grafico=object(),
    )

    result = app_module.VentanaSimple._al_cerrar(win)

    assert result is False
    assert ui_alive_when_cancelled == [True]
    assert win._ui_alive is False


def test_slow_database_initialization_starts_after_loading_shell_is_presented(
    app_module,
    monkeypatch,
):
    DeferredThread.instances.clear()
    monkeypatch.setattr(app_module.threading, "Thread", DeferredThread)
    events = []
    callbacks = []
    monkeypatch.setattr(
        app_module,
        "inicializar_db",
        lambda: events.append("database"),
    )
    monkeypatch.setattr(
        app_module,
        "obtener_versiones",
        lambda: {"kernel": "test"},
    )
    monkeypatch.setattr(
        app_module,
        "cargar_compatibilidad",
        lambda _kernel: {},
    )
    monkeypatch.setattr(
        app_module,
        "detectar_cambio_version",
        lambda _versions: (False, ()),
    )

    class StartupWindow:
        def __init__(self, _app):
            events.append("shell")
            self._ui_alive = True
            self._startup_in_progress = False
            self._startup_ready = False
            self._startup_generation = 0
            self._startup_thread = None

        def present(self):
            events.append("present")

        def _mostrar_carga_inicial(self):
            events.append("loading")

        def iniciar_inicializacion(self):
            return app_module.VentanaSimple.iniciar_inicializacion(self)

        def ejecutar_en_ui(self, callback, *args):
            callbacks.append((callback, args))

        def _finalizar_inicializacion(self, *args):
            events.append(("finalize", args))

    app = types.SimpleNamespace(get_active_window=lambda: None)

    window = _activar_ventana_principal(app, StartupWindow)

    assert events == ["shell", "present", "loading"]
    assert len(DeferredThread.instances) == 1
    assert DeferredThread.instances[0].daemon is False
    assert DeferredThread.instances[0].name == "reactor-startup"

    DeferredThread.instances[0].target()

    assert events[:4] == ["shell", "present", "loading", "database"]
    assert len(callbacks) == 1
    assert window._startup_in_progress is True


def test_close_during_startup_discards_late_database_error(
    app_module,
    monkeypatch,
):
    DeferredThread.instances.clear()
    monkeypatch.setattr(app_module.threading, "Thread", DeferredThread)
    monkeypatch.setattr(
        app_module,
        "_cargar_datos_iniciales",
        lambda: (_ for _ in ()).throw(RuntimeError("DB dañada")),
    )
    callbacks = []

    win = types.SimpleNamespace(
        _ui_alive=True,
        _startup_in_progress=False,
        _startup_ready=False,
        _startup_generation=0,
        _startup_thread=None,
        _mostrar_carga_inicial=lambda: None,
        _finalizar_inicializacion=lambda *_args: callbacks.append("late"),
        operaciones=types.SimpleNamespace(cancel_current=lambda: False),
        en_sincronizacion=False,
        en_proceso_bench=False,
        en_proceso_auto=False,
        _auth_check_en_progreso=False,
        _thermal_timer_id=None,
        _dialogo_password=None,
        grafico=object(),
    )
    win.ejecutar_en_ui = types.MethodType(
        app_module.VentanaSimple.ejecutar_en_ui,
        win,
    )

    assert app_module.VentanaSimple.iniciar_inicializacion(win) is True
    app_module.VentanaSimple._al_cerrar(win)
    DeferredThread.instances[0].target()

    assert win._ui_alive is False
    assert win._startup_in_progress is False
    assert callbacks == []


def test_database_startup_error_is_recoverable_and_can_retry(
    app_module,
    monkeypatch,
):
    DeferredThread.instances.clear()
    monkeypatch.setattr(app_module.threading, "Thread", DeferredThread)
    errors = []
    win = types.SimpleNamespace(
        _ui_alive=True,
        _startup_in_progress=True,
        _startup_ready=False,
        _startup_generation=4,
        _startup_thread=None,
        _startup_error=None,
        _mostrar_error_inicializacion=lambda error: errors.append(str(error)),
        _mostrar_carga_inicial=lambda: None,
        ejecutar_en_ui=lambda *_args: None,
    )

    result = app_module.VentanaSimple._finalizar_inicializacion(
        win,
        4,
        None,
        "SQLite no disponible",
    )

    assert result is False
    assert win._startup_in_progress is False
    assert errors == ["SQLite no disponible"]
    assert app_module.VentanaSimple.iniciar_inicializacion(win) is True
    assert win._startup_generation == 5
    assert len(DeferredThread.instances) == 1


def test_thermal_tick_preserves_indicator_while_operation_is_busy(app_module):
    sensor_reads = []
    indicator_changes = []

    def record(change):
        return lambda *_args: indicator_changes.append(change)

    win = types.SimpleNamespace(
        _ui_alive=True,
        operaciones=types.SimpleNamespace(is_busy=True),
        sensor=types.SimpleNamespace(
            obtener_temp=lambda: sensor_reads.append("read") or 80.0,
        ),
        img_termica=types.SimpleNamespace(
            set_from_icon_name=record("icon"),
            remove_css_class=record("remove-class"),
            add_css_class=record("add-class"),
        ),
        btn_termica=types.SimpleNamespace(
            set_tooltip_text=record("tooltip"),
        ),
        lbl_termica_detail=types.SimpleNamespace(
            set_label=record("label"),
        ),
    )

    keep_timer = app_module.VentanaSimple.actualizar_sensor_termico(win)

    assert keep_timer is True
    assert sensor_reads == []
    assert indicator_changes == []


def test_system_sync_is_deferred_and_acquires_without_wait(
    app_module,
    monkeypatch,
):
    DeferredThread.instances.clear()
    monkeypatch.setattr(app_module.threading, "Thread", DeferredThread)
    handle = FakeHandle()
    acquired = []

    class Coordinator:
        state = None

        def try_acquire(self, name):
            acquired.append(name)
            return handle

    class Scx:
        ultimo_error = None

        def obtener_lista(self, *, cancel_token=None):
            raise AssertionError("la consulta no debe ejecutarse en GTK")

    win = types.SimpleNamespace(
        _ui_alive=True,
        en_sincronizacion=False,
        operaciones=Coordinator(),
        compatibles=None,
        scx=Scx(),
        mostrar_operacion_ocupada=lambda: None,
        mostrar_toast=lambda *_args, **_kwargs: None,
    )

    app_module.VentanaSimple.sincronizar_sistema(win)

    assert acquired == ["sincronización del sistema"]
    assert win.en_sincronizacion is True
    assert len(DeferredThread.instances) == 1
    assert DeferredThread.instances[0].started is True
    assert handle.release_count == 0


def test_close_before_sync_worker_prevents_scx_queries(app_module, monkeypatch):
    DeferredThread.instances.clear()
    monkeypatch.setattr(app_module.threading, "Thread", DeferredThread)
    handle = FakeHandle()
    callbacks = []

    class Coordinator:
        state = None

        def try_acquire(self, _name):
            return handle

    class Scx:
        ultimo_error = None

        def obtener_lista(self, **_kwargs):
            raise AssertionError("no debe consultar la lista tras el cierre")

        def capturar_estado(self, **_kwargs):
            raise AssertionError("no debe consultar el estado tras el cierre")

    win = types.SimpleNamespace(
        _ui_alive=True,
        modo_desarrollador=False,
        en_sincronizacion=False,
        operaciones=Coordinator(),
        scx=Scx(),
        mostrar_operacion_ocupada=lambda: None,
        mostrar_toast=lambda *_args, **_kwargs: None,
        ejecutar_en_ui=lambda callback, *args: callbacks.append(
            (callback, args)
        ),
        _finalizar_sincronizacion=lambda *_args: None,
    )

    app_module.VentanaSimple.sincronizar_sistema(win)
    assert handle.token.cancel() is True
    win._ui_alive = False
    DeferredThread.instances[0].target()

    assert handle.release_count == 1
    assert len(callbacks) == 1
    assert isinstance(callbacks[0][1][1], OperationCancelled)


def test_stale_development_sync_callback_is_discarded_and_relaunched(
    app_module,
    monkeypatch,
):
    monkeypatch.setattr(app_module.threading, "Thread", ImmediateThread)
    callbacks = []
    refreshes = []
    handles = []

    class Coordinator:
        state = None

        def try_acquire(self, _name):
            handle = FakeHandle()
            handles.append(handle)
            return handle

    class Scx:
        ultimo_error = None

        def obtener_lista(self, *, cancel_token=None):
            assert cancel_token is handles[-1].token
            if win.modo_desarrollador:
                return ["scx_dev_bad"]
            return ["scx_ok", "scx_bad"]

        def capturar_estado(self, *, cancel_token=None):
            assert cancel_token is handles[-1].token
            return RealScxState()

    def reload_availability(target, names):
        refreshes.append(("availability", list(names)))
        target.compatibles = ["scx_ok"] if not target.modo_desarrollador else None

    monkeypatch.setattr(
        sys.modules["ui.disponibilidad"],
        "recargar_disponibilidad_ui",
        reload_availability,
    )
    monkeypatch.setattr(
        sys.modules["ui.automatizacion"],
        "_refrescar_auto_schedulers",
        lambda _win, names: refreshes.append(("auto", list(names))),
    )

    win = types.SimpleNamespace(
        _ui_alive=True,
        modo_desarrollador=True,
        _sync_generation=0,
        _scheduler_snapshot=(),
        en_sincronizacion=False,
        operaciones=Coordinator(),
        compatibles=None,
        scx=Scx(),
        boton_estado=FakeStatusButton(),
        modelo_schedulers=FakeStringList(),
        btn_aplicar_configuracion=FakeSensitiveButton(True),
        _actualizando_configuracion=False,
        _configuracion_pendiente=True,
        active_sc="scx_old",
        mostrar_operacion_ocupada=lambda: None,
        mostrar_toast=lambda *_args, **_kwargs: None,
        ejecutar_en_ui=lambda callback, *args: callbacks.append(
            (callback, args)
        ),
    )
    win.sincronizar_sistema = lambda: (
        app_module.VentanaSimple.sincronizar_sistema(win)
    )
    win._finalizar_sincronizacion = lambda *args: (
        app_module.VentanaSimple._finalizar_sincronizacion(win, *args)
    )

    win.sincronizar_sistema()
    assert len(callbacks) == 1
    assert handles[0].release_count == 1

    win.modo_desarrollador = False
    win._sync_generation += 1
    old_callback, old_args = callbacks.pop(0)
    old_callback(*old_args)

    assert refreshes == []
    assert win._scheduler_snapshot == ()
    assert len(callbacks) == 1

    current_callback, current_args = callbacks.pop(0)
    current_callback(*current_args)

    assert len(handles) == 2
    assert all(handle.release_count == 1 for handle in handles)
    assert win._scheduler_snapshot == ("scx_ok", "scx_bad")
    assert win.modelo_schedulers.items == ["scx_ok"]
    assert refreshes == [
        ("availability", ["scx_ok", "scx_bad"]),
        ("auto", ["scx_ok", "scx_bad"]),
    ]


def test_stale_sync_relaunch_waits_until_coordinator_is_free(
    app_module,
    monkeypatch,
):
    timers = []
    monkeypatch.setattr(
        app_module.GLib,
        "timeout_add",
        lambda interval, callback: timers.append((interval, callback)) or 91,
        raising=False,
    )
    operations = types.SimpleNamespace(is_busy=True)
    syncs = []
    win = types.SimpleNamespace(
        _ui_alive=True,
        en_sincronizacion=False,
        operaciones=operations,
        _sync_retry_timer_id=None,
        sincronizar_sistema=lambda: syncs.append("sync"),
    )

    result = app_module.VentanaSimple._programar_sincronizacion_actual(win)

    assert result is False
    assert win._sync_retry_timer_id == 91
    assert timers[0][0] == 100
    assert syncs == []

    operations.is_busy = False
    assert timers[0][1]() is False
    assert win._sync_retry_timer_id is None
    assert syncs == ["sync"]


class FakeStatusButton:
    def __init__(self):
        self.label = ""
        self.classes = set()

    def set_label(self, label):
        self.label = label

    def add_css_class(self, css_class):
        self.classes.add(css_class)

    def remove_css_class(self, css_class):
        self.classes.discard(css_class)


class FakeStringList:
    def __init__(self):
        self.items = ["old"]

    def get_n_items(self):
        return len(self.items)

    def splice(self, _position, _removed, additions):
        self.items = list(additions)


def test_cache_with_zero_compatible_schedulers_remains_verified(app_module):
    cache = {
        "scx_bad": (False, "incompatible", 1.0),
        "scx_worse": (False, "incompatible", 2.0),
    }

    assert app_module._compatibles_desde_cache(cache) == []
    assert app_module._compatibles_desde_cache({}) is None
    assert app_module._compatibles_desde_cache(
        {"scx_corrupt": "metadata rota"}
    ) is None
    assert app_module._compatibles_desde_cache(
        {"scx_truncated": (True,)}
    ) is None


def test_corrupt_startup_metadata_degrades_without_breaking_setup(
    app_module,
    monkeypatch,
):
    monkeypatch.setattr(app_module, "inicializar_db", lambda: None)
    monkeypatch.setattr(
        app_module,
        "obtener_versiones",
        lambda: {"kernel": "test"},
    )
    monkeypatch.setattr(
        app_module,
        "cargar_compatibilidad",
        lambda _kernel: {"scx_corrupt": {"unexpected": True}},
    )
    monkeypatch.setattr(
        app_module,
        "detectar_cambio_version",
        lambda _versions: ("false", ["kernel"]),
    )

    data = app_module._cargar_datos_iniciales()

    assert data["versiones"] == {"kernel": "test"}
    assert data["compatibles"] is None
    assert data["cambio_version"] is False
    assert data["componentes"] == ()


def test_not_running_sync_uses_strict_scx_state(
    app_module,
    monkeypatch,
):
    monkeypatch.setattr(app_module.threading, "Thread", ImmediateThread)
    calls = []
    handle = FakeHandle()

    class Coordinator:
        def try_acquire(self, name):
            assert name == "sincronización del sistema"
            return handle

    class Scx:
        ultimo_error = None

        def obtener_lista(self, *, cancel_token=None):
            assert cancel_token is handle.token
            calls.append("list")
            return ["scx_foreign"]

        def capturar_estado(self, *, cancel_token=None):
            assert cancel_token is handle.token
            calls.append("state")
            return STRICT_NOT_RUNNING_STATE

        def scx_run(self, _command):
            raise AssertionError("app.py no debe volver a parsear scxctl get")

    button = FakeStatusButton()
    apply_button = FakeSensitiveButton(True)
    toasts = []
    win = types.SimpleNamespace(
        _ui_alive=True,
        en_sincronizacion=False,
        operaciones=Coordinator(),
        compatibles=None,
        scx=Scx(),
        boton_estado=button,
        modelo_schedulers=FakeStringList(),
        btn_aplicar_configuracion=apply_button,
        _actualizando_configuracion=False,
        _configuracion_pendiente=True,
        active_sc="scx_old",
        mostrar_operacion_ocupada=lambda: None,
        mostrar_toast=lambda message, alta=False: toasts.append(
            (message, alta)
        ),
        ejecutar_en_ui=lambda callback, *args: callback(*args),
    )
    win._finalizar_sincronizacion = lambda data, error, generation, dev: (
        app_module.VentanaSimple._finalizar_sincronizacion(
            win,
            data,
            error,
            generation,
            dev,
        )
    )

    app_module.VentanaSimple.sincronizar_sistema(win)

    assert calls == ["list", "state"]
    assert handle.release_count == 1
    assert win.active_sc is None, toasts
    assert button.label == "STOPPED (Sistema Base)"
    assert button.classes == {"destructive-action"}
    assert win._configuracion_pendiente is False
    assert apply_button.sensitive is False


def test_sync_refreshes_views_from_raw_snapshot_without_querying_scx(
    app_module,
    monkeypatch,
):
    refreshes = []
    disponibilidad = sys.modules["ui.disponibilidad"]
    automatizacion = sys.modules["ui.automatizacion"]
    monkeypatch.setattr(
        disponibilidad,
        "recargar_disponibilidad_ui",
        lambda target, nombres: (
            setattr(target, "compatibles", ["scx_ok"]),
            refreshes.append(("availability", list(nombres))),
        )[-1],
    )
    monkeypatch.setattr(
        automatizacion,
        "_refrescar_auto_schedulers",
        lambda _win, nombres: refreshes.append(("auto", list(nombres))),
    )

    model = FakeStringList()
    win = types.SimpleNamespace(
        boton_estado=FakeStatusButton(),
        modelo_schedulers=model,
        active_sc="scx_old",
        en_sincronizacion=True,
        mostrar_toast=lambda *_args, **_kwargs: None,
    )
    data = {
        "lista": ["scx_ok", "scx_bad"],
        "lista_controles": ["scx_ok"],
        "error_lista": None,
        "estado": RealScxState(),
        "error_estado": None,
    }

    app_module.VentanaSimple._finalizar_sincronizacion(win, data, None)

    assert model.items == ["scx_ok"]
    assert refreshes == [
        ("availability", ["scx_ok", "scx_bad"]),
        ("auto", ["scx_ok", "scx_bad"]),
    ]
    assert win.en_sincronizacion is False


def test_state_failure_does_not_discard_successful_list_snapshot(
    app_module,
    monkeypatch,
):
    refreshes = []
    monkeypatch.setattr(
        sys.modules["ui.disponibilidad"],
        "recargar_disponibilidad_ui",
        lambda _win, nombres: refreshes.append(list(nombres)),
    )
    toasts = []
    model = FakeStringList()
    win = types.SimpleNamespace(
        boton_estado=FakeStatusButton(),
        modelo_schedulers=model,
        active_sc="scx_known",
        en_sincronizacion=True,
        mostrar_toast=lambda message, alta=False: toasts.append(
            (message, alta)
        ),
    )
    data = {
        "lista": ["scx_new"],
        "lista_controles": ["scx_new"],
        "error_lista": None,
        "estado": None,
        "error_estado": "estado ilegible",
    }

    app_module.VentanaSimple._finalizar_sincronizacion(win, data, None)

    assert model.items == ["scx_new"]
    assert refreshes == [["scx_new"]]
    assert win.active_sc == "scx_known"
    assert win.boton_estado.label == "Error al actualizar estado"
    assert (
        "No se actualizó el estado SCX: estado ilegible",
        True,
    ) in toasts


def test_list_failure_does_not_discard_successful_state(app_module):
    button = FakeStatusButton()
    model = FakeStringList()
    win = types.SimpleNamespace(
        boton_estado=button,
        modelo_schedulers=model,
        active_sc="scx_old",
        en_sincronizacion=True,
        mostrar_toast=lambda *_args, **_kwargs: None,
    )
    data = {
        "lista": None,
        "lista_controles": None,
        "error_lista": "falló scxctl list",
        "estado": RealScxState(),
        "error_estado": None,
    }

    app_module.VentanaSimple._finalizar_sincronizacion(win, data, None)

    assert model.items == ["old"]
    assert win.active_sc is None
    assert button.label == "STOPPED (Sistema Base)"
    assert win.en_sincronizacion is False


def test_stopped_sync_clears_active_scheduler_and_flag(app_module):
    button = FakeStatusButton()
    model = FakeStringList()
    win = types.SimpleNamespace(
        boton_estado=button,
        modelo_schedulers=model,
        active_sc="scx_old",
        en_sincronizacion=True,
        mostrar_toast=lambda *_args, **_kwargs: None,
    )
    data = {
        "lista": ["scx_test"],
        "lista_controles": ["scx_test"],
        "error_lista": None,
        "estado": RealScxState(),
        "error_estado": None,
    }

    app_module.VentanaSimple._finalizar_sincronizacion(win, data, None)

    assert win.active_sc is None
    assert win.en_sincronizacion is False
    assert button.label == "STOPPED (Sistema Base)"
    assert button.classes == {"destructive-action"}
    assert model.items == ["scx_test"]
