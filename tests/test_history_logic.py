import importlib
import math
import sys
import types
from types import SimpleNamespace

import pytest

from ui import historial


def _resultado(**overrides):
    resultado = {
        "scheduler_name": "scx_lavd",
        "test_type": "cpu",
        "timestamp": 1_700_000_000.0,
        "valor": 1000.0,
        "response": 2.5,
        "response_kind": "mean_context_switch_us",
        "p95": None,
        "modo": "default",
        "kernel_version": "7.1-test",
        "run_type": "manual",
        "status": "completed",
    }
    resultado.update(overrides)
    return resultado


def test_filtros_comparten_scheduler_y_todo_no_aplica_corte():
    filtros = historial.preparar_filtros_historial(
        "scx_lavd",
        "cpu",
        0,
        now=2_000_000_000.0,
    )

    assert filtros == {
        "scheduler": "scx_lavd",
        "test_type": "cpu",
        "days": 0.0,
        "date_from": None,
        "solo_comparables": True,
        "status": "completed",
        "kernel_version": None,
    }

    recientes = historial.preparar_filtros_historial(
        "Todos",
        "Todos",
        7,
        now=2_000_000_000.0,
    )
    assert recientes["scheduler"] is None
    assert recientes["test_type"] is None
    assert recientes["date_from"] == 2_000_000_000.0 - 7 * 86400


def test_filtro_comparable_exige_completed_y_kernel_actual():
    comparables = historial.preparar_filtros_historial(
        "scx_lavd",
        "cpu",
        30,
        solo_comparables=True,
        kernel_actual="7.1-test",
        now=2_000_000_000.0,
    )
    todos = historial.preparar_filtros_historial(
        "scx_lavd",
        "cpu",
        30,
        solo_comparables=False,
        kernel_actual="7.1-test",
        now=2_000_000_000.0,
    )
    filas = [
        _resultado(timestamp=1.0),
        _resultado(timestamp=2.0, status="partial"),
        _resultado(timestamp=3.0, kernel_version="7.0-old"),
        _resultado(timestamp=4.0, status=["completed"]),
        _resultado(timestamp=5.0, kernel_version={"release": "7.1-test"}),
        object(),
    ]

    assert comparables["status"] == "completed"
    assert comparables["kernel_version"] == "7.1-test"
    assert todos["status"] is None
    assert todos["kernel_version"] is None
    assert [
        fila["timestamp"]
        for fila in historial.filtrar_resultados_historial(
            filas,
            solo_comparables=True,
            kernel_actual="7.1-test",
        )
    ] == [1.0]
    assert len(
        historial.filtrar_resultados_historial(
            filas,
            solo_comparables=False,
            kernel_actual=None,
        )
    ) == 5
    assert historial.es_resultado_comparable(_resultado(), None) is False


def test_metadata_corrupta_no_inventa_procedencia():
    metadata = historial.preparar_metadata_historial(
        {
            "status": ["completed"],
            "modo": {"mode": "default"},
            "kernel_version": None,
            "run_type": 7,
        }
    )

    assert metadata == {
        "status": "desconocido",
        "modo": "desconocido",
        "kernel_version": "desconocido",
        "run_type": "desconocido",
    }


def test_todos_pide_un_tipo_en_vez_de_mostrar_cpu():
    estado = historial.preparar_datos_tendencia(
        [
            _resultado(test_type="cpu"),
            _resultado(test_type="latencia_fork", valor=10.0),
        ],
        None,
    )

    assert estado["datos"] == []
    assert estado["schedulers"] == ()
    assert "Selecciona un tipo" in estado["mensaje"]
    assert "sin mezclar unidades" in estado["mensaje"]


