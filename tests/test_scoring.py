import math

import pytest

from core.scoring import (
    calcular_score_categorias,
    calcular_mejores,
    calcular_ranking_manual,
    calcular_scores_finales,
    calcular_valor_grafico,
)


TIPOS_COMPLETOS = (
    "cpu",
    "threads",
    "memory",
    "latencia_fork",
    "latencia_compile",
    "latencia_loaded",
)

TIPOS_RESPONSE_OBLIGATORIA = tuple(
    tipo for tipo in TIPOS_COMPLETOS if tipo != "threads"
)


def _resultado(tipo, valor, response=10.0, fairness=0.1, **extra):
    resultado = {
        "tipo": tipo,
        "valor": valor,
        "response": response,
        "fairness": fairness,
    }
    resultado.update(extra)
    return resultado


def _resultados_completos(valor=100.0, response=10.0, fairness=0.1):
    return {
        tipo: _resultado(tipo, valor, response, fairness)
        for tipo in TIPOS_COMPLETOS
    }


def _assert_scores_equivalentes(actual, esperado):
    assert actual.keys() == esperado.keys()
    for scheduler in actual:
        assert actual[scheduler].keys() == esperado[scheduler].keys()
        for metrica in actual[scheduler]:
            assert actual[scheduler][metrica] == pytest.approx(
                esperado[scheduler][metrica]
            )


def test_scoring_en_vivo_e_historico_son_equivalentes():
    datos = {
        "rapido": {
            "cpu": (200.0, 5.0, 0.05),
            "threads": (300.0, 8.0, 0.08),
            "memory": (400.0, 4.0, 0.10),
            "latencia_fork": (40.0, 3.0, 0.04),
            "latencia_compile": (80.0, 6.0, 0.06),
            "latencia_loaded": (120.0, 9.0, 0.09),
        },
        "lento": {
            "cpu": (100.0, 10.0, 0.05),
            "threads": (150.0, 16.0, 0.08),
            "memory": (200.0, 8.0, 0.10),
            "latencia_fork": (80.0, 6.0, 0.04),
            "latencia_compile": (160.0, 12.0, 0.06),
            "latencia_loaded": (240.0, 18.0, 0.09),
        },
    }
    aliases = {
        "latencia_fork": "fork",
        "latencia_compile": "compile",
        "latencia_loaded": "loaded",
    }

    en_vivo = {}
    historico = {}
    for scheduler, pruebas in datos.items():
        en_vivo[scheduler] = {}
        historico[scheduler] = {}
        for tipo, (valor, response, fairness) in pruebas.items():
            clave_vivo = aliases.get(tipo, tipo)
            en_vivo[scheduler][clave_vivo] = _resultado(
                tipo, valor, response, fairness
            )
            historico[scheduler][tipo] = {
                "tipo": tipo,
                "valor": valor,
                "p95": response,
                "fairness": fairness,
            }

    scores_vivo = calcular_scores_finales(
        en_vivo,
        tipos=("cpu", "threads", "memory", "fork", "compile", "loaded"),
    )
    scores_historico = calcular_scores_finales(
        historico,
        tipos=(
            "cpu",
            "threads",
            "memory",
            "latencia_fork",
            "latencia_compile",
            "latencia_loaded",
        ),
    )

    _assert_scores_equivalentes(scores_vivo, scores_historico)
    assert scores_vivo["rapido"]["score"] > scores_vivo["lento"]["score"]


def test_tipo_interno_tiene_prioridad_sobre_la_clave_externa():
    brutos = {
        "a": {
            "slot_desconocido": _resultado("fork", 10.0, 2.0),
        },
        "b": {
            "latencia_fork": _resultado("latencia_fork", 20.0, 4.0),
        },
    }

    scores = calcular_scores_finales(brutos, tipos=("latencia_fork",))

    assert set(scores) == {"a", "b"}
    assert scores["a"]["score"] > scores["b"]["score"]


def test_alias_sin_tipo_tambien_se_canonicaliza():
    brutos = {
        "rapido": {
            "fork": {"valor": 10.0, "p95": 2.0, "fairness": 0.1},
        },
        "lento": {
            "latencia_fork": {
                "valor": 20.0,
                "p95": 4.0,
                "fairness": 0.1,
            },
        },
    }

    scores = calcular_scores_finales(brutos)

    assert set(scores) == {"rapido", "lento"}
    assert scores["rapido"]["score"] > scores["lento"]["score"]


