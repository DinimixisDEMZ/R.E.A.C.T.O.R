import importlib.util
from pathlib import Path
import sys
import types

import pytest

from core.operations import CancellationToken, OperationCoordinator


ROOT = Path(__file__).resolve().parents[1]


class _Widget:
    def __init__(self, *, label=None, sensitive=True, visible=True, value=0, active=False):
        self.label = label
        self.sensitive = sensitive
        self.visible = visible
        self.value = value
        self.active = active
        self.css_classes = set()

    def set_label(self, value):
        self.label = value

    def set_sensitive(self, value):
        self.sensitive = value

    def set_visible(self, value):
        self.visible = value

    def set_value(self, value):
        self.value = value

    def get_value(self):
        return self.value

    def set_active(self, value):
        self.active = value

    def get_active(self):
        return self.active

    def add_css_class(self, value):
        self.css_classes.add(value)

    def remove_css_class(self, value):
        self.css_classes.discard(value)

    def set_title(self, value):
        self.title = value

    def set_subtitle(self, value):
        self.subtitle = value

    def set_expanded(self, value):
        self.expanded = value

    def set_icon_name(self, value):
        self.icon_name = value

    def set_reveal_child(self, value):
        self.reveal_child = value


class _DeferredGLib:
    def __init__(self):
        self.pending = []

    def idle_add(self, callback, *args):
        self.pending.append((callback, args))
        return len(self.pending)

    @staticmethod
    def source_remove(_source_id):
        return True

    def drain(self):
        results = []
        while self.pending:
            callback, args = self.pending.pop(0)
            results.append(callback(*args))
        return results


