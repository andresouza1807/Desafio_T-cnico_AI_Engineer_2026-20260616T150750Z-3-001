"""Database preparation tests for task T5 (docs/specs/02-06-database-preparation.md).

Every test writes synthetic CSV files into pytest tmp_path, runs the real
prepare_database into a real SQLite file and verifies the result through an
independent connection. Expected values are written by hand from the specification.
"""

import dataclasses
import hashlib
import importlib.metadata
import sqlite3
import traceback
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from emporio_agent.data.errors import DataPreparationError, IssueCode
from emporio_agent.data.parsing import basis_points_to_percent, cents_to_decimal
from emporio_agent.data.preparation import PreparationResult, prepare_database
from emporio_agent.data.schema import KNOWN_CATALOG_TEXT_CONFLICTS
from emporio_agent.services.pricing import Promotion, quote_price

FIXED = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
DOMAIN_TABLES = ("categories", "customers", "products", "promotions", "orders", "order_items")

EXPECTED_ROWS = {
    "categories": [
        (1, "Cordas", "Violões, guitarras e baixos"),
        (2, "Teclas", "Pianos digitais"),
    ],
    "customers": [
        (1, "Cliente Teste Um", "(00) 90000-0001", "cliente01@example.invalid", "São Paulo"),
        (2, "Cliente Teste Dois", "(00) 90000-0002", "cliente02@example.invalid", "Recife"),
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
            '{"teclas": "88"}',
            "2025-12-01",
        ),
        (13, 2, "Teclado Teste", "61 teclas", 85050, 1, "coming_soon", "{}", "2026-03-01"),
    ],
    "promotions": [
        (100, 10, 1000, "Promo Teste", 1),
        (101, 10, 4000, "Black Friday Teste", 0),
        (102, 11, 1250, "Campanha Teste", 1),
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
    ],
    "order_items": [(1000, 10, 1), (1000, 11, 2), (1001, 12, 1)],
}

COLUMNS = {
    "categories": "category_id, name, description",
    "customers": "customer_id, name, phone, email, city",
    "products": (
        "product_id, category_id, name, description, price_cents, stock_quantity, status, "
        "specs_json, created_at"
    ),
    "promotions": "promotion_id, product_id, discount_basis_points, description, is_active",
    "orders": (
        "order_id, customer_id, order_date, status, total_cents, payment_method, "
        "tracking_code, estimated_delivery, notes"
    ),
    "order_items": "order_id, product_id, quantity",
}


# --- helpers -------------------------------------------------------------------------


def fixed_clock() -> datetime:
    return FIXED


def layout(tmp_path: Path) -> tuple[Path, Path]:
    source_dir = tmp_path / "sources"
    target_dir = tmp_path / "out"
    target_dir.mkdir()
    return source_dir, target_dir / "emporio.sqlite3"


