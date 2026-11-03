"""CSV reading tests for task T3 (docs/specs/02-04-csv-reading.md).

Every test writes small synthetic CSV files into pytest tmp_path. Headers, cell texts and
expected typed values are written by hand from the specification. An audit hook counts
and, in one test, fails real file opens; parsing and validation always run for real.
"""

import hashlib
import os
import sqlite3
import sys
import traceback
from pathlib import Path

import pytest

from emporio_agent.data.errors import DataPreparationError, IssueCode
from emporio_agent.data.preparation import (
    SourceFileInfo,
    SourceRecord,
    TableReading,
    read_table,
)
from emporio_agent.data.schema import TABLES

PREFIX = "desafio_tecnico_ai_eng - "
BOM = b"\xef\xbb\xbf"
SENTINEL = "SENTINEL-PII-7781"
HEADER_SENTINEL = "SENTINEL-HDR-5531"
SPEC = {table.name: table for table in TABLES}

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
    "order_items": ["order_id", "product_id", "quantity"],
}

ROWS = {
    "categories": [
        ["1", "Cordas", "Violões, guitarras e baixos"],
        ["2", "Teclas", "Pianos digitais"],
    ],
    "customers": [
        ["1", "Cliente Teste Um", "(00) 90000-0001", "cliente01@example.invalid", "São Paulo"],
        ["2", "Cliente Teste Dois", "(00) 90000-0002", "cliente02@example.invalid", "Curitiba"],
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
        ["11", "2", "Teclado Teste", "Sem fio", "19.90", "0", "coming_soon", "{}", "2026-02-28"],
    ],
    "promotions": [
        ["1", "10", "10", "Promo teste", "1"],
        ["2", "11", "12.5", "Campanha teste", "0"],
    ],
    "orders": [
        ["1", "1", "2026-03-01", "delivered", "600", "credit_3x", "TRK0001", "2026-03-05", ""],
        ["2", "2", "2026-03-02", "cancelled", "19.9", "pix", "", "", "Cliente desistiu"],
    ],
    "order_items": [
        ["1", "10", "1"],
        ["1", "11", "2"],
    ],
}

EXPECTED = {
    "categories": [
        (1, "Cordas", "Violões, guitarras e baixos"),
        (2, "Teclas", "Pianos digitais"),
    ],
    "customers": [
        (1, "Cliente Teste Um", "(00) 90000-0001", "cliente01@example.invalid", "São Paulo"),
        (2, "Cliente Teste Dois", "(00) 90000-0002", "cliente02@example.invalid", "Curitiba"),
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
        (11, 2, "Teclado Teste", "Sem fio", 1990, 0, "coming_soon", "{}", "2026-02-28"),
    ],
    "promotions": [
        (1, 10, 1000, "Promo teste", 1),
        (2, 11, 1250, "Campanha teste", 0),
    ],
    "orders": [
        (1, 1, "2026-03-01", "delivered", 60000, "credit_3x", "TRK0001", "2026-03-05", None),
        (2, 2, "2026-03-02", "cancelled", 1990, "pix", None, None, "Cliente desistiu"),
    ],
    "order_items": [
        (1, 10, 1),
        (1, 11, 2),
    ],
}

OPTIONAL_COLUMNS = {
    ("orders", "tracking_code"),
    ("orders", "estimated_delivery"),
    ("orders", "notes"),
}
REQUIRED_COLUMNS = [
    (table, column)
    for table, header in HEADERS.items()
    for column in header
    if (table, column) not in OPTIONAL_COLUMNS
]


# --- helpers -------------------------------------------------------------------------


def _quote(field: str) -> str:
    if any(char in field for char in ',"\r\n'):
        return '"' + field.replace('"', '""') + '"'
    return field


def csv_bytes(
    header: list[str],
    rows: list[list[str]],
    *,
    newline: str = "\r\n",
    final: bool = False,
    bom: bool = False,
) -> bytes:
    lines = [",".join(_quote(field) for field in row) for row in [header, *rows]]
    data = (newline.join(lines) + (newline if final else "")).encode("utf-8")
    return BOM + data if bom else data