@pytest.fixture
def automatizacion(monkeypatch):
    glib = _DeferredGLib()
    gi = types.ModuleType("gi")
    gi.require_version = lambda *_args: None
    repository = types.ModuleType("gi.repository")
    repository.Gtk = types.SimpleNamespace()
    repository.Adw = types.SimpleNamespace()
    repository.GLib = glib
    gi.repository = repository
    monkeypatch.setitem(sys.modules, "gi", gi)
    monkeypatch.setitem(sys.modules, "gi.repository", repository)

    benchmark = types.ModuleType("core.benchmark")
    benchmark.correr_benchmark = lambda *_args, **_kwargs: None
    hybrid = types.ModuleType("core.hybrid")
    hybrid.correr_hybrid = lambda *_args, **_kwargs: None
    scoring = types.ModuleType("core.scoring")
    scoring.calcular_scores_finales = lambda *_args, **_kwargs: {}
    scoring.calcular_valor_grafico = lambda *_args, **_kwargs: 0.0
    scoring._MAPA_CHART = {}
    scoring.HYBRID_TYPES = {"fork", "compile", "loaded"}
    database = types.ModuleType("core.database")
    database.guardar_run_completo = lambda *_args, **_kwargs: 1
    database.consultar_runs_auto = lambda: []
    database.cargar_resultados_de_run = lambda _run_id: []
    helpers = types.ModuleType("utils.helpers")
    helpers.log = lambda *_args, **_kwargs: None
    legend = types.ModuleType("widgets.legend")
    legend.crear_chip_leyenda = lambda *_args, **_kwargs: None
    disponibilidad = types.ModuleType("ui.disponibilidad")
    disponibilidad.contexto_compatibilidad_actual = (
        lambda win, _nombres: win._computed_compatibility_context
    )

    for name, module in {
        "core.benchmark": benchmark,
        "core.hybrid": hybrid,
        "core.scoring": scoring,
        "core.database": database,
        "utils.helpers": helpers,
        "widgets.legend": legend,
        "ui.disponibilidad": disponibilidad,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    module_name = "_reactor_test_auto_final_safety"
    spec = importlib.util.spec_from_file_location(
        module_name,
        ROOT / "ui" / "automatizacion.py",
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, module)
    spec.loader.exec_module(module)
    module._deferred_glib = glib
    return module


def _secure_win():
    toasts = []
    win = types.SimpleNamespace(
        _ui_alive=True,
        _auto_generation=7,
        _auto_contexto_aplicable=True,
        _auto_pesos_validos=True,
        _auto_permitir_aplicar=True,
        _auto_development_mode=False,
        _auto_source_status="completed",
        _auto_source_versions={"kernel": "kernel-a", "scxctl": "scxctl-a"},
        _auto_compatibility_context="context-a",
        _compatibility_context="context-a",
        _computed_compatibility_context="context-a",
        _scheduler_snapshot=["scx_lavd", "scx_rusty"],
        compatibles=["scx_lavd", "scx_rusty"],
        versiones={"kernel": "kernel-a", "scxctl": "scxctl-a"},
        ganador_final="scx_lavd",
        modo_desarrollador=False,
        btn_aplicar_recomendado=_Widget(label="Aplicar recomendado"),
        _auto_apply_operation_id=None,
    )
    win.mostrar_toast = (
        lambda message, *, alta=False: toasts.append((message, alta))
    )
    win._toasts = toasts
    return win


def _auth_win():
    win = _secure_win()
    win.en_proceso_auto = False
    win._auto_sched_checks = {
        "scx_lavd": (None, _Widget(active=True)),
        "scx_rusty": (None, _Widget(active=True)),
    }
    win.slider_pot = _Widget(value=45)
    win.slider_resp = _Widget(value=45)
    win.slider_flu = _Widget(value=10)
    win._auth_callbacks = []
    win._acquisitions = []
    win.solicitar_sudo_si_necesario = win._auth_callbacks.append
    win.mostrar_operacion_ocupada = lambda: None
    win.operaciones = types.SimpleNamespace(
        try_acquire=lambda name: win._acquisitions.append(name) or None,
    )
    return win


def test_cancel_release_toggle_y_callbacks_idle_viejos_no_pisan_run_nuevo(
    automatizacion,
):
    coordinator = OperationCoordinator()
    handle = coordinator.try_acquire("automatizacion")
    win = _secure_win()
    win.operaciones = coordinator
    win.en_proceso_auto = True
    win._auto_operation_id = handle.operation_id
    win._auto_progress_timer_id = None
    win._auto_hide_timer_id = None
    win.btn_auto = _Widget(label="Detener")
    win.btn_auto.css_classes.add("destructive-action")
    win.text_view_logs_auto = object()

    automatizacion.gestionar_click_auto(win, win.btn_auto)
    assert win.btn_auto.label == "Deteniendo..."
    assert handle.token.cancelled is True

    handle.release()
    automatizacion._programar_ui(
        win,
        automatizacion._finalizar_worker_auto,
        win,
        {"generation": 7},
    )
    win._auto_apply_operation_id = 99
    automatizacion._programar_ui(
        win,
        automatizacion._finalizar_aplicacion_recomendada,
        win,
        "scx_lavd",
        None,
        7,
        99,
    )

    nueva_generacion = automatizacion.invalidar_estado_automatizacion(win)
    assert win.btn_auto.label == "Determinar"
    assert win.btn_auto.sensitive is True
    assert "suggested-action" in win.btn_auto.css_classes
    assert "destructive-action" not in win.btn_auto.css_classes
    assert win._auto_apply_operation_id is None

    win.en_proceso_auto = True
    win._auto_operation_id = 200
    win.ganador_final = "run-nuevo"
    win._scores_finales = {"run-nuevo": {"score": 100.0}}
    win.btn_auto.set_label("Detener nuevo")
    results = automatizacion._deferred_glib.drain()

    assert results == [False, False]
    assert win._auto_generation == nueva_generacion
    assert win._auto_operation_id == 200
    assert win.ganador_final == "run-nuevo"
    assert win.btn_auto.label == "Detener nuevo"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda win: win._auto_sched_checks["scx_lavd"][1].set_active(False),
        lambda win: win.slider_pot.set_value(70),
        lambda win: setattr(win, "modo_desarrollador", True),
        lambda win: setattr(win, "_scheduler_snapshot", ["scx_rusty"]),
        lambda win: (
            setattr(win, "_compatibility_context", "context-b"),
            setattr(win, "_computed_compatibility_context", "context-b"),
        ),
    ],
    ids=("checks", "weights", "mode", "snapshot", "context"),
)
def test_configuracion_mutada_durante_auth_aborta_antes_de_adquirir(
    automatizacion,
    mutate,
):
    win = _auth_win()
    automatizacion.gestionar_click_auto(win, _Widget())
    assert len(win._auth_callbacks) == 1

    mutate(win)
    win._auth_callbacks[0]()

    assert win._acquisitions == []
    assert win._toasts[-1][1] is True


