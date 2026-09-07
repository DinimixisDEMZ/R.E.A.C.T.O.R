"""
Dimensionado de las pruebas según el equipo.

Una batería de pruebas con parámetros fijos no significa lo mismo en una
netbook de dos hilos que en un portátil de dieciséis: o la máquina pequeña se
ahoga, o la grande termina antes de que la medición se estabilice. Este módulo
traduce el perfil del equipo a los parámetros concretos de cada prueba.

Dos criterios que conviene tener presentes:

  - **No se fija la afinidad de CPU.** En un procesador híbrido resulta
    tentador anclar la carga a los núcleos de rendimiento para "medir mejor",
    pero decidir en qué núcleo corre cada tarea es precisamente el trabajo del
    planificador. Anclarla mediría el acierto de nuestra decisión, no el suyo.
    Lo que sí se hace es registrar la topología junto al resultado.

  - **La sobresuscripción de la prueba de contención es intencionada.** Sin
    más carga que núcleos no hay contención que medir, y la prueba no diría
    nada sobre el planificador.
"""

from core.hardware import obtener_perfil

# Duración de una prueba, en segundos. El mínimo evita medir sólo el arranque
# del proceso; el máximo evita que una batería completa dure horas en un equipo
# lento, que es justo donde más se nota.
DURACION_MINIMA = 5
DURACION_MAXIMA = 30
DURACION_POR_DEFECTO = 8

#: Umbral de hilos por debajo del cual un equipo se considera pequeño y las
#: pruebas se alargan, porque con pocos núcleos el ruido pesa más.
HILOS_EQUIPO_PEQUENO = 4


class Dimensionado:
    """Parámetros de una batería de pruebas, ya adaptados al equipo."""

    def __init__(self, perfil=None, duracion_solicitada=None):
        self.perfil = perfil or obtener_perfil()
        self.duracion = self._calcular_duracion(duracion_solicitada)

    # ── carga ──

    @property
    def trabajadores(self):
        """Procesos de carga para una prueba de CPU.

        Se usa el número de hilos que este proceso puede usar de verdad
        (afinidad y cuota de cgroup incluidas), no `os.cpu_count()`: dentro de
        un contenedor limitado a 2 CPUs, `cpu_count()` sigue informando los 16
        del anfitrión y la prueba se convertía en una medición del solapamiento.
        """
        return self.perfil.hilos_para_carga

    @property
    def trabajadores_contencion(self):
        """Procesos para la prueba de contención (mutex).

        Se pide más carga que núcleos a propósito: sin sobresuscripción no hay
        contención que medir y la prueba no dice nada del planificador.
        """
        return max(2, self.trabajadores * 2)

    # ── tiempo ──

    def _calcular_duracion(self, solicitada):
        """Duración por prueba, ajustada al equipo.

        En equipos con pocos hilos se alarga: hay menos paralelismo del que
        promediar, así que la varianza es mayor y una prueba corta produce
        números que no se repiten entre corridas.
        """
        base = solicitada if solicitada else DURACION_POR_DEFECTO
        if self.perfil.hilos <= HILOS_EQUIPO_PEQUENO:
            base = base * 1.5
        if self.perfil.es_virtual:
            # En una máquina virtual el anfitrión introduce ruido que sólo se
            # promedia con más tiempo de medición.
            base = base * 1.25
        return int(max(DURACION_MINIMA, min(DURACION_MAXIMA, round(base))))

    def duracion_total_estimada(self, n_schedulers, n_pruebas=6, enfriamiento=15):
        """Segundos que tardará una batería completa, para avisar al usuario.

        En un equipo lento con dieciséis planificadores esto son horas. Decirlo
        antes de empezar es la diferencia entre una herramienta usable y una que
        parece colgada.
        """
        por_scheduler = self.duracion * n_pruebas + enfriamiento
        return int(por_scheduler * max(1, n_schedulers))

    # ── contexto ──

    def como_diccionario(self):
        """Parámetros efectivos, para guardarlos junto a cada resultado.

        Sin esto es imposible saber si dos mediciones del historial se tomaron
        con la misma carga, y compararlas no significa nada.
        """
        return {
            "trabajadores": self.trabajadores,
            "trabajadores_contencion": self.trabajadores_contencion,
            "duracion_s": self.duracion,
        }

    def resumen(self):
        """Una línea legible para el registro técnico."""
        return (f"{self.trabajadores} trabajadores · {self.duracion} s por prueba "
                f"({self.trabajadores_contencion} en contención)")


_DIMENSIONADO = None


def obtener_dimensionado(recargar=False, duracion_solicitada=None):
    """Dimensionado cacheado del equipo actual."""
    global _DIMENSIONADO
    if _DIMENSIONADO is None or recargar or duracion_solicitada is not None:
        _DIMENSIONADO = Dimensionado(duracion_solicitada=duracion_solicitada)
    return _DIMENSIONADO
