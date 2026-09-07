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
    """Valor comparable de un resultado, para las barras y el radar.

    Contrato: **siempre una tasa, donde más alto es mejor**. Sin esa regla las
    barras dejan de significar lo mismo entre categorías y el gráfico miente.

    Aquí había un error de inversión: en las pruebas de latencia el resultado
    guarda `valor` = media en µs (más bajo es mejor) y `p95` = desviación
    estándar. La fórmula anterior devolvía `valor / p95` para "compile" y
    "loaded", es decir media dividida por desviación, así que **el planificador
    más lento salía con la barra más alta**. Y para "fork" usaba `1000 / p95`,
    que premiaba la baja variabilidad e ignoraba por completo la latencia real.
    """
    t = _POR_CLAVE.get(tipo)
    if t is None:
        return res.get("valor", 0)

    if t.clave == "cpu":
        # p95 = µs por cambio de contexto → cambios por milisegundo.
        return 1000.0 / max(0.01, res.get("p95") or 0.01)

    if t.clave == "threads":
        # Operaciones por segundo repartidas entre los núcleos que las hicieron,
        # para que un equipo de 16 hilos no aplaste a uno de 4 en la escala.
        ops = res.get("ops_real") or res.get("valor") or 0
        nucleos = max(1, res.get("cores") or 1)
        return ops / nucleos

    if t.clave == "memory":
        # Operaciones por segundo penalizadas por la latencia del mutex.
        ops = res.get("ops_real") or res.get("valor") or 0
        p95 = max(0.1, res.get("p95") or 1.0)
        return ops / p95

    if t.es_latencia:
        # `valor` es la media en µs y menos es mejor: se invierte para obtener
        # una tasa (operaciones por milisegundo) comparable con el resto.
        media_us = res.get("mean_us") or res.get("valor") or 0
        if media_us <= 0:
            return 0.0
        return 1000.0 / media_us

    return res.get("valor", 0)


def valor_para_ranking(res: dict, tipo: str) -> float:
    """Valor crudo de un resultado, para la tabla técnica.

    A diferencia de `valor_para_grafico`, aquí **no** se normaliza el sentido:
    se devuelve la magnitud medida en su unidad natural, porque la tabla la
    muestra junto a su unidad. En las pruebas de latencia eso significa que
    menos es mejor, y quien consume este valor debe saberlo (`es_latencia`).
    """
    t = _POR_CLAVE.get(tipo)
    if t is None:
        return res.get("valor", 0)
    if t.clave == "cpu":
        return 1000.0 / max(0.01, res.get("p95") or 0.01)
    if t.clave == "memory":
        return res.get("valor", 0) / max(0.1, res.get("p95") or 1.0)
    return res.get("valor", 0)


# ─── Catálogo de planificadores scx ───
#
# Actualizado a scx 1.1.3 (agosto de 2026). Los datos se verificaron ejecutando
# `--help` de cada binario, no copiando descripciones antiguas: varias habían
# quedado obsoletas (scx_cake se reescribió por completo y ya no usa DRR++;
# scx_rusty no es "el planificador base que prioriza cargas interactivas").
#
# Los campos `apto_diario` y `experimental` no son decorativos: gobiernan qué se
# ofrece al usuario y, sobre todo, qué entra en el ciclo de detección automática.
# scx_chaos existe para *provocar* fallos ("designed to amplify race
# conditions... attempt to induce a crash", dice su propia ayuda): incluirlo en
# una batería de pruebas desatendida sería un error.


@dataclass(frozen=True)
class InfoScheduler:
    """Ficha de un planificador scx."""

    nombre: str                     # nombre corto, sin el prefijo scx_
    descripcion: str
    categoria: str                  # gaming | escritorio | servidor | proposito general | experimental
    apto_diario: bool = True        # ¿sirve como planificador de uso cotidiano?
    experimental: bool = False      # ¿upstream lo marca como no apto para producción?
    requiere_config: bool = False   # ¿necesita configuración externa para ser útil?
    advertencia: str = ""           # aviso que la interfaz debe mostrar
    ruta_upstream: str = "rust"     # subdirectorio en el repositorio sched-ext/scx

    @property
    def canonico(self):
        return f"scx_{self.nombre}"

    @property
    def url(self):
        return (f"https://github.com/sched-ext/scx/blob/main/scheds/"
                f"{self.ruta_upstream}/scx_{self.nombre}/README.md")

    @property
    def apto_para_benchmark(self):
        """¿Puede entrar en una batería de pruebas desatendida?

        Se excluye lo que degrada a propósito, lo que necesita configuración que
        la aplicación no tiene, y lo que altera la medición por su cuenta
        (scx_flow levanta un servidor web y se autoajusta mientras corre).
        """
        return self.apto_diario and not self.requiere_config