def test_revalidacion_post_auth_no_vuelve_a_mezclar_seleccion(
    automatizacion,
    monkeypatch,
):
    win = _auth_win()
    seeds = []
    shuffles = []
    original_shuffle = automatizacion.random.Random.shuffle

    def track_shuffle(randomizer, values):
        shuffles.append(tuple(values))
        return original_shuffle(randomizer, values)

    monkeypatch.setattr(
        automatizacion.secrets,
        "randbits",
        lambda _bits: seeds.append(73) or 73,
    )
    monkeypatch.setattr(automatizacion.random.Random, "shuffle", track_shuffle)

    automatizacion.gestionar_click_auto(win, _Widget())
    win._auth_callbacks[0]()

    assert seeds == [73]
    assert len(shuffles) == 1
    assert win._acquisitions == ["automatizacion"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda win: (
            win.slider_pot.set_value(0),
            win.slider_resp.set_value(0),
            win.slider_flu.set_value(0),
        ),
        lambda win: setattr(win, "compatibles", ["scx_rusty"]),
        lambda win: setattr(win, "_computed_compatibility_context", "context-b"),
    ],
    ids=("weights", "cache-membership", "cache-context"),
)
def test_seleccion_pesos_y_cache_se_validan_antes_de_auth(
    automatizacion,
    mutate,
):
    win = _auth_win()
    mutate(win)

    automatizacion.gestionar_click_auto(win, _Widget())

    assert win._auth_callbacks == []
    assert win._acquisitions == []
    assert win._toasts[-1][1] is True


@pytest.mark.parametrize(
    "mutate",
    [
        lambda win: setattr(win, "_scheduler_snapshot", ["scx_rusty"]),
        lambda win: setattr(win, "compatibles", ["scx_rusty"]),
        lambda win: setattr(win, "compatibles", None),
        lambda win: win.versiones.__setitem__("kernel", "kernel-b"),
        lambda win: win.versiones.__setitem__("scxctl", "scxctl-b"),
        lambda win: setattr(win, "_auto_compatibility_context", "context-old"),
        lambda win: setattr(win, "_auto_source_status", "partial"),
        lambda win: setattr(win, "modo_desarrollador", True),
    ],
    ids=(
        "scheduler-missing",
        "incompatible",
        "cache-unverified",
        "kernel",
        "scxctl",
        "compatibility-context",
        "status",
        "development-mode",
    ),
)
def test_recomendacion_actual_se_invalida_si_diverge_entorno(
    automatizacion,
    mutate,
):
    win = _secure_win()
    assert automatizacion._recomendacion_aplicable_actual(win, "scx_lavd") is True

    mutate(win)

    assert automatizacion._recomendacion_aplicable_actual(win, "scx_lavd") is False


def test_sistema_base_no_requiere_binario_compatible(automatizacion):
    win = _secure_win()
    win.ganador_final = automatizacion.BASE_SYSTEM_NAME

    assert (
        automatizacion._recomendacion_aplicable_actual(
            win,
            automatizacion.BASE_SYSTEM_NAME,
        )
        is True
    )

    win._computed_compatibility_context = "context-b"
    assert (
        automatizacion._recomendacion_aplicable_actual(
            win,
            automatizacion.BASE_SYSTEM_NAME,
        )
        is False
    )