def test_hyperfine_menor_duracion_y_respuesta_gana():
    brutos = {
        "rapido": {
            "fork": _resultado("latencia_fork", 10.0, 2.0),
        },
        "lento": {
            "fork": _resultado("latencia_fork", 20.0, 4.0),
        },
    }

    scores = calcular_scores_finales(brutos)

    assert scores["rapido"]["pot"] == pytest.approx(100.0)
    assert scores["rapido"]["resp"] == pytest.approx(100.0)
    assert scores["lento"]["pot"] == pytest.approx(50.0)
    assert scores["lento"]["resp"] == pytest.approx(50.0)
    assert scores["rapido"]["score"] > scores["lento"]["score"]


def test_response_es_menor_mejor_y_prevalece_sobre_p95():
    brutos = {
        "respuesta_rapida": {
            "cpu": _resultado("cpu", 100.0, 1.0, p95=1000.0),
        },
        "respuesta_lenta": {
            "cpu": _resultado("cpu", 100.0, 10.0, p95=0.01),
        },
    }

    scores = calcular_scores_finales(brutos)

    assert scores["respuesta_rapida"]["resp"] == pytest.approx(100.0)
    assert scores["respuesta_lenta"]["resp"] == pytest.approx(10.0)
    assert (
        scores["respuesta_rapida"]["score"]
        > scores["respuesta_lenta"]["score"]
    )


def test_faltar_uno_de_los_seis_tipos_excluye_al_scheduler():
    incompleto = _resultados_completos(1_000_000.0, 0.001, 0.0)
    del incompleto["latencia_loaded"]
    brutos = {
        "completo": _resultados_completos(),
        "incompleto_pero_extremo": incompleto,
    }

    automaticos = calcular_scores_finales(brutos)
    explicitos = calcular_scores_finales(brutos, tipos=TIPOS_COMPLETOS)

    assert set(automaticos) == {"completo"}
    assert set(explicitos) == {"completo"}
    _assert_scores_equivalentes(automaticos, explicitos)


def test_sin_scheduler_completo_no_hay_ranking():
    brutos = {
        "solo_cpu": {"cpu": _resultado("cpu", 100.0, 10.0)},
        "solo_threads": {
            "threads": _resultado("threads", 100.0, 10.0),
        },
    }

    assert calcular_scores_finales(brutos) == {}


def test_valor_fairness_y_response_obligatoria_invalidos_excluyen():
    invalidos = {
        "valor_nan": _resultado("cpu", math.nan),
        "valor_inf": _resultado("cpu", math.inf),
        "valor_cero": _resultado("cpu", 0.0),
        "valor_negativo": _resultado("cpu", -1.0),
        "fairness_nan": _resultado("cpu", 100.0, 10.0, math.nan),
        "fairness_inf": _resultado("cpu", 100.0, 10.0, math.inf),
        "fairness_negativa": _resultado("cpu", 100.0, 10.0, -0.1),
        "fairness_mayor_uno": _resultado("cpu", 100.0, 10.0, 1.1),
        "sin_fairness": {"tipo": "cpu", "valor": 100.0, "p95": 10.0},
        "response_nan": _resultado("cpu", 100.0, math.nan),
        "response_inf": _resultado("cpu", 100.0, math.inf),
        "response_cero": _resultado("cpu", 100.0, 0.0),
        "response_negativo": _resultado("cpu", 100.0, -1.0),
        "response_nan_con_p95": _resultado(
            "cpu", 100.0, math.nan, p95=1.0
        ),
        "sin_response": {"tipo": "cpu", "valor": 100.0, "fairness": 0.1},
    }
    brutos = {
        "valido": {"cpu": _resultado("cpu", 100.0, 10.0)},
        **{scheduler: {"cpu": item} for scheduler, item in invalidos.items()},
    }

    scores = calcular_scores_finales(brutos, tipos=("cpu",))
    mejores = calcular_mejores(brutos, tipos=("cpu",))

    assert set(scores) == {"valido"}
    assert mejores["cpu"]["max_val"] == pytest.approx(100.0)
    assert mejores["cpu"]["min_val"] == pytest.approx(100.0)
    assert mejores["cpu"]["min_response"] == pytest.approx(10.0)


