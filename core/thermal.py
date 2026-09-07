"""
Sensor térmico adaptado al hardware del equipo.

Dos decisiones de diseño que separan este módulo de una lectura ingenua de
temperatura:

  1. **El sensor se elige por prioridad real, no por orden de aparición.**
     Se recorren todos los candidatos, se puntúan y gana el mejor. Además se
     mira `/sys/class/hwmon` antes que `/sys/class/thermal`, porque los equipos
     AMD con `k10temp` no registran ninguna zona térmica: sólo aparecen en hwmon.

  2. **Los umbrales los publica el propio hardware.** Un portátil declara su
     Tjmax en `temp*_crit` (100 °C en un Intel móvil, 95 °C en un Ryzen). Fijar
     un umbral universal de 75 °C declara limitación térmica en equipos que
     están perfectamente bien, y no la detecta en los que sí la sufren.

La señal más honesta de que una medición quedó invalidada no es la temperatura
absoluta sino el contador de limitación térmica del kernel, que también se
expone acá.
"""

import glob
import os
import re
import time

from core.hardware import leer_entero, leer_texto, throttling_termico

# Rango físicamente posible de una lectura de temperatura, en °C. sysfs publica
# valores basura (se ha visto un temp2_max de 65261 °C en un NVMe); descartarlos
# evita que un sensor roto arruine la interfaz.
TEMP_MINIMA_VALIDA = -40.0
TEMP_MAXIMA_VALIDA = 150.0

# Tjmax por defecto cuando el hardware no lo publica. Conservador a propósito.
TJMAX_POR_DEFECTO = 95.0
TJMAX_MINIMO = 60.0
TJMAX_MAXIMO = 125.0

# Fracción del Tjmax a partir de la cual se considera temperatura elevada y
# crítica. Se expresan como proporción, nunca como grados absolutos.
FRACCION_ELEVADA = 0.75
FRACCION_CRITICA = 0.90

# Sensores que nunca son la temperatura del CPU. Se descartan por nombre de
# driver o de etiqueta.
#
# La comparación usa límites de palabra a propósito: buscar la subcadena "ac"
# dentro de "Package id 0" da un falso positivo y descarta justo el mejor sensor
# del equipo. Es el mismo error de subcadena que hacía que el parser de `lscpu`
# confundiera el porcentaje de escalado con la frecuencia.
_EXCLUIDOS = (
    r"nvme\w*", r"drivetemp", r"spd\d*", r"iwlwifi\w*", r"ath\w+", r"mt\d+\w*",
    r"amdgpu", r"nouveau", r"radeon", r"i915", r"xe", r"gpu",
    r"bat\d*", r"acad", r"ac", r"adp\d*", r"hidpp\w*", r"ucsi\w*",
    r"r8\d+\w*", r"e1000\w*", r"igc", r"enp\w+", r"eth\d*",
    r"wifi", r"wlan\d*", r"nct\d*", r"it\d+", r"pch\w*",
)

_RE_EXCLUIDOS = re.compile(
    r"(?:^|[^a-z0-9])(?:" + "|".join(_EXCLUIDOS) + r")(?:[^a-z0-9]|$)"
)

# Prioridad de los candidatos: cuanto menor el número, mejor el sensor.
# El paquete del CPU es siempre preferible a un núcleo suelto, y cualquiera de
# los dos es preferible a la zona ACPI del chasis, que puede errar 20 °C.
_PRIORIDADES = (
    (1, ("package id", "tdie", "tctl")),
    (2, ("x86_pkg_temp", "k10temp", "zenpower", "coretemp")),
    (3, ("tccd", "core ")),
    (4, ("cpu_thermal", "soc_thermal", "cpu-thermal", "tcpu")),
    (9, ("acpitz",)),
)


def _es_excluido(texto):
    return bool(_RE_EXCLUIDOS.search((texto or "").lower()))


def _prioridad_de(nombre, etiqueta):
    """Puntúa un candidato. Devuelve None si no parece un sensor de CPU."""
    nombre = (nombre or "").lower()
    etiqueta = (etiqueta or "").lower()
    if _es_excluido(nombre) or _es_excluido(etiqueta):
        return None
    combinado = f"{nombre} {etiqueta}"
    for puntaje, agujas in _PRIORIDADES:
        if any(aguja in combinado for aguja in agujas):
            return puntaje
    return None


def _temperatura_valida(milesimas):
    """Convierte milésimas de grado a °C, o None si el valor es imposible."""
    if milesimas is None:
        return None
    grados = milesimas / 1000.0
    if TEMP_MINIMA_VALIDA <= grados <= TEMP_MAXIMA_VALIDA:
        return grados
    return None