def test_tendencia_descarta_corruptos_limita_y_ordena_los_mas_recientes():
    resultados = [
        _resultado(timestamp=float(indice), valor=float(indice))
        for indice in range(1, 206)
    ]
    resultados.extend(
        [
            _resultado(timestamp=float("nan")),
            _resultado(timestamp=float("inf")),
            _resultado(timestamp=1e300),
            _resultado(valor=float("nan")),
            _resultado(valor=float("inf")),
            _resultado(valor=-1.0),
            _resultado(scheduler_name=""),
            _resultado(test_type="memory"),
        ]
    )

    estado = historial.preparar_datos_tendencia(
        resultados,
        "cpu",
        limit=3,
    )

    assert [punto["timestamp"] for punto in estado["datos"]] == [203, 204, 205]
    assert estado["schedulers"] == ("scx_lavd",)
    assert "mayor es mejor" in estado["etiqueta_eje"]


def test_tendencia_separa_modo_kernel_y_solo_usa_completed():
    resultados = [
        _resultado(timestamp=1.0, valor=10.0),
        _resultado(timestamp=2.0, valor=20.0),
        _resultado(timestamp=3.0, modo="performance", valor=30.0),
        _resultado(timestamp=4.0, kernel_version="7.2-test", valor=40.0),
        _resultado(timestamp=5.0, status="partial", valor=50.0),
        _resultado(
            timestamp=6.0,
            scheduler_name="scx_bpfland",
            valor=60.0,
        ),
    ]

    estado = historial.preparar_datos_tendencia(
        resultados,
        "cpu",
        scheduler="scx_lavd",
    )

    assert estado["schedulers"] == ("scx_lavd",)
    assert {serie["key"] for serie in estado["series"]} == {
        ("scx_lavd", "default", "7.1-test"),
        ("scx_lavd", "performance", "7.1-test"),
        ("scx_lavd", "default", "7.2-test"),
    }
    assert [punto["timestamp"] for punto in estado["datos"]] == [1.0, 2.0, 3.0, 4.0]
    assert all(
        "modo" in serie["label"] and "kernel" in serie["label"]
        for serie in estado["series"]
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "partial"},
        {"status": ["completed"]},
        {"modo": None},
        {"modo": ["default"]},
        {"kernel_version": None},
        {"kernel_version": {"release": "7.1-test"}},
    ],
)
def test_tendencia_descarta_procedencia_incompleta_o_corrupta(overrides):
    estado = historial.preparar_datos_tendencia(
        [_resultado(**overrides)],
        "cpu",
    )

    assert estado["datos"] == []
    assert estado["series"] == ()


def test_latencia_declara_que_menor_es_mejor():
    estado = historial.preparar_datos_tendencia(
        [_resultado(test_type="latencia_fork", valor=12.0)],
        "latencia_fork",
    )

    assert estado["menor_es_mejor"] is True
    assert "menor es mejor" in estado["etiqueta_eje"]
    assert "menor es mejor" in estado["datos"][0]["detalle"]


def test_response_kind_controla_el_nombre_p95_y_conserva_legacy():
    moderna = historial.extraer_respuesta_historial(
        _resultado(response=8.0, response_kind="p95_us", p95=8.0)
    )
    media = historial.extraer_respuesta_historial(
        _resultado(response=2.0, response_kind="mean_mutex_us", p95=99.0)
    )
    legacy = historial.extraer_respuesta_historial(
        _resultado(response=None, response_kind=None, p95=4.0)
    )
    explicita_corrupta = historial.extraer_respuesta_historial(
        _resultado(response=float("nan"), response_kind="p95_us", p95=4.0)
    )

    assert moderna["etiqueta"] == "p95"
    assert media["etiqueta"] == "Respuesta [mean_mutex_us]"
    assert "p95" not in media["etiqueta"].casefold()
    assert legacy["etiqueta"] == "Respuesta histórica"
    assert "p95" not in legacy["etiqueta"].casefold()
    assert legacy["origen"] == "p95_historico"
    assert legacy["unidad"] == ""
    assert explicita_corrupta is None


