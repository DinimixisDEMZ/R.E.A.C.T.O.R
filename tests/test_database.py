import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from core import database


VERSIONS = {
    "kernel": "7.1-test",
    "scxctl": "scxctl 1.2.3",
    "stressng": "stress-ng 0.18",
    "hyperfine": "hyperfine 1.19",
}


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    database.desactivar_db_temporal()
    db_dir = tmp_path / "data"
    db_dir.mkdir()
    db_path = db_dir / "history.db"
    monkeypatch.setattr(database, "_DB_DIR", db_dir)
    monkeypatch.setattr(database, "_DB_PATH", db_path)
    try:
        yield db_path
    finally:
        database.desactivar_db_temporal()


def connect_rows(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def result(scheduler="scx_lavd", test_type="cpu", value=100.0, **extra):
    data = {
        "sched": scheduler,
        "tipo": test_type,
        "valor": value,
        "p95": 2.5,
        "fairness": 0.1,
        "modo": "auto",
    }
    data.update(extra)
    return data


def test_creates_current_schema_from_scratch_idempotently(isolated_db):
    database.inicializar_db()
    database.inicializar_db()

    with connect_rows(isolated_db) as conn:
        run_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(runs)")
        }
        result_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(results)")
        }
        compatibility_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(compatibility)")
        }
        indexes = conn.execute("PRAGMA index_list(results)").fetchall()
        schema_version = conn.execute("PRAGMA user_version").fetchone()[0]

    assert {"status", "closed_at", "metadata_json"} <= run_columns
    assert {
        "response",
        "response_kind",
        "payload_json",
        "raw_metrics_json",
    } <= result_columns
    assert "environment_key" in compatibility_columns
    assert schema_version == database._SCHEMA_VERSION
    assert any(
        row["name"] == "uq_results_run_scheduler_test" and row["unique"]
        for row in indexes
    )

    run_id = database.guardar_run(
        VERSIONS,
        status="running",
        metadata={"configuracion": {"duracion": 30}},
        timestamp=100.0,
    )
    with connect_rows(isolated_db) as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()

    assert row["status"] == "running"
    assert row["closed_at"] is None
    assert json.loads(row["metadata_json"])["configuracion"]["duracion"] == 30


def test_migrates_legacy_schema_and_preserves_displaced_duplicates(isolated_db):
    with connect_rows(isolated_db) as conn:
        conn.executescript(
            """
            CREATE TABLE runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                kernel_version TEXT NOT NULL,
                scxctl_version TEXT,
                stressng_version TEXT,
                hyperfine_version TEXT,
                run_type TEXT NOT NULL DEFAULT 'manual'
            );
            CREATE TABLE results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                timestamp REAL NOT NULL,
                scheduler_name TEXT NOT NULL,
                test_type TEXT NOT NULL,
                valor REAL NOT NULL,
                p95 REAL,
                fairness REAL,
                modo TEXT,
                payload_json TEXT,
                raw_metrics_json TEXT,
                FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
            );
            CREATE TABLE compatibility (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scheduler_name TEXT NOT NULL,
                kernel_version TEXT NOT NULL,
                is_compatible INTEGER NOT NULL,
                message TEXT,
                timestamp REAL NOT NULL
            );
            """
        )
        conn.execute(
            "INSERT INTO runs (timestamp, kernel_version, run_type) "
            "VALUES (100, 'legacy-kernel', 'auto')"
        )
        conn.execute(
            "INSERT INTO runs (timestamp, kernel_version, run_type) "
            "VALUES (110, 'legacy-kernel', 'auto_parcial')"
        )
        conn.execute(
            "INSERT INTO results "
            "(run_id, timestamp, scheduler_name, test_type, valor) "
            "VALUES (1, 101, 'scx_lavd', 'cpu', 10)"
        )
        conn.execute(
            "INSERT INTO results "
            "(run_id, timestamp, scheduler_name, test_type, valor) "
            "VALUES (1, 102, 'scx_lavd', 'cpu', 20)"
        )
        conn.execute(
            "UPDATE results SET payload_json = ? WHERE id = 1",
            (sqlite3.Binary(b"\x80"),),
        )
        conn.execute(
            "INSERT INTO compatibility "
            "(scheduler_name, kernel_version, is_compatible, message, timestamp) "
            "VALUES ('scx_lavd', 'legacy-kernel', 0, 'old', 100)"
        )
        conn.execute(
            "INSERT INTO compatibility "
            "(scheduler_name, kernel_version, is_compatible, message, timestamp) "
            "VALUES ('scx_lavd', 'legacy-kernel', 1, 'new', 200)"
        )
        conn.commit()

    database.inicializar_db()
    database.inicializar_db()
    auto_runs = database.consultar_runs_auto()

    with connect_rows(isolated_db) as conn:
        run = conn.execute("SELECT * FROM runs WHERE id = 1").fetchone()
        partial_run = conn.execute("SELECT * FROM runs WHERE id = 2").fetchone()
        current = conn.execute("SELECT * FROM results").fetchall()
        archived = conn.execute(
            "SELECT * FROM results_duplicate_archive"
        ).fetchall()
        compatibility = conn.execute(
            "SELECT * FROM compatibility WHERE kernel_version = 'legacy-kernel'"
        ).fetchall()
        schema_version = conn.execute("PRAGMA user_version").fetchone()[0]

        assert run["status"] == "completed"
        assert run["closed_at"] == 100.0
        assert partial_run["status"] == "partial"
        assert partial_run["closed_at"] == 110.0
        assert partial_run["run_type"] == "auto"
        assert len(current) == 1
        assert current[0]["valor"] == 20.0
        assert len(archived) == 1
        archived_row = json.loads(archived[0]["row_json"])
        assert archived_row["valor"] == 10.0
        assert archived_row["payload_json"]["blob_hex"] == "80"
        assert "UTF-8" in archived_row["payload_json"]["error"]
        assert len(compatibility) == 1
        assert compatibility[0]["message"] == "new"
        assert compatibility[0]["environment_key"] is None
        assert schema_version == database._SCHEMA_VERSION

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO results "
                "(run_id, timestamp, scheduler_name, test_type, valor) "
                "VALUES (1, 103, 'scx_lavd', 'cpu', 30)"
            )

    assert [run["id"] for run in auto_runs] == [1, 2]
    assert auto_runs[1]["run_type"] == "auto"
    assert auto_runs[1]["status"] == "partial"
    assert database.cargar_compatibilidad("legacy-kernel") == {
        "scx_lavd": (True, "new", 200.0)
    }
    assert database.cargar_compatibilidad(
        "legacy-kernel", environment_key="current-environment"
    ) == {}