@pytest.mark.parametrize("tipo_sin_response", TIPOS_RESPONSE_OBLIGATORIA)
def test_response_obligatoria_faltante_excluye_candidato_extremo(
    tipo_sin_response,
):
    extremo = _resultados_completos(response=0.001, fairness=0.0)
    for tipo in ("cpu", "threads", "memory"):
        extremo[tipo]["valor"] = 1_000_000.0
    for tipo in ("latencia_fork", "latencia_compile", "latencia_loaded"):
        extremo[tipo]["valor"] = 0.001
    extremo[tipo_sin_response]["response"] = None

    scores = calcular_scores_finales(
        {
            "valido": _resultados_completos(),
            "extremo_sin_response": extremo,
        },
        pesos=(1.0, 0.0, 0.0),
        tipos=TIPOS_COMPLETOS,
    )

    assert set(scores) == {"valido"}


@pytest.mark.parametrize("response_no_comparable", (None, math.nan, 0.0, -1.0))
def test_response_no_comparable_se_omite_para_threads_sin_excluir(
    response_no_comparable,
):
    brutos = {
        "sin_response": {
            "threads": _resultado(
                "threads", 100.0, response_no_comparable, 0.1
            ),
        },
        "con_response": {
            "threads": _resultado("threads", 100.0, 1.0, 0.1),
        },
    }

    scores = calcular_scores_finales(brutos, tipos=("threads",))

    assert scores["sin_response"] == pytest.approx(scores["con_response"])
    assert scores["sin_response"]["resp"] == 0.0


def test_threads_legacy_con_response_extremo_equivale_a_response_none():
    sin_response = {
        "potente": {
            "threads": _resultado("threads", 200.0, None, 0.1),
        },
        "menos_potente": {
            "threads": _resultado("threads", 100.0, None, 0.1),
        },
    }
    legacy = {
        "potente": {
            "threads": _resultado("threads", 200.0, 1e300, 0.1),
        },
        "menos_potente": {
            "threads": _resultado("threads", 100.0, 1e-300, 0.1),
        },
    }

    esperado = calcular_scores_finales(sin_response, tipos=("threads",))
    actual = calcular_scores_finales(legacy, tipos=("threads",))

    assert actual == esperado
    assert actual["potente"]["score"] > actual["menos_potente"]["score"]


def test_tipo_sin_response_no_duplica_throughput_al_renormalizar_pesos():
    brutos = {
        "referencia": {
            "threads": _resultado("threads", 100.0, None, 0.0),
        },
        "mitad_throughput": {
            "threads": _resultado("threads", 50.0, None, 0.0),
        },
    }
    mejores = calcular_mejores(brutos, tipos=("threads",))

    categorias, potencia, respuesta, fluidez = calcular_score_categorias(
        brutos["mitad_throughput"], mejores
    )

    esperado = ((0.5 * 0.45) + (1.0 * 0.10)) / (0.45 + 0.10)
    assert categorias == pytest.approx([esperado])
    assert potencia == pytest.approx(0.5)
    assert respuesta == 0.0
    assert fluidez == pytest.approx(1.0)


def test_faltar_solo_response_en_un_tipo_no_excluye_y_resp_usa_los_otros():
    rapido = _resultados_completos(response=10.0)
    lento = _resultados_completos(response=20.0)
    rapido["threads"]["response"] = None
    lento["threads"]["response"] = None

    scores = calcular_scores_finales(
        {"rapido": rapido, "lento": lento},
        tipos=TIPOS_COMPLETOS,
    )

    assert set(scores) == {"rapido", "lento"}
    assert scores["rapido"]["resp"] == pytest.approx(100.0)
    assert scores["lento"]["resp"] == pytest.approx(50.0)
    assert all(
        0.0 <= metrica <= 100.0
        for data in scores.values()
        for metrica in data.values()
    )


def test_response_only_omite_threads_sin_response_y_pesos_cero_no_influyen():
    rapido = _resultados_completos(response=10.0, fairness=0.0)
    lento = _resultados_completos(response=20.0, fairness=0.0)
    rapido["threads"]["response"] = None
    lento["threads"].update({"valor": 1.0, "response": None, "fairness": 1.0})

    scores = calcular_scores_finales(
        {"rapido": rapido, "lento": lento},
        pesos=(0.0, 1.0, 0.0),
        tipos=TIPOS_COMPLETOS,
    )

    assert scores["rapido"]["score"] == pytest.approx(100.0)
    assert scores["lento"]["score"] == pytest.approx(50.0)
    assert scores["lento"]["pot"] == pytest.approx((5.0 + 0.01) / 6.0 * 100.0)
    assert scores["lento"]["resp"] == pytest.approx(50.0)
    assert scores["lento"]["flu"] == pytest.approx(5.0 / 6.0 * 100.0)


