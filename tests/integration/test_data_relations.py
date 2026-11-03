"""Key uniqueness and reference tests for task T4 (docs/specs/02-05-data-relations.md).

Every test writes six small synthetic CSV files into pytest tmp_path and runs the real
reader and validator. Expected values, lines and columns are written by hand from the
specification. An audit hook only counts file opens; sqlite3.connect is only recorded
or forbidden. Nothing simulates a valid relation.
"""

import copy
import hashlib
import os
import sqlite3
import sys
import traceback
from pathlib import Path

import pytest

from emporio_agent.data.errors import DataPreparationError, IssueCode
from emporio_agent.data.preparation import TableReading, read_table, read_validated_tables
from emporio_agent.data.schema import TABLES

PREFIX = "desafio_tecnico_ai_eng - "
SENTINEL = "SENTINEL-PII-7781"
TABLE_NAMES = ("categories", "customers", "products", "promotions", "orders", "order_items")

HEADERS = {
    "categories": ["category_id", "name", "description"],
    "customers": ["customer_id", "name", "phone", "email", "city"],
    "products": [
        "product_id",
        "category_id",
        "name",
        "description",
        "price_brl",
        "stock_quantity",
        "status",
        "specs",
        "created_at",
    ],
    "promotions": ["promotion_id", "product_id", "discount_percent", "description", "is_active"],
    "orders": [
        "order_id",
        "customer_id",
        "order_date",
        "status",
        "total_brl",
        "payment_method",
        "tracking_code",
        "estimated_delivery",
        "notes",
    ],
    # Same column order as the supplied source file; header order is free.
    "order_items": ["order_id", "quantity", "product_id"],
}

# Category 3, customer 3 and order 1002 have no children. Customers 1 and 2 share an
# email. Order 1000 has two distinct products. Order 1000 total differs on purpose from
# current price x quantity (599.90 + 2 x 19.90 = 639.70).
ROWS = {
    "categories": [
        ["1", "Cordas", "Violões, guitarras e baixos"],
        ["2", "Teclas", "Pianos digitais"],
        ["3", "Sopros", "Flautas e saxofones"],
    ],
    "customers": [
        ["1", "Cliente Teste Um", "(00) 90000-0001", "compartilhado@example.invalid", "São Paulo"],
        ["2", "Cliente Teste Dois", "(00) 90000-0002", "compartilhado@example.invalid", "Recife"],
        ["3", "Cliente Teste Três", "(00) 90000-0003", "cliente03@example.invalid", "Curitiba"],
    ],
    "products": [
        [
            "10",
            "1",
            "Violão Teste",
            "Tampo maciço, cordas de aço",
            "599.9",
            "3",
            "active",
            '{"cor": "natural"}',
            "2026-01-15",
        ],
        ["11", "1", "Encordoamento Teste", "Aço", "19.90", "0", "active", "{}", "2026-01-16"],
        [
            "12",
            "2",
            "Piano Teste",
            "88 teclas",
            "1200",
            "2",
            "discontinued",
            '{"teclas": 88}',
            "2025-12-01",
        ],
    ],
    "promotions": [
        ["100", "10", "10", "Promo Teste", "1"],
        ["101", "11", "12.5", "Campanha Teste", "0"],
    ],
    "orders": [
        [
            "1000",
            "1",
            "2026-01-10",
            "delivered",
            "650.00",
            "pix",
            "TRK0001",
            "2026-01-15",
            "",
        ],
        [
            "1001",
            "2",
            "2026-02-01",
            "cancelled",
            "1200.00",
            "credit_3x",
            "",
            "",
            "Cancelado pelo cliente",
        ],
        ["1002", "1", "2026-03-05", "pending", "19.90", "boleto", "", "", ""],
    ],
    "order_items": [
        ["1000", "1", "10"],
        ["1000", "2", "11"],
        ["1001", "1", "12"],
    ],
}

