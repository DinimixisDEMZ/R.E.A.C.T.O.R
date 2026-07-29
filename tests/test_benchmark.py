"""Tests para core/benchmark.py — parser YAML minimalista."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.benchmark import _parsear_yaml_simple


class TestParsearYamlSimple:
    def test_empty_content(self):
        assert _parsear_yaml_simple("") == {}

    def test_no_metrics_section(self):
        assert _parsear_yaml_simple("stressor: cpu\nbogo-ops: 100\n") == {}

    def test_basic_metrics(self):
        yaml = """metrics:
- stressor: cpu
  bogo-ops: 15000
  bogo-ops-per-second-real-time: 3000.5
  nanosecs-per-context-switch-pipe-method: 4500
  cpu-usage-per-instance: 85.2
"""
        result = _parsear_yaml_simple(yaml)
        assert result["stressor"] == "cpu"
        assert result["bogo-ops"] == 15000.0
        assert result["bogo-ops-per-second-real-time"] == 3000.5
        assert result["nanosecs-per-context-switch-pipe-method"] == 4500.0
        assert result["cpu-usage-per-instance"] == 85.2

    def test_mutex_metrics(self):
        yaml = """metrics:
- stressor: mutex
  bogo-ops: 8000
  bogo-ops-per-second-real-time: 1600.0
  nanosecs-per-mutex: 625.0
"""
        result = _parsear_yaml_simple(yaml)
        assert result["stressor"] == "mutex"
        assert result["nanosecs-per-mutex"] == 625.0
        assert result["bogo-ops-per-second-real-time"] == 1600.0

    def test_stops_at_new_section(self):
        yaml = """metrics:
- stressor: cpu
  bogo-ops: 1000

system:
  kernel: 6.1
"""
        result = _parsear_yaml_simple(yaml)
        assert "bogo-ops" in result

    def test_real_world_fragment(self):
        yaml = """stress-ng: info: [12345] dispatching stressors: cpu, cpu, cpu, cpu
metrics:
- stressor: cpu
  bogo-ops: 25432
  bogo-ops-per-second-real-time: 5086.40
  bogo-ops-per-second-usr-sys-time: 5200.12
  cpu-usage-per-instance: 98.50
  nanosecs-per-context-switch-pipe-method: 196.60
  wall-clock-time: 5.001234
  system: { "user": 12.34, "sys": 8.56 }
"""
        result = _parsear_yaml_simple(yaml)
        assert result.get("stressor") == "cpu"
        assert result.get("bogo-ops") == 25432.0
        assert result.get("bogo-ops-per-second-real-time") == 5086.40
        assert result.get("cpu-usage-per-instance") == 98.50

    def test_quoted_values(self):
        yaml = """metrics:
- stressor: switch
  version: '0.17.0'
"""
        result = _parsear_yaml_simple(yaml)
        assert result.get("version") == "0.17.0"

    def test_non_numeric_value_kept_as_string(self):
        yaml = """metrics:
- stressor: switch
  wall-clock-time: 5.5
"""
        result = _parsear_yaml_simple(yaml)
        assert result["stressor"] == "switch"
        assert result["wall-clock-time"] == 5.5