def test_power_only_usa_los_seis_tipos_sin_otros_pesos():
    potente = _resultados_completos(response=1000.0, fairness=1.0)
    lento = _resultados_completos(response=1.0, fairness=0.0)
    for tipo in ("cpu", "threads", "memory"):
        potente[tipo]["valor"] = 200.0
        lento[tipo]["valor"] = 100.0
    for tipo in ("latencia_fork", "latencia_compile", "latencia_loaded"):
        potente[tipo]["valor"] = 10.0
        lento[tipo]["valor"] = 20.0
    potente["threads"]["response"] = None
    lento["threads"]["response"] = None

    scores = calcular_scores_finales(
        {"potente": potente, "lento": lento},
        pesos=(1.0, 0.0, 0.0),
        tipos=TIPOS_COMPLETOS,
    )

    assert scores["potente"]["score"] == pytest.approx(100.0)
    assert scores["lento"]["score"] == pytest.approx(50.0)
    assert scores["lento"]["pot"] == pytest.approx(50.0)
    assert scores["potente"]["resp"] == pytest.approx(0.1)
    assert scores["lento"]["resp"] == pytest.approx(100.0)
    assert scores["potente"]["flu"] == pytest.approx(0.0)
    assert scores["lento"]["flu"] == pytest.approx(100.0)


def test_categoria_sin_dimensiones_ponderadas_no_participa_pero_sigue_completa():
    brutos = {
        "threads_sin_response": {
            "threads": _resultado("threads", 100.0, None, 0.1),
        },
    }
    mejores = calcular_mejores(brutos, tipos=("threads",))

    categorias, potencia, respuesta, fluidez = calcular_score_categorias(
        brutos["threads_sin_response"],
        mejores,
        pesos=(0.0, 1.0, 0.0),
    )
    scores = calcular_scores_finales(
        brutos,
        pesos=(0.0, 1.0, 0.0),
        tipos=("threads",),
    )

    assert categorias == []
    assert (potencia, respuesta, fluidez) == pytest.approx((1.0, 0.0, 0.9))
    assert scores["threads_sin_response"]["score"] == 0.0
    assert scores["threads_sin_response"]["resp"] == 0.0


def test_p95_legacy_valido_satisface_response_obligatoria_en_todos_los_tipos():
    historico = _resultados_completos(response=5.0)
    for resultado in historico.values():
        resultado["p95"] = resultado.pop("response")

    scores = calcular_scores_finales(
        {"historico": historico},
        pesos=(0.0, 1.0, 0.0),
        tipos=TIPOS_COMPLETOS,
    )

    assert scores["historico"]["score"] == pytest.approx(100.0)
    assert scores["historico"]["resp"] == pytest.approx(100.0)


def test_fairness_cero_y_uno_son_limites_validos():
    brutos = {
        "perfecta": {"cpu": _resultado("cpu", 100.0, 10.0, 0.0)},
        "pesima": {"cpu": _resultado("cpu", 100.0, 10.0, 1.0)},
    }

    scores = calcular_scores_finales(brutos)

    assert set(scores) == {"perfecta", "pesima"}
    assert scores["perfecta"]["flu"] == pytest.approx(100.0)
    assert scores["pesima"]["flu"] == pytest.approx(0.0)
    assert scores["perfecta"]["score"] > scores["pesima"]["score"]


@pytest.mark.parametrize(
    "pesos",
    [
        None,
        (),
        (1.0, 2.0),
        (1.0, 2.0, 3.0, 4.0),
        (-1.0, 1.0, 1.0),
        (0.0, 0.0, 0.0),
        (math.nan, 1.0, 1.0),
        (math.inf, 1.0, 1.0),
        (True, 1.0, 1.0),
        ("1", 1.0, 1.0),
    ],
)
def test_pesos_invalidos_lanzan_value_error(pesos):
    brutos = {"a": {"cpu": _resultado("cpu", 100.0, 10.0)}}

    with pytest.raises(ValueError):
        calcular_scores_finales(brutos, pesos=pesos)


def test_ranking_manual_tambien_valida_pesos():
    datos = [{"sched": "a", **_resultado("cpu", 100.0, 10.0)}]

    with pytest.raises(ValueError):
        calcular_ranking_manual(datos, pesos=(0.0, 0.0, 0.0))