# Parsed values in schema column order, written by hand.
EXPECTED = {
    "categories": [
        (1, "Cordas", "Violões, guitarras e baixos"),
        (2, "Teclas", "Pianos digitais"),
        (3, "Sopros", "Flautas e saxofones"),
    ],
    "customers": [
        (1, "Cliente Teste Um", "(00) 90000-0001", "compartilhado@example.invalid", "São Paulo"),
        (2, "Cliente Teste Dois", "(00) 90000-0002", "compartilhado@example.invalid", "Recife"),
        (3, "Cliente Teste Três", "(00) 90000-0003", "cliente03@example.invalid", "Curitiba"),
    ],
    "products": [
        (
            10,
            1,
            "Violão Teste",
            "Tampo maciço, cordas de aço",
            59990,
            3,
            "active",
            '{"cor": "natural"}',
            "2026-01-15",
        ),
        (11, 1, "Encordoamento Teste", "Aço", 1990, 0, "active", "{}", "2026-01-16"),
        (
            12,
            2,
            "Piano Teste",
            "88 teclas",
            120000,
            2,
            "discontinued",
            '{"teclas": 88}',
            "2025-12-01",
        ),
    ],
    "promotions": [
        (100, 10, 1000, "Promo Teste", 1),
        (101, 11, 1250, "Campanha Teste", 0),
    ],
    "orders": [
        (1000, 1, "2026-01-10", "delivered", 65000, "pix", "TRK0001", "2026-01-15", None),
        (
            1001,
            2,
            "2026-02-01",
            "cancelled",
            120000,
            "credit_3x",
            None,
            None,
            "Cancelado pelo cliente",
        ),
        (1002, 1, "2026-03-05", "pending", 1990, "boleto", None, None, None),
    ],
    "order_items": [
        (1000, 10, 1),
        (1000, 11, 2),
        (1001, 12, 1),
    ],
}


# --- helpers -------------------------------------------------------------------------


def file_name(table: str, prefix: str = PREFIX) -> str:
    return f"{prefix}{table}.csv"


def csv_field(text: str) -> str:
    if any(char in text for char in ',"\r\n'):
        return '"' + text.replace('"', '""') + '"'
    return text


def csv_bytes(header: list[str], rows: list[list[str]]) -> bytes:
    lines = [",".join(csv_field(cell) for cell in row) for row in [header, *rows]]
    return ("\n".join(lines) + "\n").encode("utf-8")


def valid_rows() -> dict[str, list[list[str]]]:
    return copy.deepcopy(ROWS)


def set_cell(rows: dict[str, list[list[str]]], table: str, index: int, column: str, text: str):
    rows[table][index][HEADERS[table].index(column)] = text


def write_tables(
    directory: Path,
    rows: dict[str, list[list[str]]] | None = None,
    *,
    skip: tuple[str, ...] = (),
    prefix: str = PREFIX,
) -> dict[str, bytes]:
    rows = valid_rows() if rows is None else rows
    written = {}
    for table in TABLE_NAMES:
        if table in skip:
            continue
        data = csv_bytes(HEADERS[table], rows[table])
        (directory / file_name(table, prefix)).write_bytes(data)
        written[table] = data
    return written


def values(reading: TableReading) -> list[tuple[object, ...]]:
    return [record.values for record in reading.records]


def lines(reading: TableReading) -> list[int]:
    return [record.line for record in reading.records]


def validation_error(directory: Path, **kwargs) -> DataPreparationError:
    with pytest.raises(DataPreparationError) as caught:
        read_validated_tables(directory, **kwargs)
    return caught.value


def assert_location(error, code, table, line, column):
    assert error.code is IssueCode(code)
    assert error.file_name == (None if table is None else file_name(table))
    assert error.line == line
    assert error.column == column


def public_surfaces(error: BaseException) -> list[str]:
    attributes = [str(getattr(error, name)) for name in ("code", "file_name", "line", "column")]
    return [
        str(error),
        repr(error),
        *map(str, error.args),
        *attributes,
        *traceback.format_exception_only(type(error), error),
    ]


# --- audit hook: counts real opens, never fakes data ---------------------------------

_AUDIT: dict[str, object] = {"root": None, "events": []}
_HOOK_INSTALLED = False


def _normalize(path: object) -> str | None:
    if isinstance(path, int):
        return None
    try:
        return os.path.normcase(os.path.abspath(os.fsdecode(path)))
    except TypeError:
        return None


def _audit(event: str, args: tuple[object, ...]) -> None:
    root = _AUDIT["root"]
    if event != "open" or root is None:
        return
    path = _normalize(args[0])
    if path is None or not path.startswith(root):
        return
    _AUDIT["events"].append((path, args[1], args[2]))


@pytest.fixture
def audit(tmp_path):
    global _HOOK_INSTALLED
    if not _HOOK_INSTALLED:
        sys.addaudithook(_audit)
        _HOOK_INSTALLED = True
    _AUDIT.update(root=_normalize(tmp_path), events=[])
    yield _AUDIT
    _AUDIT.update(root=None, events=[])