def file_name(table: str, prefix: str = PREFIX) -> str:
    return f"{prefix}{table}.csv"


def write(source_dir: Path, table: str, data: bytes | str, prefix: str = PREFIX) -> Path:
    path = source_dir / file_name(table, prefix)
    path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
    return path


def write_valid(source_dir: Path, table: str) -> bytes:
    data = csv_bytes(HEADERS[table], ROWS[table])
    write(source_dir, table, data)
    return data


def with_cell(table: str, row_index: int, column: str, value: str) -> list[list[str]]:
    rows = [list(row) for row in ROWS[table]]
    rows[row_index][HEADERS[table].index(column)] = value
    return rows


def read_error(source_dir: Path, table: str) -> DataPreparationError:
    with pytest.raises(DataPreparationError) as excinfo:
        read_table(source_dir, SPEC[table])
    return excinfo.value


def assert_location(
    error: DataPreparationError,
    code: str,
    file: str | None,
    line: int | None,
    column: str | None,
) -> None:
    assert (error.code, error.file_name, error.line, error.column) == (code, file, line, column)
    assert isinstance(error.code, IssueCode)


def public_surfaces(error: DataPreparationError) -> list[str]:
    return [
        str(error),
        repr(error),
        repr(error.args),
        repr((error.code, error.file_name, error.line, error.column)),
        "".join(traceback.format_exception_only(error)),
    ]


def values(reading: TableReading) -> list[tuple[object, ...]]:
    return [record.values for record in reading.records]


def lines(reading: TableReading) -> list[int]:
    return [record.line for record in reading.records]


# Audit hooks cannot be removed; this one acts only while a test sets _AUDIT["paths"].
_AUDIT: dict[str, object] = {"paths": None, "events": [], "fail": None}
_HOOK_INSTALLED = False


def _normalize(path: object) -> str | None:
    if isinstance(path, int):
        return None
    try:
        return os.path.normcase(os.path.abspath(os.fsdecode(path)))
    except TypeError:
        return None


def _audit(event: str, args: tuple[object, ...]) -> None:
    watched = _AUDIT["paths"]
    if event != "open" or watched is None:
        return
    path = _normalize(args[0])
    if path is None or not any(path.startswith(root) for root in watched):
        return
    _AUDIT["events"].append((path, args[1], args[2]))
    if _AUDIT["fail"] == path:
        raise PermissionError(13, "denied by test")


@pytest.fixture
def audit(tmp_path):
    global _HOOK_INSTALLED
    if not _HOOK_INSTALLED:
        sys.addaudithook(_audit)
        _HOOK_INSTALLED = True
    _AUDIT.update(paths=[_normalize(tmp_path)], events=[], fail=None)
    yield _AUDIT
    _AUDIT.update(paths=None, events=[], fail=None)


# --- happy path ----------------------------------------------------------------------


@pytest.mark.parametrize("table", list(HEADERS))
def test_reads_every_table_into_typed_values_in_schema_order(tmp_path, table):
    data = write_valid(tmp_path, table)

    reading = read_table(tmp_path, SPEC[table])

    assert values(reading) == EXPECTED[table]
    assert lines(reading) == [2, 3]
    assert reading.source == SourceFileInfo(
        table_name=table,
        file_name=file_name(table),
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        row_count=2,
    )
    assert all(isinstance(record, SourceRecord) for record in reading.records)
    assert isinstance(reading.records, tuple)
    assert all(isinstance(record.values, tuple) for record in reading.records)


def test_result_types_are_immutable(tmp_path):
    write_valid(tmp_path, "categories")
    reading = read_table(tmp_path, SPEC["categories"])

    with pytest.raises(AttributeError):
        reading.source = None
    with pytest.raises(AttributeError):
        reading.records[0].line = 99
    with pytest.raises(AttributeError):
        reading.source.sha256 = "0" * 64