def _candidatos_hwmon():
    """Sensores de CPU expuestos por /sys/class/hwmon.

    Es la única vía en equipos AMD: `k10temp` no registra zona térmica.
    """
    encontrados = []
    for base in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        nombre = leer_texto(f"{base}/name") or ""
        if _es_excluido(nombre):
            continue
        for ruta_entrada in sorted(glob.glob(f"{base}/temp*_input")):
            prefijo = ruta_entrada[: -len("_input")]
            etiqueta = leer_texto(f"{prefijo}_label") or ""
            prioridad = _prioridad_de(nombre, etiqueta)
            if prioridad is None:
                continue
            encontrados.append({
                "ruta": ruta_entrada,
                "critico": f"{prefijo}_crit",
                "maximo": f"{prefijo}_max",
                "nombre": f"{nombre}/{etiqueta}" if etiqueta else nombre,
                "prioridad": prioridad,
            })
    return encontrados


def _candidatos_thermal():
    """Sensores de CPU expuestos por /sys/class/thermal."""
    encontrados = []
    for base in sorted(glob.glob("/sys/class/thermal/thermal_zone*")):
        tipo = leer_texto(f"{base}/type") or ""
        prioridad = _prioridad_de(tipo, "")
        if prioridad is None:
            continue
        if not os.path.exists(f"{base}/temp"):
            continue
        encontrados.append({
            "ruta": f"{base}/temp",
            "critico": None,
            "maximo": None,
            "zona": base,
            "nombre": tipo,
            "prioridad": prioridad,
        })
    return encontrados


def _critico_de_zona(zona):
    """Temperatura del trip point crítico de una zona térmica, en °C."""
    mejor = None
    for ruta_tipo in sorted(glob.glob(f"{zona}/trip_point_*_type")):
        tipo = (leer_texto(ruta_tipo) or "").lower()
        if tipo not in ("critical", "hot"):
            continue
        ruta_temp = ruta_tipo.replace("_type", "_temp")
        grados = _temperatura_valida(leer_entero(ruta_temp))
        if grados is None:
            continue
        if mejor is None or (tipo == "critical" and grados < mejor):
            mejor = grados
    return mejor