def test_current_schema_skips_large_table_scans_on_second_initialization(
    isolated_db, monkeypatch
):
    database.inicializar_db()
    run_id = database.guardar_run(VERSIONS)
    with connect_rows(isolated_db) as conn:
        conn.executemany(
            "INSERT INTO results ("
            "run_id, timestamp, scheduler_name, test_type, valor"
            ") VALUES (?, ?, ?, 'cpu', ?)",
            (
                (run_id, float(index), f"scx_{index}", float(index))
                for index in range(5_000)
            ),
        )

    def unexpected_scan(*_args, **_kwargs):
        pytest.fail("La inicialización actual no debe repetir scans ni backfills")

    monkeypatch.setattr(database, "_migrar_datos_legacy", unexpected_scan)
    monkeypatch.setattr(database, "_archive_duplicate_results", unexpected_scan)
    monkeypatch.setattr(database, "_deduplicate_compatibility", unexpected_scan)

    database.inicializar_db()
    assert database.contar_resultados() == 5_000


def test_environment_migration_removes_legacy_kernel_only_uniqueness(isolated_db):
    database.inicializar_db()
    with connect_rows(isolated_db) as conn:
        conn.execute("DROP TABLE compatibility")
        conn.executescript(
            """
            CREATE TABLE compatibility (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scheduler_name TEXT NOT NULL,
                kernel_version TEXT NOT NULL,
                is_compatible INTEGER NOT NULL,
                message TEXT,
                timestamp REAL NOT NULL,
                UNIQUE(scheduler_name, kernel_version)
            );
            PRAGMA user_version = 1;
            """
        )
        conn.execute(
            "INSERT INTO compatibility "
            "(scheduler_name, kernel_version, is_compatible, message, timestamp) "
            "VALUES ('scx_lavd', 'kernel-a', 1, 'legacy', 100)"
        )

    database.inicializar_db()
    database.guardar_compatibilidad(
        "scx_lavd",
        "kernel-a",
        False,
        "environment a",
        environment_key="env-a",
    )
    database.guardar_compatibilidad(
        "scx_lavd",
        "kernel-a",
        True,
        "environment b",
        environment_key="env-b",
    )

    assert set(database.cargar_compatibilidad("kernel-a")) == {"scx_lavd"}
    assert set(database.cargar_compatibilidad("kernel-a", "env-a")) == {
        "scx_lavd"
    }
    assert set(database.cargar_compatibilidad("kernel-a", "env-b")) == {
        "scx_lavd"
    }


