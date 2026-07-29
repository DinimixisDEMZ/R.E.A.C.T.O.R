"""
Registro centralizado de tipos de prueba de benchmark.
Única fuente de verdad para: clave, nombres, índices radar, fórmula de gráfico,
si es latencia (hyperfine) o híbrido, y alias.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class TipoPrueba:
    clave: str
    indice_radar: int
    nombre_radar: str
    nombre_visible: str
    es_latencia: bool = False
    es_hibrido: bool = False
    alias: tuple[str, ...] = ()


TIPOS: list[TipoPrueba] = [
    TipoPrueba("cpu",            0, "C.Contexto",   "Cambio de Contexto"),
    TipoPrueba("threads",        1, "Carga\nMixta", "Carga Mixta"),
    TipoPrueba("memory",         2, "Sincronización","Sincronización"),
    TipoPrueba("latencia_fork",  3, "Bifurcación",  "Creación de Procesos",
               es_latencia=True, es_hibrido=True, alias=("fork",)),
    TipoPrueba("latencia_compile", 4, "Compilación", "Compilación Paralela",
               es_latencia=True, es_hibrido=True, alias=("compile",)),
    TipoPrueba("latencia_loaded",  5, "Bajo\nCarga", "Bajo Carga",
               es_latencia=True, es_hibrido=True, alias=("loaded",)),
]

# ── Índices de acceso rápido ──
_POR_CLAVE: dict[str, TipoPrueba] = {}
for t in TIPOS:
    _POR_CLAVE[t.clave] = t
    for a in t.alias:
        _POR_CLAVE[a] = t


def obtener_tipo(clave: str) -> TipoPrueba | None:
    return _POR_CLAVE.get(clave)


def es_tipo_valido(clave: str) -> bool:
    return clave in _POR_CLAVE


def claves_stress_ng() -> tuple[str, ...]:
    return tuple(t.clave for t in TIPOS if not t.es_hibrido)


def claves_hibridas() -> set[str]:
    s: set[str] = set()
    for t in TIPOS:
        if t.es_hibrido:
            s.add(t.clave)
            s.update(t.alias)
    return s


# ── Constantes derivadas (compatibilidad con código legacy) ──
CATEGORIAS_RADAR: list[str] = [t.nombre_radar for t in TIPOS]

TIPOS_PRUEBA: list[tuple[str, str]] = [(t.clave, t.nombre_visible) for t in TIPOS]

MAPA_CHART: dict[str, int] = {}
for t in TIPOS:
    MAPA_CHART[t.clave] = t.indice_radar
    for a in t.alias:
        MAPA_CHART[a] = t.indice_radar

TIPOS_LATENCIA: tuple[str, ...] = tuple(t.clave for t in TIPOS if t.es_latencia)

TIPOS_CPU: tuple[str, ...] = tuple(t.clave for t in TIPOS if not t.es_latencia)


def valor_para_grafico(res: dict, tipo: str) -> float:
    t = _POR_CLAVE.get(tipo)
    if t is None:
        return res.get("valor", 0)
    if t.clave == "cpu":
        return 1000.0 / max(0.01, res.get("p95", 0))
    if t.clave == "threads":
        ops = res.get("ops_real") or res.get("valor") or 0
        cores = max(1, res.get("cores") or 1)
        return ops / cores
    if t.clave == "memory":
        ops = res.get("ops_real") or res.get("valor") or 0
        p95 = max(0.1, res.get("p95") or 1.0)
        return ops / p95
    # híbridos: fork, compile, loaded
    if t.es_hibrido:
        if t.clave in ("latencia_fork",):
            return 1000.0 / max(0.01, res.get("p95", 0))
        # compile, loaded
        ops = res.get("valor") or 0
        p95 = max(0.1, res.get("p95") or 1.0)
        return ops / p95
    return res.get("valor", 0)


def valor_para_ranking(res: dict, tipo: str) -> float:
    t = _POR_CLAVE.get(tipo)
    if t is None:
        return res.get("valor", 0)
    if t.clave == "cpu":
        return 1000.0 / max(0.01, res.get("p95", 0))
    if t.clave == "memory":
        return res.get("valor", 0) / max(0.1, res.get("p95") or 1.0)
    if t.es_latencia:
        return res.get("valor", 0)
    return res.get("valor", 0)


# ─── Información de Schedulers (descripciones, URLs) ───

INFO_SCHEDULERS: list[tuple[str, str, str]] = [
    ("beerland", "Prioritiza localidad y escalabilidad. Mantiene tareas en el mismo CPU para preservar caché, usando DSQ locales cuando el sistema no está saturado.", "https://github.com/sched-ext/scx"),
    ("bpfland", "Planificador basado en vruntime que prioriza cargas interactivas sobre las de segundo plano. Considera la jerarquía de caché L2/L3 al asignar CPUs para reducir cache misses.", "https://github.com/sched-ext/scx"),
    ("cake", "Adapta el algoritmo DRR++ (Deficit Round Robin) de CAKE para planificación CPU. Clasifica tareas en 4 niveles (Crítico/Interactivo/Marco/Masivo). Diseñado para gaming en CPUs modernos AMD/Intel.", "https://github.com/sched-ext/scx"),
    ("cosmos", "Planificador ligero optimizado para preservar localidad tarea-CPU. Reduce contención de locks usando DSQ locales, escalando bien en sistemas con muchos CPUs.", "https://github.com/sched-ext/scx"),
    ("flash", "Planificador EDF (Earliest Deadline First) con pesos de latencia dinámicos. Ajusta prioridades según qué tan temprano cada tarea libera la CPU. Ideal para multimedia y audio en tiempo real.", "https://github.com/sched-ext/scx"),
    ("flow", "Planificador para entornos de servidor y cargas dinámicas con balanceo eficiente.", "https://github.com/sched-ext/scx"),
    ("forge", "Planificador base orientado a IA, diseñado para ser personalizado y optimizado por agentes LLM. Política por defecto con colas por CPU y robo de trabajo entre núcleos.", "https://github.com/sched-ext/scx"),
    ("lavd", "Implementa LAVD (Latency-criticality Aware Virtual Deadline). Mide qué tan crítica es la latencia de cada tarea y usa esa información para decisiones de planificación y asignación de timeslice.", "https://github.com/sched-ext/scx"),
    ("pandemonium", "Clasifica cada tarea por comportamiento (frecuencia de wakeup, context switches, patrones de sueño) y adapta decisiones en tiempo real con un oscilador armónico amortiguado. Topología basada en resistencia efectiva.", "https://github.com/sched-ext/scx"),
    ("p2dq", "Planificador de propósito general con algoritmo pick-two para balanceo de carga entre LLCs y nodos NUMA. Clasifica tareas interactivas en colas separadas con autoslice configurable.", "https://github.com/sched-ext/scx"),
    ("rustland", "Planificador en espacio de usuario escrito en Rust. Prioriza cargas interactivas (gaming, video, streaming) sobre tareas CPU-intensivas de fondo. Diseñado para baja latencia.", "https://github.com/sched-ext/scx"),
    ("rusty", "Planificador baseline en Rust que prioriza workloads interactivos. Original del ecosistema sched-ext. Buena opción para uso general y gaming.", "https://github.com/sched-ext/scx"),
    ("tickless", "Planificador orientado a servidores para cloud, virtualización y HPC. Enruta eventos de planificación a través de CPUs primarias, desactivando ticks en otras para reducir ruido del sistema.", "https://github.com/sched-ext/scx"),
]