def test_reordered_header_maps_values_by_name(tmp_path):
    header = list(reversed(HEADERS["products"]))
    rows = [list(reversed(row)) for row in ROWS["products"]]
    write(tmp_path, "products", csv_bytes(header, rows))

    reading = read_table(tmp_path, SPEC["products"])

    assert values(reading) == EXPECTED["products"]


def test_fields_are_validated_in_schema_order_not_header_order(tmp_path):
    header = list(reversed(HEADERS["products"]))
    rows = [list(reversed(row)) for row in with_cell("products", 0, "product_id", "x")]
    rows[0][0] = "not-a-date"  # created_at is first in this CSV, last in the schema
    write(tmp_path, "products", csv_bytes(header, rows))

    error = read_error(tmp_path, "products")

    assert_location(error, "invalid_integer", file_name("products"), 2, "product_id")


@pytest.mark.parametrize("bom", [False, True], ids=["no-bom", "bom"])
@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
@pytest.mark.parametrize("final", [False, True], ids=["no-final", "final"])
def test_accepts_bom_line_endings_and_optional_final_newline(tmp_path, bom, newline, final):
    data = csv_bytes(
        HEADERS["categories"], ROWS["categories"], newline=newline, final=final, bom=bom
    )
    write(tmp_path, "categories", data)

    reading = read_table(tmp_path, SPEC["categories"])

    assert values(reading) == EXPECTED["categories"]
    assert lines(reading) == [2, 3]
    assert reading.source.sha256 == hashlib.sha256(data).hexdigest()
    assert reading.source.size_bytes == len(data)


def test_hash_and_size_include_the_original_bom(tmp_path):
    plain = csv_bytes(HEADERS["categories"], ROWS["categories"])
    write(tmp_path, "categories", BOM + plain)

    reading = read_table(tmp_path, SPEC["categories"])

    assert reading.source.sha256 == hashlib.sha256(BOM + plain).hexdigest()
    assert reading.source.sha256 != hashlib.sha256(plain).hexdigest()
    assert reading.source.size_bytes == len(plain) + 3
    assert values(reading) == EXPECTED["categories"]


def test_quoted_commas_escaped_quotes_and_multiline_fields_are_preserved(tmp_path):
    data = (
        b"category_id,name,description\r\n"
        b'1,"Cordas, sopros","Modelo ""Pro"", 6 cordas"\r\n'
        b'2,Teclas,"Linha um\r\nLinha dois\r\nLinha tres"\r\n'
        b'3,"Ac\xc3\xbastica","So ""aspas"""'
    )
    write(tmp_path, "categories", data)

    reading = read_table(tmp_path, SPEC["categories"])

    assert values(reading) == [
        (1, "Cordas, sopros", 'Modelo "Pro", 6 cordas'),
        (2, "Teclas", "Linha um\r\nLinha dois\r\nLinha tres"),
        (3, "Acústica", 'So "aspas"'),
    ]
    assert lines(reading) == [2, 5, 6]


def test_multiline_field_keeps_lf_when_file_uses_lf(tmp_path):
    write(tmp_path, "categories", b'category_id,name,description\n1,Cordas,"a\nb"\n')

    reading = read_table(tmp_path, SPEC["categories"])

    assert values(reading) == [(1, "Cordas", "a\nb")]
    assert lines(reading) == [3]


def test_error_line_after_multiline_record_is_physical(tmp_path):
    rows = with_cell("products", 0, "description", "linha 1\r\nlinha 2\r\nlinha 3")
    rows[1][HEADERS["products"].index("price_brl")] = "-1"
    write(tmp_path, "products", csv_bytes(HEADERS["products"], rows))

    error = read_error(tmp_path, "products")

    assert_location(error, "invalid_money", file_name("products"), 5, "price_brl")