def opened_tables(events) -> list[str]:
    names = {_normalize(Path(_AUDIT["root"]) / file_name(t)): t for t in TABLE_NAMES}
    return [names[path] for path, _mode, _flags in events if path in names]


@pytest.fixture
def connect_calls(monkeypatch):
    calls: list[tuple[object, ...]] = []

    def recorder(*args, **kwargs):
        calls.append(args)
        raise AssertionError("sqlite3.connect called during in-memory validation")

    monkeypatch.setattr(sqlite3, "connect", recorder)
    return calls


# --- happy path ----------------------------------------------------------------------


def test_six_valid_files_return_readings_in_table_order(tmp_path):
    written = write_tables(tmp_path)

    readings = read_validated_tables(tmp_path)

    assert isinstance(readings, tuple)
    assert [reading.source.table_name for reading in readings] == list(TABLE_NAMES)
    for reading in readings:
        table = reading.source.table_name
        data = written[table]
        assert isinstance(reading, TableReading)
        assert values(reading) == EXPECTED[table]
        assert lines(reading) == list(range(2, 2 + len(ROWS[table])))
        assert reading.source.file_name == file_name(table)
        assert reading.source.sha256 == hashlib.sha256(data).hexdigest()
        assert reading.source.size_bytes == len(data)
        assert reading.source.row_count == len(ROWS[table])


def test_file_prefix_is_passed_to_the_reader(tmp_path):
    write_tables(tmp_path, prefix="fonte - ")

    readings = read_validated_tables(tmp_path, file_prefix="fonte - ")

    assert [reading.source.file_name for reading in readings] == [
        f"fonte - {table}.csv" for table in TABLE_NAMES
    ]


def test_shared_customer_email_is_valid(tmp_path):
    write_tables(tmp_path)

    customers = read_validated_tables(tmp_path)[1]

    emails = [record.values[3] for record in customers.records]
    assert emails == [
        "compartilhado@example.invalid",
        "compartilhado@example.invalid",
        "cliente03@example.invalid",
    ]


def test_same_order_with_different_products_is_valid(tmp_path):
    write_tables(tmp_path)

    items = read_validated_tables(tmp_path)[5]

    assert values(items)[:2] == [(1000, 10, 1), (1000, 11, 2)]


def test_parents_without_children_and_order_without_items_are_valid(tmp_path):
    write_tables(tmp_path)

    readings = read_validated_tables(tmp_path)

    by_table = {reading.source.table_name: reading for reading in readings}
    # Category 3, customer 3 and order 1002 exist and nothing references them.
    assert (3, "Sopros", "Flautas e saxofones") in values(by_table["categories"])
    assert values(by_table["customers"])[2][0] == 3
    assert values(by_table["orders"])[2][0] == 1002
    assert all(item[0] != 1002 for item in values(by_table["order_items"]))


def test_header_only_tables_are_valid(tmp_path):
    rows = {table: [] for table in TABLE_NAMES}
    write_tables(tmp_path, rows)

    readings = read_validated_tables(tmp_path)

    assert [reading.source.table_name for reading in readings] == list(TABLE_NAMES)
    assert all(reading.records == () for reading in readings)
    assert all(reading.source.row_count == 0 for reading in readings)


def test_header_only_children_with_filled_parents_are_valid(tmp_path):
    rows = valid_rows()
    for table in ("products", "promotions", "orders", "order_items"):
        rows[table] = []
    write_tables(tmp_path, rows)

    readings = read_validated_tables(tmp_path)

    assert [reading.source.row_count for reading in readings] == [3, 3, 0, 0, 0, 0]


# --- D18: duplicate primary keys -----------------------------------------------------


@pytest.mark.parametrize(
    ("table", "column"),
    [
        ("categories", "category_id"),
        ("customers", "customer_id"),
        ("products", "product_id"),
        ("promotions", "promotion_id"),
        ("orders", "order_id"),
    ],
)
def test_d18_duplicate_single_primary_key(tmp_path, table, column):
    rows = valid_rows()
    duplicate = list(rows[table][0])
    if table == "categories":
        duplicate[1] = "Outra categoria"  # only the ID repeats
    rows[table].insert(2, duplicate)  # data line 4 repeats the key of line 2
    write_tables(tmp_path, rows)

    error = validation_error(tmp_path)

    assert_location(error, "duplicate_key", table, 4, column)


