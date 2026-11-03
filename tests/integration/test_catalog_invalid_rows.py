"""Slice 03-A invalid-row cases C68-C74 (docs/plans/03-catalog.md, spec P-12 and P-15).

Each case builds a real database with catalog_kit, stores one invalid value with
catalog_kit.corrupt (PRAGMA ignore_check_constraints or an untyped table rebuild),
checks with an independent connection that the damaged value really exists (typeof
and value), and only then opens the file with connect_readonly. Pricing runs for real:
__context__ is inspected only to prove which code path raised, never a message text.
"""

import sqlite3
import traceback
from pathlib import Path

import pytest

from emporio_agent.data.readonly import connect_readonly
from emporio_agent.services.catalog import CatalogDataError, get_product

SENTINEL = "SENTINEL-9c1e"


def corrupted(tmp_path: Path, catalog_kit, variant: str) -> Path:
    path = catalog_kit.build(tmp_path)
    catalog_kit.corrupt(path, variant)
    return path


def stored(catalog_kit, path: Path, sql: str, parameters: tuple[object, ...]) -> list[tuple]:
    return catalog_kit.query(path, sql, parameters)


def invalid_row(path: Path, product_id: int) -> CatalogDataError:
    with connect_readonly(path) as connection:
        with pytest.raises(CatalogDataError) as caught:
            get_product(connection, product_id)
    error = caught.value
    assert error.reason == "invalid_row"
    assert str(error) == "invalid_row"
    assert error.__cause__ is None
    assert error.__suppress_context__ is True
    return error


def detail_of(path: Path, product_id: int):
    with connect_readonly(path) as connection:
        return get_product(connection, product_id)


def raised_by_quote_price(error: BaseException) -> bool:
    context = error.__context__
    if type(context) is not ValueError:
        return False
    last = traceback.extract_tb(context.__traceback__)[-1]
    return last.name == "quote_price" and Path(last.filename).name == "pricing.py"


def public_surfaces(error: BaseException) -> list[str]:
    return [
        str(error),
        repr(error),
        *(str(arg) for arg in error.args),
        str(error.reason),
        "".join(traceback.format_exception_only(error)),
    ]


# --- C68: wrong types in used product fields ----------------------------------------


@pytest.mark.parametrize(
    ("variant", "column", "expected_type", "expected_value"),
    [
        ("price_text", "price_cents", "text", "abc"),
        ("price_real", "price_cents", "real", 899.9),
        ("price_null", "price_cents", "null", None),
        ("stock_text", "stock_quantity", "text", "4"),
    ],
)
def test_c68_wrong_type_is_invalid_row(
    tmp_path, catalog_kit, variant, column, expected_type, expected_value
):
    path = corrupted(tmp_path, catalog_kit, variant)
    assert stored(
        catalog_kit,
        path,
        f"SELECT typeof({column}), {column} FROM products WHERE product_id = ?",
        (201,),
    ) == [(expected_type, expected_value)]

    error = invalid_row(path, 201)
    assert not isinstance(error, (TypeError, ValueError))

    # Only rows read by the call are checked: another product is unaffected.
    detail = detail_of(path, 202)
    assert format(detail.summary.effective_price, "f") == "990.00"


# --- C69: invalid values in used product fields -------------------------------------


@pytest.mark.parametrize(
    ("variant", "column", "expected_type", "expected_value"),
    [
        ("price_negative", "price_cents", "integer", -1),
        ("stock_negative", "stock_quantity", "integer", -1),
        ("status_unknown", "status", "text", "unknown"),
    ],
)
def test_c69_invalid_value_is_invalid_row(
    tmp_path, catalog_kit, variant, column, expected_type, expected_value
):
    path = corrupted(tmp_path, catalog_kit, variant)
    assert stored(
        catalog_kit,
        path,
        f"SELECT typeof({column}), {column} FROM products WHERE product_id = ?",
        (201,),
    ) == [(expected_type, expected_value)]

    error = invalid_row(path, 201)
    if variant == "price_negative":
        # The negative price reached the real pricing service, which rejected it.
        assert raised_by_quote_price(error)


# --- C70: specs JSON ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("variant", "expected_value"),
    [
        ("specs_malformed", '{"cordas": '),
        ("specs_array", "[1, 2]"),
        ("specs_string", '"texto"'),
        ("specs_null", "null"),
        ("specs_number", "1"),
    ],
)
def test_c70_bad_specs_is_invalid_row(tmp_path, catalog_kit, variant, expected_value):
    path = corrupted(tmp_path, catalog_kit, variant)
    assert stored(
        catalog_kit,
        path,
        "SELECT typeof(specs_json), specs_json FROM products WHERE product_id = ?",
        (201,),
    ) == [("text", expected_value)]

    invalid_row(path, 201)