def test_record_lines_after_multiline_record(tmp_path):
    rows = with_cell("products", 0, "description", "linha 1\r\nlinha 2\r\nlinha 3")
    write(tmp_path, "products", csv_bytes(HEADERS["products"], rows))

    reading = read_table(tmp_path, SPEC["products"])

    assert lines(reading) == [4, 5]
    assert reading.records[0].values[3] == "linha 1\r\nlinha 2\r\nlinha 3"


@pytest.mark.parametrize("final", [False, True], ids=["no-final", "final"])
def test_header_only_file_returns_zero_records(tmp_path, final):
    data = csv_bytes(HEADERS["order_items"], [], final=final)
    write(tmp_path, "order_items", data)

    reading = read_table(tmp_path, SPEC["order_items"])

    assert reading.records == ()
    assert reading.source.row_count == 0
    assert reading.source.size_bytes == len(data)
    assert reading.source.sha256 == hashlib.sha256(data).hexdigest()


def test_only_three_optional_columns_become_none(tmp_path):
    write_valid(tmp_path, "orders")

    reading = read_table(tmp_path, SPEC["orders"])

    first, second = values(reading)
    assert first[6:] == ("TRK0001", "2026-03-05", None)
    assert second[6:] == (None, None, "Cliente desistiu")
    assert None not in first[:6] and None not in second[:6]


@pytest.mark.parametrize(("table", "column"), sorted(OPTIONAL_COLUMNS))
def test_empty_optional_column_is_none(tmp_path, table, column):
    write(tmp_path, table, csv_bytes(HEADERS[table], with_cell(table, 0, column, "")))

    reading = read_table(tmp_path, SPEC[table])

    assert reading.records[0].values[HEADERS[table].index(column)] is None


@pytest.mark.parametrize(("table", "column"), REQUIRED_COLUMNS)
def test_d35_empty_required_column_raises_value_required(tmp_path, table, column):
    write(tmp_path, table, csv_bytes(HEADERS[table], with_cell(table, 1, column, "")))

    error = read_error(tmp_path, table)

    assert_location(error, "value_required", file_name(table), 3, column)


def test_texts_are_preserved_without_trimming(tmp_path):
    rows = with_cell("customers", 0, "name", "  Nome com espaços  ")
    rows[1][HEADERS["customers"].index("city")] = "Florianópolis\t"
    write(tmp_path, "customers", csv_bytes(HEADERS["customers"], rows))

    reading = read_table(tmp_path, SPEC["customers"])

    assert reading.records[0].values[1] == "  Nome com espaços  "
    assert reading.records[1].values[4] == "Florianópolis\t"


def test_duplicates_and_unresolved_references_are_kept_for_t4(tmp_path):
    products = [list(ROWS["products"][0]), list(ROWS["products"][0])]
    products[1][HEADERS["products"].index("category_id")] = "99"
    write(tmp_path, "products", csv_bytes(HEADERS["products"], products))
    categories = [["1", "Cordas", "a"], ["1", "Cordas", "b"]]
    write(tmp_path, "categories", csv_bytes(HEADERS["categories"], categories))
    items = [["1", "10", "1"], ["1", "10", "2"], ["99", "999", "1"]]
    write(tmp_path, "order_items", csv_bytes(HEADERS["order_items"], items))

    products_read = read_table(tmp_path, SPEC["products"])
    categories_read = read_table(tmp_path, SPEC["categories"])
    items_read = read_table(tmp_path, SPEC["order_items"])

    assert [(record.line, record.values[:2]) for record in products_read.records] == [
        (2, (10, 1)),
        (3, (10, 99)),
    ]
    assert values(categories_read) == [(1, "Cordas", "a"), (1, "Cordas", "b")]
    assert lines(categories_read) == [2, 3]
    assert values(items_read) == [(1, 10, 1), (1, 10, 2), (99, 999, 1)]
    assert items_read.source.row_count == 3


