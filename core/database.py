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

CREATE INDEX IF NOT EXISTS idx_results_scheduler ON results(scheduler_name);
CREATE INDEX IF NOT EXISTS idx_results_test_type ON results(test_type);
CREATE INDEX IF NOT EXISTS idx_results_run_id ON results(run_id);
CREATE INDEX IF NOT EXISTS idx_runs_timestamp ON runs(timestamp);
"""


def _cmd_output(cmd, timeout=3):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip().split('\n')[0] if r.returncode == 0 else None
    except Exception:
        return None


def _get_conn():
    _DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


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
    finally:
        conn.close()


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
        conn.close()


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
        conn.close()


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
        conn.close()


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
        conn.close()


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
        conn.close()


def obtener_schedulers_historial():
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT scheduler_name FROM results ORDER BY scheduler_name"
        ).fetchall()
        return [r["scheduler_name"] for r in rows]
    finally:
        conn.close()


def contar_resultados():
    conn = _get_conn()
    try:
        row = conn.execute("SELECT COUNT(*) as total FROM results").fetchone()
        return row["total"]
    finally:
        conn.close()


def detectar_cambio_version(versiones):
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        conn.close()
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


def eliminar_historial():
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM results")
        conn.execute("DELETE FROM runs")
        conn.commit()
    finally:
        conn.close()