@pytest.mark.parametrize("quantity", ["1", "5"])
def test_d18_repeated_order_item_pair(tmp_path, quantity):
    rows = valid_rows()
    rows["order_items"].insert(2, ["1000", quantity, "10"])  # repeats line 2 pair
    write_tables(tmp_path, rows)

    error = validation_error(tmp_path)

    assert_location(error, "duplicate_key", "order_items", 4, "product_id")


def test_d18_same_product_in_different_orders_is_valid(tmp_path):
    rows = valid_rows()
    rows["order_items"].append(["1001", "1", "10"])
    write_tables(tmp_path, rows)

    items = read_validated_tables(tmp_path)[5]

    assert values(items)[-1] == (1001, 10, 1)


# --- D39: category name uniqueness ---------------------------------------------------


def test_d39a_repeated_category_name_with_different_ids(tmp_path, connect_calls):
    rows = valid_rows()
    rows["categories"] = [
        ["1", "Bebidas", "Primeira"],
        ["2", "Bebidas", "Segunda"],
    ]
    write_tables(tmp_path, rows)

    error = validation_error(tmp_path)

    assert_location(error, "duplicate_key", "categories", 3, "name")
    assert not isinstance(error, sqlite3.Error)
    assert connect_calls == []


@pytest.mark.parametrize("second", ["bebidas", "Bebidas ", "BEBIDAS", " Bebidas"])
def test_d39b_names_differing_in_case_or_space_are_distinct(tmp_path, second):
    rows = valid_rows()
    rows["categories"] = [
        ["1", "Bebidas", "Primeira"],
        ["2", second, "Segunda"],
    ]
    write_tables(tmp_path, rows)

    categories = read_validated_tables(tmp_path)[0]

    assert [record.values[1] for record in categories.records] == ["Bebidas", second]


# --- D19: broken references ----------------------------------------------------------


@pytest.mark.parametrize(
    ("table", "index", "column", "line"),
    [
        ("products", 1, "category_id", 3),
        ("promotions", 1, "product_id", 3),
        ("orders", 1, "customer_id", 3),
        ("order_items", 1, "order_id", 3),
        ("order_items", 2, "product_id", 4),
    ],
)
def test_d19_each_reference_fails_alone(tmp_path, table, index, column, line):
    rows = valid_rows()
    set_cell(rows, table, index, column, "99")
    write_tables(tmp_path, rows)

    error = validation_error(tmp_path)

    assert_location(error, "broken_reference", table, line, column)


@pytest.mark.parametrize(
    ("empty", "table", "column"),
    [
        (("categories",), "products", "category_id"),
        (("customers",), "orders", "customer_id"),
        (("products",), "promotions", "product_id"),
        (("products", "promotions"), "order_items", "product_id"),
        (("orders",), "order_items", "order_id"),
    ],
)
def test_child_rows_referencing_an_empty_parent_are_rejected(tmp_path, empty, table, column):
    rows = valid_rows()
    for name in empty:
        rows[name] = []
    write_tables(tmp_path, rows)

    error = validation_error(tmp_path)

    assert_location(error, "broken_reference", table, 2, column)


def test_reference_error_in_multiline_record_uses_its_end_line(tmp_path):
    rows = valid_rows()
    set_cell(rows, "products", 1, "description", "Linha um\nLinha dois\nLinha três")
    set_cell(rows, "products", 1, "category_id", "99")
    write_tables(tmp_path, rows)

    error = validation_error(tmp_path)

    # Line 2: product 10; lines 3-5: product 11 with a three-line description.
    assert_location(error, "broken_reference", "products", 5, "category_id")


def test_duplicate_after_multiline_record_uses_physical_line(tmp_path):
    rows = valid_rows()
    set_cell(rows, "products", 0, "description", "Linha um\nLinha dois")
    set_cell(rows, "products", 2, "product_id", "10")
    write_tables(tmp_path, rows)

    error = validation_error(tmp_path)

    # Lines 2-3: product 10; line 4: product 11; line 5: repeated product 10.
    assert_location(error, "duplicate_key", "products", 5, "product_id")


# --- precedence ----------------------------------------------------------------------


