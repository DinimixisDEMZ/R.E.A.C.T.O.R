import math
from types import SimpleNamespace

import pytest

from ui import diagnostico as diagnostics
from ui.diagnostico import (
    _diagnostico_esta_ocupado,
    _extraer_valores_radar,
    _flatten_lscpu,
    _intervalo_sondeo_diagnostico,
    _parse_lscpu_text,
    normalizar_radar,
    parse_cache_to_mib,
    parse_numeric,
    preparar_datos_radar,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("4,60 GHz", 4.60),
        ("1.234,56 MHz", 1234.56),
        ("1,234.56 MHz", 1234.56),
        ("CPU(s): 16", 16.0),
        ("-5", 0.0),
        ("NaN", 0.0),
        ("+Inf", 0.0),
        ("1e309", 0.0),
        (None, 0.0),
        (float("nan"), 0.0),
    ],
)
def test_parse_numeric_accepts_locales_and_rejects_nonfinite(text, expected):
    assert parse_numeric(text) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("256 KiB", 0.25),
        ("16 MiB (1 instancia)", 16.0),
        ("1,5 GiB", 1536.0),
        ("1048576 B", 1.0),
        ("NaN MiB", 0.0),
    ],
)
def test_parse_cache_to_mib_is_finite(text, expected):
    assert parse_cache_to_mib(text) == pytest.approx(expected)


def test_radar_uses_declared_axis_maxima_not_per_value_maxima():
    raw = [16, 2, 3000, 32, 8, 8]

    assert normalizar_radar(raw) == pytest.approx([0.5] * 6)


def test_radar_preserves_axis_raw_and_display_alignment_with_missing_values():
    normalized, names, raw, display = preparar_datos_radar(
        [16, 0, None, float("nan"), 8]
    )

    assert names == ["CPUs", "Hilos", "GHz", "L3", "L2", "Cores"]
    assert raw == [16.0, 0.0, 0.0, 0.0, 8.0, 0.0]
    assert display == ["16", "?", "?", "?", "8.0 MiB", "?"]
    assert len(normalized) == len(names) == len(raw) == len(display) == 6
    assert all(math.isfinite(value) and 0.0 <= value <= 1.0 for value in normalized)


def test_radar_clamps_overflow_and_rejects_nan_inf():
    values = normalizar_radar([64, float("nan"), float("inf"), -1, 100, 99])

    assert values == pytest.approx([1.0, 0.0, 0.0, 0.0, 1.0, 1.0])


def test_lscpu_values_are_parsed_and_aligned_for_radar():
    flat_map = {
        "cpu(s)": "16",
        "thread(s) per core": "2",
        "cpu max mhz": "4,60 GHz",
        "l3 cache": "32 MiB (1 instance)",
        "l2 cache": "4 MiB",
        "core(s) per socket": "8",
    }

    assert _extraer_valores_radar(flat_map, []) == pytest.approx(
        [16.0, 2.0, 4600.0, 32.0, 4.0, 8.0]
    )


def test_lscpu_flattening_ignores_malformed_nodes_and_keeps_children():
    data = {
        "lscpu": [
            None,
            {"field": "CPU(s):", "data": "16"},
            {
                "field": "Caches:",
                "data": None,
                "children": [
                    {"field": "L3 cache:", "data": "32 MiB"},
                    "invalid",
                ],
            },
            {"field": "", "data": "ignored"},
        ]
    }

    fields, flat_map = _flatten_lscpu(data)

    assert fields == [("CPU(s)", "16"), ("L3 cache", "32 MiB")]
    assert flat_map == {"cpu(s)": "16", "l3 cache": "32 MiB"}
    assert _flatten_lscpu({"lscpu": "invalid"}) == ([], {})


def test_plain_lscpu_parser_rejects_empty_fields():
    assert _parse_lscpu_text("CPU(s): 16\ninvalid\n: empty\nL3 cache: 32 MiB") == [
        ("CPU(s)", "16"),
        ("L3 cache", "32 MiB"),
    ]


def test_cpu_snapshot_uses_text_fallback_for_empty_json(monkeypatch):
    monkeypatch.setattr(
        diagnostics, "_ejecutar_lscpu_json", lambda: {"lscpu": []}
    )
    monkeypatch.setattr(
        diagnostics, "_ejecutar_lscpu_texto", lambda: "CPU(s): 16"
    )
    monkeypatch.setattr(diagnostics, "_leer_proc_cpuinfo", lambda: [])

    snapshot = diagnostics._recoger_snapshot_cpu()

    assert snapshot["lscpu"] == {"lscpu": []}
    assert snapshot["lscpu_text"] == "CPU(s): 16"


def test_busy_state_reduces_polling_and_honors_automation_fallback():
    idle_operations = SimpleNamespace(is_busy=False)
    active_operations = SimpleNamespace(is_busy=True)

    assert not _diagnostico_esta_ocupado(
        SimpleNamespace(
            operaciones=idle_operations,
            en_proceso_auto=False,
            en_proceso_bench=False,
        )
    )
    assert _diagnostico_esta_ocupado(
        SimpleNamespace(
            operaciones=idle_operations,
            en_proceso_auto=True,
            en_proceso_bench=False,
        )
    )
    assert _diagnostico_esta_ocupado(
        SimpleNamespace(
            operaciones=active_operations,
            en_proceso_auto=False,
            en_proceso_bench=False,
        )
    )
    assert _intervalo_sondeo_diagnostico(False, True) == 1_500
    assert _intervalo_sondeo_diagnostico(False, False) == 5_000
    assert _intervalo_sondeo_diagnostico(True, True) == 10_000


