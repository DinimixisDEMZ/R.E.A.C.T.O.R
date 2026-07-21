"""Lectura segura de sensores térmicos de CPU en sysfs."""

from dataclasses import dataclass
import math
import os
import re
import threading
import time


_MIN_CPU_TEMP_C = 1.0
_MAX_CPU_TEMP_C = 150.0
_HWMON_TEMP_INPUT_RE = re.compile(r"^temp(\d+)_input$")

# El orden es intencionado. Las etiquetas de paquete/die son más fiables que
# el nombre genérico del driver, y un core individual queda como último recurso.
_CPU_SENSOR_PRIORITIES = (
    ("tdie",),
    ("tctl",),
    ("x86 pkg temp",),
    ("cpu package", "package id"),
    ("package",),
    ("tcpu",),
    ("cpu thermal",),
    ("k10temp",),
    ("coretemp",),
    ("cpu core",),
)

_NON_CPU_SENSOR_MARKERS = ("acpi", "amdgpu", "gpu", "nvme", "pch")


@dataclass(frozen=True)
class _SensorCandidate:
    path: str
    name: str
    priority: int
    source: str


class _OSFilesystem:
    """Adaptador mínimo para poder sustituir sysfs en pruebas."""

    @staticmethod
    def exists(path):
        return os.path.exists(path)

    @staticmethod
    def listdir(path):
        return os.listdir(path)

    @staticmethod
    def read_text(path):
        with open(path, "r", encoding="utf-8", errors="replace") as file:
            return file.read()


def _normalize_sensor_name(name):
    text = str(name or "").strip().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def _contains_sensor_marker(name, marker):
    if " " in marker:
        return marker in name
    return re.search(rf"\b{re.escape(marker)}\b", name) is not None


def _sensor_priority(name):
    """Devuelve la prioridad CPU de un nombre o ``None`` si no da evidencia."""
    normalized = _normalize_sensor_name(name)
    if not normalized:
        return None
    if any(
        _contains_sensor_marker(normalized, marker)
        for marker in _NON_CPU_SENSOR_MARKERS
    ):
        return None
    for priority, markers in enumerate(_CPU_SENSOR_PRIORITIES):
        if any(_contains_sensor_marker(normalized, marker) for marker in markers):
            return priority
    return None


def _parse_sysfs_temperature(raw_value):
    """Convierte miligrados de sysfs a Celsius y rechaza valores inverosímiles."""
    try:
        value = float(str(raw_value).strip()) / 1000.0
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(value) or not _MIN_CPU_TEMP_C <= value <= _MAX_CPU_TEMP_C:
        return None
    return value