def test_primary_key_is_checked_before_unique_name_in_one_record(tmp_path):
    rows = valid_rows()
    rows["categories"].insert(1, ["1", "Cordas", "Repetida"])
    write_tables(tmp_path, rows)

    error = validation_error(tmp_path)

    assert_location(error, "duplicate_key", "categories", 3, "category_id")


def test_earlier_duplicate_line_wins_over_later_one(tmp_path):
    rows = valid_rows()
    rows["categories"] = [
        ["1", "Cordas", "Primeira"],
        ["2", "Cordas", "Nome repetido na linha 3"],
        ["1", "Teclas", "ID repetido na linha 4"],
    ]
    write_tables(tmp_path, rows)

    error = validation_error(tmp_path)

    assert_location(error, "duplicate_key", "categories", 3, "name")


def test_earlier_duplicate_id_wins_over_later_duplicate_name(tmp_path):
    rows = valid_rows()
    rows["categories"] = [
        ["1", "Cordas", "Primeira"],
        ["1", "Teclas", "ID repetido na linha 3"],
        ["2", "Cordas", "Nome repetido na linha 4"],
    ]
    write_tables(tmp_path, rows)

    error = validation_error(tmp_path)

    assert_location(error, "duplicate_key", "categories", 3, "category_id")


def test_uniqueness_pass_runs_before_reference_pass(tmp_path):
    rows = valid_rows()
    set_cell(rows, "products", 0, "category_id", "99")  # broken reference on line 2
    set_cell(rows, "products", 2, "product_id", "11")  # duplicate key on line 4
    write_tables(tmp_path, rows)

    error = validation_error(tmp_path)

    assert_location(error, "duplicate_key", "products", 4, "product_id")


def test_references_follow_record_order(tmp_path):
    rows = valid_rows()
    set_cell(rows, "order_items", 0, "product_id", "99")  # line 2, second reference
    set_cell(rows, "order_items", 1, "order_id", "99")  # line 3, first reference
    write_tables(tmp_path, rows)

    error = validation_error(tmp_path)

    assert_location(error, "broken_reference", "order_items", 2, "product_id")


def test_references_follow_schema_column_order_inside_a_record(tmp_path):
    rows = valid_rows()
    set_cell(rows, "order_items", 0, "order_id", "98")
    set_cell(rows, "order_items", 0, "product_id", "99")
    write_tables(tmp_path, rows)

    error = validation_error(tmp_path)

    assert_location(error, "broken_reference", "order_items", 2, "order_id")


def test_relational_error_in_earlier_table_precedes_later_field_error(tmp_path, audit):
    rows = valid_rows()
    set_cell(rows, "products", 0, "category_id", "99")
    set_cell(rows, "orders", 0, "total_brl", "12.345")
    write_tables(tmp_path, rows)
    audit["events"].clear()

    error = validation_error(tmp_path)

    assert_location(error, "broken_reference", "products", 2, "category_id")
    assert opened_tables(audit["events"]) == ["categories", "customers", "products"]


def test_relational_error_in_earlier_table_precedes_later_missing_file(tmp_path, audit):
    rows = valid_rows()
    set_cell(rows, "orders", 2, "customer_id", "99")
    write_tables(tmp_path, rows, skip=("order_items",))
    audit["events"].clear()

    error = validation_error(tmp_path)

    assert_location(error, "broken_reference", "orders", 4, "customer_id")
    assert opened_tables(audit["events"]) == list(TABLE_NAMES[:5])


def test_duplicate_category_precedes_later_customer_field_error(tmp_path, audit):
    rows = valid_rows()
    set_cell(rows, "categories", 2, "category_id", "2")
    set_cell(rows, "customers", 0, "customer_id", "abc")
    write_tables(tmp_path, rows)
    audit["events"].clear()

    error = validation_error(tmp_path)

    assert_location(error, "duplicate_key", "categories", 4, "category_id")
    assert opened_tables(audit["events"]) == ["categories"]


# --- reader errors are preserved -----------------------------------------------------


def test_reader_error_missing_source_directory(tmp_path):
    error = validation_error(tmp_path / "absent")

    assert_location(error, "source_dir_missing", None, None, None)


def test_reader_error_missing_file(tmp_path):
    write_tables(tmp_path, skip=("promotions",))

    error = validation_error(tmp_path)

    assert_location(error, "file_missing", "promotions", None, None)


