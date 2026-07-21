import math
import os

import pytest

from core.thermal import SensorTermico, _parse_sysfs_temperature


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(value), encoding="utf-8")


def _thermal_zone(root, number, name, temperature):
    zone = root / f"thermal_zone{number}"
    _write(zone / "type", name)
    _write(zone / "temp", temperature)
    return zone / "temp"


def _hwmon_sensor(root, device, driver, number, label, temperature):
    hwmon = root / f"hwmon{device}"
    _write(hwmon / "name", driver)
    _write(hwmon / f"temp{number}_label", label)
    _write(hwmon / f"temp{number}_input", temperature)
    return hwmon / f"temp{number}_input"


def test_hotter_tctl_is_not_hidden_by_higher_priority_tdie(tmp_path):
    hwmon_root = tmp_path / "hwmon"
    tdie_path = _hwmon_sensor(hwmon_root, 0, "k10temp", 1, "Tdie", 55_000)
    tctl_path = _hwmon_sensor(hwmon_root, 0, "k10temp", 2, "Tctl", 70_000)

    sensor = SensorTermico(
        thermal_root=tmp_path / "thermal",
        hwmon_root=hwmon_root,
    )

    assert sensor.obtener_temp() == pytest.approx(70.0)
    assert sensor._cached_temp_path == os.fspath(tctl_path)
    assert sensor.sensor_activo == "k10temp Tctl"
    assert sensor.estado_lectura == SensorTermico.STATUS_OK
    assert [candidate.path for candidate in sensor._cached_candidates] == [
        os.fspath(tdie_path),
        os.fspath(tctl_path),
    ]


def test_hotter_core_is_not_hidden_by_higher_priority_package(tmp_path):
    hwmon_root = tmp_path / "hwmon"
    package_path = _hwmon_sensor(
        hwmon_root, 0, "coretemp", 1, "Package id 0", 55_000
    )
    core_path = _hwmon_sensor(hwmon_root, 0, "coretemp", 2, "Core 0", 70_000)
    sensor = SensorTermico(
        thermal_root=tmp_path / "thermal",
        hwmon_root=hwmon_root,
    )

    assert sensor.obtener_temp() == pytest.approx(70.0)
    assert sensor._cached_temp_path == os.fspath(core_path)
    assert sensor.sensor_activo == "coretemp Core 0"
    assert [candidate.path for candidate in sensor._cached_candidates] == [
        os.fspath(package_path),
        os.fspath(core_path),
    ]


def test_gpu_and_acpi_are_not_used_as_cpu_fallback(tmp_path):
    thermal_root = tmp_path / "thermal"
    hwmon_root = tmp_path / "hwmon"
    _thermal_zone(thermal_root, 0, "acpitz", 43_000)
    _hwmon_sensor(hwmon_root, 0, "amdgpu", 1, "GPU Core", 51_000)

    sensor = SensorTermico(thermal_root=thermal_root, hwmon_root=hwmon_root)

    assert sensor.obtener_temp() == 0.0
    assert sensor.sensor_activo is None
    assert sensor.estado_lectura == SensorTermico.STATUS_MISSING


def test_partial_failure_keeps_all_other_valid_candidates_in_refreshed_cache(
    tmp_path,
):
    thermal_root = tmp_path / "thermal"
    hwmon_root = tmp_path / "hwmon"
    tdie_path = _hwmon_sensor(hwmon_root, 0, "k10temp", 1, "Tdie", 61_000)
    tctl_path = _hwmon_sensor(hwmon_root, 0, "k10temp", 2, "Tctl", 52_000)

    sensor = SensorTermico(thermal_root=thermal_root, hwmon_root=hwmon_root)
    assert sensor.obtener_temp() == pytest.approx(61.0)
    assert sensor._cached_temp_path == os.fspath(tdie_path)
    assert {candidate.path for candidate in sensor._cached_candidates} == {
        os.fspath(tdie_path),
        os.fspath(tctl_path),
    }

    tdie_path.write_text("0", encoding="utf-8")
    core_path = _thermal_zone(thermal_root, 0, "coretemp", 70_000)

    assert sensor.obtener_temp() == pytest.approx(70.0)
    assert sensor._cached_temp_path == os.fspath(core_path)
    assert {candidate.path for candidate in sensor._cached_candidates} == {
        os.fspath(tctl_path),
        os.fspath(core_path),
    }
    assert sensor.estado_lectura == SensorTermico.STATUS_OK