class _WidgetProbe:
    def __init__(self):
        self.fractions = []
        self.labels = []
        self.css_classes = set()

    def set_fraction(self, value):
        self.fractions.append(value)

    def set_label(self, value):
        self.labels.append(value)

    def add_css_class(self, css_class):
        self.css_classes.add(css_class)

    def remove_css_class(self, css_class):
        self.css_classes.discard(css_class)


def _monitor_window():
    return SimpleNamespace(
        _prev_cpu_total=None,
        _prev_cpu_idle=None,
        _prev_cores={},
        _prev_ctxt=None,
        _prev_ctxt_time=None,
    )


def _monitor_widgets():
    return {
        "cpu_progress": _WidgetProbe(),
        "cpu_val_lbl": _WidgetProbe(),
        "mem_progress": _WidgetProbe(),
        "mem_val_lbl": _WidgetProbe(),
        "temp_val_lbl": _WidgetProbe(),
        "sched_val_lbl": _WidgetProbe(),
        "core_bars": {"cpu0": _WidgetProbe()},
        "core_labels": {"cpu0": _WidgetProbe()},
        "ctxt_rate_lbl": _WidgetProbe(),
        "ctxt_total_lbl": _WidgetProbe(),
        "procs_running_lbl": _WidgetProbe(),
        "procs_blocked_lbl": _WidgetProbe(),
        "loadavg_lbl": _WidgetProbe(),
    }


def _monitor_snapshot(cpu, core, ctxt, timestamp):
    return {
        "cpu": cpu,
        "memory": (16.0, 8.0, 0.5),
        "temperature": 50.0,
        "scheduler": (None, None),
        "scheduler_available": True,
        "cores": {"cpu0": core},
        "planning": (ctxt, 1, 0),
        "loadavg": (1.0, 0.5, 0.25),
        "timestamp": timestamp,
    }


def test_cpu_rate_uses_only_samples_after_pause_resume():
    win = _monitor_window()
    widgets = _monitor_widgets()

    diagnostics.actualizar_diagnostico_tiempo_real(
        win, widgets, _monitor_snapshot((100, 60), (100, 60), 1_000, 10.0)
    )
    diagnostics.actualizar_diagnostico_tiempo_real(
        win, widgets, _monitor_snapshot((120, 68), (120, 68), 1_200, 12.0)
    )
    assert widgets["cpu_progress"].fractions == pytest.approx([0.6])

    assert diagnostics._invalidar_baselines_diagnostico(win)
    assert not diagnostics._invalidar_baselines_diagnostico(win)
    diagnostics.actualizar_diagnostico_tiempo_real(
        win, widgets, _monitor_snapshot((1_000, 400), (1_000, 400), 10_000, 100.0)
    )

    assert widgets["cpu_progress"].fractions == pytest.approx([0.6])
    assert win._prev_cpu_total == 1_000
    assert win._prev_cpu_idle == 400

    diagnostics.actualizar_diagnostico_tiempo_real(
        win, widgets, _monitor_snapshot((1_020, 408), (1_020, 408), 10_100, 102.0)
    )
    assert widgets["cpu_progress"].fractions == pytest.approx([0.6, 0.6])


def test_core_rate_uses_only_samples_after_pause_resume():
    win = _monitor_window()
    widgets = _monitor_widgets()

    diagnostics.actualizar_diagnostico_tiempo_real(
        win, widgets, _monitor_snapshot((100, 50), (100, 60), 1_000, 10.0)
    )
    diagnostics.actualizar_diagnostico_tiempo_real(
        win, widgets, _monitor_snapshot((120, 60), (120, 68), 1_200, 12.0)
    )
    core_baselines = win._prev_cores
    assert widgets["core_bars"]["cpu0"].fractions == pytest.approx([0.6])

    assert diagnostics._invalidar_baselines_diagnostico(win)
    assert win._prev_cores is core_baselines
    assert win._prev_cores == {}
    assert not diagnostics._invalidar_baselines_diagnostico(win)
    assert win._prev_cores is core_baselines
    diagnostics.actualizar_diagnostico_tiempo_real(
        win, widgets, _monitor_snapshot((1_000, 500), (1_000, 400), 10_000, 100.0)
    )

    assert widgets["core_bars"]["cpu0"].fractions == pytest.approx([0.6])
    assert win._prev_cores == {"cpu0": (1_000.0, 400.0)}

    diagnostics.actualizar_diagnostico_tiempo_real(
        win, widgets, _monitor_snapshot((1_020, 510), (1_020, 408), 10_100, 102.0)
    )
    assert widgets["core_bars"]["cpu0"].fractions == pytest.approx([0.6, 0.6])


def test_context_switch_rate_uses_only_samples_after_pause_resume():
    win = _monitor_window()
    widgets = _monitor_widgets()

    diagnostics.actualizar_diagnostico_tiempo_real(
        win, widgets, _monitor_snapshot((100, 50), (100, 50), 1_000, 10.0)
    )
    diagnostics.actualizar_diagnostico_tiempo_real(
        win, widgets, _monitor_snapshot((120, 60), (120, 60), 1_200, 12.0)
    )
    assert widgets["ctxt_rate_lbl"].labels == ["100 ctxt/s"]

    assert diagnostics._invalidar_baselines_diagnostico(win)
    diagnostics.actualizar_diagnostico_tiempo_real(
        win, widgets, _monitor_snapshot((1_000, 500), (1_000, 500), 10_000, 100.0)
    )

    assert widgets["ctxt_rate_lbl"].labels == ["100 ctxt/s"]
    assert win._prev_ctxt == 10_000
    assert win._prev_ctxt_time == 100.0

    diagnostics.actualizar_diagnostico_tiempo_real(
        win, widgets, _monitor_snapshot((1_020, 510), (1_020, 510), 10_100, 102.0)
    )
    assert widgets["ctxt_rate_lbl"].labels == ["100 ctxt/s", "50 ctxt/s"]