class SensorTermico:
    """Lectura de temperatura del CPU con umbrales derivados del hardware."""

    def __init__(self):
        self._candidato = None
        self._descubierto = False
        self._tjmax = None
        self.temp_base = 0.0
        self._throttling_inicial = None

    # ── descubrimiento ──

    def _descubrir(self):
        """Elige el mejor sensor disponible. Se ejecuta una sola vez."""
        self._descubierto = True
        candidatos = _candidatos_hwmon() + _candidatos_thermal()
        # Se descartan los que no devuelven una lectura creíble ahora mismo.
        validos = [
            c for c in candidatos
            if _temperatura_valida(leer_entero(c["ruta"])) is not None
        ]
        if not validos:
            self._candidato = None
            return
        # Menor prioridad numérica gana; a igualdad, hwmon antes que thermal.
        validos.sort(key=lambda c: (c["prioridad"], "zona" in c))
        self._candidato = validos[0]

    def _asegurar_sensor(self):
        if not self._descubierto or (
            self._candidato and not os.path.exists(self._candidato["ruta"])
        ):
            self._descubierto = False
            self._tjmax = None
            self._descubrir()
        return self._candidato

    @property
    def nombre_sensor(self):
        """Nombre del sensor elegido, para mostrarlo en el diagnóstico."""
        candidato = self._asegurar_sensor()
        return candidato["nombre"] if candidato else None

    # ── lectura ──

    def obtener_temp(self):
        """Temperatura del CPU en °C. Devuelve 0.0 si no hay sensor utilizable."""
        candidato = self._asegurar_sensor()
        if not candidato:
            return 0.0
        grados = _temperatura_valida(leer_entero(candidato["ruta"]))
        return grados if grados is not None else 0.0

    def hay_sensor(self):
        """True si el equipo expone una temperatura de CPU creíble."""
        return self._asegurar_sensor() is not None

    # ── umbrales derivados del hardware ──

    def tjmax(self):
        """Temperatura crítica que declara el hardware, en °C.

        Se lee de `temp*_crit`/`temp*_max` en hwmon o del trip point crítico de
        la zona térmica. Si el equipo no la publica se usa un valor conservador.
        """
        if self._tjmax is not None:
            return self._tjmax
        candidato = self._asegurar_sensor()
        valor = None
        if candidato:
            for clave in ("critico", "maximo"):
                ruta = candidato.get(clave)
                if ruta:
                    valor = _temperatura_valida(leer_entero(ruta))
                    if valor is not None:
                        break
            if valor is None and candidato.get("zona"):
                valor = _critico_de_zona(candidato["zona"])
        if valor is None or not (TJMAX_MINIMO <= valor <= TJMAX_MAXIMO):
            valor = TJMAX_POR_DEFECTO
        self._tjmax = valor
        return valor

    def umbrales(self):
        """Umbrales (estable, elevado) en °C, derivados del Tjmax del equipo."""
        limite = self.tjmax()
        return limite * FRACCION_ELEVADA, limite * FRACCION_CRITICA

    def margen(self):
        """Fracción del margen térmico consumida: 0.0 en frío, 1.0 en el límite."""
        limite = self.tjmax()
        if limite <= 0:
            return 0.0
        return max(0.0, min(1.5, self.obtener_temp() / limite))

    def nivel(self):
        """Clasifica el estado térmico: 'estable', 'elevado', 'critico'.

        Devuelve 'desconocido' cuando el equipo no expone sensores, para que la
        interfaz pueda decirlo en vez de inventar un número.
        """
        if not self.hay_sensor():
            return "desconocido"
        temperatura = self.obtener_temp()
        elevado, critico = self.umbrales()
        if temperatura >= critico:
            return "critico"
        if temperatura >= elevado:
            return "elevado"
        return "estable"

    # ── limitación térmica real ──

    def marcar_inicio_throttling(self):
        """Fija el punto de referencia de los contadores de limitación térmica."""
        self._throttling_inicial = throttling_termico()

    def hubo_throttling(self):
        """True si el hardware frenó por temperatura desde el último marcado.

        Es la señal objetiva de que una medición quedó invalidada. Devuelve
        False si el equipo no expone contadores (AMD no los publica).
        """
        if self._throttling_inicial is None:
            return False
        actual = throttling_termico()
        if not (actual["disponible"] and self._throttling_inicial["disponible"]):
            return False
        return (
            actual["nucleo"] > self._throttling_inicial["nucleo"]
            or actual["paquete"] > self._throttling_inicial["paquete"]
        )

    # ── protocolo de enfriamiento ──

    def calibrar(self, muestras=3, intervalo=0.5):
        """Toma N muestras y devuelve la temperatura base promediada."""
        lecturas = []
        for indice in range(max(1, muestras)):
            lecturas.append(self.obtener_temp())
            if indice < muestras - 1:
                time.sleep(intervalo)
        self.temp_base = sum(lecturas) / len(lecturas) if lecturas else 0.0
        return self.temp_base

    def esperar_estabilizacion(self, tolerancia=1.5, ventana=4, intervalo=1.0,
                               espera_maxima=90.0, cancelado=None):
        """Espera a que la temperatura se estabilice antes de medir.

        No se espera a alcanzar una temperatura absoluta: en un portátil pequeño
        eso puede no ocurrir nunca. Se espera a que deje de bajar, que es lo que
        de verdad hace comparables dos mediciones consecutivas.

        Args:
            tolerancia: variación máxima en °C dentro de la ventana para
                considerar la temperatura estable.
            ventana: cuántas lecturas consecutivas deben caber en la tolerancia.
            intervalo: segundos entre lecturas.
            espera_maxima: tope absoluto de espera, en segundos.
            cancelado: invocable que devuelve True para abortar la espera.

        Returns:
            dict con el resultado: si se estabilizó, cuánto esperó y a qué
            temperatura terminó.
        """
        if not self.hay_sensor():
            return {"estabilizado": False, "motivo": "sin_sensor",
                    "esperado": 0.0, "temperatura": 0.0}

        inicio = time.monotonic()
        recientes = []
        while True:
            if cancelado is not None and cancelado():
                return {"estabilizado": False, "motivo": "cancelado",
                        "esperado": time.monotonic() - inicio,
                        "temperatura": self.obtener_temp()}

            recientes.append(self.obtener_temp())
            if len(recientes) > ventana:
                recientes.pop(0)

            if len(recientes) == ventana and (max(recientes) - min(recientes)) <= tolerancia:
                return {"estabilizado": True, "motivo": "estable",
                        "esperado": time.monotonic() - inicio,
                        "temperatura": recientes[-1]}

            if time.monotonic() - inicio >= espera_maxima:
                return {"estabilizado": False, "motivo": "tiempo_agotado",
                        "esperado": time.monotonic() - inicio,
                        "temperatura": recientes[-1] if recientes else 0.0}

            time.sleep(intervalo)