@pytest.mark.parametrize("prefix", ["loja-", ""], ids=["custom", "empty"])
def test_file_prefix_is_a_parameter(tmp_path, prefix):
    data = csv_bytes(HEADERS["categories"], ROWS["categories"])
    write(tmp_path, "categories", data, prefix=prefix)

    reading = read_table(tmp_path, SPEC["categories"], file_prefix=prefix)

    assert reading.source.file_name == f"{prefix}categories.csv"
    assert values(reading) == EXPECTED["categories"]


@pytest.mark.parametrize("prefix", ["sub/", "sub\\", "../", "C:"])
def test_file_prefix_with_path_component_is_rejected(tmp_path, prefix):
    with pytest.raises(ValueError) as excinfo:
        read_table(tmp_path, SPEC["categories"], file_prefix=prefix)

    assert not isinstance(excinfo.value, DataPreparationError)
    assert prefix not in str(excinfo.value)


def test_repr_of_results_shows_no_cell_values_or_directory(tmp_path):
    rows = with_cell("customers", 0, "name", SENTINEL)
    write(tmp_path, "customers", csv_bytes(HEADERS["customers"], rows))

    reading = read_table(tmp_path, SPEC["customers"])

    for text in (repr(reading), repr(reading.records), repr(reading.records[0])):
        assert SENTINEL not in text
        assert "example.invalid" not in text
        assert str(tmp_path) not in text
    assert "line=2" in repr(reading.records[0])


# --- single read, no write, no SQLite ------------------------------------------------


def test_file_is_opened_once_for_hash_and_parsing(tmp_path, audit):
    data = write_valid(tmp_path, "products")
    target = _normalize(tmp_path / file_name("products"))
    audit["events"].clear()

    reading = read_table(tmp_path, SPEC["products"])

    opens = [event for event in audit["events"] if event[0] == target]
    assert len(opens) == 1
    assert reading.source.sha256 == hashlib.sha256(data).hexdigest()
    assert values(reading) == EXPECTED["products"]


def test_reading_writes_nothing_and_opens_no_sqlite(tmp_path, audit, monkeypatch):
    for table in HEADERS:
        write_valid(tmp_path, table)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*")}
    audit["events"].clear()

    def forbidden(*args, **kwargs):
        raise AssertionError("sqlite3.connect called")

    monkeypatch.setattr(sqlite3, "connect", forbidden)
    for table in TABLES:
        read_table(tmp_path, table)

    events = list(audit["events"])
    after = {path: path.read_bytes() for path in tmp_path.rglob("*")}
    assert after == before
    write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
    for _path, mode, flags in events:
        if isinstance(mode, str):
            assert not set(mode) & set("wax+")
        if isinstance(flags, int):
            assert flags & write_flags == 0
    assert len(events) == len(HEADERS)


# --- D10-D17: files, encoding, CSV structure -----------------------------------------


def test_d10_missing_source_directory(tmp_path):
    error = read_error(tmp_path / "absent", "categories")

    assert_location(error, "source_dir_missing", None, None, None)
    assert all(str(tmp_path) not in text for text in public_surfaces(error))


def test_d10_source_directory_that_is_a_file(tmp_path):
    not_a_dir = tmp_path / "file.txt"
    not_a_dir.write_bytes(b"x")

    error = read_error(not_a_dir, "categories")

    assert_location(error, "source_dir_missing", None, None, None)


def test_d11_missing_file(tmp_path):
    write_valid(tmp_path, "orders")

    error = read_error(tmp_path, "order_items")

    assert_location(error, "file_missing", file_name("order_items"), None, None)
    assert all(str(tmp_path) not in text for text in public_surfaces(error))
    assert error.__cause__ is None and error.__suppress_context__


def test_d11_directory_in_place_of_file_is_unreadable(tmp_path):
    (tmp_path / file_name("categories")).mkdir()

    error = read_error(tmp_path, "categories")

    assert_location(error, "file_unreadable", file_name("categories"), None, None)
    assert all(str(tmp_path) not in text for text in public_surfaces(error))