def test_formato_muestra_response_kind_y_unidades_reales():
    throughput = historial.formatear_resultado_historial(
        _resultado(response_kind="mean_context_switch_us")
    )
    latencia = historial.formatear_resultado_historial(
        _resultado(
            test_type="latencia_compile",
            valor=20.0,
            response=30.0,
            response_kind="p95_us",
            p95=30.0,
        )
    )

    assert "ops/s" in throughput
    assert "Respuesta [mean_context_switch_us]" in throughput
    assert "p95" not in throughput.casefold()
    assert "20.0 µs" in latencia
    assert "p95: 30.000 µs" in latencia
    assert "menor es mejor" in latencia


def test_limites_de_tendencia_son_finitos_e_incluyen_cero():
    minimo, maximo = historial.calcular_limites_tendencia(
        [10.0, 20.0, float("nan"), float("inf")]
    )

    assert minimo == 0.0
    assert maximo == pytest.approx(22.0)
    assert historial.calcular_limites_tendencia([0.0]) == (0.0, 1.0)
    assert historial.calcular_limites_tendencia([float("nan")]) == (0.0, 1.0)
    extremos = historial.calcular_limites_tendencia([sys.float_info.max])
    assert all(math.isfinite(limite) for limite in extremos)


def test_numeros_demasiado_grandes_se_descartan_sin_overflow():
    enorme = 10**10000

    assert historial.formatear_resultado_historial(
        _resultado(valor=enorme)
    ) == "Resultado no válido"
    assert historial.preparar_datos_tendencia(
        [_resultado(valor=enorme)],
        "cpu",
    )["datos"] == []


def test_contador_explica_filtro_y_limite():
    comparables = historial.formatear_contador_historial(
        7,
        30,
        {
            "solo_comparables": True,
            "kernel_version": "7.1-test",
        },
    )
    todos = historial.formatear_contador_historial(
        30,
        30,
        {"solo_comparables": False},
    )

    assert "completados" in comparables
    assert "kernel 7.1-test" in comparables
    assert f"límite de carga: {historial._HISTORY_ROW_LIMIT}" in comparables
    assert "todos los estados, modos y kernels" in todos


class _FakeModel:
    def __init__(self, valores):
        self.valores = list(valores)

    def get_n_items(self):
        return len(self.valores)

    def get_string(self, indice):
        return self.valores[indice]


class _FakeCombo:
    def __init__(self, valores, seleccionado=0):
        self.modelo = _FakeModel(valores)
        self.seleccionado = seleccionado

    def get_model(self):
        return self.modelo

    def set_model(self, modelo):
        self.modelo = modelo

    def get_selected(self):
        return self.seleccionado

    def set_selected(self, seleccionado):
        self.seleccionado = seleccionado


class _FakeSwitch:
    def __init__(self, active=True):
        self.active = active

    def get_active(self):
        return self.active


class _FakeBox:
    def __init__(self):
        self.children = []

    def get_first_child(self):
        return self.children[0] if self.children else None

    def remove(self, child):
        self.children.remove(child)

    def append(self, child):
        self.children.append(child)


class _FakeLabel:
    def __init__(self, label="", **_kwargs):
        self.label = label

    def set_label(self, label):
        self.label = label


class _FakeActionRow:
    def __init__(self, title, subtitle):
        self.title = title
        self.subtitle = subtitle
        self.suffixes = []

    def add_suffix(self, suffix):
        self.suffixes.append(suffix)