CATALOGO_SCHEDULERS: list[InfoScheduler] = [
    InfoScheduler(
        "beerland",
        "Mantiene cada tarea en su CPU para preservar la caché, con una cola por "
        "CPU ordenada por plazo. Sólo migra tareas al despertarlas y mientras el "
        "sistema no está saturado.",
        "proposito general"),
    InfoScheduler(
        "bpfland",
        "Clasifica las tareas por su frecuencia de cambios de contexto voluntarios "
        "y prioriza las interactivas sobre las de segundo plano. Permite acotar el "
        "dominio de CPUs preferidas para favorecer rendimiento o consumo.",
        "gaming"),
    InfoScheduler(
        "cake",
        "Planificador orientado a juegos con un único algoritmo y sin parámetros "
        "de ajuste. Bajo saturación encola los despertares en una cola global por "
        "plazo virtual y devuelve las tareas agotadas a la cola de su propia CPU.",
        "gaming"),
    InfoScheduler(
        "chaos",
        "Herramienta de inyección de fallos construida sobre scx_p2dq: introduce "
        "latencia deliberada para hacer aflorar condiciones de carrera en una "
        "aplicación. No es un planificador de uso general.",
        "experimental", apto_diario=False, experimental=True, requiere_config=True,
        advertencia="Degrada el sistema a propósito para provocar fallos. "
                    "Usalo sólo para depurar una aplicación concreta."),
    InfoScheduler(
        "cosmos",
        "Planificador ligero que preserva la localidad entre tarea y CPU con colas "
        "locales, y conmuta a una cola compartida por plazo cuando la carga sube. "
        "Escala bien en equipos con muchos núcleos.",
        "proposito general"),
    InfoScheduler(
        "flash",
        "Planificador por plazo más temprano (EDF) con pesos de latencia dinámicos: "
        "ajusta la prioridad según con cuánta antelación cada tarea libera la CPU. "
        "Pensado para audio y multimedia en tiempo real.",
        "escritorio"),
    InfoScheduler(
        "flow",
        "Planificador por presupuesto orientado a control embebido y sistemas "
        "críticos. Se autoajusta durante la ejecución y levanta un panel web de "
        "telemetría.",
        "experimental", apto_diario=False, experimental=True,
        advertencia="Abre un servidor web local y se reajusta solo mientras corre, "
                    "así que altera cualquier medición.",
        ruta_upstream="experimental"),
    InfoScheduler(
        "forge",
        "Planificador base pensado para que un agente automático lo optimice. "
        "Su política por defecto usa colas por CPU con robo de trabajo.",
        "experimental", apto_diario=False, experimental=True,
        advertencia="Upstream lo marca explícitamente como no apto para producción."),
    InfoScheduler(
        "lavd",
        "Implementa LAVD: mide qué tan crítica es la latencia de cada tarea y usa "
        "esa medida para repartir CPU y ajustar el tiempo de ejecución. Su modo "
        "automático adapta el comportamiento al consumo del equipo.",
        "gaming"),
    InfoScheduler(
        "layered",
        "Clasifica las tareas en capas configurables y aplica una política distinta "
        "a cada una. Es el más flexible del conjunto y también el que más ajuste "
        "requiere.",
        "servidor", requiere_config=True,
        advertencia="Necesita un archivo JSON que describa las capas; sin él no "
                    "arranca con una política útil."),
    InfoScheduler(
        "mlfq",
        "Cola multinivel con realimentación: tres niveles con porciones de 1, 2 y "
        "4 ms, ordenados por plazo virtual. Deliberadamente sin parámetros.",
        "escritorio", apto_diario=False, experimental=True,
        ruta_upstream="experimental"),
    InfoScheduler(
        "p2dq",
        "Planificador de propósito general que reparte carga entre dominios de "
        "caché con un algoritmo de dos opciones, y separa las tareas interactivas "
        "en colas propias.",
        "proposito general"),
    InfoScheduler(
        "pandemonium",
        "Clasifica cada tarea por su comportamiento (despertares, cambios de "
        "contexto, patrones de espera) y adapta las decisiones en tiempo real con "
        "un lazo de control derivado de la topología del equipo.",
        "escritorio"),
    InfoScheduler(
        "rustland",
        "Aplica toda la política de planificación en espacio de usuario, escrita en "
        "Rust. Prioriza las cargas interactivas; sirve sobre todo para experimentar "
        "con políticas propias.",
        "escritorio"),
    InfoScheduler(
        "rusty",
        "Planificador híbrido multidominio: un dominio por caché compartida, con "
        "reajuste continuo y balanceo de carga dirigido desde espacio de usuario. "
        "Rinde mejor en equipos de un solo socket con varios dominios de caché.",
        "proposito general"),
    InfoScheduler(
        "tickless",
        "Concentra los eventos de planificación en unas pocas CPUs y desactiva el "
        "tic periódico en el resto para reducir el ruido del sistema. Orientado a "
        "virtualización y cómputo intensivo.",
        "servidor", apto_diario=False, experimental=True, requiere_config=True,
        advertencia="Sólo aporta algo si el kernel arrancó con nohz_full, y no es "
                    "apto para cargas sensibles a la latencia."),
]

_POR_NOMBRE_SCHED = {info.nombre: info for info in CATALOGO_SCHEDULERS}


def obtener_scheduler(nombre):
    """Ficha de un planificador. Acepta nombre corto o canónico."""
    if not nombre:
        return None
    return _POR_NOMBRE_SCHED.get(str(nombre).removeprefix("scx_"))


def schedulers_para_benchmark(nombres):
    """Filtra una lista de planificadores dejando los aptos para medir.

    Los desconocidos se conservan: puede haber un scheduler nuevo que la
    aplicación todavía no cataloga, y excluirlo por no conocerlo sería peor que
    incluirlo.
    """
    resultado = []
    for nombre in nombres:
        info = obtener_scheduler(nombre)
        if info is None or info.apto_para_benchmark:
            resultado.append(nombre)
    return resultado


# Semilla para la tabla `scheduler_info` de la base de datos, en el formato
# (nombre, descripción, url) que espera core/database.py.
INFO_SCHEDULERS: list[tuple[str, str, str]] = [
    (info.nombre, info.descripcion, info.url) for info in CATALOGO_SCHEDULERS
]
