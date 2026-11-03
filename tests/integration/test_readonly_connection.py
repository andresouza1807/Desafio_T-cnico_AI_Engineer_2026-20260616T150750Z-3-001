"""Read-only connection tests for task T6 (docs/specs/02-07-readonly-connection.md).

Every test uses real SQLite files in pytest tmp_path, prepared with prepare_database
from synthetic CSV (prep_kit). Invalid variants are produced with a real writable
sqlite3 connection on the file before it is opened read-only. State is verified with
independent connections, SHA-256 of file bytes and directory listings. The connect
recorder calls the real sqlite3.connect with the same arguments and only keeps the
real connection, to check the URI and that the connection is closed after a failure.
Nothing here simulates a lock, a close, a query or persistence.
"""

import dataclasses
import hashlib
import sqlite3
import sys
import traceback
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from emporio_agent.data.preparation import prepare_database
from emporio_agent.data.readonly import (
    DatabaseUnavailableError,
    PreparationInfo,
    connect_readonly,
    read_preparation_info,
)
from emporio_agent.data.schema import TABLE_ORDER

WHEN = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
PRIVATE_DIR = "pasta-privada-x91"
DB_NAME = "emporio.sqlite3"
SENTINEL = "SENTINEL-META-4417"
ALL_TABLES = (*TABLE_ORDER, "preparation_metadata", "source_files", "data_quality_notes")
REAL_CONNECT = sqlite3.connect


# --- helpers -------------------------------------------------------------------------


def prepared(tmp_path: Path, prep_kit, *, directory: str = PRIVATE_DIR, name: str = DB_NAME):
    source_dir = tmp_path / "sources"
    if not source_dir.exists():
        prep_kit.write_sources(source_dir)
    target_dir = tmp_path / directory
    target_dir.mkdir(exist_ok=True)
    target = target_dir / name
    result = prepare_database(source_dir, target, clock=lambda: WHEN)
    return target, result


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def listing(directory: Path) -> list[str]:
    return sorted(path.name for path in directory.iterdir())


def snapshot(path: Path) -> dict[str, list[tuple[object, ...]]]:
    connection = REAL_CONNECT(path.as_uri() + "?mode=ro", uri=True)
    try:
        return {
            table: connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
            for table in ALL_TABLES
        }
    finally:
        connection.close()


def edit(path: Path, *statements: str) -> None:
    connection = REAL_CONNECT(path)
    try:
        for statement in statements:
            connection.execute(statement)
        connection.commit()
    finally:
        connection.close()