def test_lista_y_grafico_reciben_la_misma_consulta_filtrada_y_limitada(monkeypatch):
    filas = [_resultado()]
    consulta = {}
    conteo = {}
    grafico = {}

    def consultar(**kwargs):
        consulta.update(kwargs)
        return filas

    def actualizar_grafico(_win, resultados, test_type, *, scheduler=None):
        grafico["resultados"] = resultados
        grafico["test_type"] = test_type
        grafico["scheduler"] = scheduler

    monkeypatch.setattr(historial, "consultar_historial", consultar)
    def contar(**kwargs):
        conteo.update(kwargs)
        return 10

    monkeypatch.setattr(historial, "contar_historial", contar)
    monkeypatch.setattr(historial, "_actualizar_datos_grafico", actualizar_grafico)
    monkeypatch.setattr(
        historial,
        "Adw",
        SimpleNamespace(ActionRow=_FakeActionRow),
    )
    monkeypatch.setattr(historial, "Gtk", SimpleNamespace(Label=_FakeLabel))

    win = SimpleNamespace(
        _hist_combo_sched=_FakeCombo(["Todos", "scx_lavd"], 1),
        _hist_combo_test=_FakeCombo([nombre for _, nombre in historial._TEST_TYPES], 1),
        _hist_combo_fecha=_FakeCombo([nombre for _, nombre in historial._DATE_RANGES], 4),
        _hist_switch_comparables=_FakeSwitch(True),
        _hist_lbl_contador=_FakeLabel(),
        _hist_box_resultados=_FakeBox(),
        versiones={"kernel": "7.1-test"},
    )

    historial._refrescar_resultados_historial(win)

    assert consulta == {
        "scheduler": "scx_lavd",
        "test_type": "cpu",
        "date_from": None,
        "kernel_version": "7.1-test",
        "status": "completed",
        "limit": historial._HISTORY_ROW_LIMIT,
        "include_payload": False,
    }
    assert conteo == {
        "scheduler": "scx_lavd",
        "test_type": "cpu",
        "date_from": None,
        "kernel_version": "7.1-test",
        "status": "completed",
    }
    assert grafico == {
        "resultados": filas,
        "test_type": "cpu",
        "scheduler": "scx_lavd",
    }
    assert len(win._hist_box_resultados.children) == 1
    assert "mean_context_switch_us" in win._hist_box_resultados.children[0].subtitle
    assert "Estado: completed" in win._hist_box_resultados.children[0].subtitle
    assert "Modo: default" in win._hist_box_resultados.children[0].subtitle
    assert "Kernel: 7.1-test" in win._hist_box_resultados.children[0].subtitle
    assert win._hist_box_resultados.children[0].suffixes[0].label == "MANUAL"
    assert "límite de carga" in win._hist_lbl_contador.label


def test_switch_desactivado_quita_filtros_de_comparabilidad():
    win = SimpleNamespace(
        _hist_combo_sched=_FakeCombo(["Todos", "scx_lavd"], 1),
        _hist_combo_test=_FakeCombo(
            [nombre for _, nombre in historial._TEST_TYPES],
            1,
        ),
        _hist_combo_fecha=_FakeCombo(
            [nombre for _, nombre in historial._DATE_RANGES],
            1,
        ),
        _hist_switch_comparables=_FakeSwitch(False),
        versiones={"kernel": "7.1-test"},
    )

    filtros = historial._filtros_desde_ui(win)

    assert filtros["solo_comparables"] is False
    assert filtros["status"] is None
    assert filtros["kernel_version"] is None
    assert filtros["scheduler"] == "scx_lavd"


def test_refresco_publico_reconstruye_modelo_y_conserva_scheduler(monkeypatch):
    win = SimpleNamespace(
        _hist_combo_sched=_FakeCombo(["Todos", "scx_lavd"], 1),
        _hist_refreshing=False,
    )
    llamadas = []
    monkeypatch.setattr(
        historial,
        "obtener_schedulers_historial",
        lambda: ["scx_bpfland", "scx_lavd"],
    )
    monkeypatch.setattr(historial, "_crear_modelo_cadenas", _FakeModel)
    monkeypatch.setattr(
        historial,
        "_refrescar_resultados_historial",
        lambda current_win: llamadas.append(current_win),
    )

    assert historial.refrescar_historial_ui(win) is True
    assert win._hist_combo_sched.get_model().valores == [
        "Todos",
        "scx_bpfland",
        "scx_lavd",
    ]
    assert win._hist_combo_sched.get_selected() == 2
    assert llamadas == [win]
    assert win._hist_refreshing is False