def test_d11_os_error_while_opening_is_unreadable(tmp_path, audit):
    path = write(tmp_path, "categories", csv_bytes(HEADERS["categories"], ROWS["categories"]))
    audit["fail"] = _normalize(path)

    error = read_error(tmp_path, "categories")

    assert_location(error, "file_unreadable", file_name("categories"), None, None)
    assert "denied by test" not in str(error)
    assert error.__cause__ is None and error.__suppress_context__


@pytest.mark.parametrize(
    "bad",
    [b"\xff", b"\xc3", b"\xed\xa0\x80"],
    ids=["ff-byte", "truncated-multibyte", "surrogate"],
)
def test_d12_invalid_utf8(tmp_path, bad):
    data = csv_bytes(HEADERS["customers"], ROWS["customers"]).replace(b"Curitiba", b"Cur" + bad)
    write(tmp_path, "customers", data)

    error = read_error(tmp_path, "customers")

    assert_location(error, "encoding_invalid", file_name("customers"), None, None)
    assert error.__cause__ is None and error.__suppress_context__


@pytest.mark.parametrize("data", [b"", BOM], ids=["empty", "bom-only"])
def test_d13_empty_file_has_no_header(tmp_path, data):
    write(tmp_path, "categories", data)

    error = read_error(tmp_path, "categories")

    assert_location(error, "header_missing", file_name("categories"), None, None)


def test_d13_blank_first_line_has_no_header(tmp_path):
    write(tmp_path, "categories", b"\r\ncategory_id,name,description\r\n1,a,b")

    error = read_error(tmp_path, "categories")

    assert_location(error, "header_missing", file_name("categories"), 1, None)


def test_d14_duplicate_schema_header(tmp_path):
    write(tmp_path, "categories", b"category_id,name,description,name\r\n1,a,b,c")

    error = read_error(tmp_path, "categories")

    assert_location(error, "header_duplicate", file_name("categories"), 1, "name")


def test_d14_duplicate_unknown_header_is_not_echoed(tmp_path):
    header = f"category_id,name,description,{HEADER_SENTINEL},{HEADER_SENTINEL}"
    write(tmp_path, "categories", f"{header}\r\n1,a,b,c,d")

    error = read_error(tmp_path, "categories")

    assert_location(error, "header_duplicate", file_name("categories"), 1, None)
    assert all(HEADER_SENTINEL not in text for text in public_surfaces(error))


def test_d15_missing_header_names_the_schema_column(tmp_path):
    header = [name for name in HEADERS["orders"] if name != "notes"]
    rows = [row[:-1] for row in ROWS["orders"]]
    write(tmp_path, "orders", csv_bytes(header, rows))

    error = read_error(tmp_path, "orders")

    assert_location(error, "header_mismatch", file_name("orders"), 1, "notes")


def test_d15_unexpected_header_is_not_echoed(tmp_path):
    header = [*HEADERS["categories"], HEADER_SENTINEL]
    rows = [[*row, "x"] for row in ROWS["categories"]]
    write(tmp_path, "categories", csv_bytes(header, rows))

    error = read_error(tmp_path, "categories")

    assert_location(error, "header_mismatch", file_name("categories"), 1, None)
    assert all(HEADER_SENTINEL not in text for text in public_surfaces(error))


def test_d15_renamed_header_reports_the_missing_schema_column(tmp_path):
    header = ["category_id", " name", "description"]
    write(tmp_path, "categories", csv_bytes(header, ROWS["categories"]))

    error = read_error(tmp_path, "categories")

    assert_location(error, "header_mismatch", file_name("categories"), 1, "name")


def test_d15_header_names_are_case_sensitive(tmp_path):
    header = ["category_id", "Name", "description"]
    write(tmp_path, "categories", csv_bytes(header, ROWS["categories"]))

    error = read_error(tmp_path, "categories")

    assert_location(error, "header_mismatch", file_name("categories"), 1, "name")


def test_d16_extra_field_on_line_3(tmp_path):
    rows = [list(row) for row in ROWS["products"]]
    rows[1].append("extra")
    write(tmp_path, "products", csv_bytes(HEADERS["products"], rows))

    error = read_error(tmp_path, "products")

    assert_location(error, "field_count", file_name("products"), 3, None)


