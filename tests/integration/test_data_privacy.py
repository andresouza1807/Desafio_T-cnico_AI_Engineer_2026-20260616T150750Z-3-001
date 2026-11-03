"""Privacy tests for task T7 (docs/specs/02-08-data-privacy.md).

Cases D60, D61 and D70-D72. The static synthetic fixture tests/fixtures/data/valid is
copied into pytest tmp_path before any edit or import; the static files are never
written. Every test uses real files, real SQLite and the real prepare_database and
connect_readonly. Expected values are literals here or are read from the fixture bytes
with the standard library. The only patch is prep_build_spy in D61: it calls the real
sqlite3.connect, installs a real SQLite authorizer and is undone before verification.
Full tracebacks and __context__ are outside the guarantee and are not asserted.
"""

import csv
import dataclasses
import hashlib
import importlib.metadata
import io
import os
import shutil
import sqlite3
import traceback
from datetime import UTC, datetime
from pathlib import Path

import pytest

from emporio_agent.data.errors import DataPreparationError
from emporio_agent.data.preparation import PreparationResult, prepare_database
from emporio_agent.data.readonly import DatabaseUnavailableError, connect_readonly

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "data" / "valid"
PREFIX = "desafio_tecnico_ai_eng - "
TABLES = ("categories", "customers", "products", "promotions", "orders", "order_items")
PRIVATE_DIR = "pasta-privada-t7"
DB_NAME = "emporio.sqlite3"
SENTINEL = "SENTINEL-PII-7781"
WHEN = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)

EXPECTED_FILE_NAMES = {table: f"{PREFIX}{table}.csv" for table in TABLES}
EXPECTED_ROW_COUNTS = {
    "categories": 2,
    "customers": 2,
    "products": 4,
    "promotions": 3,
    "orders": 2,
    "order_items": 3,
}
EXPECTED_JOIN = [(1000, 1, 10), (1000, 1, 11), (1001, 2, 12)]
EXPECTED_QUALITY_NOTES = 2
KNOWN_NAMES = frozenset({"Cliente Teste Um", "Cliente Teste Dois"})
RESULT_FIELDS = (
    "database_path",
    "schema_version",
    "prepared_at",
    "sources",
    "row_counts",
    "quality_note_count",
)


# --- helpers -------------------------------------------------------------------------


def fixture_bytes(table: str) -> bytes:
    return (FIXTURE_DIR / EXPECTED_FILE_NAMES[table]).read_bytes()


def fixture_hashes() -> dict[str, str]:
    return {
        name: hashlib.sha256((FIXTURE_DIR / name).read_bytes()).hexdigest()
        for name in sorted(os.listdir(FIXTURE_DIR))
    }


def fixture_customers() -> list[dict[str, str]]:
    text = fixture_bytes("customers").decode("utf-8")
    return list(csv.DictReader(io.StringIO(text, newline="")))


def copy_fixture(tmp_path: Path) -> Path:
    source_dir = tmp_path / PRIVATE_DIR / "fontes"
    shutil.copytree(FIXTURE_DIR, source_dir)
    return source_dir


def target_path(tmp_path: Path) -> Path:
    directory = tmp_path / PRIVATE_DIR / "db"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / DB_NAME


def replace_once(path: Path, old: str, new: str) -> None:
    data = path.read_bytes()
    assert data.count(old.encode("utf-8")) == 1
    path.write_bytes(data.replace(old.encode("utf-8"), new.encode("utf-8")))


def path_markers(tmp_path: Path) -> tuple[str, ...]:
    return (str(tmp_path), str(tmp_path.resolve()), PRIVATE_DIR)


def public_surfaces(error: BaseException) -> list[str]:
    return [
        str(error),
        repr(error),
        *(str(arg) for arg in error.args),
        "".join(traceback.format_exception_only(error)),
    ]


def preparation_attributes(error: DataPreparationError) -> list[str]:
    return [str(error.code), str(error.file_name), str(error.line), str(error.column)]


def assert_absent(texts: list[str], *forbidden: str) -> None:
    for text in texts:
        for value in forbidden:
            assert value not in text


def preparation_error(source_dir: Path, target: Path) -> DataPreparationError:
    with pytest.raises(DataPreparationError) as info:
        prepare_database(source_dir, target, clock=lambda: WHEN)
    return info.value


def unavailable(path: Path) -> DatabaseUnavailableError:
    with pytest.raises(DatabaseUnavailableError) as info:
        with connect_readonly(path):
            pytest.fail("block must not run")
    return info.value


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def independent_rows(database: Path, sql: str) -> list[tuple[object, ...]]:
    connection = sqlite3.connect(database)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


# --- D60: errors carry location, not values ------------------------------------------