def test_dialogo_borrar_es_cancelable_y_solo_delete_es_destructivo(monkeypatch):
    class DialogoFalso:
        instancia = None

        def __init__(self, **propiedades):
            self.propiedades = propiedades
            self.respuestas = []
            self.default_response = None
            self.close_response = None
            self.appearances = []
            self.callback = None
            self.callback_args = ()
            self.presented_on = None
            DialogoFalso.instancia = self

        def add_response(self, response, label):
            self.respuestas.append((response, label))

        def set_default_response(self, response):
            self.default_response = response

        def set_close_response(self, response):
            self.close_response = response

        def set_response_appearance(self, response, appearance):
            self.appearances.append((response, appearance))

        def connect(self, signal, callback, *args):
            assert signal == "response"
            self.callback = callback
            self.callback_args = args

        def present(self, win):
            self.presented_on = win

    destructive = object()
    win = SimpleNamespace()
    borrados = []
    invalidados = []
    refrescos = []
    monkeypatch.setattr(
        historial,
        "Adw",
        SimpleNamespace(
            AlertDialog=DialogoFalso,
            ResponseAppearance=SimpleNamespace(DESTRUCTIVE=destructive),
        ),
    )
    monkeypatch.setattr(historial, "eliminar_historial", lambda: borrados.append(True))
    monkeypatch.setattr(
        historial,
        "invalidar_historial_automatico",
        lambda current_win: invalidados.append(current_win),
    )
    monkeypatch.setattr(
        historial,
        "refrescar_historial_ui",
        lambda current_win: refrescos.append(current_win),
    )

    historial._al_borrar_historial(None, win)
    dialogo = DialogoFalso.instancia

    assert dialogo.propiedades["heading"] == "Borrar Historial"
    assert "title" not in dialogo.propiedades
    assert dialogo.default_response == "cancel"
    assert dialogo.close_response == "cancel"
    assert dialogo.appearances == [("delete", destructive)]
    assert dialogo.presented_on is win

    assert dialogo.callback(dialogo, "cancel", *dialogo.callback_args) is False
    assert borrados == []
    assert invalidados == []
    assert refrescos == []

    assert dialogo.callback(dialogo, "delete", *dialogo.callback_args) is True
    assert borrados == [True]
    assert invalidados == [win]
    assert refrescos == [win]


def test_borrar_invalida_api_publica_y_nav_sin_tocar_scheduler():
    llamadas = []
    scheduler = object()
    estado = SimpleNamespace(label="viejo", sensitive=True, visible=True)
    win = SimpleNamespace(
        invalidar_historial_automatico=lambda: llamadas.append("api"),
        _historial_runs=[{"id": 1}],
        _indice_historial=0,
        _brutos_finales={"scx_lavd": {}},
        _scores_finales={"scx_lavd": 1.0},
        ganador_final="scx_lavd",
        _auto_permitir_aplicar=True,
        en_proceso_auto=False,
        lbl_nav=SimpleNamespace(set_label=lambda value: setattr(estado, "label", value)),
        btn_nav_prev=SimpleNamespace(
            set_sensitive=lambda value: setattr(estado, "sensitive", value)
        ),
        btn_nav_next=SimpleNamespace(set_sensitive=lambda _value: None),
        btn_aplicar_recomendado=SimpleNamespace(
            set_visible=lambda value: setattr(estado, "visible", value),
            set_sensitive=lambda _value: None,
        ),
        scx=scheduler,
    )

    assert historial.invalidar_historial_automatico(win) is True

    assert llamadas == ["api"]
    assert win._historial_runs == []
    assert win._indice_historial == -1
    assert win._brutos_finales == {}
    assert win._scores_finales == {}
    assert win.ganador_final is None
    assert win._auto_permitir_aplicar is False
    assert estado.label == ""
    assert estado.sensitive is False
    assert estado.visible is False
    assert win.scx is scheduler