def test_d16_missing_field(tmp_path):
    rows = [list(row) for row in ROWS["order_items"]]
    rows[0].pop()
    write(tmp_path, "order_items", csv_bytes(HEADERS["order_items"], rows))

    error = read_error(tmp_path, "order_items")

    assert_location(error, "field_count", file_name("order_items"), 2, None)


def test_d16_blank_line_inside_file(tmp_path):
    data = csv_bytes(HEADERS["orders"], ROWS["orders"])
    first_break = data.index(b"\r\n", data.index(b"\r\n") + 2)
    data = data[:first_break] + b"\r\n" + data[first_break:]
    write(tmp_path, "orders", data)

    error = read_error(tmp_path, "orders")

    assert_location(error, "field_count", file_name("orders"), 3, None)


def test_d16_two_final_line_breaks_leave_a_blank_record(tmp_path):
    data = csv_bytes(HEADERS["categories"], ROWS["categories"]) + b"\r\n\r\n"
    write(tmp_path, "categories", data)

    error = read_error(tmp_path, "categories")

    assert_location(error, "field_count", file_name("categories"), 4, None)


def test_d17_unbalanced_quote(tmp_path):
    data = b'category_id,name,description\r\n1,a,b\r\n2,"Teclas,sem fim'
    write(tmp_path, "categories", data)

    error = read_error(tmp_path, "categories")

    assert_location(error, "csv_malformed", file_name("categories"), 3, None)
    assert error.__cause__ is None and error.__suppress_context__


def test_d17_text_after_closing_quote(tmp_path):
    data = b'category_id,name,description\r\n1,"Cordas"x,b'
    write(tmp_path, "categories", data)

    error = read_error(tmp_path, "categories")

    assert_location(error, "csv_malformed", file_name("categories"), 2, None)


def test_d17_nul_byte(tmp_path):
    data = b"category_id,name,description\r\n1,Cor\x00das,b"
    write(tmp_path, "categories", data)

    error = read_error(tmp_path, "categories")

    assert_location(error, "csv_malformed", file_name("categories"), None, None)


# --- D36-D38 and field rules ---------------------------------------------------------


def test_d36_invalid_json_object(tmp_path):
    rows = with_cell("products", 1, "specs", "[1]")
    write(tmp_path, "products", csv_bytes(HEADERS["products"], rows))

    error = read_error(tmp_path, "products")

    assert_location(error, "invalid_json", file_name("products"), 3, "specs")


def test_d37_invalid_money_in_orders(tmp_path):
    rows = with_cell("orders", 0, "total_brl", "12.345")
    write(tmp_path, "orders", csv_bytes(HEADERS["orders"], rows))

    error = read_error(tmp_path, "orders")

    assert_location(error, "invalid_money", file_name("orders"), 2, "total_brl")


def test_d38_money_overflow_in_products(tmp_path):
    rows = with_cell("products", 0, "price_brl", "92233720368547758.08")
    write(tmp_path, "products", csv_bytes(HEADERS["products"], rows))

    error = read_error(tmp_path, "products")

    assert_location(error, "money_out_of_range", file_name("products"), 2, "price_brl")


@pytest.mark.parametrize(
    ("table", "column", "text", "code"),
    [
        ("order_items", "quantity", "0", "invalid_integer"),
        ("order_items", "quantity", "01", "invalid_integer"),
        ("order_items", "quantity", "1.0", "invalid_integer"),
        ("order_items", "quantity", "9223372036854775808", "out_of_range"),
        ("products", "stock_quantity", "-1", "invalid_integer"),
        ("products", "status", "Active", "invalid_enum"),
        ("orders", "payment_method", "credit_13x", "invalid_enum"),
        ("orders", "status", "paid", "invalid_enum"),
        ("orders", "notes", " ", "value_blank"),
        ("orders", "tracking_code", "\t", "value_blank"),
        ("orders", "estimated_delivery", " ", "invalid_date"),
        ("orders", "order_date", "2026-02-30", "invalid_date"),
        ("promotions", "discount_percent", "100.01", "invalid_percent"),
        ("promotions", "is_active", "true", "invalid_flag"),
        ("categories", "name", " ", "value_required"),
        ("customers", "customer_id", " 1", "invalid_integer"),
    ],
)
def test_field_rule_reported_with_location(tmp_path, table, column, text, code):
    write(tmp_path, table, csv_bytes(HEADERS[table], with_cell(table, 1, column, text)))

    error = read_error(tmp_path, table)

    assert_location(error, code, file_name(table), 3, column)