def test_migration_sanitizes_legacy_threads_response_idempotently(isolated_db):
    database.inicializar_db()
    run_id = database.guardar_run(VERSIONS)
    with connect_rows(isolated_db) as conn:
        conn.execute(
            "INSERT INTO results ("
            "run_id, timestamp, scheduler_name, test_type, valor, p95, "
            "response, response_kind"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, 100.0, "scx_lavd", "threads", 50.0, 2.5, 0.4, "legacy"),
        )
        conn.execute("PRAGMA user_version = 0")

    database.inicializar_db()
    database.inicializar_db()

    with connect_rows(isolated_db) as conn:
        row = conn.execute(
            "SELECT p95, response, response_kind FROM results WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    assert tuple(row) == (None, None, None)

    database.guardar_resultado(
        run_id,
        result(
            test_type="threads",
            p95=9.0,
            response=8.0,
            response_kind="legacy_again",
        ),
    )
    loaded = database.cargar_resultados_de_run(run_id)[0]
    assert loaded["p95"] is None
    assert loaded["response"] is None
    assert loaded["response_kind"] is None


def test_complete_run_and_batch_roll_back_on_intermediate_failure(isolated_db):
    database.inicializar_db()
    with connect_rows(isolated_db) as conn:
        conn.executescript(
            """
            CREATE TRIGGER reject_exploding_result
            BEFORE INSERT ON results
            WHEN NEW.test_type = 'explode'
            BEGIN
                SELECT RAISE(ABORT, 'forced result failure');
            END;
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced result failure"):
        database.guardar_run_completo(
            VERSIONS,
            [result(test_type="cpu"), result(test_type="explode")],
            run_type="auto",
        )

    with connect_rows(isolated_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM results").fetchone()[0] == 0

    run_id = database.guardar_run(VERSIONS)
    with pytest.raises(sqlite3.IntegrityError, match="forced result failure"):
        database.guardar_resultados_batch(
            run_id,
            [result(test_type="threads"), result(test_type="explode")],
        )

    assert database.cargar_resultados_de_run(run_id) == []
    with pytest.raises(ValueError, match="al menos un resultado"):
        database.guardar_run_completo(VERSIONS, [])


def test_transaction_rolls_back_when_deferred_commit_fails(isolated_db):
    database.activar_db_temporal()
    with database._transaction() as conn:
        conn.execute(
            "CREATE TABLE deferred_children ("
            "id INTEGER PRIMARY KEY, "
            "run_id INTEGER NOT NULL REFERENCES runs(id) "
            "DEFERRABLE INITIALLY DEFERRED)"
        )

    conn = database._db_temp
    with pytest.raises(sqlite3.IntegrityError):
        with database._transaction() as transaction:
            transaction.execute(
                "INSERT INTO deferred_children (run_id) VALUES (?)", (999,)
            )

    assert conn.in_transaction is False
    assert conn.execute("SELECT COUNT(*) FROM deferred_children").fetchone()[0] == 0


def test_schema_version_and_ddl_roll_back_when_migration_fails(
    isolated_db, monkeypatch
):
    original_migration = database._migrar_compatibilidad_por_entorno

    def fail_environment_migration(_conn):
        raise RuntimeError("forced migration failure")

    monkeypatch.setattr(
        database,
        "_migrar_compatibilidad_por_entorno",
        fail_environment_migration,
    )
    with pytest.raises(RuntimeError, match="forced migration failure"):
        database.inicializar_db()

    with connect_rows(isolated_db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert not {
        "runs",
        "results",
        "compatibility",
        "results_duplicate_archive",
    } & tables

    monkeypatch.setattr(
        database,
        "_migrar_compatibilidad_por_entorno",
        original_migration,
    )
    database.inicializar_db()
    with connect_rows(isolated_db) as conn:
        assert (
            conn.execute("PRAGMA user_version").fetchone()[0]
            == database._SCHEMA_VERSION
        )


def test_result_identity_is_idempotent_for_legacy_writers(isolated_db):
    database.inicializar_db()
    run_id = database.guardar_run(VERSIONS)

    database.guardar_resultado(run_id, result(value=10.0))
    database.guardar_resultado(run_id, result(value=20.0, response=3.0))
    database.guardar_resultados_batch(
        run_id,
        [result(value=30.0), result(value=40.0, response=1.5)],
    )

    loaded = database.cargar_resultados_de_run(run_id)
    assert database.contar_resultados() == 1
    assert len(loaded) == 1
    assert loaded[0]["valor"] == 40.0
    assert loaded[0]["response"] == 1.5


@pytest.mark.parametrize("invalid_run_id", [1.9, True, "1", 0, -1])
def test_run_id_must_be_a_positive_non_boolean_integer(
    isolated_db, invalid_run_id
):
    database.inicializar_db()
    valid_run_id = database.guardar_run(VERSIONS)
    operations = (
        lambda: database.guardar_resultado(invalid_run_id, result()),
        lambda: database.guardar_resultados_batch(
            invalid_run_id, [result()]
        ),
        lambda: database.cargar_resultados_de_run(invalid_run_id),
    )

    for operation in operations:
        with pytest.raises(ValueError, match="entero positivo"):
            operation()

    assert database.cargar_resultados_de_run(valid_run_id) == []


def test_round_trips_metadata_payload_raw_metrics_and_rejects_unsafe_json(
    isolated_db,
):
    database.inicializar_db()
    metadata = {
        "pesos": [45, 45, 10],
        "orden": ["Sistema Base", "scx_lavd"],
        "temperatura": {"base": 43.5, "maxima": 52.0},
        "governor": "performance",
        "frecuencia_mhz": 3200,
        "configuracion": {"duracion": 5, "repeticiones": 3},
    }
    payload = {"command": ["stress-ng", "--cpu", "16"], "samples": [1, 2, 3]}
    metrics = {"bogo-ops": 1234.5, "cpu-usage-per-instance": 87.0}

    run_id = database.guardar_run_completo(
        VERSIONS,
        [
            result(
                response=125.5,
                response_kind="latency_us",
                payload=payload,
                metrics=metrics,
            )
        ],
        run_type="auto",
        status="partial",
        timestamp=100.0,
        closed_at=200.0,
        metadata=metadata,
    )

    runs = database.consultar_runs_auto()
    loaded = database.cargar_resultados_de_run(run_id)[0]
    loaded_with_payload = database.cargar_resultados_de_run(
        run_id, include_payload=True
    )[0]
    history = database.consultar_historial()
    history_with_payload = database.consultar_historial(include_payload=True)

    assert runs[0]["status"] == "partial"
    assert runs[0]["closed_at"] == 200.0
    assert runs[0]["metadata"] == metadata
    assert runs[0]["metadata_error"] is None
    assert json.loads(runs[0]["metadata_json"]) == metadata
    assert loaded["response"] == 125.5
    assert loaded["response_kind"] == "latency_us"
    assert "payload_json" not in loaded
    assert "raw_metrics_json" not in loaded
    assert "payload" not in loaded
    assert "raw_metrics" not in loaded
    assert loaded_with_payload["payload"] == payload
    assert loaded_with_payload["raw_metrics"] == metrics
    assert loaded_with_payload["payload_error"] is None
    assert loaded_with_payload["raw_metrics_error"] is None
    assert history[0]["metadata"] == metadata
    assert history[0]["metadata_error"] is None
    assert "payload_json" not in history[0]
    assert "raw_metrics_json" not in history[0]
    assert "payload" not in history[0]
    assert "raw_metrics" not in history[0]
    assert history_with_payload[0]["payload"] == payload
    assert history_with_payload[0]["raw_metrics"] == metrics
    assert history_with_payload[0]["payload_error"] is None
    assert history_with_payload[0]["raw_metrics_error"] is None

    with pytest.raises(ValueError, match="tipo no serializable"):
        database.guardar_run_completo(
            VERSIONS,
            [result(test_type="memory")],
            metadata={"unsafe": {1, 2}},
        )
    with pytest.raises(ValueError, match="número no finito"):
        database.guardar_run_completo(
            VERSIONS,
            [result(test_type="memory", payload={"sample": float("nan")})],
        )

    with connect_rows(isolated_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM results").fetchone()[0] == 1


def test_summary_queries_skip_large_payload_deserialization_by_default(
    isolated_db, monkeypatch
):
    database.inicializar_db()
    payload = {"times_us": list(range(10_000))}
    metrics = {"samples": 10_000}
    run_id = database.guardar_run_completo(
        VERSIONS,
        [result(payload=payload, raw_metrics=metrics)],
    )
    original_deserializer = database._deserializar_json
    deserialized_blobs = []

    def track_deserialization(value, field_name):
        if field_name.endswith(("payload_json", "raw_metrics_json")):
            deserialized_blobs.append(field_name)
        return original_deserializer(value, field_name)

    monkeypatch.setattr(database, "_deserializar_json", track_deserialization)

    summaries = (
        database.consultar_historial()[0],
        database.cargar_resultados_de_run(run_id)[0],
        database.consultar_tendencia("cpu", days=None)[0],
    )

    assert database.contar_historial() == 1
    assert deserialized_blobs == []
    for summary in summaries:
        assert "payload_json" not in summary
        assert "raw_metrics_json" not in summary
        assert "payload" not in summary
        assert "raw_metrics" not in summary

    audited = (
        database.consultar_historial(include_payload=True)[0],
        database.cargar_resultados_de_run(run_id, include_payload=True)[0],
        database.consultar_tendencia(
            "cpu", days=None, include_payload=True
        )[0],
    )

    assert len(deserialized_blobs) == 6
    for row in audited:
        assert row["payload"] == payload
        assert row["raw_metrics"] == metrics


@pytest.mark.parametrize(
    "corrupt_metadata",
    [
        "{invalid",
        sqlite3.Binary(b"\x80"),
        "[" * 2_000 + "0" + "]" * 2_000,
    ],
    ids=("syntax", "invalid-utf8", "excessive-depth"),
)
def test_corrupt_metadata_is_reported_without_blocking_startup_or_history(
    isolated_db, corrupt_metadata
):
    database.inicializar_db()
    run_id = database.guardar_run_completo(
        VERSIONS,
        [result()],
        run_type="auto",
        metadata={"candidate_order": ["Sistema Base", "scx_lavd"]},
    )
    with connect_rows(isolated_db) as conn:
        conn.execute(
            "UPDATE runs SET metadata_json = ? WHERE id = ?",
            (corrupt_metadata, run_id),
        )

    database.inicializar_db()
    run = database.consultar_runs_auto()[0]
    history = database.consultar_historial()[0]

    for row in (run, history):
        assert row["metadata"] is None
        assert "metadata_json" in row["metadata_error"]
        assert "JSON inválido" in row["metadata_error"]


@pytest.mark.parametrize(
    ("column", "decoded_field", "error_field", "corrupt_blob"),
    [
        ("payload_json", "payload", "payload_error", "{invalid"),
        (
            "payload_json",
            "payload",
            "payload_error",
            sqlite3.Binary(b"\x80"),
        ),
        (
            "raw_metrics_json",
            "raw_metrics",
            "raw_metrics_error",
            "{invalid",
        ),
        (
            "raw_metrics_json",
            "raw_metrics",
            "raw_metrics_error",
            sqlite3.Binary(b"\x80"),
        ),
    ],
)
def test_corrupt_result_blobs_are_only_read_with_explicit_opt_in(
    isolated_db, column, decoded_field, error_field, corrupt_blob
):
    database.inicializar_db()
    run_id = database.guardar_run_completo(VERSIONS, [result()])
    with connect_rows(isolated_db) as conn:
        conn.execute(
            f"UPDATE results SET {column} = ? WHERE run_id = ?",
            (corrupt_blob, run_id),
        )

    summaries = (
        database.consultar_historial(),
        database.cargar_resultados_de_run(run_id),
        database.consultar_tendencia("cpu", days=None),
    )
    assert all(len(rows) == 1 for rows in summaries)

    audited_queries = (
        lambda: database.consultar_historial(include_payload=True),
        lambda: database.cargar_resultados_de_run(
            run_id, include_payload=True
        ),
        lambda: database.consultar_tendencia(
            "cpu", days=None, include_payload=True
        ),
    )
    for query in audited_queries:
        audited = query()[0]
        assert audited[decoded_field] is None
        assert column in audited[error_field]
        assert "JSON inválido" in audited[error_field]


@pytest.mark.parametrize("days", [None, 0, -1])
def test_consultar_tendencia_without_positive_days_returns_all_history(
    isolated_db, days
):
    database.inicializar_db()
    database.guardar_run_completo(
        VERSIONS,
        [result(value=10.0, timestamp=100.0)],
        timestamp=100.0,
    )
    database.guardar_run_completo(
        VERSIONS,
        [result(value=20.0, timestamp=200.0)],
        timestamp=200.0,
    )

    rows = database.consultar_tendencia("cpu", days=days)

    assert [row["valor"] for row in rows] == [10.0, 20.0]


def test_consultar_tendencia_filters_scheduler_and_keeps_default_cutoff(
    isolated_db, monkeypatch
):
    database.inicializar_db()
    now = 10_000_000.0
    monkeypatch.setattr(database.time, "time", lambda: now)
    database.guardar_run_completo(
        VERSIONS,
        [result(value=10.0, timestamp=now - 31 * 86400)],
    )
    database.guardar_run_completo(
        VERSIONS,
        [result(value=20.0, timestamp=now - 2 * 86400)],
    )
    database.guardar_run_completo(
        VERSIONS,
        [
            result(
                scheduler="scx_bpfland",
                value=30.0,
                timestamp=now - 86400,
            )
        ],
    )

    recent = database.consultar_tendencia("cpu")
    lavd = database.consultar_tendencia("cpu", scheduler="scx_lavd")
    all_lavd = database.consultar_tendencia(
        "cpu", days=None, scheduler="scx_lavd"
    )

    assert [row["valor"] for row in recent] == [20.0, 30.0]
    assert [row["valor"] for row in lavd] == [20.0]
    assert [row["valor"] for row in all_lavd] == [10.0, 20.0]


def test_historial_filters_kernel_and_single_or_multiple_statuses(isolated_db):
    database.inicializar_db()
    cases = (
        ("kernel-a", "completed", 10.0),
        ("kernel-a", "partial", 20.0),
        ("kernel-b", "failed", 30.0),
    )
    for index, (kernel, status, value) in enumerate(cases, start=1):
        versions = dict(VERSIONS, kernel=kernel)
        database.guardar_run_completo(
            versions,
            [
                result(
                    scheduler=f"scx_{index}",
                    value=value,
                    timestamp=float(index),
                )
            ],
            status=status,
            timestamp=float(index),
        )

    kernel_a = database.consultar_historial(kernel_version="kernel-a")
    partial = database.consultar_historial(status="partial")
    comparable = database.consultar_historial(
        kernel_version="kernel-a", status={"completed", "partial"}
    )

    assert {row["valor"] for row in kernel_a} == {10.0, 20.0}
    assert [row["valor"] for row in partial] == [20.0]
    assert {row["valor"] for row in comparable} == {10.0, 20.0}
    assert database.contar_historial(
        kernel_version="kernel-a", status=("completed", "partial")
    ) == 2
    assert database.contar_resultados() == 3
    assert database.consultar_historial(status=[]) == []
    assert database.contar_historial(status=[]) == 0
    assert database.consultar_historial(date_to=0) == []
    assert database.contar_historial(date_to=0) == 0


def test_historial_filters_are_parameterized_and_status_is_validated(isolated_db):
    database.inicializar_db()
    database.guardar_run_completo(
        dict(VERSIONS, kernel="safe-kernel"),
        [result(value=10.0)],
        status="completed",
    )

    injection = "safe-kernel' OR 1=1 --"
    assert database.consultar_historial(kernel_version=injection) == []
    assert database.contar_historial(kernel_version=injection) == 0
    assert database.contar_resultados() == 1

    invalid_statuses = (
        "completed' OR 1=1 --",
        ["completed", "unknown"],
        ["completed", {"unhashable": True}],
        {"completed": True},
    )
    for status in invalid_statuses:
        with pytest.raises(ValueError, match="status"):
            database.consultar_historial(status=status)


def test_temporary_database_is_safe_across_worker_threads(isolated_db):
    database.activar_db_temporal()

    def save(worker_id):
        return database.guardar_run_completo(
            VERSIONS,
            [
                result(
                    scheduler=f"scx_worker_{worker_id}",
                    value=float(worker_id),
                    payload={"worker": worker_id},
                )
            ],
            run_type="auto",
            metadata={"worker": worker_id},
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        run_ids = list(executor.map(save, range(24)))

    assert len(set(run_ids)) == 24
    assert database.contar_resultados() == 24
    assert len(database.consultar_runs_auto()) == 24


def test_reemplazar_compatibilidad_replaces_whole_snapshot_with_zero_compatible(
    isolated_db, monkeypatch
):
    database.inicializar_db()
    database.guardar_compatibilidad("scx_old", "kernel-a", True, "old")
    database.guardar_compatibilidad("scx_other", "kernel-b", True, "other")
    monkeypatch.setattr(database.time, "time", lambda: 500.0)

    database.reemplazar_compatibilidad(
        "kernel-a",
        [
            ("scx_lavd", False, "not supported"),
            {
                "scheduler_name": "scx_bpfland",
                "is_compatible": False,
                "message": "not available",
            },
        ],
    )

    snapshot = database.cargar_compatibilidad("kernel-a")
    assert snapshot == {
        "scx_lavd": (False, "not supported", 500.0),
        "scx_bpfland": (False, "not available", 500.0),
    }
    assert database.cargar_compatibilidad("kernel-b")["scx_other"][0] is True


def test_compatibility_context_is_exact_and_legacy_never_matches_current_key(
    isolated_db, monkeypatch
):
    database.inicializar_db()
    monkeypatch.setattr(database.time, "time", lambda: 500.0)

    database.guardar_compatibilidad(
        "scx_lavd", "kernel-a", True, "legacy"
    )
    database.guardar_compatibilidad(
        "scx_lavd",
        "kernel-a",
        False,
        "environment a old",
        environment_key="env-a",
    )
    database.guardar_compatibilidad(
        "scx_lavd",
        "kernel-a",
        True,
        "environment b",
        environment_key="env-b",
    )

    database.reemplazar_compatibilidad(
        "kernel-a",
        [
            ("scx_lavd", True, "environment a new"),
            ("scx_bpfland", False, "environment a only"),
        ],
        environment_key="env-a",
    )

    assert database.cargar_compatibilidad("kernel-a") == {
        "scx_lavd": (True, "legacy", 500.0)
    }
    assert database.cargar_compatibilidad("kernel-a", "env-a") == {
        "scx_lavd": (True, "environment a new", 500.0),
        "scx_bpfland": (False, "environment a only", 500.0),
    }
    assert database.cargar_compatibilidad("kernel-a", "env-b") == {
        "scx_lavd": (True, "environment b", 500.0)
    }
    assert database.cargar_compatibilidad("kernel-a", "env-missing") == {}

    history = database.obtener_historial_compatibilidad()
    assert {row["environment_key"] for row in history} == {
        None,
        "env-a",
        "env-b",
    }
    assert all(
        row["environment_key"] == "env-a"
        for row in history
        if row["scheduler_name"] == "scx_bpfland"
    )


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        (
            [("scx_lavd", True, "ok"), ("scx_lavd", False, "duplicate")],
            "duplicado",
        ),
        ([("   ", True, "blank")], "vacío"),
    ],
)
def test_reemplazar_compatibilidad_rejects_invalid_snapshot_before_write(
    isolated_db, monkeypatch, rows, message
):
    database.inicializar_db()
    database.guardar_compatibilidad("scx_old", "kernel-a", True, "preserved")

    def unexpected_transaction():
        pytest.fail("No debe abrirse una transacción para un snapshot inválido")

    monkeypatch.setattr(database, "_transaction", unexpected_transaction)

    with pytest.raises(ValueError, match=message):
        database.reemplazar_compatibilidad("kernel-a", rows)

    snapshot = database.cargar_compatibilidad("kernel-a")
    assert set(snapshot) == {"scx_old"}
    assert snapshot["scx_old"][:2] == (True, "preserved")


def test_reemplazar_compatibilidad_rolls_back_delete_when_insert_fails(isolated_db):
    database.inicializar_db()
    database.guardar_compatibilidad(
        "scx_old",
        "kernel-a",
        True,
        "preserved",
        environment_key="env-current",
    )
    database.guardar_compatibilidad(
        "scx_other",
        "kernel-a",
        False,
        "other context",
        environment_key="env-other",
    )
    original = database.cargar_compatibilidad("kernel-a", "env-current")
    other_context = database.cargar_compatibilidad("kernel-a", "env-other")
    with connect_rows(isolated_db) as conn:
        conn.executescript(
            """
            CREATE TRIGGER reject_compatibility_insert
            BEFORE INSERT ON compatibility
            WHEN NEW.scheduler_name = 'scx_explode'
            BEGIN
                SELECT RAISE(ABORT, 'forced compatibility failure');
            END;
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced compatibility failure"):
        database.reemplazar_compatibilidad(
            "kernel-a",
            [
                ("scx_new", True, "new"),
                ("scx_explode", False, "fails"),
            ],
            environment_key="env-current",
        )

    assert database.cargar_compatibilidad(
        "kernel-a", "env-current"
    ) == original
    assert database.cargar_compatibilidad(
        "kernel-a", "env-other"
    ) == other_context
    assert database.cargar_compatibilidad("kernel-a") == {}


def test_concurrent_compatibility_replacements_never_mix_snapshots(isolated_db):
    database.inicializar_db()
    worker_count = 8
    barrier = threading.Barrier(worker_count)
    snapshots = {
        worker: [
            (f"scx_{worker}_{index}", index % 2 == 0, f"worker {worker}")
            for index in range(4)
        ]
        for worker in range(worker_count)
    }

    def replace(worker):
        barrier.wait(timeout=5)
        database.reemplazar_compatibilidad("shared-kernel", snapshots[worker])

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        list(executor.map(replace, range(worker_count)))

    current = database.cargar_compatibilidad("shared-kernel")
    current_names = set(current)
    expected_name_sets = [
        {name for name, _compatible, _message in rows}
        for rows in snapshots.values()
    ]
    assert current_names in expected_name_sets
    assert len(current) == 4


def test_compatibility_replacement_pins_temporary_database_during_validation(
    isolated_db, monkeypatch
):
    database.inicializar_db()
    database.activar_db_temporal()
    validation_started = threading.Event()
    release_validation = threading.Event()
    original_prepare = database._preparar_snapshot_compatibilidad

    def blocked_prepare(*args, **kwargs):
        validation_started.set()
        if not release_validation.wait(timeout=2):
            raise TimeoutError("validation was not released")
        return original_prepare(*args, **kwargs)

    monkeypatch.setattr(
        database, "_preparar_snapshot_compatibilidad", blocked_prepare
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        replacement = executor.submit(
            database.reemplazar_compatibilidad,
            "kernel-a",
            [("scx_lavd", True, "ok")],
        )
        assert validation_started.wait(timeout=2)
        release_timer = threading.Timer(0.1, release_validation.set)
        release_timer.start()
        try:
            database.desactivar_db_temporal()
        finally:
            release_validation.set()
            release_timer.join()
        replacement.result(timeout=2)

    with connect_rows(isolated_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM compatibility").fetchone()[0] == 0


def test_write_keeps_temporary_connection_pinned_during_preparation(
    isolated_db, monkeypatch
):
    database.inicializar_db()
    database.activar_db_temporal()
    preparation_started = threading.Event()
    release_preparation = threading.Event()
    original_prepare = database._preparar_run

    def blocked_prepare(*args, **kwargs):
        preparation_started.set()
        if not release_preparation.wait(timeout=2):
            raise TimeoutError("preparation was not released")
        return original_prepare(*args, **kwargs)

    monkeypatch.setattr(database, "_preparar_run", blocked_prepare)

    with ThreadPoolExecutor(max_workers=1) as executor:
        write = executor.submit(database.guardar_run, VERSIONS)
        assert preparation_started.wait(timeout=2)
        release_timer = threading.Timer(0.1, release_preparation.set)
        release_timer.start()
        try:
            database.desactivar_db_temporal()
        finally:
            release_preparation.set()
            release_timer.join()
        write.result(timeout=2)

    with connect_rows(isolated_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


def test_version_change_detection_includes_scxctl_without_real_commands(
    isolated_db, monkeypatch
):
    database.inicializar_db()
    database.guardar_run(VERSIONS)

    assert database.detectar_cambio_version(dict(VERSIONS)) == (False, [])
    changed = dict(VERSIONS, scxctl="scxctl 2.0")
    assert database.detectar_cambio_version(changed) == (True, ["scxctl"])

    command_versions = {
        "scxctl": "mock-scxctl",
        "stress-ng": "mock-stress-ng",
        "hyperfine": "mock-hyperfine",
    }
    monkeypatch.setattr(database.platform, "release", lambda: "mock-kernel")
    monkeypatch.setattr(
        database,
        "_cmd_output",
        lambda command, timeout=3: command_versions[command[0]],
    )
    assert database.obtener_versiones() == {
        "kernel": "mock-kernel",
        "scxctl": "mock-scxctl",
        "stressng": "mock-stress-ng",
        "hyperfine": "mock-hyperfine",
    }


def test_obtener_versiones_runs_commands_concurrently_with_short_timeouts(
    monkeypatch,
):
    kernel_read = threading.Event()
    workers_ready = threading.Barrier(3)
    calls = []
    calls_lock = threading.Lock()

    def release():
        kernel_read.set()
        return "mock-kernel"

    def command_version(command, timeout):
        assert kernel_read.is_set()
        assert 0 < timeout <= 1.5
        with calls_lock:
            calls.append((command[0], timeout, threading.get_ident()))
        workers_ready.wait(timeout=2)
        return f"mock-{command[0]}"

    monkeypatch.setattr(database.platform, "release", release)
    monkeypatch.setattr(database, "_cmd_output", command_version)

    assert database.obtener_versiones() == {
        "kernel": "mock-kernel",
        "scxctl": "mock-scxctl",
        "stressng": "mock-stress-ng",
        "hyperfine": "mock-hyperfine",
    }
    assert {name for name, _timeout, _thread_id in calls} == {
        "scxctl",
        "stress-ng",
        "hyperfine",
    }
    assert len({thread_id for _name, _timeout, thread_id in calls}) == 3
    assert not any(
        thread.name.startswith("reactor-versions")
        for thread in threading.enumerate()
    )