class _FakeDrawingArea:
    def __init__(self):
        self.signals = {}
        self.draw_count = 0

    def set_draw_func(self, *_args):
        pass

    def set_hexpand(self, *_args):
        pass

    def set_content_height(self, *_args):
        pass

    def set_margin_top(self, *_args):
        pass

    def set_margin_bottom(self, *_args):
        pass

    def set_margin_start(self, *_args):
        pass

    def set_margin_end(self, *_args):
        pass

    def add_controller(self, *_args):
        pass

    def connect(self, signal, callback):
        self.signals[signal] = callback
        return len(self.signals)

    def queue_draw(self):
        self.draw_count += 1

    def get_width(self):
        return 400

    def get_height(self):
        return 400


class _FakeMotionController:
    def __init__(self):
        self.signals = {}

    def connect(self, signal, callback):
        self.signals[signal] = callback


class _FakeStyleManager:
    @classmethod
    def get_default(cls):
        return cls()

    def get_dark(self):
        return False


class _FakeGLib:
    next_id = 1
    callbacks = {}
    intervals = []
    removed = []

    @classmethod
    def reset(cls):
        cls.next_id = 1
        cls.callbacks = {}
        cls.intervals = []
        cls.removed = []

    @classmethod
    def timeout_add(cls, interval, callback):
        source_id = cls.next_id
        cls.next_id += 1
        cls.callbacks[source_id] = callback
        cls.intervals.append(interval)
        return source_id

    @classmethod
    def source_remove(cls, source_id):
        cls.callbacks.pop(source_id, None)
        cls.removed.append(source_id)
        return True


@pytest.fixture
def grafico_module(monkeypatch):
    original = sys.modules.pop("ui.grafico", None)
    _FakeGLib.reset()

    gi_module = types.ModuleType("gi")
    gi_module.require_version = lambda *_args: None
    repository = types.ModuleType("gi.repository")
    repository.Gtk = SimpleNamespace(
        DrawingArea=_FakeDrawingArea,
        EventControllerMotion=_FakeMotionController,
    )
    repository.Adw = SimpleNamespace(StyleManager=_FakeStyleManager)
    repository.GLib = _FakeGLib
    gi_module.repository = repository
    monkeypatch.setitem(sys.modules, "gi", gi_module)
    monkeypatch.setitem(sys.modules, "gi.repository", repository)

    module = importlib.import_module("ui.grafico")
    monkeypatch.setattr(module, "_generar_color_hash", lambda _name: (0.1, 0.2, 0.3))
    monkeypatch.setattr(module, "_obtener_color_tema", lambda _name: None)
    try:
        yield module
    finally:
        sys.modules.pop("ui.grafico", None)
        if original is not None:
            sys.modules["ui.grafico"] = original


def _avanzar_hasta_reposo(grafico, max_ticks=1000):
    for _ in range(max_ticks):
        if grafico.tick() is False:
            return
    pytest.fail("la animación no alcanzó el reposo")


def test_grafico_activa_timer_bajo_demanda_y_lo_detiene(grafico_module):
    grafico = grafico_module.GraficoComparativo()
    assert grafico._tick_source_id is None
    assert _FakeGLib.intervals == []

    grafico.registrar_scheduler("scx_lavd")
    assert grafico.actualizar_dato("scx_lavd", 0, 100.0) is True
    assert grafico._tick_source_id == 1
    assert _FakeGLib.intervals == [16]

    _avanzar_hasta_reposo(grafico)
    assert grafico._tick_source_id is None
    assert grafico.tick() is False

    assert grafico.actualizar_dato("scx_lavd", 0, 50.0) is True
    assert grafico._tick_source_id == 2
    _avanzar_hasta_reposo(grafico)

    grafico.iniciar_pulso()
    assert grafico._tick_source_id == 3
    assert grafico.tick() is True
    grafico.detener_pulso()
    _avanzar_hasta_reposo(grafico)
    assert grafico._tick_source_id is None