def test_reader_error_field_in_valid_relations(tmp_path):
    rows = valid_rows()
    set_cell(rows, "orders", 1, "total_brl", "12.345")
    write_tables(tmp_path, rows)

    error = validation_error(tmp_path)

    assert_location(error, "invalid_money", "orders", 3, "total_brl")


def test_reader_error_header(tmp_path):
    write_tables(tmp_path)
    path = tmp_path / file_name("order_items")
    path.write_bytes(b"order_id,product_id\n1000,10\n")

    error = validation_error(tmp_path)

    assert_location(error, "header_mismatch", "order_items", 1, "quantity")


def test_reader_error_field_beats_duplicate_in_same_file(tmp_path):
    rows = valid_rows()
    set_cell(rows, "products", 0, "product_id", "11")  # duplicate of line 3
    set_cell(rows, "products", 2, "price_brl", "1,50")  # field error on line 4
    write_tables(tmp_path, rows)

    error = validation_error(tmp_path)

    assert_location(error, "invalid_money", "products", 4, "price_brl")


def test_reader_value_error_for_prefix_with_path(tmp_path):
    write_tables(tmp_path)

    with pytest.raises(ValueError, match="path component"):
        read_validated_tables(tmp_path, file_prefix="sub/")


# --- reads, data preservation, privacy, side effects ---------------------------------


def test_each_file_is_read_once(tmp_path, audit):
    write_tables(tmp_path)
    audit["events"].clear()

    read_validated_tables(tmp_path)

    events = list(audit["events"])
    assert opened_tables(events) == list(TABLE_NAMES)
    assert len(events) == len(TABLE_NAMES)


def test_returned_readings_equal_plain_reader_output(tmp_path):
    write_tables(tmp_path)

    readings = read_validated_tables(tmp_path)

    assert readings == tuple(read_table(tmp_path, table) for table in TABLES)
    orders = readings[4]
    # Historical totals kept as written, not recalculated from prices.
    assert [record.values[4] for record in orders.records] == [65000, 120000, 1990]
    products = readings[2]
    assert products.records[2].values[5:7] == (2, "discontinued")


def test_sentinel_in_duplicate_name_is_not_in_public_surfaces(tmp_path):
    rows = valid_rows()
    rows["categories"] = [
        ["1", SENTINEL, "Primeira"],
        ["2", SENTINEL, "Segunda"],
    ]
    write_tables(tmp_path, rows)

    error = validation_error(tmp_path)

    assert_location(error, "duplicate_key", "categories", 3, "name")
    assert str(error) == f"{file_name('categories')} line 3, column name: duplicate_key"
    assert error.__cause__ is None
    assert error.__context__ is None
    for text in public_surfaces(error):
        assert SENTINEL not in text
        assert str(tmp_path) not in text


@pytest.mark.parametrize(
    ("table", "index", "column", "code"),
    [
        ("customers", 2, "customer_id", "duplicate_key"),
        ("order_items", 1, "product_id", "broken_reference"),
    ],
)
def test_key_and_reference_values_are_not_in_public_surfaces(tmp_path, table, index, column, code):
    marker = "987654321"
    rows = valid_rows()
    if code == "duplicate_key":
        set_cell(rows, table, 0, column, marker)
    set_cell(rows, table, index, column, marker)
    write_tables(tmp_path, rows)

    error = validation_error(tmp_path)

    assert error.code is IssueCode(code)
    assert error.__cause__ is None
    for text in public_surfaces(error):
        assert marker not in text


def test_validation_writes_nothing_and_opens_no_sqlite(tmp_path, audit, connect_calls):
    write_tables(tmp_path)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*")}
    audit["events"].clear()

    read_validated_tables(tmp_path)

    events = list(audit["events"])
    after = {path: path.read_bytes() for path in tmp_path.rglob("*")}
    assert after == before
    assert connect_calls == []
    write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
    for _path, mode, flags in events:
        if isinstance(mode, str):
            assert not set(mode) & set("wax+")
        if isinstance(flags, int):
            assert flags & write_flags == 0


def test_failed_validation_writes_nothing_and_opens_no_sqlite(tmp_path, connect_calls):
    rows = valid_rows()
    set_cell(rows, "order_items", 2, "product_id", "99")
    write_tables(tmp_path, rows)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*")}

    error = validation_error(tmp_path)

    assert_location(error, "broken_reference", "order_items", 4, "product_id")
    assert {path: path.read_bytes() for path in tmp_path.rglob("*")} == before
    assert connect_calls == []