def test_c70_withheld_specs_are_not_parsed(tmp_path, catalog_kit):
    path = corrupted(tmp_path, catalog_kit, "specs_conflict_array")
    assert stored(
        catalog_kit,
        path,
        "SELECT typeof(specs_json), specs_json FROM products WHERE product_id = ?",
        (135,),
    ) == [("text", "[1, 2]")]

    detail = detail_of(path, 135)
    assert detail.specs is None
    assert detail.description is None
    assert detail.withheld_fields == ("description", "specs")
    assert format(detail.summary.effective_price, "f") == "1350.00"


# --- C71: promotions, through the real pricing path ---------------------------------


@pytest.mark.parametrize(
    ("variant", "promotion_id", "column", "expected_type", "expected_value", "product_id"),
    [
        ("percent_over", 301, "discount_basis_points", "integer", 20000, 202),
        ("percent_over_inactive", 306, "discount_basis_points", "integer", 20000, 212),
        ("active_two", 301, "is_active", "integer", 2, 202),
        ("percent_text", 301, "discount_basis_points", "text", "1000", 202),
    ],
)
def test_c71_bad_promotion_is_invalid_row(
    tmp_path, catalog_kit, variant, promotion_id, column, expected_type, expected_value, product_id
):
    path = corrupted(tmp_path, catalog_kit, variant)
    assert stored(
        catalog_kit,
        path,
        f"SELECT typeof({column}), {column} FROM promotions WHERE promotion_id = ?",
        (promotion_id,),
    ) == [(expected_type, expected_value)]

    error = invalid_row(path, product_id)
    if variant in ("percent_over", "percent_over_inactive"):
        # 200% reached quote_price, active or not, and pricing rejected it.
        assert raised_by_quote_price(error)


# --- C72: quality notes -------------------------------------------------------------


def test_c72_unknown_note_is_invalid_row(tmp_path, catalog_kit):
    path = corrupted(tmp_path, catalog_kit, "note_unknown")
    assert stored(
        catalog_kit,
        path,
        "SELECT typeof(code), code FROM data_quality_notes WHERE entity = ? AND entity_id = ?",
        ("product", 208),
    ) == [("text", "other")]

    invalid_row(path, 208)
    detail = detail_of(path, 201)
    assert detail.summary.quality_notes == ()
    assert detail.summary.availability == "available"


# --- C73: sanitized errors ----------------------------------------------------------


@pytest.mark.parametrize(
    ("variant", "column", "expected_type"),
    [("price_sentinel", "price_cents", "text"), ("specs_sentinel", "specs_json", "text")],
)
def test_c73_invalid_row_error_is_sanitized(tmp_path, catalog_kit, variant, column, expected_type):
    path = corrupted(tmp_path, catalog_kit, variant)
    rows = stored(
        catalog_kit,
        path,
        f"SELECT typeof({column}), {column} FROM products WHERE product_id = ?",
        (201,),
    )
    assert rows[0][0] == expected_type
    assert SENTINEL in rows[0][1]

    error = invalid_row(path, 201)
    assert error.args == ("invalid_row",)
    for surface in public_surfaces(error):
        assert SENTINEL not in surface
        assert str(tmp_path) not in surface
        assert catalog_kit.DB_DIR not in surface
        assert "SELECT" not in surface.upper()
        assert "json" not in surface.lower()
        assert "Expecting" not in surface
        assert "decimal" not in surface.lower()


# --- C74: caller's connection after an error ----------------------------------------


def test_c74_connection_usable_after_invalid_row(tmp_path, catalog_kit):
    path = corrupted(tmp_path / "a", catalog_kit, "price_text")
    before = catalog_kit.snapshot(path)

    with connect_readonly(path) as connection:
        with pytest.raises(CatalogDataError) as caught:
            get_product(connection, 201)
        assert caught.value.reason == "invalid_row"
        assert connection.execute("SELECT 1").fetchone()[0] == 1
        assert connection.row_factory is sqlite3.Row
        folk = get_product(connection, 202)
        assert format(folk.summary.effective_price, "f") == "990.00"
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")

    assert catalog_kit.snapshot(path) == before


def test_c74_connection_usable_after_query_failed(tmp_path, catalog_kit):
    path = catalog_kit.build(tmp_path / "b", edit="no_products_table")
    before = catalog_kit.snapshot(path)

    with connect_readonly(path) as connection:
        with pytest.raises(CatalogDataError) as caught:
            get_product(connection, 201)
        assert caught.value.reason == "query_failed"
        assert connection.execute("SELECT 1").fetchone()[0] == 1
        assert connection.row_factory is sqlite3.Row
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")

    assert catalog_kit.snapshot(path) == before
