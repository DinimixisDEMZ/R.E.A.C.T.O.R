"""
Sensor térmico del sistema.
Lee la temperatura del CPU desde /sys/class/thermal/ con caché inteligente.
"""

import os


class SensorTermico:
    """Gestor de temperatura del CPU con caché de ruta."""

    def __init__(self):
        self._cached_temp_path = None
        self.temp_base = 0.0

    def obtener_temp(self):
        """Lectura de temperatura del CPU en °C. Retorna 0.0 si no encuentra sensor."""
        # 1. Intentar la ruta cacheada
        if self._cached_temp_path and os.path.exists(self._cached_temp_path):
            try:
                with open(self._cached_temp_path, 'r') as ft:
                    return int(ft.read().strip()) / 1000.0
            except (OSError, ValueError):
                self._cached_temp_path = None

        # 2. Búsqueda inteligente del sensor adecuado
        try:
            prioridades = ["x86_pkg_temp", "tcpu", "package id 0", "core", "coretemp", "acpitz", "k10temp"]
            zonas = sorted([d for d in os.listdir("/sys/class/thermal/") if d.startswith("thermal_zone")])
            fallback_temp_path = None

            for zone in zonas:
                type_path = f"/sys/class/thermal/{zone}/type"
                temp_path = f"/sys/class/thermal/{zone}/temp"
                if not os.path.exists(type_path) or not os.path.exists(temp_path):
                    continue

                with open(type_path, 'r') as f:
                    name = f.read().strip().lower()

                if fallback_temp_path is None:
                    fallback_temp_path = temp_path

                if any(p in name for p in prioridades):
                    self._cached_temp_path = temp_path
                    with open(temp_path, 'r') as ft:
                        return int(ft.read().strip()) / 1000.0

            if fallback_temp_path is not None:
                self._cached_temp_path = fallback_temp_path
                with open(fallback_temp_path, 'r') as ft:
                    return int(ft.read().strip()) / 1000.0
        except (OSError, ValueError):
            pass
        return 0.0

    def calibrar(self, muestras=3, intervalo=0.5):
        """Toma N muestras y devuelve la temperatura base promediada."""
        import time
        t_samples = []
        for _ in range(muestras):
            t_samples.append(self.obtener_temp())
            time.sleep(intervalo)
        self.temp_base = sum(t_samples) / len(t_samples) if t_samples else 0.0
        return self.temp_base