@pytest.mark.parametrize(
    ("table", "old", "new", "code", "line", "column"),
    [
        (
            "customers",
            "1,Cliente Teste Um,",
            f"{SENTINEL},Cliente Teste Um,",
            "invalid_integer",
            2,
            "customer_id",
        ),
        (
            "products",
            ",1200,",
            f",{SENTINEL},",
            "invalid_money",
            4,
            "price_brl",
        ),
    ],
    ids=["customers_customer_id", "products_price_brl"],
)
def test_d60_sentinel_value_not_in_error(tmp_path, table, old, new, code, line, column):
    source_dir = copy_fixture(tmp_path)
    replace_once(source_dir / EXPECTED_FILE_NAMES[table], old, new)
    target = target_path(tmp_path)

    error = preparation_error(source_dir, target)

    file_name = EXPECTED_FILE_NAMES[table]
    assert error.code == code
    assert error.file_name == file_name
    assert error.line == line
    assert error.column == column
    assert str(error) == f"{file_name} line {line}, column {column}: {code}"
    assert error.args == (str(error),)
    surfaces = public_surfaces(error) + preparation_attributes(error)
    assert_absent(surfaces, SENTINEL, *path_markers(tmp_path))
    assert file_name in "".join(traceback.format_exception_only(error))
    assert error.__cause__ is None
    assert error.__suppress_context__ is True
    assert not target.exists()


# --- D61: no directory path in errors or metadata -------------------------------------


def test_d61_persisted_provenance_has_no_directory(tmp_path):
    source_dir = copy_fixture(tmp_path)
    target = target_path(tmp_path)

    prepare_database(source_dir, target, clock=lambda: WHEN)

    sources = independent_rows(target, "SELECT table_name, file_name FROM source_files")
    assert dict(sources) == EXPECTED_FILE_NAMES
    metadata = dict(independent_rows(target, "SELECT key, value FROM preparation_metadata"))
    assert metadata == {
        "schema_version": "1",
        "prepared_at": "2026-11-02T12:00:00+00:00",
        "importer_version": importlib.metadata.version("emporio-agent"),
    }
    texts = [name for _table, name in sources] + list(metadata.values())
    assert_absent(texts, "/", "\\", *path_markers(tmp_path))


def test_d61_source_dir_missing_has_no_directory(tmp_path):
    target = target_path(tmp_path)

    error = preparation_error(tmp_path / PRIVATE_DIR / "ausente", target)

    assert error.code == "source_dir_missing"
    assert (error.file_name, error.line, error.column) == (None, None, None)
    assert str(error) == "source_dir_missing"
    assert error.args == (str(error),)
    assert error.__cause__ is None
    assert_absent(public_surfaces(error) + preparation_attributes(error), *path_markers(tmp_path))


def test_d61_file_missing_has_no_directory(tmp_path, prep_kit):
    source_dir = copy_fixture(tmp_path)
    (source_dir / prep_kit.file_name("order_items")).unlink()
    target = target_path(tmp_path)

    error = preparation_error(source_dir, target)

    assert error.code == "file_missing"
    assert error.file_name == "desafio_tecnico_ai_eng - order_items.csv"
    assert (error.line, error.column) == (None, None)
    assert str(error) == "desafio_tecnico_ai_eng - order_items.csv: file_missing"
    assert error.args == (str(error),)
    assert error.__cause__ is None
    assert_absent(public_surfaces(error) + preparation_attributes(error), *path_markers(tmp_path))
    assert not target.exists()


def test_d61_build_failure_has_no_directory(tmp_path, prep_build_spy):
    source_dir = copy_fixture(tmp_path)
    target = target_path(tmp_path)
    spy = prep_build_spy.install(target.parent, deny_insert_into="order_items")

    try:
        error = preparation_error(source_dir, target)
    finally:
        prep_build_spy.undo()

    assert len(spy.connects) == 1
    assert spy.denied >= 1
    temporary = spy.connects[0][0]
    assert error.code == "database_build_failed"
    assert (error.file_name, error.line, error.column) == (None, None, None)
    assert str(error) == "database_build_failed"
    assert error.args == (str(error),)
    assert error.__cause__ is None
    surfaces = public_surfaces(error) + preparation_attributes(error)
    assert_absent(surfaces, temporary, os.path.basename(temporary), *path_markers(tmp_path))
    assert not target.exists()
    assert os.listdir(target.parent) == []
    assert not os.path.exists(temporary)
    assert not os.path.exists(temporary + "-journal")


# --- D70: public fixtures are synthetic -----------------------------------------------


def test_d70_fixture_directory_holds_six_files():
    assert sorted(os.listdir(FIXTURE_DIR)) == sorted(EXPECTED_FILE_NAMES.values())


def test_d70_customer_contacts_are_fictional():
    text = fixture_bytes("customers").decode("utf-8")
    header = next(csv.reader(io.StringIO(text, newline="")))
    assert header == ["customer_id", "name", "phone", "email", "city"]
    customers = fixture_customers()
    assert customers
    for customer in customers:
        assert customer["name"] in KNOWN_NAMES
        assert customer["email"].endswith("@example.invalid")
        assert customer["phone"].startswith("(00)")