def test_pesos_se_normalizan_y_todos_los_resultados_estan_acotados():
    brutos = {
        "a": {
            "cpu": _resultado("cpu", 200.0, 2.0, 0.0),
            "fork": _resultado("latencia_fork", 10.0, 1.0, 0.0),
        },
        "b": {
            "cpu": _resultado("cpu", 1.0, 2000.0, 1.0),
            "fork": _resultado("latencia_fork", 1000.0, 1000.0, 1.0),
        },
    }

    porcentajes = calcular_scores_finales(brutos, pesos=(45.0, 45.0, 10.0))
    fracciones = calcular_scores_finales(brutos, pesos=(0.45, 0.45, 0.10))
    enormes = calcular_scores_finales(brutos, pesos=(4.5e307, 4.5e307, 1e307))

    _assert_scores_equivalentes(porcentajes, fracciones)
    _assert_scores_equivalentes(porcentajes, enormes)
    for data in porcentajes.values():
        assert all(0.0 <= valor <= 100.0 for valor in data.values())


def test_ranking_manual_usa_todas_las_metricas_del_registro_elegido():
    datos = [
        {
            "sched": "a",
            **_resultado("cpu", 200.0, 100.0, 0.9),
            "timestamp": 1.0,
        },
        {
            "sched": "a",
            **_resultado("cpu", 100.0, 1.0, 0.0),
            "timestamp": 2.0,
        },
        {
            "sched": "b",
            **_resultado("cpu", 150.0, 10.0, 0.1),
            "timestamp": 3.0,
        },
    ]

    scores = calcular_ranking_manual(datos)
    scores_invertidos = calcular_ranking_manual(list(reversed(datos)))

    assert scores["b"] > scores["a"]
    assert scores == pytest.approx(scores_invertidos)
    assert list(scores) == list(scores_invertidos)


def test_ranking_manual_es_coherente_con_menor_es_mejor_en_hyperfine():
    datos = [
        {"sched": "rapido", **_resultado("fork", 10.0, 2.0)},
        {"sched": "lento", **_resultado("latencia_fork", 20.0, 4.0)},
    ]

    scores = calcular_ranking_manual(datos)

    assert scores["rapido"] > scores["lento"]


def test_ranking_manual_no_mezcla_runs_y_el_resultado_es_determinista():
    run_antiguo = [
        {
            "scheduler_name": "a",
            "test_type": "cpu",
            "valor": 300.0,
            "p95": 10.0,
            "fairness": 0.1,
            "run_id": 1,
            "timestamp": 100.0,
        },
        {
            "scheduler_name": "b",
            "test_type": "cpu",
            "valor": 100.0,
            "p95": 10.0,
            "fairness": 0.1,
            "run_id": 1,
            "timestamp": 100.0,
        },
    ]
    run_reciente = [
        {
            "scheduler_name": "a",
            "test_type": "cpu",
            "valor": 100.0,
            "p95": 10.0,
            "fairness": 0.1,
            "run_id": 2,
            "timestamp": 200.0,
        },
        {
            "scheduler_name": "b",
            "test_type": "cpu",
            "valor": 300.0,
            "p95": 10.0,
            "fairness": 0.1,
            "run_id": 2,
            "timestamp": 200.0,
        },
    ]
    historial = run_antiguo + run_reciente

    scores = calcular_ranking_manual(historial)
    esperados = calcular_ranking_manual(run_reciente)
    invertidos = calcular_ranking_manual(list(reversed(historial)))

    assert scores == pytest.approx(esperados)
    assert scores == pytest.approx(invertidos)
    assert scores["b"] > scores["a"]


@pytest.mark.parametrize(
    "tipo",
    (
        "fork",
        "compile",
        "loaded",
        "latencia_fork",
        "latencia_compile",
        "latencia_loaded",
    ),
)
def test_valor_grafico_hyperfine_invierte_duracion_y_respuesta(tipo):
    referencia = {"valor": 10.0, "response": 2.0, "p95": 1000.0}
    duracion_lenta = {"valor": 20.0, "response": 2.0, "p95": 0.01}
    respuesta_lenta = {"valor": 10.0, "response": 4.0, "p95": 0.01}

    valor_referencia = calcular_valor_grafico(referencia, tipo)

    assert valor_referencia > calcular_valor_grafico(duracion_lenta, tipo)
    assert valor_referencia > calcular_valor_grafico(respuesta_lenta, tipo)


def test_valor_grafico_hyperfine_admite_p95_historico_sin_inventar_default():
    rapido = {"valor": 10.0, "p95": 2.0}
    lento = {"valor": 20.0, "p95": 4.0}

    assert calcular_valor_grafico(rapido, "fork") > calcular_valor_grafico(
        lento, "fork"
    )
    assert calcular_valor_grafico({"valor": 10.0}, "fork") == 0.0