def _historical_case(automatizacion):
    order = (automatizacion.BASE_SYSTEM_NAME, "scx_lavd")
    brutos = {
        scheduler: {
            test_type: {
                "tipo": test_type,
                "sched": scheduler,
                "valor": 100.0,
            }
            for test_type in automatizacion.TIPOS_CANONICOS
        }
        for scheduler in order
    }
    scores = {
        automatizacion.BASE_SYSTEM_NAME: {
            "score": 90.0,
            "pot": 90.0,
            "resp": 90.0,
            "flu": 90.0,
        },
        "scx_lavd": {
            "score": 100.0,
            "pot": 100.0,
            "resp": 100.0,
            "flu": 100.0,
        },
    }
    run = {
        "status": "completed",
        "kernel_version": "kernel-a",
        "scxctl_version": "scxctl-a",
        "metadata": {
            "status": "completed",
            "candidate_order": list(order),
            "development_mode": False,
            "compatibility_context": "context-a",
            "scheduler_snapshot": ["scx_lavd", "scx_rusty"],
            "effective_weights": {
                "potencia": 0.45,
                "respuesta": 0.45,
                "fluidez": 0.10,
            },
            "configuration": {
                "development_mode": False,
                "compatibility_context": "context-a",
                "scheduler_snapshot": ["scx_lavd", "scx_rusty"],
                "seleccionados": ["scx_lavd"],
                "selected_scx": ["scx_lavd"],
            },
        },
    }
    return run, brutos, scores


@pytest.mark.parametrize(
    "mutate",
    [
        lambda _win, run: run.__setitem__("status", "partial"),
        lambda _win, run: run.__setitem__("kernel_version", "kernel-b"),
        lambda _win, run: run.__setitem__("scxctl_version", "scxctl-b"),
        lambda win, _run: setattr(win, "compatibles", []),
        lambda win, _run: setattr(win, "_scheduler_snapshot", ["scx_rusty"]),
        lambda _win, run: run["metadata"].__setitem__(
            "compatibility_context", "context-b"
        ),
        lambda _win, run: run["metadata"].pop("effective_weights"),
        lambda _win, run: run["metadata"]["configuration"].__setitem__(
            "development_mode", True
        ),
        lambda _win, run: run.__setitem__("metadata", {"status": "completed"}),
    ],
    ids=(
        "status",
        "kernel",
        "scxctl",
        "incompatible",
        "scheduler-missing",
        "context",
        "weights",
        "contradictory-mode",
        "corrupt-metadata",
    ),
)
def test_run_historico_divergente_solo_se_muestra_como_referencia(
    automatizacion,
    mutate,
):
    win = _secure_win()
    run, brutos, scores = _historical_case(automatizacion)
    assert (
        automatizacion._motivo_run_historico_no_aplicable(
            win, run, brutos, scores
        )
        is None
    )

    mutate(win, run)

    assert automatizacion._motivo_run_historico_no_aplicable(
        win, run, brutos, scores
    )


def test_run_historico_ganado_por_sistema_base_es_aplicable(automatizacion):
    win = _secure_win()
    run, brutos, scores = _historical_case(automatizacion)
    scores[automatizacion.BASE_SYSTEM_NAME]["score"] = 110.0
    win.compatibles = []

    assert (
        automatizacion._motivo_run_historico_no_aplicable(
            win,
            run,
            brutos,
            scores,
        )
        is None
    )