def test_quantity_uses_id_grammar_and_accepts_large_values(tmp_path):
    rows = with_cell("order_items", 0, "quantity", "9223372036854775807")
    write(tmp_path, "order_items", csv_bytes(HEADERS["order_items"], rows))

    reading = read_table(tmp_path, SPEC["order_items"])

    assert reading.records[0].values[2] == 9223372036854775807


def test_money_and_percent_are_exact_integers(tmp_path):
    rows = with_cell("products", 0, "price_brl", "1.15")
    rows[1][HEADERS["products"].index("price_brl")] = "0.1"
    write(tmp_path, "products", csv_bytes(HEADERS["products"], rows))
    promos = with_cell("promotions", 0, "discount_percent", "100")
    promos[1][HEADERS["promotions"].index("discount_percent")] = "0"
    write(tmp_path, "promotions", csv_bytes(HEADERS["promotions"], promos))

    products = read_table(tmp_path, SPEC["products"])
    promotions = read_table(tmp_path, SPEC["promotions"])

    prices = [record.values[4] for record in products.records]
    discounts = [record.values[2] for record in promotions.records]
    assert prices == [115, 10] and all(type(price) is int for price in prices)
    assert discounts == [10000, 0] and all(type(bp) is int for bp in discounts)


# --- D60 (reader part): wrapped field errors carry location, not values ---------------


@pytest.mark.parametrize(
    ("table", "column", "code"),
    [("customers", "customer_id", "invalid_integer"), ("products", "price_brl", "invalid_money")],
)
def test_d60_field_error_is_wrapped_without_value(tmp_path, table, column, code):
    write(tmp_path, table, csv_bytes(HEADERS[table], with_cell(table, 0, column, SENTINEL)))

    error = read_error(tmp_path, table)

    assert_location(error, code, file_name(table), 2, column)
    assert error.__cause__ is None
    assert error.__suppress_context__ is True
    for text in public_surfaces(error):
        assert SENTINEL not in text
        assert str(tmp_path) not in text
    assert file_name(table) in str(error)
    assert "line 2" in str(error)
    assert column in str(error)


# --- DataPreparationError text format ------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "text"),
    [
        ({}, "source_dir_missing"),
        ({"file_name": "a.csv"}, "a.csv: source_dir_missing"),
        ({"file_name": "a.csv", "line": 1}, "a.csv line 1: source_dir_missing"),
        (
            {"file_name": "a.csv", "line": 4, "column": "price_brl"},
            "a.csv line 4, column price_brl: source_dir_missing",
        ),
    ],
)
def test_data_preparation_error_text(kwargs, text):
    error = DataPreparationError(IssueCode.SOURCE_DIR_MISSING, **kwargs)

    assert str(error) == text
    assert error.args == (text,)
    assert repr(error) == f"DataPreparationError({text!r})"
    assert "".join(traceback.format_exception_only(error)).strip().endswith(text)


def test_data_preparation_error_converts_code_and_keeps_attributes():
    error = DataPreparationError("field_count", file_name="b.csv", line=7)

    assert error.code is IssueCode.FIELD_COUNT
    assert (error.file_name, error.line, error.column) == ("b.csv", 7, None)
    assert isinstance(error, Exception)
    assert str(error) == "b.csv line 7: field_count"
