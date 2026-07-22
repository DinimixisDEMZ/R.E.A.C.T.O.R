"""
Persistencia de historial de benchmarks en SQLite.
Almacena resultados individuales con metadata de entorno (versiones, kernel).
"""

import platform
import sqlite3
import subprocess
import time
from pathlib import Path

_DB_DIR = Path.home() / ".local" / "share" / "scxctl"
_DB_PATH = _DB_DIR / "history.db"
_db_temp = None

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    kernel_version TEXT NOT NULL,
    scxctl_version TEXT,
    stressng_version TEXT,
    hyperfine_version TEXT,
    run_type TEXT NOT NULL DEFAULT 'manual'
);

CREATE TABLE IF NOT EXISTS results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    timestamp REAL NOT NULL,
    scheduler_name TEXT NOT NULL,
    test_type TEXT NOT NULL,
    valor REAL NOT NULL,
    p95 REAL,
    fairness REAL,
    modo TEXT,
    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS compatibility (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scheduler_name TEXT NOT NULL,
    kernel_version TEXT NOT NULL,
    is_compatible INTEGER NOT NULL,
    message TEXT,
    timestamp REAL NOT NULL,
    UNIQUE(scheduler_name, kernel_version)
);

CREATE INDEX IF NOT EXISTS idx_results_scheduler ON results(scheduler_name);
CREATE INDEX IF NOT EXISTS idx_results_test_type ON results(test_type);
CREATE INDEX IF NOT EXISTS idx_results_run_id ON results(run_id);
CREATE INDEX IF NOT EXISTS idx_runs_timestamp ON runs(timestamp);
CREATE INDEX IF NOT EXISTS idx_compat_kernel ON compatibility(kernel_version);

CREATE TABLE IF NOT EXISTS scheduler_info (
    scheduler_name TEXT PRIMARY KEY,
    description TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT 'https://github.com/sched-ext/scx'
);
"""


def _cmd_output(cmd, timeout=3):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip().split('\n')[0] if r.returncode == 0 else None
    except Exception:
        return None


def _get_conn():
    global _db_temp
    if _db_temp is not None:
        return _db_temp
    _DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def _close_conn(conn):
    global _db_temp
    if conn is not _db_temp:
        conn.close()


def activar_db_temporal():
    global _db_temp
    if _db_temp is not None:
        return
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    for stmt in _SCHEMA_SQL.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    conn.commit()
    _db_temp = conn


def desactivar_db_temporal():
    global _db_temp
    if _db_temp is not None:
        _db_temp.close()
        _db_temp = None


def obtener_versiones():
    return {
        "kernel": platform.release(),
        "scxctl": _cmd_output(["scxctl", "--version"]),
        "stressng": _cmd_output(["stress-ng", "--version"]),
        "hyperfine": _cmd_output(["hyperfine", "--version"]),
    }


def inicializar_db():
    conn = _get_conn()
    try:
        conn.executescript(_SCHEMA_SQL)
        sembrar_info_schedulers()
    finally:
        _close_conn(conn)


def guardar_run(versiones, run_type="manual"):
    conn = _get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO runs (timestamp, kernel_version, scxctl_version, stressng_version, hyperfine_version, run_type) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (time.time(), versiones.get("kernel", ""), versiones.get("scxctl"),
             versiones.get("stressng"), versiones.get("hyperfine"), run_type)
        )
        run_id = cur.lastrowid
        conn.commit()
        return run_id
    finally:
        _close_conn(conn)


def guardar_resultado(run_id, result):
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO results (run_id, timestamp, scheduler_name, test_type, valor, p95, fairness, modo) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, result.get("timestamp", time.time()), result["sched"],
             result["tipo"], result["valor"], result.get("p95"),
             result.get("fairness"), result.get("modo"))
        )
        conn.commit()
    finally:
        _close_conn(conn)


def guardar_resultados_batch(run_id, results):
    conn = _get_conn()
    try:
        conn.executemany(
            "INSERT INTO results (run_id, timestamp, scheduler_name, test_type, valor, p95, fairness, modo) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(run_id, r.get("timestamp", time.time()), r["sched"], r["tipo"],
              r["valor"], r.get("p95"), r.get("fairness"), r.get("modo")) for r in results]
        )
        conn.commit()
    finally:
        _close_conn(conn)


def consultar_historial(scheduler=None, test_type=None, date_from=None, date_to=None, limit=200):
    conn = _get_conn()
    try:
        query = ("SELECT r.*, ru.timestamp as run_ts, ru.kernel_version, ru.scxctl_version, "
                 "ru.stressng_version, ru.hyperfine_version, ru.run_type "
                 "FROM results r JOIN runs ru ON r.run_id = ru.id WHERE 1=1")
        params = []
        if scheduler:
            query += " AND r.scheduler_name = ?"
            params.append(scheduler)
        if test_type:
            query += " AND r.test_type = ?"
            params.append(test_type)
        if date_from:
            query += " AND r.timestamp >= ?"
            params.append(date_from)
        if date_to:
            query += " AND r.timestamp <= ?"
            params.append(date_to)
        query += " ORDER BY r.timestamp DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        _close_conn(conn)


def consultar_tendencia(test_type, days=30):
    conn = _get_conn()
    try:
        cutoff = time.time() - (days * 86400)
        rows = conn.execute(
            "SELECT r.scheduler_name, r.timestamp, r.valor, r.p95 "
            "FROM results r WHERE r.test_type = ? AND r.timestamp >= ? "
            "ORDER BY r.timestamp ASC",
            (test_type, cutoff)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        _close_conn(conn)


def obtener_schedulers_historial():
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT scheduler_name FROM results ORDER BY scheduler_name"
        ).fetchall()
        return [r["scheduler_name"] for r in rows]
    finally:
        _close_conn(conn)


def contar_resultados():
    conn = _get_conn()
    try:
        row = conn.execute("SELECT COUNT(*) as total FROM results").fetchone()
        return row["total"]
    finally:
        _close_conn(conn)


def detectar_cambio_version(versiones):
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        _close_conn(conn)
    if not row:
        return False, None
    cambios = []
    if row["kernel_version"] != versiones.get("kernel"):
        cambios.append("kernel")
    if row["stressng_version"] != versiones.get("stressng"):
        cambios.append("stress-ng")
    if row["hyperfine_version"] != versiones.get("hyperfine"):
        cambios.append("hyperfine")
    return len(cambios) > 0, cambios


def consultar_runs_auto():
    """Devuelve la lista de ejecuciones automáticas (run_type='auto'), ordenadas por timestamp ASC."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, timestamp, kernel_version, scxctl_version, stressng_version, "
            "hyperfine_version, run_type FROM runs WHERE run_type = 'auto' "
            "ORDER BY timestamp ASC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        _close_conn(conn)


