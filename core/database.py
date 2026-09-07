"""
Persistencia de historial de benchmarks en SQLite.
Almacena resultados individuales con metadata de entorno (versiones, kernel).
"""

import json
import os
import platform
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

def _directorio_datos():
    """Directorio de datos de la aplicación, según la especificación XDG.

    La aplicación se llama reactor pero guardaba su historial en
    `~/.local/share/scxctl/`, el nombre de una herramienta ajena que ni siquiera
    es una dependencia obligatoria. Se respeta `XDG_DATA_HOME` cuando está
    definido, en vez de asumir `~/.local/share`.
    """
    base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / "reactor"


_DB_DIR = _directorio_datos()
_DB_PATH = _DB_DIR / "history.db"
_DB_DIR_ANTIGUO = Path(
    os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / "scxctl"
_db_temp = None


def _migrar_ruta_antigua():
    """Mueve el historial de `scxctl/` a `reactor/` la primera vez.

    Se copia y no se borra el original: si algo saliera mal, el usuario no
    pierde meses de mediciones. Sólo actúa si el destino aún no existe.
    """
    if _DB_PATH.exists() or not (_DB_DIR_ANTIGUO / "history.db").exists():
        return
    try:
        _DB_DIR.mkdir(parents=True, exist_ok=True)
        for nombre in ("history.db", "history.db-wal", "history.db-shm"):
            origen = _DB_DIR_ANTIGUO / nombre
            if origen.exists():
                shutil.copy2(origen, _DB_DIR / nombre)
    except OSError:
        # Si la migración falla se sigue adelante con una base nueva: es
        # preferible a impedir que la aplicación arranque.
        pass

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    kernel_version TEXT NOT NULL,
    scxctl_version TEXT,
    stressng_version TEXT,
    hyperfine_version TEXT,
    run_type TEXT NOT NULL DEFAULT 'manual',
    log TEXT DEFAULT '',
    -- Retrato del equipo en formato JSON (CPU, núcleos, RAM, gobernador,
    -- batería, virtualización). Sin esto es imposible saber si dos corridas
    -- del historial son siquiera comparables, y la aplicación las mezclaba.
    entorno TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    timestamp REAL NOT NULL,
    scheduler_name TEXT NOT NULL,
    test_type TEXT NOT NULL,
    valor REAL NOT NULL,
    p95 REAL,
    waste REAL,
    modo TEXT,
    -- 0 cuando la herramienta no llegó a medir la latencia. Antes se guardaba
    -- el valor centinela 1.0 y esa medición inventada terminaba usándose como
    -- referencia para puntuar a las demás.
    p95_valido INTEGER DEFAULT 1,
    -- 1 si el hardware limitó por temperatura mientras se medía.
    throttling INTEGER DEFAULT 0,
    -- Parámetros de carga efectivos, en JSON.
    dimensionado TEXT DEFAULT '',
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
    except (subprocess.SubprocessError, OSError, IndexError):
        return None


def _get_conn():
    global _db_temp
    if _db_temp is not None:
        return _db_temp
    _DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
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
    _migrar_ruta_antigua()
    conn = _get_conn()
    try:
        conn.executescript(_SCHEMA_SQL)
        # Migración: renombrar columna fairness → waste en DBs existentes
        try:
            conn.execute("ALTER TABLE results RENAME COLUMN fairness TO waste")
            conn.commit()
        except sqlite3.OperationalError:
            pass
        # Migraciones aditivas. Cada una se intenta por separado y se ignora
        # si la columna ya existe: así una base vieja se pone al día sin
        # perder una sola fila.
        for tabla, columna, definicion in (
            ("runs", "log", "TEXT DEFAULT ''"),
            ("runs", "entorno", "TEXT DEFAULT ''"),
            ("results", "p95_valido", "INTEGER DEFAULT 1"),
            ("results", "throttling", "INTEGER DEFAULT 0"),
            ("results", "dimensionado", "TEXT DEFAULT ''"),
        ):
            try:
                conn.execute(f"ALTER TABLE {tabla} ADD COLUMN {columna} {definicion}")
                conn.commit()
            except sqlite3.OperationalError:
                pass
        sembrar_info_schedulers()
    finally:
        _close_conn(conn)


def guardar_run(versiones, run_type="manual", log=None):
    conn = _get_conn()
    try:
        try:
            from core.hardware import obtener_perfil
            perfil = obtener_perfil()
            perfil.refrescar_volatiles()
            entorno = json.dumps(perfil.como_diccionario(), ensure_ascii=False)
        except Exception:
            entorno = ""
        cur = conn.execute(
            "INSERT INTO runs (timestamp, kernel_version, scxctl_version, stressng_version, hyperfine_version, run_type, log, entorno) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (time.time(), versiones.get("kernel", ""), versiones.get("scxctl"),
             versiones.get("stressng"), versiones.get("hyperfine"), run_type, log or "", entorno)
        )
        run_id = cur.lastrowid
        conn.commit()
        return run_id
    finally:
        _close_conn(conn)