def assert_closed(connection: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")


def surfaces(error: DatabaseUnavailableError) -> list[str]:
    return [
        str(error),
        repr(error),
        *(str(arg) for arg in error.args),
        str(error.reason),
        str(error.database_name),
        "".join(traceback.format_exception_only(error)),
    ]


def assert_sanitized(error: DatabaseUnavailableError, tmp_path: Path, *forbidden: str) -> None:
    for text in surfaces(error):
        for value in (str(tmp_path), str(tmp_path.resolve()), PRIVATE_DIR, *forbidden):
            assert value not in text


def assert_no_chained_cause(error: BaseException) -> None:
    assert error.__cause__ is None
    assert error.__suppress_context__ or error.__context__ is None


def unavailable(path: Path) -> DatabaseUnavailableError:
    with pytest.raises(DatabaseUnavailableError) as info:
        with connect_readonly(path):
            pytest.fail("block must not run")
    return info.value


@pytest.fixture
def connect_recorder(monkeypatch):
    """Wrap the real sqlite3.connect; keep arguments and the real connection."""
    calls: list[tuple[tuple[object, ...], dict[str, object], sqlite3.Connection]] = []

    def connect(*args, **kwargs):
        connection = REAL_CONNECT(*args, **kwargs)
        calls.append((args, kwargs, connection))
        return connection

    monkeypatch.setattr(sqlite3, "connect", connect)
    return calls


# --- DatabaseUnavailableError ----------------------------------------------------------


def test_error_message_args_and_attributes():
    error = DatabaseUnavailableError("missing", database_name=DB_NAME)
    assert error.reason == "missing"
    assert error.database_name == DB_NAME
    assert str(error) == f"{DB_NAME}: missing"
    assert error.args == (str(error),)
    assert repr(error) == f"DatabaseUnavailableError('{DB_NAME}: missing')"
    assert traceback.format_exception_only(error) == [
        f"emporio_agent.data.readonly.DatabaseUnavailableError: {DB_NAME}: missing\n"
    ]


def test_error_without_name():
    error = DatabaseUnavailableError("open_failed")
    assert error.database_name is None
    assert str(error) == "open_failed"
    assert error.args == ("open_failed",)


@pytest.mark.parametrize("reason", ["", "other", "MISSING"])
def test_error_rejects_unknown_reason(reason):
    with pytest.raises(ValueError, match="unknown reason"):
        DatabaseUnavailableError(reason)  # type: ignore[arg-type]


@pytest.mark.parametrize("name", ["", "dir/db.sqlite3", "dir\\db.sqlite3", "/abs.db", "C:\\x.db"])
def test_error_rejects_name_with_directory(name):
    with pytest.raises(ValueError, match="database_name must be a base name"):
        DatabaseUnavailableError("missing", database_name=name)


# --- D50: missing database ------------------------------------------------------------


def test_d50_missing_database_not_created(tmp_path, connect_recorder):
    target = tmp_path / PRIVATE_DIR / DB_NAME
    before = listing(tmp_path)
    error = unavailable(target)
    assert error.reason == "missing"
    assert error.database_name == DB_NAME
    assert str(error) == f"{DB_NAME}: missing"
    assert not target.exists()
    assert not target.parent.exists()
    assert listing(tmp_path) == before
    assert connect_recorder == []
    assert_sanitized(error, tmp_path)


def test_d50_missing_file_in_existing_directory(tmp_path, connect_recorder):
    directory = tmp_path / PRIVATE_DIR
    directory.mkdir()
    error = unavailable(directory / DB_NAME)
    assert error.reason == "missing"
    assert listing(directory) == []
    assert connect_recorder == []


def test_d50_directory_is_rejected(tmp_path, connect_recorder):
    directory = tmp_path / PRIVATE_DIR / DB_NAME
    directory.mkdir(parents=True)
    error = unavailable(directory)
    assert error.reason == "missing"
    assert error.database_name == DB_NAME
    assert directory.is_dir()
    assert listing(directory) == []
    assert connect_recorder == []
    assert_sanitized(error, tmp_path)


def test_empty_path_name_gives_no_database_name(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    error = unavailable(Path("."))
    assert error.reason == "missing"
    assert error.database_name is None


# --- configuration --------------------------------------------------------------------


def test_connect_uses_readonly_uri(tmp_path, prep_kit, connect_recorder):
    target, _ = prepared(tmp_path, prep_kit)
    connect_recorder.clear()  # drop the build connection of prepare_database
    with connect_readonly(target) as connection:
        assert len(connect_recorder) == 1
        args, kwargs, real = connect_recorder[0]
        assert args == (target.resolve().as_uri() + "?mode=ro",)
        assert kwargs == {"uri": True}
        assert connection is real


def test_row_factory_and_query_only_observable(tmp_path, prep_kit):
    target, _ = prepared(tmp_path, prep_kit)
    with connect_readonly(target) as connection:
        assert isinstance(connection, sqlite3.Connection)
        assert connection.row_factory is sqlite3.Row
        row = connection.execute("SELECT name FROM categories WHERE category_id = ?", (1,))
        assert row.fetchone()["name"] == "Cordas"
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1


def test_open_and_close_leave_directory_and_bytes_unchanged(tmp_path, prep_kit):
    target, _ = prepared(tmp_path, prep_kit)
    before_list, before_hash = listing(target.parent), sha256(target)
    with connect_readonly(target) as connection:
        connection.execute("SELECT count(*) FROM orders").fetchone()
        assert listing(target.parent) == before_list
    assert listing(target.parent) == before_list
    assert sha256(target) == before_hash


# --- D51: writes rejected -------------------------------------------------------------

WRITES = {
    "insert": "INSERT INTO categories (category_id, name, description) VALUES (9, 'X', 'Y')",
    "update": "UPDATE products SET stock_quantity = 99",
    "delete": "DELETE FROM orders",
    "create": "CREATE TABLE extra (x INTEGER)",
}


@pytest.mark.parametrize("statement", WRITES.values(), ids=WRITES.keys())
def test_d51_write_rejected_and_state_unchanged(tmp_path, prep_kit, statement):
    target, _ = prepared(tmp_path, prep_kit)
    before_rows, before_hash, before_list = snapshot(target), sha256(target), listing(target.parent)
    seen: list[sqlite3.OperationalError] = []
    with pytest.raises(sqlite3.OperationalError) as info:
        with connect_readonly(target) as connection:
            try:
                connection.execute(statement)
            except sqlite3.OperationalError as error:
                seen.append(error)
                raise
    assert info.value is seen[0]
    assert type(info.value) is sqlite3.OperationalError
    assert_closed(connection)
    assert snapshot(target) == before_rows
    assert sha256(target) == before_hash
    assert listing(target.parent) == before_list


def test_d51_query_only_off_still_rejected_by_mode_ro(tmp_path, prep_kit):
    target, _ = prepared(tmp_path, prep_kit)
    before_rows, before_hash = snapshot(target), sha256(target)
    with connect_readonly(target) as connection:
        connection.execute("PRAGMA query_only = OFF")
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 0
        for statement in WRITES.values():
            with pytest.raises(sqlite3.OperationalError):
                connection.execute(statement)
    assert snapshot(target) == before_rows
    assert sha256(target) == before_hash


# --- D52: explicit close --------------------------------------------------------------


def test_d52_closed_after_normal_exit(tmp_path, prep_kit):
    target, _ = prepared(tmp_path, prep_kit)
    with connect_readonly(target) as connection:
        connection.execute("SELECT 1").fetchone()
    assert_closed(connection)


class ConsumerError(Exception):
    pass


class ConsumerInterrupt(BaseException):
    pass


@pytest.mark.parametrize("kind", [ConsumerError, ConsumerInterrupt, ValueError])
def test_d52_consumer_exception_preserved_and_connection_closed(tmp_path, prep_kit, kind):
    target, _ = prepared(tmp_path, prep_kit)
    raised = kind("consumer failure")
    with pytest.raises(kind) as info:
        with connect_readonly(target) as connection:
            raise raised
    assert info.value is raised
    assert type(info.value) is kind
    assert info.value.__cause__ is None
    assert info.value.__context__ is None
    assert_closed(connection)


# --- D53: preparation metadata --------------------------------------------------------


def test_d53_preparation_info_equals_result(tmp_path, prep_kit):
    target, result = prepared(tmp_path, prep_kit)
    with connect_readonly(target) as connection:
        info = read_preparation_info(connection)
        assert connection.execute("SELECT count(*) FROM categories").fetchone()[0] == 2
        assert read_preparation_info(connection) == info
    assert isinstance(info, PreparationInfo)
    assert info.schema_version == result.schema_version == 1
    assert info.prepared_at == result.prepared_at == WHEN
    assert info.prepared_at.tzinfo is UTC
    assert info.sources == result.sources
    assert tuple(source.table_name for source in info.sources) == TABLE_ORDER
    with pytest.raises(dataclasses.FrozenInstanceError):
        info.schema_version = 2  # type: ignore[misc]


def test_d53_prepared_at_from_other_offset_is_utc(tmp_path, prep_kit):
    prep_kit.write_sources(tmp_path / "sources")
    (tmp_path / PRIVATE_DIR).mkdir()
    target = tmp_path / PRIVATE_DIR / DB_NAME
    local = datetime(2026, 11, 2, 9, 0, tzinfo=timezone(timedelta(hours=-3)))
    result = prepare_database(tmp_path / "sources", target, clock=lambda: local)
    with connect_readonly(target) as connection:
        info = read_preparation_info(connection)
    assert info.prepared_at == result.prepared_at == WHEN
    assert info.prepared_at.tzinfo is UTC


def dict_factory(cursor, row):
    return {column[0]: value for column, value in zip(cursor.description, row, strict=True)}


@pytest.mark.parametrize("factory", [None, sqlite3.Row, dict_factory])
def test_d53_caller_connection_stays_open_and_unchanged(tmp_path, prep_kit, factory):
    target, result = prepared(tmp_path, prep_kit)
    connection = REAL_CONNECT(target.as_uri() + "?mode=ro", uri=True)
    try:
        connection.row_factory = factory
        info = read_preparation_info(connection)
        assert info.sources == result.sources
        assert connection.row_factory is factory
        assert not connection.in_transaction
        assert connection.execute("SELECT 1").fetchone() is not None
    finally:
        connection.close()


def test_read_info_on_closed_connection_propagates_programming_error(tmp_path, prep_kit):
    target, _ = prepared(tmp_path, prep_kit)
    with connect_readonly(target) as connection:
        pass
    with pytest.raises(sqlite3.ProgrammingError):
        read_preparation_info(connection)


def test_read_info_memory_database_has_no_name():
    connection = REAL_CONNECT(":memory:")
    try:
        with pytest.raises(DatabaseUnavailableError) as info:
            read_preparation_info(connection)
        assert info.value.reason == "schema_mismatch"
        assert info.value.database_name is None
        assert str(info.value) == "schema_mismatch"
        assert_no_chained_cause(info.value)
        assert connection.execute("SELECT 1").fetchone() == (1,)
    finally:
        connection.close()


def test_read_info_rejects_other_schema_version_on_caller_connection(tmp_path, prep_kit):
    target, _ = prepared(tmp_path, prep_kit)
    edit(
        target, f"UPDATE preparation_metadata SET value = '{SENTINEL}' WHERE key = 'schema_version'"
    )
    connection = REAL_CONNECT(target.as_uri() + "?mode=ro", uri=True)
    try:
        with pytest.raises(DatabaseUnavailableError) as info:
            read_preparation_info(connection)
        assert info.value.reason == "schema_mismatch"
        assert info.value.database_name == DB_NAME
        assert_sanitized(info.value, tmp_path, SENTINEL)
        assert_no_chained_cause(info.value)
    finally:
        connection.close()


MALFORMED_INFO = {
    "prepared_at_missing": ("DELETE FROM preparation_metadata WHERE key = 'prepared_at'",),
    "prepared_at_not_iso": (
        f"UPDATE preparation_metadata SET value = '{SENTINEL}' WHERE key = 'prepared_at'",
    ),
    "prepared_at_naive": (
        "UPDATE preparation_metadata SET value = '2026-11-02T12:00:00' WHERE key = 'prepared_at'",
    ),
    "prepared_at_overflow": (
        "UPDATE preparation_metadata SET value = '0001-01-01T00:00:00+01:00' "
        "WHERE key = 'prepared_at'",
    ),
    "prepared_at_not_text": (
        "CREATE TABLE m2 AS SELECT key, value FROM preparation_metadata",
        "DROP TABLE preparation_metadata",
        "CREATE TABLE preparation_metadata (key, value)",
        "INSERT INTO preparation_metadata SELECT key, value FROM m2 WHERE key <> 'prepared_at'",
        "INSERT INTO preparation_metadata VALUES ('prepared_at', 20261102)",
        "DROP TABLE m2",
    ),
    "source_row_missing": ("DELETE FROM source_files WHERE table_name = 'orders'",),
    "source_table_missing": ("DROP TABLE source_files",),
    "source_column_missing": (
        "DROP TABLE source_files",
        "CREATE TABLE source_files (table_name TEXT, file_name TEXT)",
    ),
    "source_wrong_type": (
        "CREATE TABLE s2 AS SELECT * FROM source_files",
        "DROP TABLE source_files",
        "CREATE TABLE source_files (table_name, file_name, sha256, size_bytes, row_count)",
        "INSERT INTO source_files SELECT * FROM s2",
        f"UPDATE source_files SET size_bytes = '{SENTINEL}' WHERE table_name = 'orders'",
        "DROP TABLE s2",
    ),
    "source_unknown_row": (
        "CREATE TABLE s2 AS SELECT * FROM source_files",
        "DROP TABLE source_files",
        "CREATE TABLE source_files (table_name, file_name, sha256, size_bytes, row_count)",
        "INSERT INTO source_files SELECT * FROM s2",
        f"INSERT INTO source_files VALUES ('{SENTINEL}', 'x.csv', 'ab', 1, 1)",
        "DROP TABLE s2",
    ),
    "source_repeated_row": (
        "CREATE TABLE s2 AS SELECT * FROM source_files",
        "DROP TABLE source_files",
        "CREATE TABLE source_files (table_name, file_name, sha256, size_bytes, row_count)",
        "INSERT INTO source_files SELECT * FROM s2",
        "INSERT INTO source_files SELECT * FROM s2 WHERE table_name = 'orders'",
        "DROP TABLE s2",
    ),
}


@pytest.mark.parametrize("statements", MALFORMED_INFO.values(), ids=MALFORMED_INFO.keys())
def test_malformed_metadata_gives_sanitized_schema_mismatch(tmp_path, prep_kit, statements):
    target, _ = prepared(tmp_path, prep_kit)
    edit(target, *statements)
    before = sha256(target)
    with connect_readonly(target) as connection:
        with pytest.raises(DatabaseUnavailableError) as info:
            read_preparation_info(connection)
        assert connection.execute("SELECT 1").fetchone()[0] == 1
    error = info.value
    assert error.reason == "schema_mismatch"
    assert error.database_name == DB_NAME
    assert str(error) == f"{DB_NAME}: schema_mismatch"
    assert_sanitized(error, tmp_path, SENTINEL, "0001-01-01", "2026-11-02", "20261102")
    assert_no_chained_cause(error)
    assert sha256(target) == before


# --- D54: schema mismatch and open failure --------------------------------------------


def plain_database(path: Path, *statements: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    edit(path, *statements)


MISMATCH = {
    "no_metadata_table": ("CREATE TABLE t (x INTEGER)", "INSERT INTO t VALUES (1)"),
    "version_missing": (
        "CREATE TABLE preparation_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
        "INSERT INTO preparation_metadata VALUES ('prepared_at', '2026-11-02T12:00:00+00:00')",
    ),
    "version_other": (
        "CREATE TABLE preparation_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
        "INSERT INTO preparation_metadata VALUES ('schema_version', '2')",
    ),
    "version_sentinel": (
        "CREATE TABLE preparation_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
        f"INSERT INTO preparation_metadata VALUES ('schema_version', '{SENTINEL}')",
    ),
    "version_integer": (
        "CREATE TABLE preparation_metadata (key, value)",
        "INSERT INTO preparation_metadata VALUES ('schema_version', 1)",
    ),
    "value_column_missing": ("CREATE TABLE preparation_metadata (key TEXT)",),
}


@pytest.mark.parametrize("statements", MISMATCH.values(), ids=MISMATCH.keys())
def test_d54_schema_mismatch(tmp_path, statements, connect_recorder):
    target = tmp_path / PRIVATE_DIR / DB_NAME
    plain_database(target, *statements)
    connect_recorder.clear()
    before_hash, before_list = sha256(target), listing(target.parent)
    error = unavailable(target)
    assert error.reason == "schema_mismatch"
    assert error.database_name == DB_NAME
    assert str(error) == f"{DB_NAME}: schema_mismatch"
    assert_sanitized(error, tmp_path, SENTINEL)
    assert_no_chained_cause(error)
    assert sha256(target) == before_hash
    assert listing(target.parent) == before_list
    assert len(connect_recorder) == 1
    assert_closed(connect_recorder[0][2])


def test_d54_prepared_database_with_other_version(tmp_path, prep_kit, connect_recorder):
    target, _ = prepared(tmp_path, prep_kit)
    edit(target, "UPDATE preparation_metadata SET value = '2' WHERE key = 'schema_version'")
    connect_recorder.clear()
    before = sha256(target)
    error = unavailable(target)
    assert error.reason == "schema_mismatch"
    assert sha256(target) == before
    assert_closed(connect_recorder[0][2])


def test_d54_missing_table_uses_from_none(tmp_path):
    target = tmp_path / PRIVATE_DIR / DB_NAME
    plain_database(target, "CREATE TABLE t (x INTEGER)")
    error = unavailable(target)
    assert error.__cause__ is None
    assert error.__suppress_context__ is True


def test_d54_empty_file_is_schema_mismatch(tmp_path, connect_recorder):
    target = tmp_path / PRIVATE_DIR / DB_NAME
    target.parent.mkdir()
    target.write_bytes(b"")
    error = unavailable(target)
    assert error.reason == "schema_mismatch"
    assert target.read_bytes() == b""
    assert listing(target.parent) == [DB_NAME]
    assert_closed(connect_recorder[0][2])


def test_d54_non_sqlite_file_is_open_failed(tmp_path, connect_recorder):
    target = tmp_path / PRIVATE_DIR / DB_NAME
    target.parent.mkdir()
    target.write_bytes((f"{SENTINEL} not a database\n" * 40).encode())
    before_hash, before_list = sha256(target), listing(target.parent)
    error = unavailable(target)
    assert error.reason == "open_failed"
    assert error.database_name == DB_NAME
    assert str(error) == f"{DB_NAME}: open_failed"
    assert error.__cause__ is None
    assert error.__suppress_context__ is True
    assert_sanitized(error, tmp_path, SENTINEL, "file is not a database")
    assert sha256(target) == before_hash
    assert listing(target.parent) == before_list
    assert len(connect_recorder) == 1
    assert_closed(connect_recorder[0][2])


# --- D55: paths -----------------------------------------------------------------------

RESERVED = [
    "a#b",
    "100%41",
    "x&y=z",
    "semi;colon",
    "%23",
    pytest.param(
        "q?mode=rwc",
        marks=pytest.mark.skipif(
            sys.platform == "win32", reason="Windows does not allow '?' in file names"
        ),
    ),
]


def test_d55_path_with_space_and_non_ascii(tmp_path, prep_kit):
    target, result = prepared(tmp_path, prep_kit, directory="dados ação", name="banco ç.sqlite3")
    with connect_readonly(target) as connection:
        info = read_preparation_info(connection)
    assert info.sources == result.sources
    assert listing(target.parent) == ["banco ç.sqlite3"]


@pytest.mark.parametrize("chars", RESERVED)
def test_d55_reserved_uri_characters(tmp_path, prep_kit, chars):
    target, result = prepared(tmp_path, prep_kit, directory=f"dir {chars}", name=f"db{chars}.db")
    before_top, before_dir = listing(tmp_path), listing(target.parent)
    with connect_readonly(target) as connection:
        info = read_preparation_info(connection)
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("DELETE FROM orders")
    assert info.sources == result.sources
    assert listing(tmp_path) == before_top
    assert listing(target.parent) == before_dir


@pytest.mark.parametrize("chars", RESERVED)
def test_d55_missing_path_with_reserved_characters(tmp_path, chars):
    target = tmp_path / f"dir {chars}" / f"db{chars}.db"
    error = unavailable(target)
    assert error.reason == "missing"
    assert error.database_name == f"db{chars}.db"
    assert listing(tmp_path) == []
