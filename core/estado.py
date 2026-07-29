"""
Dataclasses de estado por módulo.
Evita el patrón god-object en VentanaSimple.
"""

import threading
from dataclasses import dataclass, field


@dataclass
class EstadoPrueba:
    """Estado del test manual de rendimiento (rendimiento.py)."""
    en_proceso: bool = False
    datos: list = field(default_factory=list)
    btn_activo: object = None
    icono_original: str = ""


@dataclass
class EstadoMonitor:
    """Estado de deltas del monitor en vivo (monitoreo.py)."""
    prev_cpu_total: float | None = None
    prev_cpu_idle: float | None = None
    prev_cores: dict = field(default_factory=dict)
    prev_ctxt: int | None = None
    prev_ctxt_time: float | None = None


@dataclass
class EstadoDeteccionAuto:
    """Estado del motor de detección automática (automatizacion/*)."""
    en_proceso: bool = False
    progreso_actual: float = 0.0
    progreso_objetivo: float = 0.0
    segundos_actuales: float = 0.0
    segundos_objetivos: float = 0.0
    ganador_final: str | None = None
    desc_final: str = ""
    brutos_finales: dict = field(default_factory=dict)
    brutos_lock: threading.Lock = field(default_factory=threading.Lock)
    scores_finales: dict = field(default_factory=dict)
    ajustando_pesos: bool = False
    historial_runs: list = field(default_factory=list)
    indice_historial: int = -1
    cargando_historial: bool = False
    peso_timer: int = 0
    recalc_timer: int = 0
    info_clicks: int = 0