def _fila_resultado(run_id, result):
    """Convierte un resultado de benchmark en la fila que espera `results`."""
    dimensionado = result.get("dimensionado")
    return (
        run_id,
        result.get("timestamp", time.time()),
        result.get("sched", result.get("scheduler_name", "")),
        result.get("tipo", result.get("test_type", "")),
        result.get("valor", 0),
        result.get("p95"),
        result.get("waste"),
        result.get("modo"),
        1 if result.get("p95_valido", True) else 0,
        1 if result.get("throttling") else 0,
        json.dumps(dimensionado, ensure_ascii=False) if dimensionado else "",
    )


def guardar_resultado(run_id, result):
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO results (run_id, timestamp, scheduler_name, test_type, valor, p95, waste, modo, "
            "p95_valido, throttling, dimensionado) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            _fila_resultado(run_id, result)
        )
        conn.commit()
    finally:
        _close_conn(conn)


def guardar_resultados_batch(run_id, results):
    conn = _get_conn()
    try:
        conn.executemany(
            "INSERT INTO results (run_id, timestamp, scheduler_name, test_type, valor, p95, waste, modo, "
            "p95_valido, throttling, dimensionado) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [_fila_resultado(run_id, r) for r in results]
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
            "hyperfine_version, run_type, log FROM runs WHERE run_type IN ('auto', 'auto_parcial') "
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
            "SELECT scheduler_name, test_type, valor, p95, waste, modo, timestamp "
            "FROM results WHERE run_id = ? ORDER BY scheduler_name, test_type",
            (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        _close_conn(conn)


def actualizar_log_run(run_id, log_text):
    conn = _get_conn()
    try:
        conn.execute("UPDATE runs SET log = ? WHERE id = ?", (log_text, run_id))
        conn.commit()
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
    """Guarda el resultado de compatibilidad de un planificador.

    El nombre se normaliza a su forma canónica (`scx_lavd`). Sin esto, las
    entradas antiguas guardadas en forma corta (`lavd`) convivirían con las
    nuevas y el mismo planificador aparecería dos veces.
    """
    from core.sched_ext import canonizar, nombre_corto
    nombre = canonizar(nombre) or nombre
    conn = _get_conn()
    try:
        # Se borran ambas formas para no dejar el duplicado antiguo detrás.
        conn.execute(
            "DELETE FROM compatibility WHERE scheduler_name IN (?, ?) AND kernel_version=?",
            (nombre, nombre_corto(nombre), kernel)
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
        # Las filas guardadas antes de la normalización usan la forma corta:
        # se canonizan al leer para que una caché previa siga siendo válida.
        from core.sched_ext import canonizar
        return {
            (canonizar(r["scheduler_name"]) or r["scheduler_name"]):
                (bool(r["is_compatible"]), r["message"], r["timestamp"])
            for r in rows
        }
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


from core.tipos import INFO_SCHEDULERS as _INFO_SCHED_SEED


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
