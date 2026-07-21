import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
import types

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def gtk_stubs(monkeypatch):
    gi = types.ModuleType("gi")
    gi.require_version = lambda *_args: None
    repository = types.ModuleType("gi.repository")
    repository.Gtk = types.SimpleNamespace()
    repository.Adw = types.SimpleNamespace()
    repository.GLib = types.SimpleNamespace(
        idle_add=lambda callback, *args: callback(*args),
        source_remove=lambda _source_id: True,
    )
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
def automatizacion(monkeypatch, gtk_stubs):
    benchmark = types.ModuleType("core.benchmark")
    benchmark.correr_benchmark = (
        lambda *_args, cancel_token=None, **_kwargs: None
    )
    hybrid = types.ModuleType("core.hybrid")
    hybrid.correr_hybrid = lambda *_args, cancel_token=None, **_kwargs: None
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

    for name, module in {
        "core.benchmark": benchmark,
        "core.hybrid": hybrid,
        "core.scoring": scoring,
        "core.database": database,
        "utils.helpers": helpers,
        "widgets.legend": legend,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    return _load_module(
        monkeypatch,
        "_reactor_test_automatizacion_logic",
        "ui/automatizacion.py",
    )


@pytest.fixture
def disponibilidad(monkeypatch, gtk_stubs):
    helpers = types.ModuleType("utils.helpers")
    helpers.log = lambda *_args, **_kwargs: None
    helpers.limpiar_texto = lambda texto: texto or ""
    database = types.ModuleType("core.database")
    database.cargar_compatibilidad = (
        lambda _kernel, environment_key=None: {}
    )
    database.guardar_compatibilidad = lambda *_args: None
    database.reemplazar_compatibilidad = (
        lambda _kernel, _resultados, environment_key=None: None
    )
    database.limpiar_compatibilidad = lambda: None
    database.obtener_historial_compatibilidad = lambda: []
    monkeypatch.setitem(sys.modules, "utils.helpers", helpers)
    monkeypatch.setitem(sys.modules, "core.database", database)
    return _load_module(
        monkeypatch,
        "_reactor_test_disponibilidad_logic",
        "ui/disponibilidad.py",
    )


def test_normalizar_pesos_conserva_proporciones(automatizacion):
    assert automatizacion._normalizar_pesos((45, 45, 10)) == pytest.approx(
        (0.45, 0.45, 0.10)
    )
    assert automatizacion._normalizar_pesos((9, 0, 1)) == pytest.approx(
        (0.9, 0.0, 0.1)
    )
    assert math.fsum(
        automatizacion._normalizar_pesos((4.5e307, 4.5e307, 1e307))
    ) == pytest.approx(1.0)


@pytest.mark.parametrize(
    "pesos",
    [
        (),
        (1, 2),
        (1, 2, 3, 4),
        (0, 0, 0),
        (-1, 1, 1),
        (math.nan, 1, 1),
        (math.inf, 1, 1),
        (True, 1, 1),
    ],
)
def test_normalizar_pesos_rechaza_entradas_invalidas(automatizacion, pesos):
    with pytest.raises(ValueError):
        automatizacion._normalizar_pesos(pesos)


def test_orden_candidatos_incluye_base_y_es_reproducible(automatizacion):
    schedulers = ["scx_lavd", "scx_rusty", "scx_lavd", "Sistema Base"]
    primero = automatizacion._preparar_orden_candidatos(schedulers, semilla=9173)
    segundo = automatizacion._preparar_orden_candidatos(schedulers, semilla=9173)

    assert primero == segundo
    orden, semilla = primero
    assert semilla == 9173
    assert set(orden) == {"Sistema Base", "scx_lavd", "scx_rusty"}
    assert orden.count("Sistema Base") == 1


def test_orden_requiere_un_scheduler_scx(automatizacion):
    with pytest.raises(ValueError, match="scheduler SCX"):
        automatizacion._preparar_orden_candidatos(["Sistema Base"], semilla=1)


def test_metadata_auto_es_json_safe_y_copia_entradas(automatizacion):
    temperaturas = {
        "baseline_c": 48.5,
        "before_candidate_c": {"Sistema Base": 49.0},
    }
    configuracion = {"canonical_test_types": ["cpu", "threads"]}
    metadata = automatizacion._crear_metadata_auto(
        ("scx_lavd", "Sistema Base"),
        (0.45, 0.45, 0.10),
        temperaturas,
        configuracion,
        "partial",
        123,
    )
    temperaturas["before_candidate_c"]["Sistema Base"] = 99.0
    configuracion["canonical_test_types"].append("memory")

    assert metadata["candidate_order"] == ["scx_lavd", "Sistema Base"]
    assert metadata["shuffle_seed"] == 123
    assert metadata["status"] == "partial"
    assert metadata["temperatures"]["before_candidate_c"]["Sistema Base"] == 49.0
    assert metadata["configuration"]["canonical_test_types"] == [
        "cpu",
        "threads",
    ]
    assert sum(metadata["effective_weights"].values()) == pytest.approx(1.0)
    json.dumps(metadata, allow_nan=False)


def test_sistema_base_solo_no_es_recomendacion(automatizacion):
    scores = {"Sistema Base": {"score": 100.0}}
    assert automatizacion._recomendacion_desde_scores(scores) is None


@pytest.mark.parametrize(
    ("returncode", "stdout", "stderr", "compatible"),
    [
        (124, "", "", False),
        (124, "Initializing BPF scheduler", "", False),
        (124, "Calibration complete", "", False),
        (124, "Preparing sched_ext scheduler", "", False),
        (124, "scheduler running: false", "", False),
        (124, "scheduler started: false", "", False),
        (124, "struct_ops registered: false", "", False),
        (124, "attached sched_ext: false", "", False),
        (124, "received shutdown signal", "", False),
        (124, "Scheduler started\nReceived shutdown signal", "", True),
        (137, "SCHEDULER IS RUNNING", "", True),
        (0, "Calibration COMPLETE\nscheduler STARTED", "", True),
        (0, "", "", False),
        (1, "scheduler started", "fatal: process crashed", False),
        (124, "scheduler started", "[ERROR] verifier rejected program", False),
        (124, "scheduler started", "FAILED TO LOAD BPF: Invalid argument", False),
        (0, "ACTIVE", "Operation Not Permitted", False),
    ],
)
def test_clasificacion_bpf_es_conservadora_y_case_insensitive(
    disponibilidad,
    returncode,
    stdout,
    stderr,
    compatible,
):
    resultado, mensaje, advertencia = disponibilidad._clasificar_salida_bpf(
        returncode,
        stdout,
        stderr,
    )
    assert resultado is compatible
    assert mensaje
    assert advertencia is False


def test_modo_dev_no_depende_de_hash_aleatorio(disponibilidad):
    nombre = "scx_lavd"
    esperado = (
        int.from_bytes(
            hashlib.sha256(nombre.casefold().encode("utf-8")).digest()[:8],
            "big",
        )
        % 100
        < 75
    )
    assert disponibilidad._compatibilidad_dev_determinista(nombre) is esperado
    assert disponibilidad._compatibilidad_dev_determinista(nombre) is esperado


def test_nombre_binario_no_duplica_prefijo_scx(disponibilidad):
    assert disponibilidad._nombre_binario_scheduler("lavd") == "scx_lavd"
    assert disponibilidad._nombre_binario_scheduler("scx_lavd") == "scx_lavd"


class _FakeHandle:
    def __init__(self):
        from core.operations import CancellationToken

        self.token = CancellationToken()
        self.released = False
        self.operation_id = 1

    def check_cancelled(self):
        self.token.raise_if_cancelled()

    def release(self):
        self.released = True
        return True


class _FakeSession:
    def __init__(self, token, cancel_on_exit=False):
        from core.scx import ScxState

        self.token = token
        self.cancel_on_exit = cancel_on_exit
        self.initial_state = ScxState()
        self.restore_error = None

    def __enter__(self):
        return self

    def aplicar(self, _estado):
        self.token.raise_if_cancelled()

    def __exit__(self, _exc_type, _exc_value, _traceback):
        if self.cancel_on_exit:
            self.token.cancel()
        return False


class _FakeScx:
    def __init__(self, cancel_on_exit=False):
        self.cancel_on_exit = cancel_on_exit

    def sesion(self, token):
        return _FakeSession(token, self.cancel_on_exit)


def _rellenar_run_completo(
    module,
    _win,
    _scx,
    _sensor,
    _token,
    orden,
    _pesos,
    _modo_dev,
    _log_view,
    brutos,
    resultados,
    temperaturas,
    generacion,
):
    del generacion
    temperaturas["baseline_c"] = 45.0
    for scheduler in orden:
        brutos[scheduler] = {}
        for tipo in module.TIPOS_CANONICOS:
            resultado = {
                "tipo": tipo,
                "sched": scheduler,
                "valor": 100.0,
                "response": 10.0,
                "response_kind": "test",
                "fairness": 0.1,
            }
            brutos[scheduler][tipo] = resultado
            resultados.append(resultado)


class _FakeWidget:
    def __init__(self, *args, **kwargs):
        self.title = kwargs.get("title")
        self.subtitle = kwargs.get("subtitle")
        self.label = kwargs.get("label")
        self.visible = kwargs.get("visible", True)
        self.sensitive = kwargs.get("sensitive", True)
        self.value = kwargs.get("value", 0.0)
        self.active = kwargs.get("active", False)
        self.css_classes = set(kwargs.get("css_classes", ()))

    def set_visible(self, visible):
        self.visible = visible

    def set_sensitive(self, sensitive):
        self.sensitive = sensitive

    def set_reveal_child(self, reveal):
        self.reveal_child = reveal

    def set_label(self, label):
        self.label = label

    def get_value(self):
        return self.value

    def set_value(self, value):
        self.value = value

    def get_active(self):
        return self.active

    def set_active(self, active):
        self.active = active

    def set_title(self, title):
        self.title = title

    def set_subtitle(self, subtitle):
        self.subtitle = subtitle

    def set_expanded(self, expanded):
        self.expanded = expanded

    def set_icon_name(self, icon_name):
        self.icon_name = icon_name

    def set_from_icon_name(self, icon_name):
        self.icon_name = icon_name

    def set_tooltip_text(self, tooltip):
        self.tooltip = tooltip

    def set_fraction(self, fraction):
        self.fraction = fraction

    def set_markup(self, markup):
        self.markup = markup

    def set_opacity(self, opacity):
        self.opacity = opacity

    def add_css_class(self, css_class):
        self.css_classes.add(css_class)

    def remove_css_class(self, css_class):
        self.css_classes.discard(css_class)

    def add_suffix(self, child):
        pass

    def add_row(self, row):
        pass

    def remove(self, child):
        pass

    def get_first_child(self):
        return None

    def detener_pulso(self):
        pass

    def registrar_scheduler(self, scheduler):
        pass

    def queue_draw(self):
        pass

    def reset(self):
        self.reset_count = getattr(self, "reset_count", 0) + 1

    def actualizar_dato(self, scheduler, indice, valor):
        self.datos = getattr(self, "datos", [])
        self.datos.append((scheduler, indice, valor))


class _FakeContainer(_FakeWidget):
    def __init__(self, children=()):
        super().__init__()
        self.children = list(children)

    def get_first_child(self):
        return self.children[0] if self.children else None

    def remove(self, child):
        self.children.remove(child)


class _FailOnTouch:
    def set_fraction(self, _fraction):
        raise AssertionError("El timer tocó la barra tras cerrar la UI")

    def set_visible(self, _visible):
        raise AssertionError("El timer tocó la barra tras cerrar la UI")

    def set_label(self, _label):
        raise AssertionError("El timer tocó la etiqueta tras cerrar la UI")


def _scores_para(*schedulers):
    return {
        scheduler: {
            "score": 95.0 - indice,
            "pot": 90.0,
            "resp": 90.0,
            "flu": 90.0,
        }
        for indice, scheduler in enumerate(schedulers)
    }


def _brutos_completos(module, *schedulers):
    return {
        scheduler: {
            tipo: {"tipo": tipo, "sched": scheduler, "valor": 100.0}
            for tipo in module.TIPOS_CANONICOS
        }
        for scheduler in schedulers
    }


def _resultados_persistidos(module, *schedulers):
    return [
        {
            "scheduler_name": scheduler,
            "test_type": tipo,
            "valor": 100.0,
            "response": 10.0,
            "response_kind": "test",
            "p95": None,
            "fairness": 0.1,
        }
        for scheduler in schedulers
        for tipo in module.TIPOS_CANONICOS
    ]


def _win_finalizacion():
    toasts = []
    win = types.SimpleNamespace(
        _ui_alive=True,
        _auto_generation=1,
        _filas_ranking=[],
        fila_ganador=_FakeWidget(),
        btn_aplicar_recomendado=_FakeWidget(visible=False, sensitive=False),
        revealer_pesos=_FakeWidget(),
        ganador_final=None,
        modo_desarrollador=False,
    )
    win.mostrar_toast = (
        lambda mensaje, *, alta=False: toasts.append((mensaje, alta))
    )
    return win, toasts


def test_fallo_al_crear_worker_no_deja_resultados_anteriores(
    automatizacion,
    monkeypatch,
):
    handle = _FakeHandle()
    fila_anterior = object()
    leyenda_anterior = object()
    win = types.SimpleNamespace(
        _ui_alive=True,
        _auto_generation=4,
        _auto_progress_timer_id=None,
        _auto_hide_timer_id=None,
        _filas_ranking=[fila_anterior],
        _historial_runs=[{"id": 9}],
        _indice_historial=0,
        _brutos_finales={"viejo": {"cpu": {"valor": 1.0}}},
        _scores_finales={"viejo": {"score": 100.0}},
        ganador_final="viejo",
        _auto_contexto_aplicable=True,
        _auto_permitir_aplicar=True,
        _auto_pesos_validos=True,
        _auto_development_mode=True,
        _pesos_auto_efectivos=(0.45, 0.45, 0.10),
        modo_desarrollador=False,
        en_proceso_auto=False,
        operaciones=types.SimpleNamespace(
            try_acquire=lambda _nombre: handle,
        ),
        scx=object(),
        sensor=object(),
        versiones={"kernel": "test"},
        text_view_logs_auto=object(),
        fila_ganador=_FakeWidget(title="Recomendado: viejo"),
        btn_aplicar_recomendado=_FakeWidget(visible=True, sensitive=True),
        btn_auto=_FakeWidget(),
        btn_nav_prev=_FakeWidget(sensitive=True),
        btn_nav_next=_FakeWidget(sensitive=True),
        lbl_nav=_FakeWidget(label="Run viejo"),
        grafico=_FakeWidget(),
        box_leyenda=_FakeContainer([leyenda_anterior]),
        revealer_pesos=_FakeWidget(),
        barra_progreso=_FakeWidget(),
        revealer_tiempo=_FakeWidget(),
        label_tiempo=_FakeWidget(),
    )
    toasts = []
    win.mostrar_toast = (
        lambda mensaje, *, alta=False: toasts.append((mensaje, alta))
    )

    def fallar_thread(**_kwargs):
        raise RuntimeError("no se pudo crear el hilo")

    monkeypatch.setattr(automatizacion.threading, "Thread", fallar_thread)

    automatizacion.iniciar_auto_test(
        win,
        configuracion={
            "orden": ("Sistema Base", "scx_lavd"),
            "pesos": (0.45, 0.45, 0.10),
            "semilla": 7,
            "development_mode": False,
        },
    )

    assert handle.released is True
    assert win._brutos_finales == {}
    assert win._scores_finales == {}
    assert win.ganador_final is None
    assert win._filas_ranking == []
    assert win.box_leyenda.children == []
    assert win._historial_runs == []
    assert win.btn_aplicar_recomendado.visible is False
    assert "errores" in win.fila_ganador.title.lower()
    assert toasts[-1][1] is True


def test_pesos_cero_ocultan_ganador_sin_perder_contexto(
    automatizacion,
    monkeypatch,
):
    scores = {
        "scx_lavd": {
            "score": 95.0,
            "pot": 90.0,
            "resp": 90.0,
            "flu": 90.0,
        },
        "Sistema Base": {
            "score": 90.0,
            "pot": 85.0,
            "resp": 85.0,
            "flu": 85.0,
        },
    }
    win = types.SimpleNamespace(
        slider_pot=_FakeWidget(value=0),
        slider_resp=_FakeWidget(value=0),
        slider_flu=_FakeWidget(value=0),
        _brutos_finales={"datos": {"cpu": {"valor": 1.0}}},
        _scores_finales=scores,
        _pesos_auto_efectivos=(0.45, 0.45, 0.10),
        _auto_contexto_aplicable=True,
        _auto_permitir_aplicar=True,
        _auto_pesos_validos=True,
        _auto_development_mode=False,
        modo_desarrollador=False,
        ganador_final="scx_lavd",
        btn_aplicar_recomendado=_FakeWidget(visible=True, sensitive=True),
        fila_ganador=_FakeWidget(),
        _filas_ranking=[object()],
    )
    monkeypatch.setattr(
        automatizacion,
        "calcular_scores_finales",
        lambda *_args, **_kwargs: scores,
    )
    monkeypatch.setattr(automatizacion.Adw, "ActionRow", _FakeWidget, raising=False)
    monkeypatch.setattr(automatizacion.Gtk, "Label", _FakeWidget, raising=False)

    automatizacion._recalcular_ranking(win)

    assert win._auto_contexto_aplicable is True
    assert win._auto_pesos_validos is False
    assert win._auto_permitir_aplicar is False
    assert win.ganador_final is None
    assert win.btn_aplicar_recomendado.visible is False

    win.slider_pot.set_value(1)
    automatizacion._recalcular_ranking(win)

    assert win._auto_contexto_aplicable is True
    assert win._auto_pesos_validos is True
    assert win._auto_permitir_aplicar is True
    assert win.ganador_final == "scx_lavd"
    assert win.btn_aplicar_recomendado.visible is True


def test_seleccion_vacia_se_rechaza_antes_de_autorizar(automatizacion):
    autorizaciones = []
    toasts = []
    win = types.SimpleNamespace(
        en_proceso_auto=False,
        compatibles=[],
        modo_desarrollador=False,
        _auto_sched_checks={},
        slider_pot=_FakeWidget(value=45),
        slider_resp=_FakeWidget(value=45),
        slider_flu=_FakeWidget(value=10),
        solicitar_sudo_si_necesario=lambda callback: autorizaciones.append(callback),
        mostrar_toast=lambda mensaje, *, alta=False: toasts.append((mensaje, alta)),
    )

    automatizacion.gestionar_click_auto(win, _FakeWidget())

    assert autorizaciones == []
    assert "scheduler scx" in toasts[-1][0].lower()


def test_cargar_ultimo_run_no_requiere_navegacion(automatizacion, monkeypatch):
    llamadas = []
    win = types.SimpleNamespace(_historial_runs=[{"id": 1}, {"id": 2}])
    monkeypatch.setattr(
        automatizacion,
        "_cargar_run_historico",
        lambda _win, indice, *, mostrar_toast=True: llamadas.append(
            (indice, mostrar_toast)
        )
        or True,
    )

    assert automatizacion._cargar_ultimo_run(win) is True
    assert llamadas == [(1, False)]


def test_run_parcial_con_dos_candidatos_completos_no_es_aplicable(
    automatizacion,
    monkeypatch,
):
    orden = ("Sistema Base", "scx_lavd", "scx_rusty")
    scores = _scores_para("scx_lavd", "Sistema Base")
    brutos = _brutos_completos(automatizacion, "scx_lavd", "Sistema Base")
    brutos["scx_rusty"] = {
        "cpu": {"tipo": "cpu", "sched": "scx_rusty", "valor": 100.0}
    }
    win, _toasts = _win_finalizacion()
    monkeypatch.setattr(
        automatizacion,
        "finalizar_auto_test",
        lambda _win, _generacion: True,
    )
    monkeypatch.setattr(
        automatizacion,
        "calcular_scores_finales",
        lambda *_args, **_kwargs: scores,
    )
    monkeypatch.setattr(automatizacion.Adw, "ActionRow", _FakeWidget, raising=False)
    monkeypatch.setattr(automatizacion.Gtk, "Label", _FakeWidget, raising=False)

    automatizacion._finalizar_worker_auto(
        win,
        {
            "brutos": brutos,
            "scores": scores,
            "pesos": (0.45, 0.45, 0.10),
            "orden": orden,
            "status": "partial",
            "run_id": None,
            "cancelado": False,
            "error": None,
            "development_mode": False,
            "generation": 1,
        },
    )

    assert win._auto_permitir_aplicar is False
    assert win.ganador_final is None
    assert win.btn_aplicar_recomendado.visible is False
    assert win.btn_aplicar_recomendado.sensitive is False


def test_historial_de_otro_kernel_muestra_ranking_sin_aplicarlo(
    automatizacion,
    monkeypatch,
):
    schedulers = ("Sistema Base", "scx_lavd")
    scores = _scores_para(*schedulers)
    run = {
        "id": 17,
        "status": "completed",
        "kernel_version": "6.12.1-old",
        "metadata": {
            "candidate_order": list(schedulers),
            "development_mode": False,
        },
    }
    ranking_cargado = []
    win = types.SimpleNamespace(
        _historial_runs=[run],
        _indice_historial=-1,
        _auto_permitir_aplicar=True,
        _pesos_auto_efectivos=(0.45, 0.45, 0.10),
        modo_desarrollador=False,
        versiones={"kernel": "6.13.0-current"},
        ganador_final="scx_anterior",
        btn_aplicar_recomendado=_FakeWidget(visible=True, sensitive=True),
        grafico=_FakeWidget(),
        box_leyenda=_FakeWidget(),
        fila_ganador=_FakeWidget(),
        revealer_pesos=_FakeWidget(),
        btn_auto=_FakeWidget(),
    )
    toasts = []
    win.mostrar_toast = (
        lambda mensaje, *, alta=False: toasts.append((mensaje, alta))
    )
    monkeypatch.setattr(
        automatizacion,
        "cargar_resultados_de_run",
        lambda _run_id: _resultados_persistidos(automatizacion, *schedulers),
    )
    monkeypatch.setattr(automatizacion, "_resetear_grafico", lambda _grafico: None)
    monkeypatch.setattr(
        automatizacion,
        "_actualizar_botones_nav",
        lambda _win: None,
    )
    monkeypatch.setattr(
        automatizacion,
        "_poblar_ranking",
        lambda _win, pesos=None: ranking_cargado.append(pesos) or scores,
    )

    automatizacion._navegar_historial(win, 1)

    assert ranking_cargado == [(0.45, 0.45, 0.10)]
    assert win._auto_permitir_aplicar is False
    assert win.ganador_final is None
    assert win.btn_aplicar_recomendado.visible is False
    assert win.btn_aplicar_recomendado.sensitive is False
    assert "kernel" in win.fila_ganador.subtitle.lower()
    assert "no aplicable" in toasts[-1][0].lower()


def test_historial_requiere_la_comparacion_completa_esperada(automatizacion):
    schedulers = ("Sistema Base", "scx_lavd")
    run = {
        "status": "completed",
        "kernel_version": "6.13.0",
        "metadata": {
            "candidate_order": [*schedulers, "scx_rusty"],
            "development_mode": False,
        },
    }
    win = types.SimpleNamespace(
        versiones={"kernel": "6.13.0"},
        modo_desarrollador=False,
    )
    brutos = _brutos_completos(automatizacion, *schedulers)
    scores = _scores_para(*schedulers)

    motivo = automatizacion._motivo_run_historico_no_aplicable(
        win,
        run,
        brutos,
        scores,
    )

    assert "comparación completa" in motivo


def test_run_guardado_refresca_historial_interno_y_publico(
    automatizacion,
    monkeypatch,
):
    schedulers = ("Sistema Base", "scx_lavd")
    scores = _scores_para(*schedulers)
    win, _toasts = _win_finalizacion()
    refrescos = []
    win.refrescar_historial = lambda: refrescos.append("publico")
    monkeypatch.setattr(
        automatizacion,
        "finalizar_auto_test",
        lambda _win, _generacion: True,
    )
    monkeypatch.setattr(
        automatizacion,
        "_poblar_ranking",
        lambda current_win, pesos=None: setattr(
            current_win,
            "ganador_final",
            "scx_lavd",
        )
        or scores,
    )
    monkeypatch.setattr(
        automatizacion,
        "_refrescar_historial",
        lambda _win: refrescos.append("interno"),
    )

    automatizacion._finalizar_worker_auto(
        win,
        {
            "brutos": _brutos_completos(automatizacion, *schedulers),
            "scores": scores,
            "pesos": (0.45, 0.45, 0.10),
            "orden": schedulers,
            "status": "completed",
            "run_id": 91,
            "cancelado": False,
            "error": None,
            "development_mode": False,
            "generation": 1,
        },
    )

    assert refrescos == ["interno", "publico"]


def test_timer_ocultado_respeta_generacion_y_cierre(
    automatizacion,
    monkeypatch,
):
    callbacks = []
    eliminados = []
    monkeypatch.setattr(
        automatizacion.GLib,
        "timeout_add",
        lambda _intervalo, callback: callbacks.append(callback) or len(callbacks),
        raising=False,
    )
    monkeypatch.setattr(
        automatizacion.GLib,
        "source_remove",
        lambda source_id: eliminados.append(source_id) or True,
        raising=False,
    )
    monkeypatch.setattr(automatizacion, "_resetear_grafico", lambda _grafico: None)
    monkeypatch.setattr(
        automatizacion,
        "_actualizar_botones_nav",
        lambda _win: None,
    )
    win = types.SimpleNamespace(
        _ui_alive=True,
        _auto_generation=7,
        _auto_progress_timer_id=None,
        _auto_hide_timer_id=91,
        en_proceso_auto=False,
        _auto_permitir_aplicar=True,
        ganador_final="scx_lavd",
        btn_aplicar_recomendado=_FakeWidget(),
        btn_auto=_FakeWidget(),
        btn_nav_prev=_FakeWidget(),
        btn_nav_next=_FakeWidget(),
        barra_progreso=_FakeWidget(),
        revealer_tiempo=_FakeWidget(),
        progreso_actual=0.0,
        progreso_objetivo=0.0,
        segundos_actuales=0.0,
        segundos_objetivos=0.0,
        label_tiempo=_FakeWidget(),
        fila_ganador=_FakeWidget(),
        grafico=_FakeWidget(),
        box_leyenda=_FakeWidget(),
        _auto_operation_id=1,
    )

    automatizacion._preparar_interfaz_auto(win, ("Sistema Base",), 7)
    assert callbacks == []
    assert eliminados == [91]

    automatizacion.finalizar_auto_test(win, 7)
    ocultar_progreso = callbacks[-1]
    win._auto_generation = 8
    win.barra_progreso = _FailOnTouch()
    assert ocultar_progreso() is False

    win._auto_hide_timer_id = 100
    win._ui_alive = False
    assert ocultar_progreso() is False


def test_mediciones_auto_propagan_el_token_a_ambos_backends(
    automatizacion,
    monkeypatch,
):
    token = _FakeHandle().token
    llamadas = []
    tipos_hibridos = {
        "fork": "latencia_fork",
        "compile": "latencia_compile",
        "loaded": "latencia_loaded",
    }

    def correr_normal(tipo, _scx, _log, *, cancel_token=None, **_kwargs):
        llamadas.append(("benchmark", tipo, cancel_token))
        return {
            "tipo": tipo,
            "sched": automatizacion.BASE_SYSTEM_NAME,
            "valor": 100.0,
        }

    def correr_hibrido(tipo, _scx, _log, *, cancel_token=None, **_kwargs):
        llamadas.append(("hybrid", tipo, cancel_token))
        return {
            "tipo": tipos_hibridos[tipo],
            "sched": automatizacion.BASE_SYSTEM_NAME,
            "valor": 100.0,
        }

    monkeypatch.setattr(automatizacion, "correr_benchmark", correr_normal)
    monkeypatch.setattr(automatizacion, "correr_hybrid", correr_hibrido)
    monkeypatch.setattr(
        automatizacion,
        "_calibrar_termica",
        lambda _sensor, _token: None,
    )
    monkeypatch.setattr(
        automatizacion,
        "_esperar_cancelable",
        lambda current_token, _segundos: current_token.raise_if_cancelled(),
    )
    monkeypatch.setattr(
        automatizacion,
        "_programar_ui",
        lambda *_args: None,
    )
    brutos = {}
    resultados = []

    automatizacion._ejecutar_mediciones(
        object(),
        types.SimpleNamespace(restaurar_estado=lambda _estado: None),
        types.SimpleNamespace(obtener_temp=lambda: 0.0),
        token,
        (automatizacion.BASE_SYSTEM_NAME,),
        (0.45, 0.45, 0.10),
        False,
        object(),
        brutos,
        resultados,
        {},
        1,
    )

    assert [(backend, tipo) for backend, tipo, _token in llamadas] == [
        ("benchmark", "cpu"),
        ("benchmark", "threads"),
        ("benchmark", "memory"),
        ("hybrid", "fork"),
        ("hybrid", "compile"),
        ("hybrid", "loaded"),
    ]
    assert all(recibido is token for _backend, _tipo, recibido in llamadas)
    assert len(resultados) == 6


def test_aplicar_recomendado_adquiere_handle_solo_tras_auth(
    automatizacion,
    monkeypatch,
):
    class DialogoFalso:
        instancia = None

        def __init__(self, **_kwargs):
            self.responder = None
            DialogoFalso.instancia = self

        def add_response(self, *_args):
            pass

        def set_default_response(self, *_args):
            pass

        def set_close_response(self, *_args):
            pass

        def set_response_appearance(self, *_args):
            pass

        def connect(self, _signal, callback):
            self.responder = callback

        def present(self, _win):
            pass

    callbacks_auth = []
    adquisiciones = []
    operaciones = types.SimpleNamespace(
        try_acquire=lambda nombre: adquisiciones.append(nombre) or None,
    )
    win = types.SimpleNamespace(
        ganador_final="scx_lavd",
        modo_desarrollador=False,
        _auto_development_mode=False,
        _auto_contexto_aplicable=True,
        _auto_pesos_validos=True,
        _auto_permitir_aplicar=True,
        operaciones=operaciones,
        solicitar_sudo_si_necesario=lambda callback: callbacks_auth.append(callback),
        mostrar_operacion_ocupada=lambda: None,
    )
    monkeypatch.setattr(
        automatizacion.Adw,
        "AlertDialog",
        DialogoFalso,
        raising=False,
    )
    monkeypatch.setattr(
        automatizacion.Adw,
        "ResponseAppearance",
        types.SimpleNamespace(SUGGESTED=object()),
        raising=False,
    )

    automatizacion.confirmar_aplicar_recomendado(win)
    DialogoFalso.instancia.responder(DialogoFalso.instancia, "apply")

    assert len(callbacks_auth) == 1
    assert adquisiciones == []

    callbacks_auth[0]()

    assert adquisiciones == ["aplicar recomendado"]


def test_recomendacion_simulada_no_se_autoriza_en_modo_real(automatizacion):
    autorizaciones = []
    toasts = []
    win = types.SimpleNamespace(
        ganador_final="scx_lavd",
        modo_desarrollador=False,
        _auto_development_mode=True,
        _auto_contexto_aplicable=True,
        _auto_pesos_validos=True,
        _auto_permitir_aplicar=True,
        solicitar_sudo_si_necesario=lambda callback: autorizaciones.append(callback),
        mostrar_toast=lambda mensaje, *, alta=False: toasts.append((mensaje, alta)),
    )

    automatizacion.confirmar_aplicar_recomendado(win)

    assert autorizaciones == []
    assert toasts[-1][1] is True
    assert "modo distinto" in toasts[-1][0].lower()


def test_aplicacion_en_vuelo_aborta_si_cambia_la_procedencia(
    automatizacion,
    monkeypatch,
):
    targets = []
    finales = []
    conservados = []
    sincronizaciones = []
    handle = _FakeHandle()

    class ThreadDiferido:
        def __init__(self, *, target):
            self.target = target

        def start(self):
            targets.append(self.target)

    class Sesion:
        initial_state = types.SimpleNamespace(scheduler=None, mode=None)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def conservar_ganador(self, scheduler, mode):
            conservados.append((scheduler, mode))

    monkeypatch.setattr(automatizacion.threading, "Thread", ThreadDiferido)
    monkeypatch.setattr(
        automatizacion,
        "_programar_ui",
        lambda _win, callback, *args: finales.append((callback, args)),
    )
    win = types.SimpleNamespace(
        _ui_alive=True,
        _auto_generation=1,
        ganador_final="scx_lavd",
        modo_desarrollador=False,
        _auto_development_mode=False,
        _auto_contexto_aplicable=True,
        _auto_pesos_validos=True,
        _auto_permitir_aplicar=True,
        operaciones=types.SimpleNamespace(
            try_acquire=lambda _nombre: handle,
        ),
        scx=types.SimpleNamespace(
            sesion=lambda _token: Sesion(),
            restaurar_estado=lambda _estado: None,
        ),
        btn_aplicar_recomendado=_FakeWidget(),
        sincronizar_sistema=lambda: sincronizaciones.append(True),
    )

    automatizacion._iniciar_aplicacion_recomendada(win, "scx_lavd")
    win._auto_generation = 2
    win.modo_desarrollador = True
    targets[0]()

    assert conservados == []
    assert handle.released is True
    callback, args = finales[-1]
    assert callback is automatizacion._finalizar_aplicacion_recomendada
    assert callback(*args) is False
    assert win.btn_aplicar_recomendado.label == "Aplicar recomendado"
    assert sincronizaciones == [True]


def test_final_de_generacion_antigua_no_toca_el_run_actual(automatizacion):
    win = types.SimpleNamespace(
        _ui_alive=True,
        _auto_generation=12,
        ganador_final="actual",
        btn_auto=_FailOnTouch(),
        barra_progreso=_FailOnTouch(),
    )

    aplicado = automatizacion._finalizar_worker_auto(
        win,
        {
            "generation": 11,
            "brutos": {"viejo": {}},
            "scores": {"viejo": {"score": 100.0}},
            "status": "completed",
            "development_mode": False,
        },
    )

    assert aplicado is False
    assert win.ganador_final == "actual"


def test_progreso_encolado_no_pisa_un_run_ya_finalizado(automatizacion):
    win = types.SimpleNamespace(
        _ui_alive=True,
        _auto_generation=5,
        en_proceso_auto=False,
        barra_progreso=_FailOnTouch(),
        label_tiempo=_FailOnTouch(),
        fila_ganador=_FailOnTouch(),
    )

    assert (
        automatizacion._actualizar_progreso_ui(
            win,
            0.5,
            20,
            "Resultado antiguo",
            5,
        )
        is False
    )
    assert automatizacion._actualizar_lider_provisional(win, 5, "viejo") is False


def test_worker_auto_sella_token_y_guarda_run_completo(
    automatizacion,
    monkeypatch,
):
    orden = ("Sistema Base", "scx_lavd")
    handle = _FakeHandle()
    guardados = []
    finales = []
    monkeypatch.setattr(
        automatizacion,
        "_ejecutar_mediciones",
        lambda *args, **kwargs: _rellenar_run_completo(
            automatizacion,
            *args,
            **kwargs,
        ),
    )
    monkeypatch.setattr(
        automatizacion,
        "calcular_scores_finales",
        lambda brutos, **_kwargs: {
            scheduler: {
                "score": 90.0,
                "pot": 90.0,
                "resp": 90.0,
                "flu": 90.0,
            }
            for scheduler in brutos
        },
    )
    monkeypatch.setattr(
        automatizacion,
        "guardar_run_completo",
        lambda versiones, resultados, **kwargs: guardados.append(
            (versiones, list(resultados), kwargs)
        )
        or 77,
    )
    monkeypatch.setattr(
        automatizacion,
        "_programar_ui",
        lambda _win, callback, *args: finales.append((callback, args)),
    )

    automatizacion._worker_automatizacion(
        object(),
        handle,
        _FakeScx(),
        object(),
        orden,
        (0.45, 0.45, 0.10),
        False,
        {"kernel": "test"},
        object(),
        123,
        1,
    )

    assert handle.released is True
    assert handle.token.cancelled is False
    assert handle.token.accepting_cancellation is False
    assert len(guardados) == 1
    assert guardados[0][2]["run_type"] == "auto"
    assert guardados[0][2]["status"] == "completed"
    assert guardados[0][2]["metadata"]["candidate_order"] == list(orden)
    assert guardados[0][2]["metadata"]["development_mode"] is False
    assert len(guardados[0][1]) == len(orden) * 6
    assert finales[0][1][1]["run_id"] == 77


def test_worker_auto_detecta_cancelacion_durante_restauracion(
    automatizacion,
    monkeypatch,
):
    orden = ("Sistema Base", "scx_lavd")
    handle = _FakeHandle()
    guardados = []
    finales = []
    monkeypatch.setattr(
        automatizacion,
        "_ejecutar_mediciones",
        lambda *args, **kwargs: _rellenar_run_completo(
            automatizacion,
            *args,
            **kwargs,
        ),
    )
    monkeypatch.setattr(
        automatizacion,
        "calcular_scores_finales",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        automatizacion,
        "guardar_run_completo",
        lambda _versions, _results, **kwargs: guardados.append(kwargs) or 88,
    )
    monkeypatch.setattr(
        automatizacion,
        "_programar_ui",
        lambda _win, callback, *args: finales.append((callback, args)),
    )

    automatizacion._worker_automatizacion(
        object(),
        handle,
        _FakeScx(cancel_on_exit=True),
        object(),
        orden,
        (0.45, 0.45, 0.10),
        False,
        {"kernel": "test"},
        object(),
        456,
        1,
    )

    assert handle.released is True
    assert handle.token.cancelled is True
    assert guardados[0]["status"] == "partial"
    resultado = finales[0][1][1]
    assert resultado["cancelado"] is True
    assert resultado["status"] == "partial"


def test_worker_compatibilidad_cancelado_no_modifica_cache(
    disponibilidad,
    monkeypatch,
):
    nombres = ("scx_lavd",)
    contexto = "contexto-cancelacion"
    handle = _FakeHandle()
    guardados = []
    callbacks = []
    win = types.SimpleNamespace(
        _disp_generation=1,
        _compatibility_context=contexto,
        modo_desarrollador=False,
        versiones={"kernel": "kernel-test", "scxctl": "test"},
    )
    monkeypatch.setattr(
        disponibilidad,
        "_capturar_contexto_compatibilidad",
        lambda _win, names: (
            contexto,
            {name: "/bin/tool" for name in names},
        ),
    )
    monkeypatch.setattr(disponibilidad.shutil, "which", lambda _name: "/bin/tool")
    monkeypatch.setattr(
        disponibilidad,
        "_verificar_binario_bpf",
        lambda *_args: (True, "Disponible (Residente)", False),
    )
    monkeypatch.setattr(
        disponibilidad,
        "reemplazar_compatibilidad",
        lambda *args, environment_key=None: guardados.append(
            (args, environment_key)
        ),
    )
    monkeypatch.setattr(
        disponibilidad,
        "_programar_ui",
        lambda _win, callback, *args: callbacks.append((callback, args)),
    )

    disponibilidad._worker_verificacion(
        win,
        handle,
        _FakeScx(cancel_on_exit=True),
        (("scx_lavd", object(), object(), object()),),
        False,
        "kernel-test",
        object(),
        {"scx_lavd": (False, "Estado anterior", 123.0)},
        [],
        True,
        1,
        contexto,
        nombres,
        {"scx_lavd": "/bin/tool"},
    )

    assert handle.released is True
    assert guardados == []
    callback, args = callbacks[-1]
    assert callback is disponibilidad._finalizar_verificacion
    assert args[2] is False
    assert args[3] is True
    assert args[5] == {"scx_lavd": (False, "Estado anterior", 123.0)}
    assert args[6] == []
    assert args[7] is True
    assert args[10] == contexto
    assert args[11] == nombres


def test_worker_compatibilidad_reemplaza_snapshot_una_sola_vez(
    disponibilidad,
    monkeypatch,
):
    nombres = ("scx_lavd", "scx_rusty")
    contexto = "contexto-completo"
    handle = _FakeHandle()
    reemplazos = []
    callbacks = []
    win = types.SimpleNamespace(
        _disp_generation=1,
        _compatibility_context=contexto,
        modo_desarrollador=True,
        versiones={"kernel": "kernel-test", "scxctl": "test"},
    )
    monkeypatch.setattr(
        disponibilidad,
        "_capturar_contexto_compatibilidad",
        lambda _win, names: (
            contexto,
            {name: None for name in names},
        ),
    )
    monkeypatch.setattr(
        disponibilidad,
        "_compatibilidad_dev_determinista",
        lambda _nombre: False,
    )

    def reemplazar(kernel, resultados, environment_key=None):
        assert handle.token.accepting_cancellation is False
        reemplazos.append((kernel, resultados, environment_key))

    monkeypatch.setattr(
        disponibilidad,
        "reemplazar_compatibilidad",
        reemplazar,
    )
    monkeypatch.setattr(
        disponibilidad,
        "_programar_ui",
        lambda _win, callback, *args: callbacks.append((callback, args)),
    )

    disponibilidad._worker_verificacion(
        win,
        handle,
        _FakeScx(),
        (
            ("scx_lavd", object(), object(), object()),
            ("scx_rusty", object(), object(), object()),
        ),
        True,
        "kernel-test",
        object(),
        generacion=1,
        contexto=contexto,
        nombres_snapshot=nombres,
        binarios_snapshot={name: None for name in nombres},
    )

    assert handle.released is True
    assert reemplazos == [
        (
            "kernel-test",
            (
                (
                    "scx_lavd",
                    False,
                    "Programa incompatible (Simulado determinista)",
                ),
                (
                    "scx_rusty",
                    False,
                    "Programa incompatible (Simulado determinista)",
                ),
            ),
            contexto,
        )
    ]
    callback, args = callbacks[-1]
    assert callback is disponibilidad._finalizar_verificacion
    assert args[2] is True
    assert args[3] is False
    assert args[10] == contexto
    assert args[11] == nombres


def test_worker_no_persiste_si_cambia_generacion_del_snapshot(
    disponibilidad,
    monkeypatch,
):
    handle = _FakeHandle()
    win = types.SimpleNamespace(
        _disp_generation=1,
        modo_desarrollador=False,
    )
    reemplazos = []

    def verificar(*_args):
        win._disp_generation = 2
        return True, "Disponible (Residente)", False

    monkeypatch.setattr(disponibilidad.shutil, "which", lambda _name: "/bin/tool")
    monkeypatch.setattr(disponibilidad, "_verificar_binario_bpf", verificar)
    monkeypatch.setattr(
        disponibilidad,
        "reemplazar_compatibilidad",
        lambda *args, environment_key=None: reemplazos.append(
            (args, environment_key)
        ),
    )
    monkeypatch.setattr(
        disponibilidad,
        "_programar_ui",
        lambda *_args: None,
    )

    disponibilidad._worker_verificacion(
        win,
        handle,
        _FakeScx(),
        (("scx_lavd", object(), object(), object()),),
        False,
        "kernel-test",
        object(),
        generacion=1,
    )

    assert reemplazos == []
    assert handle.released is True


def test_worker_no_persiste_si_falla_restauracion_scx(
    disponibilidad,
    monkeypatch,
):
    handle = _FakeHandle()
    reemplazos = []

    class SesionConFallo(_FakeSession):
        def __exit__(self, *_args):
            self.restore_error = RuntimeError("falló restauración")
            return False

    scx = types.SimpleNamespace(
        sesion=lambda token: SesionConFallo(token),
    )
    monkeypatch.setattr(
        disponibilidad,
        "reemplazar_compatibilidad",
        lambda *args, environment_key=None: reemplazos.append(
            (args, environment_key)
        ),
    )
    monkeypatch.setattr(
        disponibilidad,
        "_programar_ui",
        lambda *_args: None,
    )

    disponibilidad._worker_verificacion(
        types.SimpleNamespace(
            _disp_generation=1,
            modo_desarrollador=True,
        ),
        handle,
        scx,
        (("scx_lavd", object(), object(), object()),),
        True,
        "kernel-test",
        object(),
        generacion=1,
    )

    assert reemplazos == []
    assert handle.released is True


def test_snapshot_completo_sin_compatibles_conserva_lista_vacia(
    disponibilidad,
    monkeypatch,
):
    nombres = ("scx_lavd", "scx_rusty")
    contexto = "contexto-sin-compatibles"
    refrescos_auto = []
    auto_stub = types.ModuleType("ui.automatizacion")
    auto_stub._refrescar_auto_schedulers = (
        lambda _win, nombres=None: refrescos_auto.append(tuple(nombres or ()))
    )
    monkeypatch.setitem(sys.modules, "ui.automatizacion", auto_stub)
    monkeypatch.setattr(
        disponibilidad,
        "_refrescar_historial_compat",
        lambda _win: None,
    )
    badges = []
    monkeypatch.setattr(
        disponibilidad,
        "_actualizar_badge_compatibilidad",
        lambda _win, compatibles, verificada: badges.append(
            (tuple(compatibles), verificada)
        ),
    )
    filas = {
        nombre: (_FakeWidget(), _FakeWidget(), _FakeWidget())
        for nombre in nombres
    }
    win = types.SimpleNamespace(
        _disp_filas=filas,
        _disp_generation=1,
        _compatibility_context=contexto,
        _auto_sched_checks={},
        _verificando=True,
        _btn_verificar_disp=_FakeWidget(sensitive=False),
        _btn_limpiar_disp=_FakeWidget(sensitive=False),
        compatibles=None,
        modo_desarrollador=False,
        versiones={"kernel": "kernel-test", "scxctl": "test"},
        mostrar_toast=lambda *_args, **_kwargs: None,
        sincronizar_sistema=lambda: None,
    )
    monkeypatch.setattr(
        disponibilidad,
        "_capturar_contexto_compatibilidad",
        lambda _win, names: (
            contexto,
            {name: None for name in names},
        ),
    )

    disponibilidad._finalizar_verificacion(
        win,
        (
            ("scx_lavd", False, "Incompatible"),
            ("scx_rusty", False, "Incompatible"),
        ),
        True,
        False,
        None,
        {},
        None,
        False,
        False,
        1,
        contexto,
        nombres,
    )

    assert win.compatibles == []
    assert badges == [((), True)]
    assert refrescos_auto == [("scx_lavd", "scx_rusty")]
    assert all(
        icono.icon_name == "dialog-error-symbolic"
        for _row, _spinner, icono in filas.values()
    )


def test_cancelacion_restaura_cache_visual_y_lista_anteriores(
    disponibilidad,
    monkeypatch,
):
    auto_stub = types.ModuleType("ui.automatizacion")
    auto_stub._refrescar_auto_schedulers = lambda *_args, **_kwargs: None
    monkeypatch.setitem(sys.modules, "ui.automatizacion", auto_stub)
    monkeypatch.setattr(
        disponibilidad,
        "_refrescar_historial_compat",
        lambda _win: None,
    )
    monkeypatch.setattr(
        disponibilidad,
        "_actualizar_badge_compatibilidad",
        lambda *_args, **_kwargs: None,
    )
    row = _FakeWidget(subtitle="Resultado nuevo")
    spinner = _FakeWidget(visible=True)
    icono = _FakeWidget()
    win = types.SimpleNamespace(
        _disp_filas={"scx_lavd": (row, spinner, icono)},
        _verificando=True,
        _btn_verificar_disp=_FakeWidget(sensitive=False),
        _btn_limpiar_disp=_FakeWidget(sensitive=False),
        compatibles=["scx_lavd"],
        mostrar_toast=lambda *_args, **_kwargs: None,
        sincronizar_sistema=lambda: None,
    )

    disponibilidad._finalizar_verificacion(
        win,
        (("scx_lavd", True, "Disponible (Residente)"),),
        False,
        True,
        None,
        {"scx_lavd": (False, "failed to load BPF", 123.0)},
        [],
        True,
    )

    assert win.compatibles == []
    assert spinner.visible is False
    assert icono.icon_name == "dialog-error-symbolic"
    assert row.subtitle == "BPF failed"


def test_final_de_otro_modo_no_reactiva_cache_simulada(
    disponibilidad,
    monkeypatch,
):
    refrescos = []
    invalidaciones = []
    auto_stub = types.ModuleType("ui.automatizacion")
    auto_stub._refrescar_auto_schedulers = (
        lambda _win, nombres=None: refrescos.append(tuple(nombres or ()))
    )
    auto_stub._invalidar_auto_schedulers = (
        lambda _win: invalidaciones.append(True)
    )
    monkeypatch.setitem(sys.modules, "ui.automatizacion", auto_stub)
    monkeypatch.setattr(
        disponibilidad,
        "_actualizar_badge_compatibilidad",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        disponibilidad,
        "_refrescar_historial_compat",
        lambda _win: None,
    )
    monkeypatch.setattr(
        disponibilidad,
        "_capturar_contexto_compatibilidad",
        lambda current_win, names: (
            (
                "contexto-dev"
                if current_win.modo_desarrollador
                else "contexto-real"
            ),
            {name: None for name in names},
        ),
    )
    row, spinner, icono = _FakeWidget(), _FakeWidget(), _FakeWidget()
    win = types.SimpleNamespace(
        modo_desarrollador=False,
        _disp_generation=3,
        _compatibility_context="contexto-dev",
        _verificando=True,
        _disp_filas={"scx_lavd": (row, spinner, icono)},
        _disp_grupo_scheds=_FakeWidget(),
        _auto_sched_checks={},
        _btn_verificar_disp=_FakeWidget(),
        _btn_limpiar_disp=_FakeWidget(),
        compatibles=["scx_lavd"],
        versiones={"kernel": "kernel-test", "scxctl": "test"},
        mostrar_toast=lambda *_args, **_kwargs: None,
    )

    disponibilidad._finalizar_verificacion(
        win,
        (("scx_lavd", True, "Disponible (Simulado determinista)"),),
        True,
        False,
        None,
        {},
        None,
        False,
        True,
        3,
        "contexto-dev",
        ("scx_lavd",),
    )

    assert win.compatibles is None
    assert win._compatibility_context == "contexto-real"
    assert row.subtitle == "Sin verificar"
    assert invalidaciones == [True]
    assert refrescos == []


def test_recargar_disponibilidad_usa_modelo_sin_consultar_scx(
    disponibilidad,
    monkeypatch,
):
    class Modelo:
        def get_n_items(self):
            return 1

        def get_string(self, _indice):
            return "scx_lavd"

    cargas = []
    row, spinner, icono = _FakeWidget(), _FakeWidget(), _FakeWidget()
    win = types.SimpleNamespace(
        modelo_schedulers=Modelo(),
        scx=types.SimpleNamespace(
            obtener_lista=lambda *_args: pytest.fail("consulta SCX en GTK"),
        ),
        versiones={"kernel": "kernel-test", "scxctl": "test"},
        modo_desarrollador=False,
        _disp_generation=0,
        _compatibility_context=None,
        compatibles=None,
        _disp_filas={"scx_lavd": (row, spinner, icono)},
        _disp_grupo_scheds=_FakeWidget(),
    )
    monkeypatch.setattr(
        disponibilidad,
        "cargar_compatibilidad",
        lambda kernel, environment_key=None: cargas.append(
            (kernel, environment_key)
        )
        or {"scx_lavd": (True, "Disponible (Residente)", 123.0)},
    )
    monkeypatch.setattr(
        disponibilidad,
        "_refrescar_historial_compat",
        lambda _win: None,
    )

    disponibilidad.recargar_disponibilidad_ui(win)

    assert win.compatibles == ["scx_lavd"]
    assert cargas == [("kernel-test", win._compatibility_context)]
    assert win._compatibility_context
    assert icono.icon_name == "emblem-ok-symbolic"


def test_cache_solo_es_completa_para_el_mismo_snapshot(disponibilidad):
    cache = {"scx_lavd": (False, "Incompatible", 1.0)}

    assert disponibilidad._cache_cubre_snapshot(cache, ("scx_lavd",)) is True
    assert disponibilidad._cache_cubre_snapshot(cache, ()) is False
    assert (
        disponibilidad._cache_cubre_snapshot(
            cache,
            ("scx_lavd", "scx_rusty"),
        )
        is False
    )


def test_refrescar_auto_schedulers_usa_modelo_sin_consultar_scx(
    automatizacion,
):
    class Modelo:
        def get_n_items(self):
            return 1

        def get_string(self, _indice):
            return "scx_lavd"

    check = _FakeWidget(active=True)
    win = types.SimpleNamespace(
        modelo_schedulers=Modelo(),
        scx=types.SimpleNamespace(
            obtener_lista=lambda *_args: pytest.fail("consulta SCX en GTK"),
        ),
        modo_desarrollador=False,
        compatibles=["scx_lavd"],
        _auto_sched_checks={"scx_lavd": (_FakeWidget(), check)},
        _auto_sched_listbox=_FakeWidget(),
        _auto_expander=_FakeWidget(),
        _scores_finales={},
    )

    automatizacion._refrescar_auto_schedulers(win)

    assert win._auto_expander.subtitle == "1/1 seleccionados"
    assert win._auto_expander.visible is True


def test_limpiar_cache_deja_none_e_invalida_checklist(
    disponibilidad,
    monkeypatch,
):
    invalidaciones = []
    auto_stub = types.ModuleType("ui.automatizacion")
    auto_stub._invalidar_auto_schedulers = lambda _win: invalidaciones.append(True)
    monkeypatch.setitem(sys.modules, "ui.automatizacion", auto_stub)
    monkeypatch.setattr(disponibilidad.Gtk, "Image", _FakeWidget, raising=False)
    monkeypatch.setattr(
        disponibilidad,
        "_refrescar_historial_compat",
        lambda _win: None,
    )
    limpiezas = []
    monkeypatch.setattr(
        disponibilidad,
        "limpiar_compatibilidad",
        lambda: limpiezas.append(True),
    )
    imagen = _FakeWidget()
    nav = _FakeWidget()
    nav.get_child = lambda: types.SimpleNamespace(
        get_first_child=lambda: imagen,
    )
    win = types.SimpleNamespace(
        _verificando=False,
        compatibles=["scx_lavd"],
        _disp_filas={
            "scx_lavd": (_FakeWidget(), _FakeWidget(), _FakeWidget()),
        },
        nav_disponibilidad=nav,
        text_view_logs_disp=object(),
    )

    disponibilidad._limpiar_cache(win)

    assert limpiezas == [True]
    assert win.compatibles is None
    assert invalidaciones == [True]


def test_error_de_worker_compatibilidad_no_reemplaza_cache(
    disponibilidad,
    monkeypatch,
):
    nombres = ("scx_lavd",)
    contexto = "contexto-error"
    handle = _FakeHandle()
    reemplazos = []
    callbacks = []
    win = types.SimpleNamespace(
        _disp_generation=1,
        _compatibility_context=contexto,
        modo_desarrollador=False,
        versiones={"kernel": "kernel-test", "scxctl": "test"},
    )
    monkeypatch.setattr(
        disponibilidad,
        "_capturar_contexto_compatibilidad",
        lambda _win, names: (
            contexto,
            {name: "/bin/tool" for name in names},
        ),
    )
    monkeypatch.setattr(disponibilidad.shutil, "which", lambda _name: "/bin/tool")
    monkeypatch.setattr(
        disponibilidad,
        "_verificar_binario_bpf",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("fallo de prueba")),
    )
    monkeypatch.setattr(
        disponibilidad,
        "reemplazar_compatibilidad",
        lambda *args, environment_key=None: reemplazos.append(
            (args, environment_key)
        ),
    )
    monkeypatch.setattr(
        disponibilidad,
        "_programar_ui",
        lambda _win, callback, *args: callbacks.append((callback, args)),
    )

    disponibilidad._worker_verificacion(
        win,
        handle,
        _FakeScx(),
        (("scx_lavd", object(), object(), object()),),
        False,
        "kernel-test",
        object(),
        {"scx_lavd": (True, "Anterior", 1.0)},
        ["scx_lavd"],
        True,
        1,
        contexto,
        nombres,
        {"scx_lavd": "/bin/tool"},
    )

    assert reemplazos == []
    callback, args = callbacks[-1]
    assert callback is disponibilidad._finalizar_verificacion
    assert args[2] is False
    assert args[3] is False
    assert args[4] == "fallo de prueba"
    assert args[10] == contexto
    assert args[11] == nombres