def query(path: Path, sql: str, parameters: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        return connection.execute(sql, parameters).fetchall()
    finally:
        connection.close()


def rows(path: Path, table: str) -> list[tuple[object, ...]]:
    return query(path, f"SELECT {COLUMNS[table]} FROM {table} ORDER BY rowid")


def notes(path: Path) -> list[tuple[object, ...]]:
    return query(path, "SELECT entity, entity_id, code FROM data_quality_notes ORDER BY 2, 3")


def metadata(path: Path) -> dict[str, str]:
    return dict(query(path, "SELECT key, value FROM preparation_metadata"))


def prepare(source_dir: Path, database_path: Path, **kwargs) -> PreparationResult:
    kwargs.setdefault("clock", fixed_clock)
    return prepare_database(source_dir, database_path, **kwargs)


def preparation_error(source_dir: Path, database_path: Path, **kwargs) -> DataPreparationError:
    with pytest.raises(DataPreparationError) as caught:
        prepare(source_dir, database_path, **kwargs)
    return caught.value


def assert_location(error, code, file_name, line, column):
    assert error.code is IssueCode(code)
    assert error.file_name == file_name
    assert error.line == line
    assert error.column == column


def public_surfaces(error: BaseException) -> list[str]:
    attributes = [str(getattr(error, name, "")) for name in ("code", "file_name", "line")]
    return [
        str(error),
        repr(error),
        *map(str, error.args),
        *attributes,
        *traceback.format_exception_only(type(error), error),
    ]


def add_product(data, product_id: str, status: str, stock: str) -> None:
    data["products"].append(
        [product_id, "1", "Produto Sintético", "Texto sintético", "10", stock, status, "{}"]
        + ["2026-01-01"]
    )


# --- happy path ----------------------------------------------------------------------


def test_d01_six_tables_imported_with_relations(tmp_path, prep_kit):
    source_dir, target = layout(tmp_path)
    prep_kit.write_sources(source_dir)

    result = prepare(source_dir, target)

    counts = {table: query(target, f"SELECT count(*) FROM {table}")[0][0] for table in COLUMNS}
    assert counts == {
        "categories": 2,
        "customers": 2,
        "products": 4,
        "promotions": 3,
        "orders": 2,
        "order_items": 3,
    }
    assert dict(result.row_counts) == counts
    assert list(result.row_counts) == list(DOMAIN_TABLES)
    assert query(target, "PRAGMA foreign_key_check") == []
    joined = query(
        target,
        "SELECT o.order_id, c.customer_id, p.product_id FROM order_items i "
        "JOIN orders o ON o.order_id = i.order_id "
        "JOIN customers c ON c.customer_id = o.customer_id "
        "JOIN products p ON p.product_id = i.product_id ORDER BY 1, 3",
    )
    assert joined == [(1000, 1, 10), (1000, 1, 11), (1001, 2, 12)]
    tables = {name for (name,) in query(target, "SELECT name FROM sqlite_schema")}
    assert set(COLUMNS) | {"preparation_metadata", "source_files", "data_quality_notes"} <= tables


def test_domain_rows_equal_source_values(tmp_path, prep_kit):
    source_dir, target = layout(tmp_path)
    prep_kit.write_sources(source_dir)

    prepare(source_dir, target)

    for table in DOMAIN_TABLES:
        assert rows(target, table) == EXPECTED_ROWS[table], table


def test_d03_historical_total_not_recalculated(tmp_path, prep_kit):
    source_dir, target = layout(tmp_path)
    prep_kit.write_sources(source_dir)

    prepare(source_dir, target)

    assert query(target, "SELECT total_cents FROM orders WHERE order_id = 1000") == [(65000,)]
    current = query(
        target,
        "SELECT sum(p.price_cents * i.quantity) FROM order_items i "
        "JOIN products p ON p.product_id = i.product_id WHERE i.order_id = 1000",
    )
    assert current == [(63970,)]
    assert [note for note in notes(target) if note[1] in (1000, 1001)] == []


def test_d04_status_stock_conflicts_preserved_and_noted(tmp_path, prep_kit):
    source_dir, target = layout(tmp_path)
    prep_kit.write_sources(source_dir)

    result = prepare(source_dir, target)

    stored = query(target, "SELECT product_id, status, stock_quantity FROM products ORDER BY 1")
    assert stored == [(10, "active", 3), (11, "active", 0), (12, "discontinued", 2)] + [
        (13, "coming_soon", 1)
    ]
    assert notes(target) == [
        ("product", 12, "status_stock_conflict"),
        ("product", 13, "status_stock_conflict"),
    ]
    assert result.quality_note_count == 2


def test_status_without_stock_gives_no_note(tmp_path, prep_kit):
    source_dir, target = layout(tmp_path)
    data = prep_kit.valid_rows()
    prep_kit.set_cell(data, "products", 2, "stock_quantity", "0")
    prep_kit.set_cell(data, "products", 3, "stock_quantity", "0")
    prep_kit.write_sources(source_dir, data)

    result = prepare(source_dir, target)

    assert notes(target) == []
    assert result.quality_note_count == 0


def test_d05_curated_catalog_conflict_noted_and_text_preserved(tmp_path, prep_kit):
    source_dir, target = layout(tmp_path)
    data = prep_kit.valid_rows()
    data["products"].append(
        [
            "135",
            "2",
            "Nome Sintético",
            'Descrição "sintética", divergente',
            "10",
            "0",
            "active",
            '{"modelo": "X"}',
            "2026-01-01",
        ]
    )
    prep_kit.write_sources(source_dir, data)

    result = prepare(source_dir, target)

    assert 135 in KNOWN_CATALOG_TEXT_CONFLICTS
    assert query(
        target, "SELECT name, description, specs_json FROM products WHERE product_id=135"
    ) == [("Nome Sintético", 'Descrição "sintética", divergente', '{"modelo": "X"}')]
    assert notes(target) == [
        ("product", 12, "status_stock_conflict"),
        ("product", 13, "status_stock_conflict"),
        ("product", 135, "catalog_text_conflict"),
    ]
    assert result.quality_note_count == 3


def test_product_matching_both_rules_gets_two_notes(tmp_path, prep_kit):
    source_dir, target = layout(tmp_path)
    data = prep_kit.valid_rows()
    add_product(data, "137", "discontinued", "5")
    prep_kit.write_sources(source_dir, data)

    result = prepare(source_dir, target)

    assert [note for note in notes(target) if note[1] == 137] == [
        ("product", 137, "catalog_text_conflict"),
        ("product", 137, "status_stock_conflict"),
    ]
    assert result.quality_note_count == 4
    assert query(target, "SELECT status, stock_quantity FROM products WHERE product_id = 137") == [
        ("discontinued", 5)
    ]


def test_d06_optional_fields_become_null(tmp_path, prep_kit):
    source_dir, target = layout(tmp_path)
    prep_kit.write_sources(source_dir)

    prepare(source_dir, target)

    assert query(
        target,
        "SELECT order_id, tracking_code, estimated_delivery, notes FROM orders ORDER BY 1",
    ) == [
        (1000, "TRK0001", "2026-01-15", None),
        (1001, None, None, "Cancelado pelo cliente"),
    ]
    assert query(target, "SELECT count(*) FROM orders WHERE notes = '' OR tracking_code = ''") == [
        (0,)
    ]


def test_d07_quoted_comma_non_ascii_and_quotes_preserved(tmp_path, prep_kit):
    source_dir, target = layout(tmp_path)
    data = prep_kit.valid_rows()
    tricky = 'Aço\'); DROP TABLE products; -- "x"'
    prep_kit.set_cell(data, "products", 1, "description", tricky)
    prep_kit.write_sources(source_dir, data)

    prepare(source_dir, target)

    assert query(target, "SELECT description FROM products WHERE product_id = 10") == [
        ("Tampo maciço, cordas de aço",)
    ]
    assert query(target, "SELECT description FROM products WHERE product_id = 11") == [(tricky,)]
    assert query(target, "SELECT city FROM customers WHERE customer_id = 1") == [("São Paulo",)]


def test_d08_provenance_hashes_and_base_names(tmp_path, prep_kit):
    source_dir, target = layout(tmp_path)
    written = prep_kit.write_sources(source_dir)

    result = prepare(source_dir, target)

    expected = [
        (
            table,
            prep_kit.file_name(table),
            hashlib.sha256(written[table]).hexdigest(),
            len(written[table]),
            len(prep_kit.valid_rows()[table]),
        )
        for table in DOMAIN_TABLES
    ]
    stored = query(
        target,
        "SELECT table_name, file_name, sha256, size_bytes, row_count FROM source_files",
    )
    assert sorted(stored) == sorted(expected)
    assert [dataclasses.astuple(source) for source in result.sources] == expected
    for _table, name, *_ in stored:
        assert "/" not in name and "\\" not in name

    promotions = source_dir / prep_kit.file_name("promotions")
    data = promotions.read_bytes()
    edited = data.replace(b"Promo Teste", b"Promo Testf")
    assert len(edited) == len(data) and edited != data
    promotions.write_bytes(edited)

    prepare(source_dir, target)

    after = dict(query(target, "SELECT table_name, sha256 FROM source_files"))
    before = {row[0]: row[2] for row in expected}
    changed = {table for table in DOMAIN_TABLES if after[table] != before[table]}
    assert changed == {"promotions"}
    assert after["promotions"] == hashlib.sha256(edited).hexdigest()


def test_d09_prices_and_percentages_convertible_for_pricing(tmp_path, prep_kit):
    source_dir, target = layout(tmp_path)
    prep_kit.write_sources(source_dir)

    prepare(source_dir, target)

    [(price_cents,)] = query(target, "SELECT price_cents FROM products WHERE product_id = 10")
    promos = query(
        target,
        "SELECT discount_basis_points, is_active FROM promotions WHERE product_id = 10 "
        "ORDER BY promotion_id",
    )
    [(basis_points,)] = query(
        target, "SELECT discount_basis_points FROM promotions WHERE promotion_id = 102"
    )
    assert cents_to_decimal(price_cents) == Decimal("599.90")
    assert basis_points_to_percent(basis_points) == Decimal("12.5")
    quote = quote_price(
        cents_to_decimal(price_cents),
        promotions=[Promotion(basis_points_to_percent(bp), bool(flag)) for bp, flag in promos],
    )
    assert quote.final_price == Decimal("539.91")


# --- metadata, clock and result ------------------------------------------------------


def test_d46_metadata_with_fixed_clock(tmp_path, prep_kit):
    source_dir, target = layout(tmp_path)
    prep_kit.write_sources(source_dir)

    result = prepare(source_dir, target)

    assert metadata(target) == {
        "schema_version": "1",
        "prepared_at": "2026-11-02T12:00:00+00:00",
        "importer_version": importlib.metadata.version("emporio-agent"),
    }
    assert result.schema_version == 1
    assert result.prepared_at == FIXED
    assert result.prepared_at.tzinfo is UTC


def test_d46_clock_with_other_timezone_converted_to_utc(tmp_path, prep_kit):
    source_dir, target = layout(tmp_path)
    prep_kit.write_sources(source_dir)
    local = datetime(2026, 11, 2, 9, 0, 0, 250, tzinfo=timezone(timedelta(hours=-3)))

    result = prepare(source_dir, target, clock=lambda: local)

    assert result.prepared_at == datetime(2026, 11, 2, 12, 0, 0, 250, tzinfo=UTC)
    assert result.prepared_at.utcoffset() == timedelta(0)
    assert metadata(target)["prepared_at"] == "2026-11-02T12:00:00.000250+00:00"
    assert datetime.fromisoformat(metadata(target)["prepared_at"]) == local


def test_default_clock_is_utc_now(tmp_path, prep_kit):
    source_dir, target = layout(tmp_path)
    prep_kit.write_sources(source_dir)
    before = datetime.now(UTC)

    result = prepare_database(source_dir, target)

    assert before <= result.prepared_at <= datetime.now(UTC)
    assert result.prepared_at.utcoffset() == timedelta(0)


@pytest.mark.parametrize("value", [datetime(2026, 11, 2, 12, 0), "2026-11-02T12:00:00+00:00", None])
def test_d46_naive_or_invalid_clock_rejected_before_any_file(
    tmp_path, prep_kit, prep_forbid_build, value
):
    source_dir, target = layout(tmp_path)
    prep_kit.write_sources(source_dir)

    with pytest.raises(ValueError) as caught:
        prepare(source_dir, target, clock=lambda: value)

    assert not isinstance(caught.value, DataPreparationError)
    assert prep_forbid_build.connect == [] and prep_forbid_build.mkstemp == []
    assert list(target.parent.iterdir()) == []
    assert str(tmp_path) not in str(caught.value)


def test_clock_exception_propagates_unchanged(tmp_path, prep_kit, prep_forbid_build):
    source_dir, target = layout(tmp_path)
    prep_kit.write_sources(source_dir)
    failure = LookupError("clock failure")

    def broken():
        raise failure

    with pytest.raises(LookupError) as caught:
        prepare(source_dir, target, clock=broken)

    assert caught.value is failure
    assert list(target.parent.iterdir()) == []


def test_metadata_and_result_agree(tmp_path, prep_kit):
    source_dir, target = layout(tmp_path)
    prep_kit.write_sources(source_dir)

    result = prepare(source_dir, target)

    stored_sources = query(
        target,
        "SELECT table_name, file_name, sha256, size_bytes, row_count FROM source_files",
    )
    assert sorted(stored_sources) == sorted(dataclasses.astuple(s) for s in result.sources)
    assert [source.table_name for source in result.sources] == list(DOMAIN_TABLES)
    for source in result.sources:
        assert result.row_counts[source.table_name] == source.row_count
    assert int(metadata(target)["schema_version"]) == result.schema_version
    assert datetime.fromisoformat(metadata(target)["prepared_at"]) == result.prepared_at
    assert query(target, "SELECT count(*) FROM data_quality_notes") == [
        (result.quality_note_count,)
    ]


def test_result_is_frozen_and_row_counts_read_only(tmp_path, prep_kit):
    source_dir, target = layout(tmp_path)
    prep_kit.write_sources(source_dir)

    result = prepare(source_dir, target)

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.quality_note_count = 0  # type: ignore[misc]
    with pytest.raises(TypeError):
        result.row_counts["products"] = 0  # type: ignore[index]
    with pytest.raises(TypeError):
        del result.row_counts["products"]  # type: ignore[attr-defined]
    assert result.row_counts["products"] == 4
    assert isinstance(result.sources, tuple)


def test_result_database_path_is_argument_and_repr_hides_it(tmp_path, prep_kit):
    source_dir, target = layout(tmp_path)
    prep_kit.write_sources(source_dir)

    result = prepare(source_dir, target)

    assert result.database_path is target
    text = repr(result)
    assert str(tmp_path) not in text
    assert "emporio.sqlite3" not in text
    for secret in ("Cliente Teste Um", "cliente01@example.invalid", "(00) 90000-0001", "Violão"):
        assert secret not in text
    assert "PreparationResult(" in text and "quality_note_count=2" in text


def test_importer_version_matches_package_metadata(tmp_path, prep_kit):
    source_dir, target = layout(tmp_path)
    prep_kit.write_sources(source_dir)

    prepare(source_dir, target)

    assert metadata(target)["importer_version"] == importlib.metadata.version("emporio-agent")


# --- build protocol observed through a real authorizer -------------------------------


def test_build_uses_one_connection_and_explicit_transaction(tmp_path, prep_kit, prep_build_spy):
    source_dir, target = layout(tmp_path)
    prep_kit.write_sources(source_dir)
    spy = prep_build_spy.install(target.parent)

    prepare(source_dir, target)
    prep_build_spy.undo()

    assert len(spy.connects) == 1
    temporary, existed = spy.connects[0]
    assert existed
    assert Path(temporary).name.startswith(".emporio.sqlite3.")
    assert not Path(temporary).exists()
    pragmas = [(a1.lower(), (a2 or "").lower()) for kind, a1, a2 in spy.actions if kind == "pragma"]
    transactions = [a1 for kind, a1, _ in spy.actions if kind == "transaction"]
    assert transactions == ["BEGIN", "COMMIT"]
    begin = spy.actions.index(("transaction", "BEGIN", None))
    commit = spy.actions.index(("transaction", "COMMIT", None))
    before = [(kind, a1.lower(), (a2 or "").lower()) for kind, a1, a2 in spy.actions[:begin]]
    assert before == [
        ("pragma", "journal_mode", "delete"),
        ("pragma", "synchronous", "full"),
        ("pragma", "foreign_keys", "on"),
    ]
    inside = [(a1.lower()) for kind, a1, _ in spy.actions[begin:commit] if kind == "pragma"]
    assert inside == ["foreign_key_check", "integrity_check"]
    assert len(pragmas) == 5
    inserted = {
        a1
        for kind, a1, _ in spy.actions[begin:commit]
        if kind == "insert" and not a1.startswith("sqlite_")
    }
    assert inserted == set(COLUMNS) | {"preparation_metadata", "source_files", "data_quality_notes"}
    assert sorted(path.name for path in target.parent.iterdir()) == ["emporio.sqlite3"]


# --- validation errors through prepare_database --------------------------------------


def assert_nothing_built(target: Path, calls) -> None:
    assert calls.connect == []
    assert calls.mkstemp == []
    assert not target.exists()
    assert list(target.parent.iterdir()) == []


def test_d18_duplicate_key_through_prepare(tmp_path, prep_kit, prep_forbid_build):
    source_dir, target = layout(tmp_path)
    data = prep_kit.valid_rows()
    prep_kit.set_cell(data, "products", 1, "product_id", "10")
    prep_kit.write_sources(source_dir, data)

    error = preparation_error(source_dir, target)

    assert_location(error, "duplicate_key", prep_kit.file_name("products"), 3, "product_id")
    assert_nothing_built(target, prep_forbid_build)


def test_d18_repeated_order_item_pair_through_prepare(tmp_path, prep_kit, prep_forbid_build):
    source_dir, target = layout(tmp_path)
    data = prep_kit.valid_rows()
    data["order_items"].append(["1000", "5", "10"])
    prep_kit.write_sources(source_dir, data)

    error = preparation_error(source_dir, target)

    assert_location(error, "duplicate_key", prep_kit.file_name("order_items"), 5, "product_id")
    assert_nothing_built(target, prep_forbid_build)


@pytest.mark.parametrize(
    ("table", "index", "column", "line"),
    [
        ("products", 0, "category_id", 2),
        ("orders", 1, "customer_id", 3),
        ("order_items", 2, "product_id", 4),
    ],
)
def test_d19_broken_reference_through_prepare(
    tmp_path, prep_kit, prep_forbid_build, table, index, column, line
):
    source_dir, target = layout(tmp_path)
    data = prep_kit.valid_rows()
    prep_kit.set_cell(data, table, index, column, "99")
    prep_kit.write_sources(source_dir, data)

    error = preparation_error(source_dir, target)

    assert_location(error, "broken_reference", prep_kit.file_name(table), line, column)
    assert_nothing_built(target, prep_forbid_build)


def test_d39a_repeated_category_name_through_prepare(tmp_path, prep_kit, prep_forbid_build):
    source_dir, target = layout(tmp_path)
    data = prep_kit.valid_rows()
    data["categories"][0][1] = "Bebidas"
    data["categories"][1][1] = "Bebidas"
    prep_kit.write_sources(source_dir, data)

    error = preparation_error(source_dir, target)

    assert not isinstance(error, sqlite3.Error)
    assert_location(error, "duplicate_key", prep_kit.file_name("categories"), 3, "name")
    assert_nothing_built(target, prep_forbid_build)


def test_d39b_names_differing_in_case_or_space_stored_byte_equal(tmp_path, prep_kit):
    source_dir, target = layout(tmp_path)
    data = prep_kit.valid_rows()
    data["categories"][0][1] = "Bebidas"
    data["categories"][1][1] = "Bebidas "
    data["categories"].append(["3", "bebidas", "Terceira"])
    prep_kit.write_sources(source_dir, data)

    prepare(source_dir, target)

    assert query(target, "SELECT name FROM categories ORDER BY category_id") == [
        ("Bebidas",),
        ("Bebidas ",),
        ("bebidas",),
    ]


def test_d10_missing_source_dir_through_prepare(tmp_path, prep_forbid_build):
    _source_dir, target = layout(tmp_path)

    error = preparation_error(tmp_path / "absent", target)

    assert_location(error, "source_dir_missing", None, None, None)
    assert_nothing_built(target, prep_forbid_build)
    for text in public_surfaces(error):
        assert str(tmp_path) not in text


def test_d11_missing_file_through_prepare(tmp_path, prep_kit, prep_forbid_build):
    source_dir, target = layout(tmp_path)
    prep_kit.write_sources(source_dir)
    (source_dir / prep_kit.file_name("order_items")).unlink()

    error = preparation_error(source_dir, target)

    assert_location(error, "file_missing", prep_kit.file_name("order_items"), None, None)
    assert_nothing_built(target, prep_forbid_build)


def test_field_error_through_prepare_keeps_location(tmp_path, prep_kit, prep_forbid_build):
    source_dir, target = layout(tmp_path)
    data = prep_kit.valid_rows()
    prep_kit.set_cell(data, "orders", 0, "total_brl", "12.345")
    prep_kit.write_sources(source_dir, data)

    error = preparation_error(source_dir, target)

    assert_location(error, "invalid_money", prep_kit.file_name("orders"), 2, "total_brl")
    assert error.__cause__ is None
    assert_nothing_built(target, prep_forbid_build)


def test_prefix_with_path_component_rejected(tmp_path, prep_kit, prep_forbid_build):
    source_dir, target = layout(tmp_path)
    prep_kit.write_sources(source_dir)

    with pytest.raises(ValueError, match="file_prefix"):
        prepare(source_dir, target, file_prefix="../x")

    assert_nothing_built(target, prep_forbid_build)


# --- precedence ----------------------------------------------------------------------


def test_target_directory_checked_before_clock_and_sources(tmp_path, prep_forbid_build):
    target = tmp_path / "missing" / "emporio.sqlite3"
    calls = []

    error = preparation_error(
        tmp_path / "absent", target, clock=lambda: calls.append(1) or datetime(2026, 1, 1)
    )

    assert_location(error, "target_directory_missing", None, None, None)
    assert calls == []
    assert not target.parent.exists()


def test_clock_checked_before_sources(tmp_path, prep_forbid_build):
    _source_dir, target = layout(tmp_path)

    with pytest.raises(ValueError):
        prepare(tmp_path / "absent", target, clock=lambda: datetime(2026, 1, 1))


def test_source_error_wins_over_target_being_a_source(tmp_path, prep_kit, prep_forbid_build):
    source_dir = tmp_path / "sources"
    data = prep_kit.valid_rows()
    prep_kit.set_cell(data, "orders", 0, "total_brl", "x")
    prep_kit.write_sources(source_dir, data)

    error = preparation_error(source_dir, source_dir / prep_kit.file_name("customers"))

    assert error.code is IssueCode.INVALID_MONEY


# --- target must never be a source file ----------------------------------------------


@pytest.mark.parametrize("spelling", ["direct", "dotdot", "case"], ids=["direct", "dotdot", "case"])
def test_target_equal_to_source_file_rejected(tmp_path, prep_kit, prep_forbid_build, spelling):
    source_dir = tmp_path / "sources"
    written = prep_kit.write_sources(source_dir)
    name = prep_kit.file_name("products")
    target = {
        "direct": source_dir / name,
        "dotdot": source_dir / "sub" / ".." / name,
        "case": source_dir / name.upper(),
    }[spelling]
    if spelling == "dotdot":
        (source_dir / "sub").mkdir()
    if spelling == "case" and not target.exists():
        pytest.skip("case-sensitive file system: upper-case name is a different file")

    with pytest.raises(ValueError) as caught:
        prepare(source_dir, target)

    assert not isinstance(caught.value, DataPreparationError)
    assert "source file" in str(caught.value)
    for text in public_surfaces(caught.value):
        assert str(tmp_path) not in text and name not in text
    assert prep_forbid_build.connect == [] and prep_forbid_build.mkstemp == []
    for table in prep_kit.TABLE_NAMES:
        assert (source_dir / prep_kit.file_name(table)).read_bytes() == written[table]
    expected = sorted([*(prep_kit.file_name(t) for t in prep_kit.TABLE_NAMES)])
    assert sorted(p.name for p in source_dir.iterdir() if p.is_file()) == expected


def test_target_hard_link_to_source_file_rejected(tmp_path, prep_kit, prep_forbid_build):
    source_dir, target = layout(tmp_path)
    written = prep_kit.write_sources(source_dir)
    try:
        target.hardlink_to(source_dir / prep_kit.file_name("orders"))
    except OSError as error:
        pytest.skip(f"hard links unavailable: {type(error).__name__}")

    with pytest.raises(ValueError, match="source file"):
        prepare(source_dir, target)

    assert (source_dir / prep_kit.file_name("orders")).read_bytes() == written["orders"]
    assert target.read_bytes() == written["orders"]
    assert prep_forbid_build.connect == [] and prep_forbid_build.mkstemp == []


def test_target_in_source_dir_with_other_name_allowed(tmp_path, prep_kit):
    source_dir = tmp_path / "sources"
    written = prep_kit.write_sources(source_dir)
    target = source_dir / "emporio.sqlite3"

    prepare(source_dir, target)

    assert rows(target, "order_items") == EXPECTED_ROWS["order_items"]
    for table in prep_kit.TABLE_NAMES:
        assert (source_dir / prep_kit.file_name(table)).read_bytes() == written[table]