def test_grafico_reset_limpia_estado_y_conserva_colores(grafico_module):
    grafico = grafico_module.GraficoComparativo()
    grafico.registrar_scheduler("scx_lavd")
    grafico.actualizar_dato("scx_lavd", 0, 50.0)
    grafico.ocultos.add("scx_lavd")
    grafico.highlight_sc = "scx_lavd"
    grafico.focus_animado["scx_lavd"] = 1.0
    grafico.iniciar_pulso()
    color = grafico.colores["scx_lavd"]
    source_id = grafico._tick_source_id

    grafico.reset(categorias=["A", "B", "C"])

    assert grafico.categorias == ["A", "B", "C"]
    assert grafico.datos_raw == {}
    assert grafico.valores_animados == {}
    assert grafico.max_por_categoria == [0.0, 0.0, 0.0]
    assert grafico.max_animados == [0.0, 0.0, 0.0]
    assert grafico.ocultos == set()
    assert grafico.highlight_sc is None
    assert grafico.focus_animado == {}
    assert grafico._pulse_active is False
    assert grafico.anim_tick == 0
    assert grafico._tick_source_id is None
    assert source_id in _FakeGLib.removed
    assert grafico.colores["scx_lavd"] == color


def test_grafico_redimensiona_arrays_rechaza_corruptos_y_respeta_cero(
    grafico_module,
):
    grafico = grafico_module.GraficoComparativo()
    grafico.registrar_scheduler("scx_lavd")
    grafico.actualizar_dato("scx_lavd", 5, 10.0)

    grafico.categorias = ["A", "B", "C"]
    assert len(grafico.datos_raw["scx_lavd"]) == 3
    assert len(grafico.valores_animados["scx_lavd"]) == 3
    assert len(grafico.max_por_categoria) == 3
    assert len(grafico.max_animados) == 3

    grafico.num_categorias = 8
    assert len(grafico.categorias) == 8
    assert len(grafico.datos_raw["scx_lavd"]) == 8
    assert grafico.actualizar_dato("scx_lavd", 8, 1.0) is False
    assert grafico.actualizar_dato("scx_lavd", 0, float("nan")) is False
    assert grafico.actualizar_dato("scx_lavd", 0, float("inf")) is False
    assert grafico.actualizar_dato("scx_lavd", 0, 10**10000) is False
    assert grafico.actualizar_dato("scx_lavd", 0, -1.0) is False
    assert grafico.actualizar_dato("scx_lavd", 0, 0.0) is True
    assert grafico_module._fraccion_radial(0.0) == 0.0

    grafico.datos_raw["scx_lavd"] = [1.0]
    grafico.valores_animados["scx_lavd"] = [0.0] * 20
    grafico.max_por_categoria = []
    grafico.max_animados = [0.0]
    grafico.tick()
    assert len(grafico.datos_raw["scx_lavd"]) == 8
    assert len(grafico.valores_animados["scx_lavd"]) == 8
    assert len(grafico.max_por_categoria) == 8
    assert len(grafico.max_animados) == 8


def test_grafico_suspende_al_unrealize_y_limpia_al_destruir(grafico_module):
    grafico = grafico_module.GraficoComparativo()
    grafico.registrar_scheduler("scx_lavd")
    grafico.actualizar_dato("scx_lavd", 0, 100.0)
    grafico.iniciar_pulso()
    source_id = grafico._tick_source_id

    grafico._on_unrealize()

    assert grafico._tick_source_id is None
    assert source_id in _FakeGLib.removed
    assert grafico._pulse_active is True

    grafico._on_realize()
    resumed_source_id = grafico._tick_source_id
    assert resumed_source_id is not None

    grafico._on_destroy()
    assert resumed_source_id in _FakeGLib.removed
    assert grafico._tick_source_id is None
    assert grafico._pulse_active is False