class SensorTermico:
    """Gestor de temperatura CPU con selección priorizada y caché invalidable.

    ``filesystem`` debe implementar ``exists``, ``listdir`` y ``read_text``.
    Las raíces, el reloj y la espera son inyectables para no depender de
    ``/sys`` ni de pausas reales durante las pruebas.
    """

    STATUS_OK = "ok"
    STATUS_MISSING = "missing"
    STATUS_ERROR = "error"

    def __init__(
        self,
        thermal_root="/sys/class/thermal",
        hwmon_root="/sys/class/hwmon",
        filesystem=None,
        clock=None,
        sleep=None,
        cache_ttl=30.0,
    ):
        self.thermal_root = os.fspath(thermal_root) if thermal_root is not None else ""
        self.hwmon_root = os.fspath(hwmon_root) if hwmon_root is not None else ""
        self._fs = filesystem or _OSFilesystem()
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        try:
            self._cache_ttl = float(cache_ttl)
        except (TypeError, ValueError, OverflowError):
            self._cache_ttl = 0.0
        if not math.isfinite(self._cache_ttl) or self._cache_ttl < 0:
            self._cache_ttl = 0.0
        self._lock = threading.RLock()
        self._cached_temp_path = None
        self._cached_sensor_name = None
        self._cached_candidates = ()
        self._last_discovery_at = None
        self._last_status = self.STATUS_MISSING
        self._last_error = None
        self._filesystem_error = None
        self._last_checked_at = None
        self.temp_base = 0.0

    @property
    def estado_lectura(self):
        """Estado de la última lectura: ``ok``, ``missing`` o ``error``."""
        with self._lock:
            return self._last_status

    @property
    def ultimo_error(self):
        with self._lock:
            return self._last_error

    @property
    def sensor_activo(self):
        with self._lock:
            return self._cached_sensor_name

    def _exists(self, path):
        try:
            return bool(self._fs.exists(path))
        except Exception as error:
            self._filesystem_error = error
            return False

    def _mark_result(self, status, error=None):
        self._last_status = status
        self._last_error = str(error) if error is not None else None
        self._last_checked_at = self._now()

    def _now(self):
        try:
            now = float(self._clock())
        except Exception:
            return None
        return now if math.isfinite(now) else None

    def _cache_is_fresh(self):
        if not self._cached_candidates or self._last_discovery_at is None:
            return False
        now = self._now()
        if now is None:
            return False
        age = now - self._last_discovery_at
        return 0.0 <= age <= self._cache_ttl

    def _invalidate_cache(self):
        self._cached_temp_path = None
        self._cached_sensor_name = None
        self._cached_candidates = ()

    def _cache_readings(self, readings):
        hottest_candidate, temperature = max(readings, key=lambda item: item[1])
        self._cached_candidates = tuple(candidate for candidate, _temp in readings)
        self._cached_temp_path = hottest_candidate.path
        self._cached_sensor_name = hottest_candidate.name
        return temperature

    def _read_temperature(self, path):
        if not self._exists(path):
            raise FileNotFoundError(path)
        raw_value = self._fs.read_text(path)
        temperature = _parse_sysfs_temperature(raw_value)
        if temperature is None:
            raise ValueError(f"Lectura térmica inválida en {path}")
        return temperature

    def _discover_thermal_candidates(self):
        candidates = []
        had_error = False
        if not self._exists(self.thermal_root):
            return candidates, had_error
        try:
            zones = sorted(
                entry
                for entry in self._fs.listdir(self.thermal_root)
                if str(entry).startswith("thermal_zone")
            )
        except Exception:
            return candidates, True

        for zone in zones:
            zone_root = os.path.join(self.thermal_root, str(zone))
            type_path = os.path.join(zone_root, "type")
            temp_path = os.path.join(zone_root, "temp")
            if not self._exists(type_path):
                continue
            try:
                name = self._fs.read_text(type_path).strip()
            except Exception:
                had_error = True
                continue
            priority = _sensor_priority(name)
            if priority is None:
                continue
            if not self._exists(temp_path):
                had_error = True
                continue
            candidates.append(
                _SensorCandidate(temp_path, name, priority, "thermal")
            )
        return candidates, had_error

    def _discover_hwmon_candidates(self):
        candidates = []
        had_error = False
        if not self._exists(self.hwmon_root):
            return candidates, had_error
        try:
            devices = sorted(
                entry
                for entry in self._fs.listdir(self.hwmon_root)
                if str(entry).startswith("hwmon")
            )
        except Exception:
            return candidates, True

        for device in devices:
            device_root = os.path.join(self.hwmon_root, str(device))
            driver_name = ""
            name_path = os.path.join(device_root, "name")
            if self._exists(name_path):
                try:
                    driver_name = self._fs.read_text(name_path).strip()
                except Exception:
                    had_error = True
            try:
                entries = sorted(self._fs.listdir(device_root))
            except Exception:
                had_error = True
                continue

            for entry in entries:
                match = _HWMON_TEMP_INPUT_RE.match(str(entry))
                if match is None:
                    continue
                temp_path = os.path.join(device_root, str(entry))
                label = ""
                label_path = os.path.join(
                    device_root, f"temp{match.group(1)}_label"
                )
                if self._exists(label_path):
                    try:
                        label = self._fs.read_text(label_path).strip()
                    except Exception:
                        had_error = True
                sensor_name = " ".join(
                    value for value in (driver_name, label) if value
                )
                priority = _sensor_priority(sensor_name)
                if priority is None:
                    continue
                candidates.append(
                    _SensorCandidate(temp_path, sensor_name, priority, "hwmon")
                )
        return candidates, had_error

    def _discover_candidates(self):
        thermal, thermal_error = self._discover_thermal_candidates()
        hwmon, hwmon_error = self._discover_hwmon_candidates()
        candidates = thermal + hwmon
        candidates.sort(
            key=lambda candidate: (
                candidate.priority,
                0 if candidate.source == "hwmon" else 1,
                candidate.path,
            )
        )
        return candidates, thermal_error or hwmon_error

    def obtener_temp(self):
        """Lee la temperatura CPU en °C; conserva ``0.0`` como valor sin datos."""
        with self._lock:
            self._filesystem_error = None
            failed_paths = set()
            last_error = None

            if self._cache_is_fresh():
                cached_readings = []
                for candidate in self._cached_candidates:
                    try:
                        temperature = self._read_temperature(candidate.path)
                    except Exception as error:
                        failed_paths.add(candidate.path)
                        last_error = error
                    else:
                        cached_readings.append((candidate, temperature))
                if not failed_paths and cached_readings:
                    temperature = self._cache_readings(cached_readings)
                    self._mark_result(self.STATUS_OK)
                    return temperature
                self._invalidate_cache()

            candidates, discovery_error = self._discover_candidates()
            self._last_discovery_at = self._now()
            readings = []
            for candidate in candidates:
                if candidate.path in failed_paths:
                    continue
                try:
                    temperature = self._read_temperature(candidate.path)
                except Exception as error:
                    failed_paths.add(candidate.path)
                    last_error = error
                else:
                    readings.append((candidate, temperature))
            if readings:
                temperature = self._cache_readings(readings)
                self._mark_result(self.STATUS_OK)
                return temperature

            self._invalidate_cache()
            if candidates or failed_paths or discovery_error or self._filesystem_error:
                self._mark_result(
                    self.STATUS_ERROR,
                    last_error
                    or self._filesystem_error
                    or "No se pudo leer un sensor térmico CPU válido.",
                )
            else:
                self._mark_result(self.STATUS_MISSING)
            return 0.0

    def calibrar(self, muestras=3, intervalo=0.5):
        """Promedia únicamente muestras térmicas válidas y finitas."""
        sample_count = max(0, int(muestras))
        try:
            sleep_interval = float(intervalo)
        except (TypeError, ValueError, OverflowError):
            sleep_interval = 0.0
        if not math.isfinite(sleep_interval) or sleep_interval < 0:
            sleep_interval = 0.0

        valid_samples = []
        for index in range(sample_count):
            temperature = self.obtener_temp()
            try:
                valid = (
                    math.isfinite(temperature)
                    and _MIN_CPU_TEMP_C <= temperature <= _MAX_CPU_TEMP_C
                )
            except TypeError:
                valid = False
            if valid:
                valid_samples.append(temperature)
            if sleep_interval and index + 1 < sample_count:
                self._sleep(sleep_interval)

        with self._lock:
            self.temp_base = (
                sum(valid_samples) / len(valid_samples) if valid_samples else 0.0
            )
            return self.temp_base