def test_d70_fixture_is_importable_with_valid_relations(tmp_path):
    before = fixture_hashes()
    source_dir = copy_fixture(tmp_path)
    target = target_path(tmp_path)

    prepare_database(source_dir, target, clock=lambda: WHEN)

    for table, count in EXPECTED_ROW_COUNTS.items():
        assert independent_rows(target, f"SELECT count(*) FROM {table}") == [(count,)]
    assert independent_rows(target, "PRAGMA foreign_key_check") == []
    join = independent_rows(
        target,
        "SELECT o.order_id, c.customer_id, p.product_id FROM order_items i "
        "JOIN orders o ON o.order_id = i.order_id "
        "JOIN customers c ON c.customer_id = o.customer_id "
        "JOIN products p ON p.product_id = i.product_id "
        "ORDER BY o.order_id, p.product_id",
    )
    assert join == EXPECTED_JOIN
    assert fixture_hashes() == before


# --- D71: read-only errors hide paths -------------------------------------------------


def assert_readonly_error(error: DatabaseUnavailableError, reason: str) -> None:
    message = f"{DB_NAME}: {reason}"
    assert error.reason == reason
    assert error.database_name == DB_NAME
    assert str(error) == message
    assert error.args == (message,)
    assert repr(error) == f"DatabaseUnavailableError({message!r})"
    assert traceback.format_exception_only(error) == [
        f"emporio_agent.data.readonly.DatabaseUnavailableError: {message}\n"
    ]
    assert error.__cause__ is None


def test_d71_missing_database_hides_directory(tmp_path):
    target = tmp_path / PRIVATE_DIR / DB_NAME
    before = sorted(os.listdir(tmp_path))

    error = unavailable(target)

    assert_readonly_error(error, "missing")
    surfaces = public_surfaces(error) + [error.reason, str(error.database_name)]
    assert_absent(surfaces, *path_markers(tmp_path))
    assert not target.exists()
    assert not target.parent.exists()
    assert sorted(os.listdir(tmp_path)) == before


def test_d71_database_without_metadata_hides_directory_and_sqlite_message(tmp_path):
    target = tmp_path / PRIVATE_DIR / DB_NAME
    target.parent.mkdir()
    connection = sqlite3.connect(target)
    try:
        connection.execute("CREATE TABLE t (x TEXT)")
        connection.execute("INSERT INTO t (x) VALUES (?)", (SENTINEL,))
        connection.commit()
    finally:
        connection.close()
    before_hash = sha256_file(target)
    before_listing = sorted(os.listdir(target.parent))

    error = unavailable(target)

    assert_readonly_error(error, "schema_mismatch")
    surfaces = public_surfaces(error) + [error.reason, str(error.database_name)]
    assert_absent(
        surfaces, SENTINEL, "no such table", "preparation_metadata", *path_markers(tmp_path)
    )
    assert sha256_file(target) == before_hash
    assert sorted(os.listdir(target.parent)) == before_listing


# --- D72: result object has no cell values or paths -----------------------------------


def test_d72_result_has_no_customer_values_or_path(tmp_path):
    customers = fixture_customers()
    contacts = [c[key] for c in customers for key in ("name", "phone", "email")]
    assert len(contacts) == 6
    assert all(contacts)
    source_dir = copy_fixture(tmp_path)
    target = target_path(tmp_path)

    result = prepare_database(source_dir, target, clock=lambda: WHEN)

    text = repr(result)
    assert_absent([text], *contacts, str(target), DB_NAME, *path_markers(tmp_path))
    assert result.database_path is target


def test_d72_other_fields_hold_only_contract_data(tmp_path):
    customers = fixture_customers()
    contacts = [c[key] for c in customers for key in ("name", "phone", "email")]
    source_dir = copy_fixture(tmp_path)
    target = target_path(tmp_path)

    result = prepare_database(source_dir, target, clock=lambda: WHEN)

    assert tuple(f.name for f in dataclasses.fields(PreparationResult)) == RESULT_FIELDS
    assert result.schema_version == 1
    assert result.prepared_at == WHEN
    assert dict(result.row_counts) == EXPECTED_ROW_COUNTS
    assert result.quality_note_count == EXPECTED_QUALITY_NOTES
    assert [s.table_name for s in result.sources] == list(TABLES)
    for source in result.sources:
        data = fixture_bytes(source.table_name)
        assert source.file_name == EXPECTED_FILE_NAMES[source.table_name]
        assert source.sha256 == hashlib.sha256(data).hexdigest()
        assert source.size_bytes == len(data)
        assert source.row_count == EXPECTED_ROW_COUNTS[source.table_name]
    others = [str(getattr(result, name)) for name in RESULT_FIELDS if name != "database_path"]
    assert_absent(others, *contacts, str(target), DB_NAME, *path_markers(tmp_path))