def test_run_vivo_e_historico_guardan_versiones_y_contexto(
    automatizacion,
    monkeypatch,
):
    win = _secure_win()
    win.revealer_pesos = _Widget()
    win.fila_ganador = _Widget()
    monkeypatch.setattr(automatizacion, "finalizar_auto_test", lambda *_args: True)

    automatizacion._finalizar_worker_auto(
        win,
        {
            "generation": 7,
            "scores": {"scx_lavd": {"score": 100.0}},
            "status": "completed",
            "development_mode": False,
            "source_versions": {"kernel": "kernel-live", "scxctl": "scxctl-live"},
            "compatibility_context": "context-live",
        },
    )

    assert win._auto_source_versions == {
        "kernel": "kernel-live",
        "scxctl": "scxctl-live",
    }
    assert win._auto_compatibility_context == "context-live"

    run, brutos, scores = _historical_case(automatizacion)
    run["id"] = 41
    win._historial_runs = [run]
    win._indice_historial = -1
    win._auto_progress_timer_id = None
    win._auto_hide_timer_id = None
    win._filas_ranking = []
    win.btn_auto = _Widget()
    win.slider_pot = _Widget()
    win.slider_resp = _Widget()
    win.slider_flu = _Widget()
    win._lbl_pot = _Widget()
    win._lbl_resp = _Widget()
    win._lbl_flu = _Widget()
    win._ajustando_pesos = False
    win._pesos_auto_efectivos = (0.45, 0.45, 0.10)
    persisted = [
        {
            "scheduler_name": scheduler,
            "test_type": test_type,
            "valor": result["valor"],
        }
        for scheduler, scheduler_results in brutos.items()
        for test_type, result in scheduler_results.items()
    ]
    monkeypatch.setattr(
        automatizacion,
        "cargar_resultados_de_run",
        lambda _run_id: persisted,
    )
    monkeypatch.setattr(
        automatizacion,
        "_poblar_grafico_desde_brutos",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        automatizacion,
        "_poblar_ranking",
        lambda *_args, **_kwargs: scores,
    )
    monkeypatch.setattr(
        automatizacion,
        "_actualizar_botones_nav",
        lambda *_args: None,
    )

    assert automatizacion._cargar_run_historico(win, 0) is True
    assert win._auto_source_versions == {
        "kernel": "kernel-a",
        "scxctl": "scxctl-a",
    }
    assert win._auto_compatibility_context == "context-a"


def test_invalidar_durante_apply_normaliza_etiqueta_sin_liberar_id_activo(
    automatizacion,
):
    coordinator = OperationCoordinator()
    handle = coordinator.try_acquire("aplicar recomendado")
    win = _secure_win()
    win.operaciones = coordinator
    win._auto_progress_timer_id = None
    win._auto_hide_timer_id = None
    win._auto_apply_operation_id = handle.operation_id
    win.btn_auto = _Widget(label="Detener")
    win.btn_aplicar_recomendado.set_label("Aplicando...")
    old_generation = win._auto_generation

    automatizacion.invalidar_estado_automatizacion(win)

    assert win._auto_apply_operation_id == handle.operation_id
    assert win.btn_aplicar_recomendado.label == "Aplicar recomendado"
    handle.release()
    assert (
        automatizacion._finalizar_aplicacion_recomendada(
            win,
            "scx_lavd",
            None,
            old_generation,
            handle.operation_id,
        )
        is False
    )
    assert win._auto_apply_operation_id is None
    assert win.btn_aplicar_recomendado.label == "Aplicar recomendado"


