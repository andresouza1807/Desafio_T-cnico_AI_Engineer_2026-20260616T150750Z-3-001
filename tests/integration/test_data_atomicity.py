"""Build-and-replace atomicity tests for task T5 (docs/specs/02-06-database-preparation.md).

Every test uses real SQLite files in pytest tmp_path. State is verified through the
SHA-256 of the target bytes, directory listings and independent connections opened
after any patch is undone. Construction failures are produced by a real SQLite
authorizer that denies INSERT on order_items; replace failures by a real open handle.
The integrity failure test calls the build helper directly with inconsistent readings
and a real authorizer that ignores only the foreign_keys PRAGMA; the PRAGMA checks,
transaction, rollback and file state stay real. These tests cover handled failures
only. They prove nothing about process kills, OS crashes, power loss or durability
of the rename.
"""

import dataclasses
import hashlib
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from emporio_agent.data.errors import DataPreparationError, IssueCode
from emporio_agent.data.preparation import (
    _write_database,
    prepare_database,
    read_validated_tables,
)
from emporio_agent.data.schema import TABLES as TABLE_SPECS

FIRST = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
SECOND = datetime(2026, 11, 3, 8, 30, tzinfo=UTC)
TABLES = (
    "categories",
    "customers",
    "products",
    "promotions",
    "orders",
    "order_items",
    "preparation_metadata",
    "source_files",
    "data_quality_notes",
)
TARGET_NAME = "emporio.sqlite3"


# --- helpers -------------------------------------------------------------------------


def layout(tmp_path: Path) -> tuple[Path, Path]:
    source_dir = tmp_path / "sources"
    target_dir = tmp_path / "out"
    target_dir.mkdir()
    return source_dir, target_dir / TARGET_NAME


def prepare(source_dir: Path, target: Path, when: datetime = FIRST):
    return prepare_database(source_dir, target, clock=lambda: when)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(path: Path) -> dict[str, list[tuple[object, ...]]]:
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        return {
            table: connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
            for table in TABLES
        }
    finally:
        connection.close()


def listing(directory: Path) -> list[str]:
    return sorted(path.name for path in directory.iterdir())


def build_error(source_dir: Path, target: Path) -> DataPreparationError:
    with pytest.raises(DataPreparationError) as caught:
        prepare(source_dir, target, SECOND)
    return caught.value


def assert_build_failure(error: DataPreparationError, code: IssueCode, tmp_path: Path) -> None:
    assert error.code is code
    assert (error.file_name, error.line, error.column) == (None, None, None)
    assert error.__cause__ is None
    assert error.__suppress_context__ is True
    assert str(error) == code.value
    assert error.args == (code.value,)
    for text in (str(error), repr(error)):
        assert str(tmp_path) not in text
        assert "not authorized" not in text


# --- repetition and rebuild ----------------------------------------------------------


def test_d02_second_import_without_duplication(tmp_path, prep_kit):
    source_dir, target = layout(tmp_path)
    prep_kit.write_sources(source_dir)

    first_result = prepare(source_dir, target, FIRST)
    first = snapshot(target)
    second_result = prepare(source_dir, target, SECOND)
    second = snapshot(target)

    metadata_first = dict(first.pop("preparation_metadata"))
    metadata_second = dict(second.pop("preparation_metadata"))
    assert first == second
    assert metadata_first.pop("prepared_at") == FIRST.isoformat()
    assert metadata_second.pop("prepared_at") == SECOND.isoformat()
    assert metadata_first == metadata_second
    assert first_result.row_counts == second_result.row_counts
    assert len(second["products"]) == 4
    assert len(second["order_items"]) == 3
    assert listing(target.parent) == [TARGET_NAME]


def test_d45_rebuild_reflects_new_sources(tmp_path, prep_kit):
    source_dir, target = layout(tmp_path)
    prep_kit.write_sources(source_dir)
    prepare(source_dir, target, FIRST)
    data = prep_kit.valid_rows()
    prep_kit.set_cell(data, "products", 0, "price_brl", "649.9")
    data["customers"].append(
        ["3", "Cliente Teste Três", "(00) 90000-0003", "cliente03@example.invalid", "Natal"]
    )
    written = prep_kit.write_sources(source_dir, data)

    result = prepare(source_dir, target, SECOND)

    state = snapshot(target)
    products = {row[0]: row for row in state["products"]}
    assert products[10][4] == 64990
    assert len(state["products"]) == 4
    assert [row[0] for row in state["customers"]] == [1, 2, 3]
    assert result.row_counts["customers"] == 3
    hashes = dict((row[0], row[2]) for row in state["source_files"])
    assert hashes["products"] == hashlib.sha256(written["products"]).hexdigest()
    assert dict(state["preparation_metadata"])["prepared_at"] == SECOND.isoformat()
    assert listing(target.parent) == [TARGET_NAME]


