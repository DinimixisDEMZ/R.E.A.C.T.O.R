"""Tests para core/scoring.py"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.scoring import (
    _first, media_armonica, calcular_mejores, _normalizar_entrada,
    calcular_score_categorias, calcular_scores_finales, calcular_ranking_manual,
)
from core.constantes import PESOS_POR_DEFECTO
from core.tipos import valor_para_grafico


class TestFirst:
    def test_returns_first_non_none(self):
        assert _first(None, 5, 10) == 5

    def test_all_none_returns_zero(self):
        assert _first(None, None, None) == 0

    def test_first_value_is_used(self):
        assert _first(1, 2, 3) == 1


class TestMediaArmonica:
    def test_empty_list(self):
        assert media_armonica([]) == 0

    def test_single_value(self):
        assert media_armonica([5.0]) == 5.0

    def test_equal_values(self):
        result = media_armonica([10.0, 10.0, 10.0])
        assert abs(result - 10.0) < 0.01

    def test_penalizes_lows(self):
        result_balanced = media_armonica([8.0, 8.0])
        result_unbalanced = media_armonica([10.0, 4.0])
        assert result_balanced > result_unbalanced

    def test_near_zero_clamped(self):
        result = media_armonica([0.001])
        assert result >= 0.01


class TestCalcularMejores:
    def test_basic(self):
        brutos = {
            "scx_rusty": {"cpu": {"valor": 100, "p95": 5.0, "waste": 0.1}},
            "scx_lavd": {"cpu": {"valor": 200, "p95": 3.0, "waste": 0.2}},
        }
        mejores = calcular_mejores(brutos, tipos=("cpu",))
        assert "cpu" in mejores
        assert mejores["cpu"]["max_val"] == 200
        assert mejores["cpu"]["min_p95"] == 3.0

    def test_missing_type_skipped(self):
        brutos = {"scx_rusty": {"cpu": {"valor": 100, "p95": 5.0}}}
        mejores = calcular_mejores(brutos, tipos=("cpu", "memory"))
        assert "cpu" in mejores
        assert "memory" not in mejores


class TestNormalizarEntrada:
    def test_basic_fields(self):
        item = {"valor": 100, "p95": 5.0, "waste": 0.1}
        result = _normalizar_entrada(item)
        assert result["val"] == 100
        assert result["p95"] == 5.0
        assert result["waste"] == 0.1

    def test_metrics_fallback(self):
        item = {"metrics": {"bogo-ops-per-second-real-time": 500, "nanosecs-per-context-switch": 3000}}
        result = _normalizar_entrada(item)
        assert result["val"] == 500
        assert result["p95"] == 3.0

    def test_waste_from_cpu_usage(self):
        item = {"cpu_usage": 80.0}
        result = _normalizar_entrada(item)
        assert abs(result["waste"] - 0.2) < 0.01

    def test_no_data_defaults(self):
        item = {}
        result = _normalizar_entrada(item)
        assert result["val"] == 0
        assert result["waste"] == 0.5


class TestCalcularScoresFinales:
    def test_two_schedulers(self):
        brutos = {
            "scx_rusty": {
                "cpu": {"valor": 100, "p95": 5.0, "waste": 0.1},
                "threads": {"valor": 200, "p95": 8.0, "waste": 0.2},
            },
            "scx_lavd": {
                "cpu": {"valor": 150, "p95": 3.0, "waste": 0.15},
                "threads": {"valor": 180, "p95": 10.0, "waste": 0.3},
            },
        }
        scores = calcular_scores_finales(brutos)
        assert len(scores) == 2
        for sc in ("scx_rusty", "scx_lavd"):
            assert "score" in scores[sc]
            assert 0 <= scores[sc]["score"] <= 100

    def test_better_scheduler_scores_higher(self):
        brutos = {
            "winner": {
                "cpu": {"valor": 500, "p95": 1.0, "waste": 0.01},
            },
            "loser": {
                "cpu": {"valor": 100, "p95": 10.0, "waste": 0.5},
            },
        }
        scores = calcular_scores_finales(brutos)
        assert scores["winner"]["score"] > scores["loser"]["score"]

    def test_custom_pesos(self):
        brutos = {
            "scx_a": {"cpu": {"valor": 100, "p95": 5.0, "waste": 0.1}},
        }
        scores_default = calcular_scores_finales(brutos)
        scores_custom = calcular_scores_finales(brutos, pesos=(0.1, 0.1, 0.8))
        assert scores_default != scores_custom


class TestCalcularRankingManual:
    def test_empty_returns_empty(self):
        assert calcular_ranking_manual([]) == {}

    def test_basic_ranking(self):
        datos = [
            {"sched": "scx_rusty", "tipo": "cpu", "valor": 100, "p95": 5.0, "waste": 0.1},
            {"sched": "scx_lavd", "tipo": "cpu", "valor": 200, "p95": 3.0, "waste": 0.2},
        ]
        scores = calcular_ranking_manual(datos)
        assert "scx_rusty" in scores
        assert "scx_lavd" in scores


class TestCalcularValorGrafico:
    def test_cpu_type(self):
        res = {"p95": 5.0}
        assert valor_para_grafico(res, "cpu") == 200.0

    def test_threads_type(self):
        res = {"valor": 1000, "cores": 4}
        assert valor_para_grafico(res, "threads") == 250.0

    def test_memory_type(self):
        res = {"valor": 12000, "p95": 3.0}
        assert valor_para_grafico(res, "memory") == 4000.0

    def test_fork_type(self):
        res = {"p95": 2.0}
        assert valor_para_grafico(res, "fork") == 500.0

    def test_unknown_type_returns_valor(self):
        res = {"valor": 42}
        assert valor_para_grafico(res, "unknown") == 42


class TestCalcularScoreCategorias:
    def test_basic_scoring(self):
        data = {
            "cpu": {"valor": 100, "p95": 5, "waste": 0.1, "cores": 4},
            "threads": {"valor": 200, "p95": 3, "waste": 0.05, "cores": 4},
        }
        mejores = {
            "cpu": {"max_val": 200, "min_val": 100, "min_p95": 3},
            "threads": {"max_val": 200, "min_val": 100, "min_p95": 2},
        }
        scores, pot, resp, flu = calcular_score_categorias(data, mejores)
        assert len(scores) == 2
        assert all(s > 0 for s in scores)

    def test_empty_data_returns_empty(self):
        scores, pot, resp, flu = calcular_score_categorias({}, {})
        assert scores == []
        assert pot == 0
        assert resp == 0
        assert flu == 0

    def test_custom_pesos(self):
        data = {"cpu": {"valor": 100, "p95": 5, "waste": 0.1, "cores": 4}}
        mejores = {"cpu": {"max_val": 100, "min_val": 100, "min_p95": 5}}
        scores, pot, resp, flu = calcular_score_categorias(data, mejores, pesos=(0.5, 0.3, 0.2))
        assert len(scores) == 1
        assert 0 < scores[0] <= 1.0
