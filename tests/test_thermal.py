"""Tests para core/thermal.py — sensor térmico con filesystem mock."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch, MagicMock, mock_open
from core.thermal import SensorTermico


class TestSensorTermico:
    def test_no_sensor_returns_zero(self):
        sensor = SensorTermico()
        with patch("os.listdir", return_value=[]):
            with patch("os.path.exists", return_value=False):
                temp = sensor.obtener_temp()
                assert temp == 0.0

    def test_cached_path_used(self):
        sensor = SensorTermico()
        sensor._cached_temp_path = "/tmp/fake_temp"
        m = mock_open(read_data="55000")
        with patch("os.path.exists", return_value=True), \
             patch("builtins.open", m):
            temp = sensor.obtener_temp()
            assert temp == 55.0

    def test_cached_path_broken_falls_through(self):
        sensor = SensorTermico()
        sensor._cached_temp_path = "/tmp/broken"
        with patch("os.path.exists", return_value=False), \
             patch("os.listdir", return_value=[]):
            temp = sensor.obtener_temp()
            assert temp == 0.0

    def test_priority_zone_detection(self):
        sensor = SensorTermico()

        def fake_listdir(path):
            return ["thermal_zone0", "thermal_zone1"]

        def fake_exists(path):
            return True

        def fake_open(path, mode="r"):
            content_map = {
                "/sys/class/thermal/thermal_zone0/type": "acpitz",
                "/sys/class/thermal/thermal_zone0/temp": "42000",
                "/sys/class/thermal/thermal_zone1/type": "x86_pkg_temp",
                "/sys/class/thermal/thermal_zone1/temp": "58000",
            }
            m = mock_open(read_data=content_map.get(path, "0"))
            return m()

        with patch("os.listdir", side_effect=fake_listdir), \
             patch("os.path.exists", side_effect=fake_exists), \
             patch("builtins.open", side_effect=fake_open):
            temp = sensor.obtener_temp()
            # Both match priorities; sorted order means acpitz (zone0) is found first
            assert temp == 42.0
            assert sensor._cached_temp_path == "/sys/class/thermal/thermal_zone0/temp"

    def test_fallback_when_no_priority_match(self):
        sensor = SensorTermico()

        def fake_listdir(path):
            return ["thermal_zone0"]

        def fake_exists(path):
            return True

        def fake_open(path, mode="r"):
            content_map = {
                "/sys/class/thermal/thermal_zone0/type": "some_unknown",
                "/sys/class/thermal/thermal_zone0/temp": "47000",
            }
            m = mock_open(read_data=content_map.get(path, "0"))
            return m()

        with patch("os.listdir", side_effect=fake_listdir), \
             patch("os.path.exists", side_effect=fake_exists), \
             patch("builtins.open", side_effect=fake_open):
            temp = sensor.obtener_temp()
            assert temp == 47.0
            assert sensor._cached_temp_path == "/sys/class/thermal/thermal_zone0/temp"

    def test_calibrar_averages(self):
        sensor = SensorTermico()
        with patch.object(sensor, "obtener_temp", side_effect=[40.0, 42.0, 44.0]):
            import time as _time
            with patch.object(_time, "sleep"):
                result = sensor.calibrar(muestras=3, intervalo=0.1)
                assert abs(result - 42.0) < 0.01
                assert sensor.temp_base == 42.0