def test_closed_handles_allow_replace_reopen_and_delete(tmp_path, prep_kit):
    source_dir, target = layout(tmp_path)
    prep_kit.write_sources(source_dir)

    prepare(source_dir, target, FIRST)
    snapshot(target)
    prepare(source_dir, target, SECOND)
    assert dict(snapshot(target)["preparation_metadata"])["prepared_at"] == SECOND.isoformat()

    # On Windows, deleting fails while any handle to the file is open.
    target.unlink()
    assert listing(target.parent) == []


# --- validation failures -------------------------------------------------------------


def test_d40_initial_failure_leaves_no_database(tmp_path, prep_kit):
    source_dir, target = layout(tmp_path)
    data = prep_kit.valid_rows()
    prep_kit.set_cell(data, "order_items", 2, "product_id", "99")
    prep_kit.write_sources(source_dir, data)

    with pytest.raises(DataPreparationError) as caught:
        prepare(source_dir, target)

    assert caught.value.code is IssueCode.BROKEN_REFERENCE
    assert not target.exists()
    assert listing(target.parent) == []


@pytest.mark.parametrize(
    ("table", "edit", "code"),
    [
        ("order_items", "duplicate_pair", IssueCode.DUPLICATE_KEY),
        ("categories", "invalid_id", IssueCode.INVALID_INTEGER),
    ],
    ids=["d41_late_table", "d42_early_table"],
)
def test_d41_d42_validation_failure_keeps_previous_database(tmp_path, prep_kit, table, edit, code):
    source_dir, target = layout(tmp_path)
    prep_kit.write_sources(source_dir)
    prepare(source_dir, target, FIRST)
    digest = sha256(target)
    before = snapshot(target)
    data = prep_kit.valid_rows()
    if edit == "duplicate_pair":
        data["order_items"].append(["1000", "4", "10"])
    else:
        prep_kit.set_cell(data, "categories", 0, "category_id", "x")
    prep_kit.write_sources(source_dir, data)

    with pytest.raises(DataPreparationError) as caught:
        prepare(source_dir, target, SECOND)

    assert caught.value.code is code
    assert caught.value.file_name == prep_kit.file_name(table)
    assert sha256(target) == digest
    assert snapshot(target) == before
    assert listing(target.parent) == [TARGET_NAME]


def test_d43_missing_target_directory(tmp_path, prep_kit, prep_forbid_build):
    source_dir = tmp_path / "sources"
    prep_kit.write_sources(source_dir)
    target = tmp_path / "missing" / TARGET_NAME

    with pytest.raises(DataPreparationError) as caught:
        prepare(source_dir, target)

    error = caught.value
    assert error.code is IssueCode.TARGET_DIRECTORY_MISSING
    assert (error.file_name, error.line, error.column) == (None, None, None)
    assert str(tmp_path) not in str(error)
    assert not target.parent.exists()
    assert prep_forbid_build.connect == [] and prep_forbid_build.mkstemp == []


def test_d46_naive_clock_keeps_previous_database(tmp_path, prep_kit, prep_build_spy):
    source_dir, target = layout(tmp_path)
    prep_kit.write_sources(source_dir)
    prepare(source_dir, target, FIRST)
    digest = sha256(target)
    before = snapshot(target)
    spy = prep_build_spy.install(target.parent)

    with pytest.raises(ValueError):
        prepare(source_dir, target, datetime(2026, 11, 3, 8, 30))
    prep_build_spy.undo()

    assert spy.connects == []
    assert sha256(target) == digest
    assert snapshot(target) == before
    assert listing(target.parent) == [TARGET_NAME]


# --- construction failures (real authorizer) -----------------------------------------


def test_d47_construction_failure_keeps_previous_database(tmp_path, prep_kit, prep_build_spy):
    source_dir, target = layout(tmp_path)
    prep_kit.write_sources(source_dir)
    prepare(source_dir, target, FIRST)
    digest = sha256(target)
    before = snapshot(target)
    spy = prep_build_spy.install(target.parent, deny_insert_into="order_items")

    error = build_error(source_dir, target)
    prep_build_spy.undo()

    assert_build_failure(error, IssueCode.DATABASE_BUILD_FAILED, tmp_path)
    assert spy.denied == 1
    assert len(spy.connects) == 1
    temporary, existed = spy.connects[0]
    assert existed
    assert not Path(temporary).exists()
    assert not Path(temporary + "-journal").exists()
    transactions = [a1 for kind, a1, _ in spy.actions if kind == "transaction"]
    assert transactions == ["BEGIN", "ROLLBACK"]
    assert listing(target.parent) == [TARGET_NAME]
    assert sha256(target) == digest
    assert snapshot(target) == before
    assert dict(snapshot(target)["preparation_metadata"])["prepared_at"] == FIRST.isoformat()