def test_same_priority_package_sensors_use_hottest_reading(tmp_path):
    hwmon_root = tmp_path / "hwmon"
    package_zero = _hwmon_sensor(
        hwmon_root, 0, "coretemp", 1, "Package id 0", 45_000
    )
    package_one = _hwmon_sensor(
        hwmon_root, 0, "coretemp", 2, "Package id 1", 63_000
    )
    sensor = SensorTermico(
        thermal_root=tmp_path / "thermal",
        hwmon_root=hwmon_root,
    )

    assert sensor.obtener_temp() == pytest.approx(63.0)
    assert sensor._cached_temp_path == os.fspath(package_one)
    assert {candidate.path for candidate in sensor._cached_candidates} == {
        os.fspath(package_zero),
        os.fspath(package_one),
    }


def test_cache_ttl_discovers_a_hotter_sensor_that_appears_later(tmp_path):
    thermal_root = tmp_path / "thermal"
    hwmon_root = tmp_path / "hwmon"
    _thermal_zone(thermal_root, 0, "x86_pkg_temp", 60_000)
    now = [0.0]
    sensor = SensorTermico(
        thermal_root=thermal_root,
        hwmon_root=hwmon_root,
        clock=lambda: now[0],
        cache_ttl=30.0,
    )

    assert sensor.obtener_temp() == pytest.approx(60.0)
    _hwmon_sensor(hwmon_root, 0, "k10temp", 1, "Tdie", 70_000)
    now[0] = 10.0
    assert sensor.obtener_temp() == pytest.approx(60.0)
    now[0] = 31.0
    assert sensor.obtener_temp() == pytest.approx(70.0)


def test_missing_and_broken_sensor_have_distinct_internal_status(tmp_path):
    missing = SensorTermico(
        thermal_root=tmp_path / "missing-thermal",
        hwmon_root=tmp_path / "missing-hwmon",
    )
    assert missing.obtener_temp() == 0.0
    assert missing.estado_lectura == SensorTermico.STATUS_MISSING
    assert missing.ultimo_error is None

    hwmon_root = tmp_path / "hwmon"
    _hwmon_sensor(hwmon_root, 0, "coretemp", 1, "Package id 0", "nan")
    broken = SensorTermico(
        thermal_root=tmp_path / "thermal",
        hwmon_root=hwmon_root,
    )
    assert broken.obtener_temp() == 0.0
    assert broken.estado_lectura == SensorTermico.STATUS_ERROR
    assert broken.ultimo_error


def test_filesystem_clock_and_roots_are_injectable(tmp_path):
    thermal_root = tmp_path / "thermal"
    temp_path = _thermal_zone(thermal_root, 0, "x86_pkg_temp", 47_500)

    class FilesystemSpy:
        def __init__(self):
            self.reads = []

        def exists(self, path):
            return os.path.exists(path)

        def listdir(self, path):
            return os.listdir(path)

        def read_text(self, path):
            self.reads.append(path)
            with open(path, "r", encoding="utf-8") as file:
                return file.read()

    filesystem = FilesystemSpy()
    sensor = SensorTermico(
        thermal_root=thermal_root,
        hwmon_root=None,
        filesystem=filesystem,
        clock=lambda: 123.5,
    )

    assert sensor.obtener_temp() == pytest.approx(47.5)
    assert os.fspath(temp_path) in filesystem.reads
    assert sensor._last_checked_at == 123.5


def test_filesystem_failure_is_reported_as_error_not_missing():
    class BrokenFilesystem:
        @staticmethod
        def exists(_path):
            raise PermissionError("denied")

    sensor = SensorTermico(
        thermal_root="thermal",
        hwmon_root="hwmon",
        filesystem=BrokenFilesystem(),
    )

    assert sensor.obtener_temp() == 0.0
    assert sensor.estado_lectura == SensorTermico.STATUS_ERROR
    assert "denied" in sensor.ultimo_error


def test_calibration_excludes_zero_nonfinite_and_errors():
    sleeps = []
    sensor = SensorTermico(sleep=sleeps.append)
    readings = iter([0.0, float("nan"), 40.0, 42.0, None])
    sensor.obtener_temp = lambda: next(readings)

    assert sensor.calibrar(muestras=5, intervalo=0.25) == pytest.approx(41.0)
    assert sleeps == [0.25, 0.25, 0.25, 0.25]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("42500", 42.5),
        ("0", None),
        ("-1000", None),
        ("151000", None),
        ("nan", None),
        ("inf", None),
    ],
)
def test_sysfs_temperature_validation(raw, expected):
    result = _parse_sysfs_temperature(raw)
    if expected is None:
        assert result is None
    else:
        assert math.isfinite(result)
        assert result == pytest.approx(expected)