def test_analisis_aplica_candidatos_mediante_sesion(
    automatizacion,
    monkeypatch,
):
    applied = []

    class Session:
        def aplicar(self, state):
            applied.append(state)

    class Manager:
        def restaurar_estado(self, _state):
            raise AssertionError("el análisis no debe aplicar mediante el manager")

    adapter = automatizacion._ScxEnSesion(Manager(), Session())
    current = {"scheduler": automatizacion.BASE_SYSTEM_NAME}

    def apply_and_track(state):
        applied.append(state)
        current["scheduler"] = state.scheduler or automatizacion.BASE_SYSTEM_NAME

    adapter._sesion.aplicar = apply_and_track

    def result_for(test_type, *_args, **_kwargs):
        canonical = {
            "fork": "latencia_fork",
            "compile": "latencia_compile",
            "loaded": "latencia_loaded",
        }.get(test_type, test_type)
        return {
            "tipo": canonical,
            "sched": current["scheduler"],
            "valor": 100.0,
        }

    monkeypatch.setattr(automatizacion, "correr_benchmark", result_for)
    monkeypatch.setattr(automatizacion, "correr_hybrid", result_for)
    monkeypatch.setattr(automatizacion, "_calibrar_termica", lambda *_args: None)
    monkeypatch.setattr(automatizacion, "_esperar_cancelable", lambda *_args: None)
    monkeypatch.setattr(automatizacion, "_programar_ui", lambda *_args: None)

    automatizacion._ejecutar_mediciones(
        object(),
        adapter,
        types.SimpleNamespace(obtener_temp=lambda: 0.0),
        CancellationToken(),
        (automatizacion.BASE_SYSTEM_NAME, "scx_lavd"),
        (0.45, 0.45, 0.10),
        False,
        object(),
        {},
        [],
        {},
        1,
    )

    assert applied == [
        automatizacion.ScxState(),
        automatizacion.ScxState("scx_lavd", "auto"),
    ]


class _ImmediateThread:
    def __init__(self, *, target):
        self.target = target

    def start(self):
        self.target()


def test_conservacion_del_ganador_adapta_la_api_scx_actual(automatizacion):
    calls = []
    session = types.SimpleNamespace(
        conservar_ganador=lambda scheduler, mode: calls.append((scheduler, mode))
    )

    automatizacion._conservar_ganador_en_sesion(
        session,
        automatizacion.ScxState("scx_lavd", "auto"),
    )

    assert calls == [("scx_lavd", "auto")]


def test_aplicar_recomendado_usa_aplicar_y_conserva_estado_actual(
    automatizacion,
    monkeypatch,
):
    events = []

    class Session:
        def __enter__(self):
            events.append("enter")
            return self

        def aplicar(self, state):
            events.append(("apply", state))

        def keep_current_as_winner(self):
            events.append("keep")

        def __exit__(self, *_args):
            events.append("exit")
            return False

    class Manager:
        def sesion(self, _token):
            return Session()

        def restaurar_estado(self, _state):
            raise AssertionError("no debe existir rollback directo")

    monkeypatch.setattr(automatizacion.threading, "Thread", _ImmediateThread)
    win = _secure_win()
    win.operaciones = OperationCoordinator()
    win.scx = Manager()

    automatizacion._iniciar_aplicacion_recomendada(win, "scx_lavd")

    assert events == [
        "enter",
        ("apply", automatizacion.ScxState("scx_lavd", "auto")),
        "keep",
        "exit",
    ]
    assert win.operaciones.state is None
    assert automatizacion._deferred_glib.drain() == [True]


def test_cambio_externo_no_invoca_rollback_directo(
    automatizacion,
    monkeypatch,
):
    external = {"state": "admin"}
    events = []

    class Session:
        def __enter__(self):
            return self

        def aplicar(self, _state):
            events.append("apply")
            raise RuntimeError("cambio externo detectado")

        def keep_current_as_winner(self):
            events.append("keep")

        def __exit__(self, *_args):
            events.append("exit")
            return False

    class Manager:
        def sesion(self, _token):
            return Session()

        def restaurar_estado(self, _state):
            external["state"] = "rollback"
            raise AssertionError("rollback externo invocado")

    monkeypatch.setattr(automatizacion.threading, "Thread", _ImmediateThread)
    win = _secure_win()
    win.operaciones = OperationCoordinator()
    win.scx = Manager()

    automatizacion._iniciar_aplicacion_recomendada(win, "scx_lavd")
    results = automatizacion._deferred_glib.drain()

    assert events == ["apply", "exit"]
    assert external["state"] == "admin"
    assert results == [False]
    assert "cambio externo" in win._toasts[-1][0]