def test_d48_construction_failure_without_previous_database(tmp_path, prep_kit, prep_build_spy):
    source_dir, target = layout(tmp_path)
    prep_kit.write_sources(source_dir)
    spy = prep_build_spy.install(target.parent, deny_insert_into="order_items")

    error = build_error(source_dir, target)
    prep_build_spy.undo()

    assert_build_failure(error, IssueCode.DATABASE_BUILD_FAILED, tmp_path)
    assert spy.denied == 1
    temporary, existed = spy.connects[0]
    assert existed
    assert not Path(temporary).exists()
    assert not Path(temporary + "-journal").exists()
    assert [a1 for kind, a1, _ in spy.actions if kind == "transaction"] == ["BEGIN", "ROLLBACK"]
    assert not target.exists()
    assert listing(target.parent) == []


def test_construction_failure_leaves_other_files_untouched(tmp_path, prep_kit, prep_build_spy):
    source_dir, target = layout(tmp_path)
    prep_kit.write_sources(source_dir)
    other = target.parent / f".{TARGET_NAME}.foreign.tmp"
    other.write_bytes(b"not ours")
    prep_build_spy.install(target.parent, deny_insert_into="order_items")

    build_error(source_dir, target)
    prep_build_spy.undo()

    assert listing(target.parent) == [other.name]
    assert other.read_bytes() == b"not ours"


# --- replace failure -----------------------------------------------------------------


def test_d44_replace_failure_with_open_handle(tmp_path, prep_kit):
    source_dir, target = layout(tmp_path)
    prep_kit.write_sources(source_dir)
    prepare(source_dir, target, FIRST)
    digest = sha256(target)
    before = snapshot(target)
    data = prep_kit.valid_rows()
    prep_kit.set_cell(data, "products", 0, "price_brl", "649.9")
    prep_kit.write_sources(source_dir, data)

    holder = sqlite3.connect(target)
    try:
        holder.execute("SELECT count(*) FROM products").fetchall()
        try:
            prepare(source_dir, target, SECOND)
        except DataPreparationError as caught:
            error = caught
        else:
            error = None
    finally:
        holder.close()

    if error is None:
        pytest.skip(
            f"platform {sys.platform} replaced a file held open by another connection; "
            "target_write_failed not demonstrated here"
        )
    assert_build_failure(error, IssueCode.TARGET_WRITE_FAILED, tmp_path)
    assert sha256(target) == digest
    assert snapshot(target) == before
    assert listing(target.parent) == [TARGET_NAME]


# --- integrity check failure (helper, real authorizer) -------------------------------


def inconsistent_readings(source_dir: Path, prep_kit):
    """Valid readings with one order_items row pointing to a product that does not exist."""
    prep_kit.write_sources(source_dir)
    readings = list(read_validated_tables(source_dir))
    index = [spec.name for spec in TABLE_SPECS].index("order_items")
    spec = TABLE_SPECS[index]
    position = [column.column_name for column in spec.columns].index("product_id")
    first, *rest = readings[index].records
    values = list(first.values)
    values[position] = 99
    broken = dataclasses.replace(first, values=tuple(values))
    readings[index] = dataclasses.replace(readings[index], records=(broken, *rest))
    return tuple(readings)


def test_integrity_check_failure_from_real_foreign_key_finding(tmp_path, prep_kit, monkeypatch):
    readings = inconsistent_readings(tmp_path / "sources", prep_kit)
    metadata = (("schema_version", "1"),)
    real_connect = sqlite3.connect
    actions: list[tuple[str, str | None, str | None]] = []
    ignored: list[str | None] = []

    def authorizer(action, arg1, arg2, _database, _source):
        if action == sqlite3.SQLITE_PRAGMA:
            actions.append(("pragma", arg1, arg2))
            if arg1 == "foreign_keys":
                ignored.append(arg2)
                return sqlite3.SQLITE_IGNORE
        elif action == sqlite3.SQLITE_TRANSACTION:
            actions.append(("transaction", arg1, arg2))
        return sqlite3.SQLITE_OK

    def connect(database, *args, **kwargs):
        connection = real_connect(database, *args, **kwargs)
        connection.set_authorizer(authorizer)
        return connection

    temporary = tmp_path / "build.tmp"
    temporary.write_bytes(b"")
    monkeypatch.setattr(sqlite3, "connect", connect)
    with pytest.raises(DataPreparationError) as caught:
        _write_database(str(temporary), readings, metadata, ())
    monkeypatch.setattr(sqlite3, "connect", real_connect)

    assert_build_failure(caught.value, IssueCode.INTEGRITY_CHECK_FAILED, tmp_path)
    assert ignored == ["ON"]
    pragmas = [a1 for kind, a1, _ in actions if kind == "pragma"]
    assert "foreign_key_check" in pragmas and "integrity_check" in pragmas
    assert [a1 for kind, a1, _ in actions if kind == "transaction"] == ["BEGIN", "ROLLBACK"]
    # Rollback is real: nothing was persisted in the temporary file.
    check = real_connect(temporary)
    try:
        assert check.execute("SELECT name FROM sqlite_schema").fetchall() == []
    finally:
        check.close()

    # Control: with foreign_keys really on, SQLite rejects the same readings at insert.
    control = tmp_path / "control.tmp"
    control.write_bytes(b"")
    with pytest.raises(sqlite3.IntegrityError):
        _write_database(str(control), readings, metadata, ())