def test_verificacion_captura_snapshot_despues_de_autorizar(
    disponibilidad,
    monkeypatch,
):
    autorizaciones = []
    capturas = []
    cargas = []
    workers = []
    handle = _FakeHandle()

    class ThreadInmediato:
        def __init__(self, *, target):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(disponibilidad.threading, "Thread", ThreadInmediato)

    def capturar(_win, nombres):
        nombres = tuple(nombres)
        capturas.append(nombres)
        return (
            f"contexto:{','.join(nombres)}",
            {nombre: f"/capturado/{nombre}" for nombre in nombres},
        )

    monkeypatch.setattr(
        disponibilidad,
        "_capturar_contexto_compatibilidad",
        capturar,
    )
    monkeypatch.setattr(
        disponibilidad,
        "cargar_compatibilidad",
        lambda kernel, environment_key=None: cargas.append(
            (kernel, environment_key)
        )
        or {"scx_nuevo": (False, "Anterior", 1.0)},
    )
    monkeypatch.setattr(
        disponibilidad,
        "_worker_verificacion",
        lambda *args: workers.append(args),
    )
    win = types.SimpleNamespace(
        _verificando=False,
        _disp_filas={
            "scx_viejo": (_FakeWidget(), _FakeWidget(), _FakeWidget()),
        },
        _disp_generation=4,
        _compatibility_context="contexto:scx_viejo",
        modo_desarrollador=False,
        versiones={"kernel": "kernel-test", "scxctl": "test"},
        compatibles=["scx_viejo"],
        operaciones=types.SimpleNamespace(
            try_acquire=lambda _nombre: handle,
        ),
        scx=object(),
        text_view_logs_disp=object(),
        _btn_verificar_disp=_FakeWidget(),
        _btn_limpiar_disp=_FakeWidget(),
        solicitar_sudo_si_necesario=lambda callback: autorizaciones.append(callback),
        mostrar_toast=lambda *_args, **_kwargs: None,
    )

    disponibilidad.iniciar_verificacion(win)
    assert capturas == []
    assert cargas == []

    win._disp_filas = {
        "scx_nuevo": (_FakeWidget(), _FakeWidget(), _FakeWidget()),
    }
    autorizaciones[0]()

    assert capturas == [("scx_nuevo",), ("scx_nuevo",)]
    assert cargas == [("kernel-test", "contexto:scx_nuevo")]
    assert [fila[0] for fila in workers[0][3]] == ["scx_nuevo"]
    assert workers[0][7] == {
        "scx_nuevo": (False, "Anterior", 1.0),
    }
    assert workers[0][11] == "contexto:scx_nuevo"
    assert workers[0][12] == ("scx_nuevo",)
    assert workers[0][13] == {"scx_nuevo": "/capturado/scx_nuevo"}
