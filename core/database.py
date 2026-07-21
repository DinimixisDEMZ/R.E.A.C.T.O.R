"""
Persistencia de historial de benchmarks en SQLite.
Almacena resultados individuales con metadata de entorno (versiones, kernel).
"""

import json
import math
import platform
import sqlite3
import subprocess
import threading
import time
from collections.abc import Collection, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

_DB_DIR = Path.home() / ".local" / "share" / "scxctl"
_DB_PATH = _DB_DIR / "history.db"
_db_temp = None
_DB_LOCK = threading.RLock()

_RUN_STATUSES = frozenset({"running", "completed", "partial", "failed"})
_SCHEMA_VERSION = 2
_VERSION_COMMAND_TIMEOUT = 1.5

_CREATE_COMPATIBILITY_TABLE = """
    CREATE TABLE IF NOT EXISTS compatibility (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scheduler_name TEXT NOT NULL,
        kernel_version TEXT NOT NULL,
        is_compatible INTEGER NOT NULL,
        message TEXT,
        timestamp REAL NOT NULL,
        environment_key TEXT
    )
"""

_CREATE_TABLE_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp REAL NOT NULL,
        kernel_version TEXT NOT NULL,
        scxctl_version TEXT,
        stressng_version TEXT,
        hyperfine_version TEXT,
        run_type TEXT NOT NULL DEFAULT 'manual',
        status TEXT NOT NULL DEFAULT 'completed'
            CHECK (status IN ('running', 'completed', 'partial', 'failed')),
        closed_at REAL,
        metadata_json TEXT
    )
    """,
    """
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
        response REAL,
        response_kind TEXT,
        payload_json TEXT,
        raw_metrics_json TEXT,
        FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
    )
    """,
    _CREATE_COMPATIBILITY_TABLE,
    """
    CREATE TABLE IF NOT EXISTS results_duplicate_archive (
        archive_id INTEGER PRIMARY KEY AUTOINCREMENT,
        original_result_id INTEGER NOT NULL UNIQUE,
        run_id INTEGER,
        scheduler_name TEXT,
        test_type TEXT,
        row_json TEXT NOT NULL,
        archived_at REAL NOT NULL,
        reason TEXT NOT NULL
    )
    """,
)

_INDEX_STATEMENTS = (
    "CREATE INDEX IF NOT EXISTS idx_results_scheduler ON results(scheduler_name)",
    "CREATE INDEX IF NOT EXISTS idx_results_test_type ON results(test_type)",
    "CREATE INDEX IF NOT EXISTS idx_results_run_id ON results(run_id)",
    "CREATE INDEX IF NOT EXISTS idx_runs_timestamp ON runs(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_compat_kernel ON compatibility(kernel_version)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_results_run_scheduler_test "
    "ON results(run_id, scheduler_name, test_type)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_compat_scheduler_kernel_legacy "
    "ON compatibility(scheduler_name, kernel_version) "
    "WHERE environment_key IS NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_compat_scheduler_kernel_environment "
    "ON compatibility(scheduler_name, kernel_version, environment_key) "
    "WHERE environment_key IS NOT NULL",
)

_STATUS_TRIGGER_STATEMENTS = (
    """
    CREATE TRIGGER IF NOT EXISTS trg_runs_status_insert
    BEFORE INSERT ON runs
    WHEN NEW.status IS NULL
         OR NEW.status NOT IN ('running', 'completed', 'partial', 'failed')
    BEGIN
        SELECT RAISE(ABORT, 'invalid run status');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_runs_status_update
    BEFORE UPDATE OF status ON runs
    WHEN NEW.status IS NULL
         OR NEW.status NOT IN ('running', 'completed', 'partial', 'failed')
    BEGIN
        SELECT RAISE(ABORT, 'invalid run status');
    END
    """,
)

# Conservado como script completo para consumidores que lo importasen, aunque la
# inicialización real usa inspección de columnas y migraciones incrementales.
_SCHEMA_SQL = ";\n\n".join(
    statement.strip()
    for statement in (
        *_CREATE_TABLE_STATEMENTS,
        *_INDEX_STATEMENTS,
        *_STATUS_TRIGGER_STATEMENTS,
    )
) + ";"

_RUN_COLUMN_MIGRATIONS = {
    "timestamp": "REAL NOT NULL DEFAULT 0",
    "kernel_version": "TEXT NOT NULL DEFAULT ''",
    "scxctl_version": "TEXT",
    "stressng_version": "TEXT",
    "hyperfine_version": "TEXT",
    "run_type": "TEXT NOT NULL DEFAULT 'manual'",
    "status": (
        "TEXT NOT NULL DEFAULT 'completed' "
        "CHECK (status IN ('running', 'completed', 'partial', 'failed'))"
    ),
    "closed_at": "REAL",
    "metadata_json": "TEXT",
}

_RESULT_COLUMN_MIGRATIONS = {
    "run_id": "INTEGER",
    "timestamp": "REAL NOT NULL DEFAULT 0",
    "scheduler_name": "TEXT NOT NULL DEFAULT ''",
    "test_type": "TEXT NOT NULL DEFAULT ''",
    "valor": "REAL NOT NULL DEFAULT 0",
    "p95": "REAL",
    "fairness": "REAL",
    "modo": "TEXT",
    "response": "REAL",
    "response_kind": "TEXT",
    "payload_json": "TEXT",
    "raw_metrics_json": "TEXT",
}

_COMPAT_COLUMN_MIGRATIONS = {
    "scheduler_name": "TEXT NOT NULL DEFAULT ''",
    "kernel_version": "TEXT NOT NULL DEFAULT ''",
    "is_compatible": "INTEGER NOT NULL DEFAULT 0",
    "message": "TEXT",
    "timestamp": "REAL NOT NULL DEFAULT 0",
    "environment_key": "TEXT",
}

_ARCHIVE_COLUMN_MIGRATIONS = {
    "original_result_id": "INTEGER",
    "run_id": "INTEGER",
    "scheduler_name": "TEXT",
    "test_type": "TEXT",
    "row_json": "TEXT",
    "archived_at": "REAL",
    "reason": "TEXT",
}

_RESULT_UPSERT_SQL = """
    INSERT INTO results (
        run_id, timestamp, scheduler_name, test_type, valor, p95, fairness,
        modo, response, response_kind, payload_json, raw_metrics_json
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(run_id, scheduler_name, test_type) DO UPDATE SET
        timestamp=excluded.timestamp,
        valor=excluded.valor,
        p95=excluded.p95,
        fairness=excluded.fairness,
        modo=excluded.modo,
        response=excluded.response,
        response_kind=excluded.response_kind,
        payload_json=excluded.payload_json,
        raw_metrics_json=excluded.raw_metrics_json
"""

_COMPATIBILITY_UPSERT_SQL = """
    INSERT INTO compatibility (
        scheduler_name, kernel_version, is_compatible, message, timestamp,
        environment_key
    ) VALUES (?, ?, ?, ?, ?, ?)
    ON CONFLICT DO UPDATE SET
        is_compatible=excluded.is_compatible,
        message=excluded.message,
        timestamp=excluded.timestamp
"""


def _cmd_output(cmd, timeout=_VERSION_COMMAND_TIMEOUT):
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.stdout.strip().split("\n")[0] if result.returncode == 0 else None
    except Exception:
        return None


def _configure_conn(conn):
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def _get_conn():
    global _db_temp
    if _db_temp is not None:
        return _db_temp

    _DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), timeout=5)
    try:
        _configure_conn(conn)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn
    except Exception:
        conn.close()
        raise


def _close_conn(conn):
    global _db_temp
    if conn is not _db_temp:
        conn.close()


@contextmanager
def _connection():
    with _DB_LOCK:
        conn = _get_conn()
        try:
            yield conn
        finally:
            _close_conn(conn)


@contextmanager
def _transaction():
    with _connection() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise


def _validar_json_seguro(value, field_name, path="$", seen=None):
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field_name} contiene un número no finito en {path}")
        return

    if seen is None:
        seen = set()

    if isinstance(value, list):
        object_id = id(value)
        if object_id in seen:
            raise ValueError(f"{field_name} contiene una referencia circular en {path}")
        seen.add(object_id)
        try:
            for index, item in enumerate(value):
                _validar_json_seguro(item, field_name, f"{path}[{index}]", seen)
        finally:
            seen.remove(object_id)
        return

    if isinstance(value, dict):
        object_id = id(value)
        if object_id in seen:
            raise ValueError(f"{field_name} contiene una referencia circular en {path}")
        seen.add(object_id)
        try:
            for key, item in value.items():
                if not isinstance(key, str):
                    raise ValueError(
                        f"{field_name} contiene una clave no textual en {path}"
                    )
                _validar_json_seguro(item, field_name, f"{path}.{key}", seen)
        finally:
            seen.remove(object_id)
        return

    raise ValueError(
        f"{field_name} contiene un tipo no serializable en {path}: "
        f"{type(value).__name__}"
    )


def _serializar_json(value, field_name):
    if value is None:
        return None
    _validar_json_seguro(value, field_name)
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"No se pudo serializar {field_name}: {exc}") from exc


def _deserializar_json(value, field_name):
    if value is None:
        return None

    def reject_constant(constant):
        raise ValueError(f"constante no válida: {constant}")

    try:
        decoded = json.loads(value, parse_constant=reject_constant)
        _validar_json_seguro(decoded, field_name)
    except (TypeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ValueError(f"JSON inválido almacenado en {field_name}: {exc}") from exc
    return decoded


def _column_names(conn, table):
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _ensure_columns(conn, table, migrations):
    columns = _column_names(conn, table)
    if not columns:
        raise RuntimeError(f"No se pudo inspeccionar la tabla SQLite {table}")
    for name, definition in migrations.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
            columns.add(name)
    return columns


def _ensure_primary_key(conn, table, column="id"):
    columns = {
        row["name"]: row for row in conn.execute(f"PRAGMA table_info({table})")
    }
    if column not in columns or not columns[column]["pk"]:
        raise RuntimeError(
            f"La tabla SQLite {table} no tiene la clave primaria requerida {column}"
        )


def _archive_duplicate_results(conn):
    rows = conn.execute(
        "SELECT id, run_id, timestamp, scheduler_name, test_type, valor, p95, "
        "fairness, modo, response, response_kind, "
        "CAST(payload_json AS BLOB) AS payload_json, "
        "CAST(raw_metrics_json AS BLOB) AS raw_metrics_json FROM results "
        "ORDER BY run_id, scheduler_name, test_type, timestamp DESC, id DESC"
    ).fetchall()
    seen = set()
    archived_at = time.time()

    for row in rows:
        row_data = dict(row)
        for column in ("payload_json", "raw_metrics_json"):
            stored = row_data[column]
            if isinstance(stored, bytes):
                try:
                    row_data[column] = stored.decode("utf-8")
                except UnicodeDecodeError as exc:
                    row_data[column] = {
                        "error": f"UTF-8 inválido: {exc}",
                        "blob_hex": stored.hex(),
                    }
        key = (
            row_data.get("run_id"),
            row_data.get("scheduler_name"),
            row_data.get("test_type"),
        )
        if key not in seen:
            seen.add(key)
            continue

        original_id = row_data["id"]
        row_json = _serializar_json(row_data, f"resultado duplicado {original_id}")
        conn.execute(
            "INSERT OR IGNORE INTO results_duplicate_archive "
            "(original_result_id, run_id, scheduler_name, test_type, row_json, archived_at, reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                original_id,
                row_data.get("run_id"),
                row_data.get("scheduler_name"),
                row_data.get("test_type"),
                row_json,
                archived_at,
                "duplicate_before_unique_constraint",
            ),
        )
        archived = conn.execute(
            "SELECT 1 FROM results_duplicate_archive WHERE original_result_id = ?",
            (original_id,),
        ).fetchone()
        if archived is None:
            raise RuntimeError(f"No se pudo preservar el resultado duplicado {original_id}")
        conn.execute("DELETE FROM results WHERE id = ?", (original_id,))


def _deduplicate_compatibility(conn):
    rows = conn.execute(
        "SELECT id, scheduler_name, kernel_version, environment_key "
        "FROM compatibility "
        "ORDER BY timestamp DESC, id DESC"
    ).fetchall()
    seen = set()
    duplicate_ids = []
    for row in rows:
        key = (
            row["scheduler_name"],
            row["kernel_version"],
            row["environment_key"],
        )
        if key in seen:
            duplicate_ids.append((row["id"],))
        else:
            seen.add(key)
    if duplicate_ids:
        conn.executemany("DELETE FROM compatibility WHERE id = ?", duplicate_ids)


def _migrar_datos_legacy(conn, run_columns_before):
    if "status" not in run_columns_before:
        conn.execute(
            "UPDATE runs SET status = 'partial' WHERE run_type = 'auto_parcial'"
        )
    conn.execute(
        "UPDATE runs SET status = CASE "
        "WHEN run_type = 'auto_parcial' THEN 'partial' ELSE 'completed' END "
        "WHERE status IS NULL OR status = ''"
    )
    conn.execute(
        "UPDATE runs SET run_type = 'auto', status = 'partial' "
        "WHERE run_type = 'auto_parcial'"
    )
    conn.execute(
        "UPDATE runs SET closed_at = timestamp "
        "WHERE closed_at IS NULL AND status IN ('completed', 'partial', 'failed')"
    )
    conn.execute(
        "UPDATE results SET response = NULL, response_kind = NULL, p95 = NULL "
        "WHERE test_type = 'threads' AND "
        "(response IS NOT NULL OR response_kind IS NOT NULL OR p95 IS NOT NULL)"
    )

    _archive_duplicate_results(conn)
    _deduplicate_compatibility(conn)


def _migrar_compatibilidad_por_entorno(conn):
    conn.execute("ALTER TABLE compatibility RENAME TO compatibility_legacy")
    conn.execute(_CREATE_COMPATIBILITY_TABLE)
    conn.execute(
        "INSERT INTO compatibility ("
        "id, scheduler_name, kernel_version, is_compatible, message, timestamp, "
        "environment_key"
        ") SELECT id, scheduler_name, kernel_version, is_compatible, message, "
        "timestamp, environment_key FROM compatibility_legacy"
    )
    conn.execute("DROP TABLE compatibility_legacy")


def _schema_version(conn):
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _set_schema_version(conn, version):
    conn.execute(f"PRAGMA user_version = {int(version)}")


def _migrar_schema(conn):
    version = _schema_version(conn)
    if version > _SCHEMA_VERSION:
        raise RuntimeError(
            f"La base de datos usa schema {version}, superior al soportado "
            f"({_SCHEMA_VERSION})"
        )

    for statement in _CREATE_TABLE_STATEMENTS:
        conn.execute(statement)

    _ensure_primary_key(conn, "runs")
    _ensure_primary_key(conn, "results")
    _ensure_primary_key(conn, "compatibility")
    _ensure_primary_key(conn, "results_duplicate_archive", "archive_id")

    run_columns_before = _column_names(conn, "runs")
    _ensure_columns(conn, "runs", _RUN_COLUMN_MIGRATIONS)
    _ensure_columns(conn, "results", _RESULT_COLUMN_MIGRATIONS)
    _ensure_columns(conn, "compatibility", _COMPAT_COLUMN_MIGRATIONS)
    _ensure_columns(conn, "results_duplicate_archive", _ARCHIVE_COLUMN_MIGRATIONS)

    if version < 1:
        _migrar_datos_legacy(conn, run_columns_before)
        _set_schema_version(conn, 1)
    if version < 2:
        _migrar_compatibilidad_por_entorno(conn)
        _set_schema_version(conn, 2)

    for statement in _INDEX_STATEMENTS:
        conn.execute(statement)
    for statement in _STATUS_TRIGGER_STATEMENTS:
        conn.execute(statement)


def _numero(value, field_name, required=False):
    if value is None:
        if required:
            raise ValueError(f"Falta el valor numérico requerido {field_name}")
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} debe ser un número finito")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{field_name} debe ser un número finito")
    return value


def _texto(value, field_name, required=False):
    if value is None:
        if required:
            raise ValueError(f"Falta el texto requerido {field_name}")
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} debe ser texto")
    if required and not value:
        raise ValueError(f"{field_name} no puede estar vacío")
    return value


def _validar_run_id(run_id):
    if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0:
        raise ValueError("run_id debe ser un entero positivo")
    return run_id


def _normalizar_status_filter(status):
    if status is None:
        return None
    if isinstance(status, str):
        statuses = (status,)
    elif isinstance(status, Collection) and not isinstance(
        status, (bytes, bytearray, Mapping)
    ):
        statuses = tuple(status)
    else:
        raise ValueError("status debe ser texto o una colección de estados")

    invalid = [
        value
        for value in statuses
        if not isinstance(value, str) or value not in _RUN_STATUSES
    ]
    if invalid:
        valid = ", ".join(sorted(_RUN_STATUSES))
        raise ValueError(f"status debe contener solo estos estados: {valid}")
    return tuple(dict.fromkeys(statuses))


def _construir_filtros_historial(
    scheduler, test_type, date_from, date_to, kernel_version, status
):
    clauses = []
    params = []
    if scheduler:
        clauses.append("r.scheduler_name = ?")
        params.append(scheduler)
    if test_type:
        clauses.append("r.test_type = ?")
        params.append(test_type)
    if date_from is not None:
        clauses.append("r.timestamp >= ?")
        params.append(date_from)
    if date_to is not None:
        clauses.append("r.timestamp <= ?")
        params.append(date_to)
    if kernel_version is not None:
        clauses.append("ru.kernel_version = ?")
        params.append(_texto(kernel_version, "kernel_version"))

    statuses = _normalizar_status_filter(status)
    if statuses == ():
        clauses.append("0 = 1")
    elif statuses:
        placeholders = ", ".join("?" for _ in statuses)
        clauses.append(f"ru.status IN ({placeholders})")
        params.extend(statuses)

    if not clauses:
        return "", params
    return " AND " + " AND ".join(clauses), params


def _preparar_fila_compatibilidad(kernel, result, timestamp, environment_key):
    if isinstance(result, Mapping):
        nombre = next(
            (
                result[key]
                for key in ("nombre", "scheduler_name", "sched")
                if key in result
            ),
            None,
        )
        compatible_marker = object()
        compatible = next(
            (
                result[key]
                for key in ("compatible", "is_compatible")
                if key in result
            ),
            compatible_marker,
        )
        mensaje = next(
            (result[key] for key in ("mensaje", "message") if key in result),
            None,
        )
        if compatible is compatible_marker:
            raise ValueError("Falta el booleano requerido compatible")
    elif isinstance(result, Sequence) and not isinstance(
        result, (str, bytes, bytearray)
    ):
        if len(result) != 3:
            raise ValueError(
                "Cada compatibilidad debe contener nombre, compatible y mensaje"
            )
        nombre, compatible, mensaje = result
    else:
        raise ValueError("Cada compatibilidad debe ser un mapping o una secuencia")

    nombre = _texto(nombre, "compatibilidad.nombre", required=True)
    if not nombre.strip():
        raise ValueError("compatibilidad.nombre no puede estar vacío")
    if not isinstance(compatible, bool):
        raise ValueError("compatibilidad.compatible debe ser booleano")

    return (
        nombre,
        kernel,
        1 if compatible else 0,
        _texto(mensaje, "compatibilidad.mensaje"),
        timestamp,
        environment_key,
    )


def _preparar_snapshot_compatibilidad(
    kernel, resultados, environment_key=None
):
    kernel = _texto(kernel, "kernel", required=True)
    if not kernel.strip():
        raise ValueError("kernel no puede estar vacío")
    environment_key = _texto(environment_key, "environment_key")
    if isinstance(resultados, (str, bytes, bytearray, Mapping)):
        raise ValueError("resultados debe ser una colección de compatibilidades")
    try:
        resultados = list(resultados)
    except TypeError as exc:
        raise ValueError(
            "resultados debe ser una colección de compatibilidades"
        ) from exc

    timestamp = time.time()
    prepared = []
    nombres = set()
    for index, result in enumerate(resultados):
        try:
            values = _preparar_fila_compatibilidad(
                kernel, result, timestamp, environment_key
            )
        except ValueError as exc:
            raise ValueError(f"resultados[{index}]: {exc}") from exc
        nombre = values[0]
        if nombre in nombres:
            raise ValueError(f"Scheduler duplicado en resultados: {nombre}")
        nombres.add(nombre)
        prepared.append(values)
    return kernel, environment_key, prepared


def _preparar_run(versiones, run_type, status, closed_at, metadata, timestamp):
    if not isinstance(versiones, Mapping):
        raise ValueError("versiones debe ser un mapping")
    if status not in _RUN_STATUSES:
        valid = ", ".join(sorted(_RUN_STATUSES))
        raise ValueError(f"status debe ser uno de: {valid}")

    now = time.time()
    timestamp_value = _numero(
        now if timestamp is None else timestamp, "timestamp", required=True
    )
    if status == "running":
        if closed_at is not None:
            raise ValueError("Un run con status 'running' no puede tener closed_at")
        closed_at_value = None
    else:
        closed_at_value = _numero(
            now if closed_at is None else closed_at, "closed_at", required=True
        )

    return (
        timestamp_value,
        _texto(versiones.get("kernel", ""), "versiones.kernel") or "",
        _texto(versiones.get("scxctl"), "versiones.scxctl"),
        _texto(versiones.get("stressng"), "versiones.stressng"),
        _texto(versiones.get("hyperfine"), "versiones.hyperfine"),
        _texto(run_type, "run_type", required=True),
        status,
        closed_at_value,
        _serializar_json(metadata, "metadata"),
    )


def _result_field(result, old_name, new_name):
    if old_name in result:
        return result[old_name]
    if new_name in result:
        return result[new_name]
    return None


def _preparar_resultado(run_id, result):
    run_id = _validar_run_id(run_id)
    if not isinstance(result, Mapping):
        raise ValueError("Cada resultado debe ser un mapping")

    scheduler = _result_field(result, "sched", "scheduler_name")
    test_type = _result_field(result, "tipo", "test_type")
    if "valor" not in result:
        raise ValueError("Falta el valor requerido valor")

    if "payload" in result:
        payload = result["payload"]
    elif "raw_payload" in result:
        payload = result["raw_payload"]
    else:
        payload = dict(result)

    if "raw_metrics" in result:
        raw_metrics = result["raw_metrics"]
    elif "metrics" in result:
        raw_metrics = result["metrics"]
    elif "raw" in result:
        raw_metrics = result["raw"]
    else:
        raw_metrics = None

    scheduler = _texto(scheduler, "result.sched", required=True)
    test_type = _texto(test_type, "result.tipo", required=True)
    p95 = _numero(result.get("p95"), "result.p95")
    response = _numero(result.get("response"), "result.response")
    response_kind = _texto(result.get("response_kind"), "result.response_kind")
    if test_type == "threads":
        p95 = None
        response = None
        response_kind = None

    return (
        run_id,
        _numero(result.get("timestamp", time.time()), "result.timestamp", required=True),
        scheduler,
        test_type,
        _numero(result["valor"], "result.valor", required=True),
        p95,
        _numero(result.get("fairness"), "result.fairness"),
        _texto(result.get("modo"), "result.modo"),
        response,
        response_kind,
        _serializar_json(payload, "result.payload"),
        _serializar_json(raw_metrics, "result.raw_metrics"),
    )


def _insert_run(conn, values):
    cursor = conn.execute(
        "INSERT INTO runs ("
        "timestamp, kernel_version, scxctl_version, stressng_version, "
        "hyperfine_version, run_type, status, closed_at, metadata_json"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        values,
    )
    return cursor.lastrowid


def _upsert_result(conn, values):
    conn.execute(_RESULT_UPSERT_SQL, values)


def _cargar_json_aislado(data, column, target, error_target, field_name):
    data[error_target] = None
    stored = data[column]
    try:
        data[target] = _deserializar_json(stored, field_name)
        if isinstance(stored, bytes):
            data[column] = stored.decode("utf-8")
    except ValueError as exc:
        data[target] = None
        data[error_target] = str(exc)


def _run_row(row):
    data = dict(row)
    if "metadata_json" in data:
        _cargar_json_aislado(
            data,
            "metadata_json",
            "metadata",
            "metadata_error",
            f"runs[{data.get('id')}].metadata_json",
        )
    return data


def _result_row(row):
    data = dict(row)
    if "payload_json" in data:
        _cargar_json_aislado(
            data,
            "payload_json",
            "payload",
            "payload_error",
            f"results[{data.get('id')}].payload_json",
        )
    if "raw_metrics_json" in data:
        _cargar_json_aislado(
            data,
            "raw_metrics_json",
            "raw_metrics",
            "raw_metrics_error",
            f"results[{data.get('id')}].raw_metrics_json",
        )
    if "metadata_json" in data:
        _cargar_json_aislado(
            data,
            "metadata_json",
            "metadata",
            "metadata_error",
            f"runs[{data.get('run_id')}].metadata_json",
        )
    return data


def activar_db_temporal():
    global _db_temp
    with _DB_LOCK:
        if _db_temp is not None:
            return
        conn = sqlite3.connect(":memory:", check_same_thread=False, timeout=5)
        try:
            _configure_conn(conn)
            conn.execute("BEGIN IMMEDIATE")
            _migrar_schema(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            conn.close()
            raise
        _db_temp = conn


def desactivar_db_temporal():
    global _db_temp
    with _DB_LOCK:
        if _db_temp is not None:
            _db_temp.close()
            _db_temp = None


def obtener_versiones():
    versions = {"kernel": platform.release()}
    commands = {
        "scxctl": ["scxctl", "--version"],
        "stressng": ["stress-ng", "--version"],
        "hyperfine": ["hyperfine", "--version"],
    }
    with ThreadPoolExecutor(
        max_workers=len(commands), thread_name_prefix="reactor-versions"
    ) as executor:
        futures = {
            name: executor.submit(
                _cmd_output, command, _VERSION_COMMAND_TIMEOUT
            )
            for name, command in commands.items()
        }
        for name, future in futures.items():
            try:
                versions[name] = future.result()
            except Exception:
                versions[name] = None
    return versions


def inicializar_db():
    with _transaction() as conn:
        _migrar_schema(conn)


def guardar_run(
    versiones,
    run_type="manual",
    status="completed",
    closed_at=None,
    metadata=None,
    timestamp=None,
):
    with _transaction() as conn:
        values = _preparar_run(
            versiones, run_type, status, closed_at, metadata, timestamp
        )
        return _insert_run(conn, values)


def guardar_resultado(run_id, result):
    with _transaction() as conn:
        values = _preparar_resultado(run_id, result)
        _upsert_result(conn, values)


def guardar_resultados_batch(run_id, results):
    with _transaction() as conn:
        run_id = _validar_run_id(run_id)
        prepared = [_preparar_resultado(run_id, result) for result in results]
        if not prepared:
            return
        for values in prepared:
            _upsert_result(conn, values)


def guardar_run_completo(
    versiones,
    results,
    run_type="manual",
    status="completed",
    closed_at=None,
    metadata=None,
    timestamp=None,
):
    """Guarda un run y todos sus resultados en una única transacción."""
    with _transaction() as conn:
        results = list(results)
        if not results:
            raise ValueError("Un run completo debe contener al menos un resultado")
        run_values = _preparar_run(
            versiones, run_type, status, closed_at, metadata, timestamp
        )
        run_id = _insert_run(conn, run_values)
        for result in results:
            _upsert_result(conn, _preparar_resultado(run_id, result))
        return run_id


def consultar_historial(
    scheduler=None,
    test_type=None,
    date_from=None,
    date_to=None,
    limit=200,
    include_payload=False,
    kernel_version=None,
    status=None,
):
    filters, params = _construir_filtros_historial(
        scheduler, test_type, date_from, date_to, kernel_version, status
    )
    with _connection() as conn:
        result_columns = (
            "r.id, r.run_id, r.timestamp, r.scheduler_name, r.test_type, "
            "r.valor, r.p95, r.fairness, r.modo, r.response, r.response_kind"
        )
        if include_payload:
            result_columns += (
                ", CAST(r.payload_json AS BLOB) AS payload_json, "
                "CAST(r.raw_metrics_json AS BLOB) AS raw_metrics_json"
            )
        query = (
            f"SELECT {result_columns}, ru.timestamp as run_ts, ru.kernel_version, "
            "ru.scxctl_version, ru.stressng_version, ru.hyperfine_version, "
            "ru.run_type, ru.status, ru.closed_at, "
            "CAST(ru.metadata_json AS BLOB) AS metadata_json "
            "FROM results r JOIN runs ru ON r.run_id = ru.id WHERE 1=1"
        )
        query += filters
        query += " ORDER BY r.timestamp DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [_result_row(row) for row in rows]


def consultar_tendencia(
    test_type, days=30, scheduler=None, include_payload=False
):
    with _connection() as conn:
        result_columns = (
            "SELECT id, run_id, scheduler_name, timestamp, valor, p95, "
            "response, response_kind"
        )
        if include_payload:
            result_columns += (
                ", CAST(payload_json AS BLOB) AS payload_json, "
                "CAST(raw_metrics_json AS BLOB) AS raw_metrics_json"
            )
        query = f"{result_columns} FROM results WHERE test_type = ?"
        params = [test_type]
        if days is not None and days > 0:
            query += " AND timestamp >= ?"
            params.append(time.time() - (days * 86400))
        if scheduler is not None:
            query += " AND scheduler_name = ?"
            params.append(scheduler)
        query += " ORDER BY timestamp ASC"
        rows = conn.execute(query, params).fetchall()
        return [_result_row(row) for row in rows]


def obtener_schedulers_historial():
    with _connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT scheduler_name FROM results ORDER BY scheduler_name"
        ).fetchall()
        return [row["scheduler_name"] for row in rows]


def contar_resultados():
    with _connection() as conn:
        row = conn.execute("SELECT COUNT(*) as total FROM results").fetchone()
        return row["total"]


def contar_historial(
    scheduler=None,
    test_type=None,
    date_from=None,
    date_to=None,
    kernel_version=None,
    status=None,
):
    """Cuenta resultados con los mismos filtros del historial, sin cargar blobs."""
    filters, params = _construir_filtros_historial(
        scheduler, test_type, date_from, date_to, kernel_version, status
    )
    with _connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS total FROM results r "
            "JOIN runs ru ON r.run_id = ru.id WHERE 1=1" + filters,
            params,
        ).fetchone()
        return row["total"]


def detectar_cambio_version(versiones):
    with _connection() as conn:
        row = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        previous = dict(row) if row else None
    if not previous:
        return False, None

    cambios = []
    if previous["kernel_version"] != versiones.get("kernel"):
        cambios.append("kernel")
    if previous["scxctl_version"] != versiones.get("scxctl"):
        cambios.append("scxctl")
    if previous["stressng_version"] != versiones.get("stressng"):
        cambios.append("stress-ng")
    if previous["hyperfine_version"] != versiones.get("hyperfine"):
        cambios.append("hyperfine")
    return len(cambios) > 0, cambios


def consultar_runs_auto():
    """Devuelve ejecuciones automáticas ordenadas por timestamp ascendente."""
    with _connection() as conn:
        rows = conn.execute(
            "SELECT id, timestamp, kernel_version, scxctl_version, "
            "stressng_version, hyperfine_version, run_type, status, closed_at, "
            "CAST(metadata_json AS BLOB) AS metadata_json "
            "FROM runs WHERE run_type = 'auto' "
            "ORDER BY timestamp ASC"
        ).fetchall()
        return [_run_row(row) for row in rows]


def cargar_resultados_de_run(run_id, include_payload=False):
    """Devuelve los resultados del run y carga sus blobs solo bajo demanda."""
    run_id = _validar_run_id(run_id)
    with _connection() as conn:
        result_columns = (
            "SELECT id, run_id, scheduler_name, test_type, valor, p95, fairness, "
            "modo, timestamp, response, response_kind"
        )
        if include_payload:
            result_columns += (
                ", CAST(payload_json AS BLOB) AS payload_json, "
                "CAST(raw_metrics_json AS BLOB) AS raw_metrics_json"
            )
        rows = conn.execute(
            f"{result_columns} FROM results WHERE run_id = ? "
            "ORDER BY scheduler_name, test_type",
            (run_id,),
        ).fetchall()
        return [_result_row(row) for row in rows]


def eliminar_historial():
    with _transaction() as conn:
        conn.execute("DELETE FROM results")
        conn.execute("DELETE FROM runs")
        conn.execute("DELETE FROM results_duplicate_archive")


def guardar_compatibilidad(
    nombre, kernel, compatible, mensaje, environment_key=None
):
    environment_key = _texto(environment_key, "environment_key")
    with _transaction() as conn:
        conn.execute(
            _COMPATIBILITY_UPSERT_SQL,
            (
                nombre,
                kernel,
                1 if compatible else 0,
                mensaje,
                time.time(),
                environment_key,
            ),
        )


def reemplazar_compatibilidad(kernel, resultados, environment_key=None):
    """Reemplaza atómicamente el snapshot completo de compatibilidad del kernel."""
    with _DB_LOCK:
        kernel, environment_key, prepared = _preparar_snapshot_compatibilidad(
            kernel, resultados, environment_key
        )
        with _transaction() as conn:
            if environment_key is None:
                conn.execute(
                    "DELETE FROM compatibility "
                    "WHERE kernel_version = ? AND environment_key IS NULL",
                    (kernel,),
                )
            else:
                conn.execute(
                    "DELETE FROM compatibility "
                    "WHERE kernel_version = ? AND environment_key = ?",
                    (kernel, environment_key),
                )
            conn.executemany(_COMPATIBILITY_UPSERT_SQL, prepared)


def cargar_compatibilidad(kernel, environment_key=None):
    environment_key = _texto(environment_key, "environment_key")
    with _connection() as conn:
        if environment_key is None:
            rows = conn.execute(
                "SELECT scheduler_name, is_compatible, message, timestamp "
                "FROM compatibility "
                "WHERE kernel_version = ? AND environment_key IS NULL",
                (kernel,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT scheduler_name, is_compatible, message, timestamp "
                "FROM compatibility "
                "WHERE kernel_version = ? AND environment_key = ?",
                (kernel, environment_key),
            ).fetchall()
        return {
            row["scheduler_name"]: (
                bool(row["is_compatible"]),
                row["message"],
                row["timestamp"],
            )
            for row in rows
        }


def limpiar_compatibilidad():
    with _transaction() as conn:
        conn.execute("DELETE FROM compatibility")


def obtener_historial_compatibilidad():
    with _connection() as conn:
        rows = conn.execute(
            "SELECT scheduler_name, kernel_version, is_compatible, message, "
            "timestamp, environment_key FROM compatibility "
            "ORDER BY timestamp DESC"
        ).fetchall()
        return [dict(row) for row in rows]