def cargar_resultados_de_run(run_id):
    """Devuelve lista de dicts con todos los resultados de un run dado."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT scheduler_name, test_type, valor, p95, fairness, modo, timestamp "
            "FROM results WHERE run_id = ? ORDER BY scheduler_name, test_type",
            (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        _close_conn(conn)


def eliminar_historial():
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM results")
        conn.execute("DELETE FROM runs")
        conn.commit()
    finally:
        _close_conn(conn)


def guardar_compatibilidad(nombre, kernel, compatible, mensaje):
    conn = _get_conn()
    try:
        conn.execute(
            "DELETE FROM compatibility WHERE scheduler_name=? AND kernel_version=?",
            (nombre, kernel)
        )
        conn.execute(
            "INSERT INTO compatibility (scheduler_name, kernel_version, is_compatible, message, timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            (nombre, kernel, 1 if compatible else 0, mensaje, time.time())
        )
        conn.commit()
    finally:
        _close_conn(conn)


def cargar_compatibilidad(kernel):
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT scheduler_name, is_compatible, message, timestamp "
            "FROM compatibility WHERE kernel_version = ?",
            (kernel,)
        ).fetchall()
        return {r["scheduler_name"]: (bool(r["is_compatible"]), r["message"], r["timestamp"]) for r in rows}
    finally:
        _close_conn(conn)


def limpiar_compatibilidad():
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM compatibility")
        conn.commit()
    finally:
        _close_conn(conn)


def obtener_historial_compatibilidad():
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT scheduler_name, kernel_version, is_compatible, message, timestamp "
            "FROM compatibility ORDER BY timestamp DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        _close_conn(conn)


_INFO_SCHED_SEED = [
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


def sembrar_info_schedulers():
    """Inserta o actualiza descripciones conocidas de schedulers."""
    conn = _get_conn()
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO scheduler_info (scheduler_name, description, url) "
            "VALUES (?, ?, ?)",
            _INFO_SCHED_SEED,
        )
        conn.commit()
    finally:
        _close_conn(conn)


def obtener_info_scheduler(name):
    """Retorna (description, url) para un scheduler, o (None, None) si no existe."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT description, url FROM scheduler_info WHERE scheduler_name = ?",
            (name,),
        ).fetchone()
        if row:
            return row["description"], row["url"]
        return None, None
    finally:
        _close_conn(conn)
