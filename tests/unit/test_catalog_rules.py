"""Slice 03 unit cases (docs/plans/03-catalog.md).

Slice A: C03, C07, C20. Slice B: C35, C36, C42, C43, C49, C51 and the normalization
rows of C37 and C38. Slice C: C64 and the closed-connection part of C63.

Pure rules and argument validation, no database file. Validation tests pass a closed
connection: if any query ran before validation, sqlite3.ProgrammingError would escape
instead of CatalogInputError.
"""

import sqlite3
import traceback
import typing
from decimal import Decimal

import pytest

from emporio_agent.services.catalog import (
    CatalogDataError,
    CatalogInputError,
    PaymentMethod,
    availability_of,
    get_product,
    normalize_search_text,
    quote_product,
    search_products,
)


def closed_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.close()
    return connection


def public_surfaces(error: BaseException) -> list[str]:
    return [
        str(error),
        repr(error),
        *(str(arg) for arg in error.args),
        "".join(traceback.format_exception_only(error)),
    ]


# --- C03: product_id validation before any query --------------------------------------


@pytest.mark.parametrize(
    ("value", "code"),
    [
        (0, "out_of_range"),
        (-1, "out_of_range"),
        (2**63, "out_of_range"),
        (True, "invalid_type"),
        (False, "invalid_type"),
        ("201", "invalid_type"),
        (201.0, "invalid_type"),
        (Decimal(201), "invalid_type"),
        (None, "invalid_type"),
    ],
    ids=["zero", "minus_one", "two_pow_63", "true", "false", "str", "float", "decimal", "none"],
)
def test_c03_invalid_product_id_rejected_before_any_query(value, code):
    with pytest.raises(CatalogInputError) as caught:
        get_product(closed_connection(), value)

    error = caught.value
    assert isinstance(error, ValueError)
    assert error.field == "product_id"
    assert error.code == code
    assert str(error) == f"product_id: {code}"
    assert error.args == (str(error),)


# --- C07: availability states -------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "stock", "expected"),
    [
        ("active", 1, "available"),
        ("active", 0, "out_of_stock"),
        ("discontinued", 0, "discontinued"),
        ("discontinued", 5, "discontinued"),
        ("coming_soon", 0, "coming_soon"),
        ("coming_soon", 5, "coming_soon"),
    ],
)
def test_c07_availability_states(status, stock, expected):
    assert availability_of(status, stock) == expected


def test_c07_unknown_status_is_invalid_row():
    with pytest.raises(CatalogDataError) as caught:
        availability_of("unknown", 1)

    error = caught.value
    assert error.reason == "invalid_row"
    assert str(error) == "invalid_row"
    assert error.args == ("invalid_row",)
    assert error.__cause__ is None
    assert error.__suppress_context__ is True


# --- C20: no argument value in the error ---------------------------------------------


def test_c20_sentinel_product_id_never_reaches_the_error():
    sentinel = "SENTINEL-7f3a"
    with pytest.raises(CatalogInputError) as caught:
        get_product(closed_connection(), sentinel)

    error = caught.value
    assert str(error) == "product_id: invalid_type"
    assert (error.field, error.code) == ("product_id", "invalid_type")
    for surface in public_surfaces(error):
        assert sentinel not in surface


# --- Slice B ------------------------------------------------------------------------


def search_error(**arguments) -> CatalogInputError:
    with pytest.raises(CatalogInputError) as caught:
        search_products(closed_connection(), **arguments)
    error = caught.value
    assert str(error) == f"{error.field}: {error.code}"
    assert error.args == (str(error),)
    return error


# --- C35: offset validation before any query ----------------------------------------


@pytest.mark.parametrize(
    ("value", "code"),
    [
        (-1, "out_of_range"),
        (True, "invalid_type"),
        (1.0, "invalid_type"),
        ("0", "invalid_type"),
        (None, "invalid_type"),
    ],
    ids=["minus_one", "true", "float", "str", "none"],
)
def test_c35_invalid_offset_rejected_before_any_query(value, code):
    error = search_error(offset=value)
    assert (error.field, error.code) == ("offset", code)


# --- C36: available_only must be a bool ---------------------------------------------


@pytest.mark.parametrize("value", [1, 0, "true", None], ids=["one", "zero", "str", "none"])
def test_c36_available_only_must_be_bool(value):
    error = search_error(available_only=value)
    assert (error.field, error.code) == ("available_only", "invalid_type")


# --- C37, C38: normalization rows ---------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("  VIOLÃO   Ômega ", "violao omega"),  # C37
        ("  violão   alfa  ", "violao alfa"),  # C38
        ("alfa violão", "alfa violao"),  # C38
        ("violãoalfa", "violaoalfa"),  # C38
    ],
)
def test_c37_c38_normalize_search_text(text, expected):
    assert normalize_search_text(text) == expected


# --- C42: blank text is rejected ----------------------------------------------------


@pytest.mark.parametrize("field", ["query", "category"])
@pytest.mark.parametrize(
    "value",
    ["", "   ", "\N{COMBINING ACUTE ACCENT}"],
    ids=["empty", "spaces", "combining_mark_only"],
)
def test_c42_blank_text_rejected(field, value):
    error = search_error(**{field: value})
    assert (error.field, error.code) == (field, "blank")


# --- C43: length limit in raw code points -------------------------------------------


@pytest.mark.parametrize("field", ["query", "category"])
def test_c43_text_over_limit_rejected(field):
    error = search_error(**{field: "a" * 4001})
    assert (error.field, error.code) == (field, "too_long")


# --- C49: budget validation before any query ----------------------------------------


@pytest.mark.parametrize(
    ("value", "code"),
    [
        (Decimal("-0.01"), "negative"),
        (Decimal("NaN"), "not_finite"),
        (Decimal("sNaN"), "not_finite"),
        (Decimal("Infinity"), "not_finite"),
        (Decimal("-Infinity"), "not_finite"),
        (Decimal("999.999"), "excess_precision"),
        (Decimal("92233720368547758.08"), "out_of_range"),
        (Decimal("1E+30"), "out_of_range"),
        (1000, "invalid_type"),
        (1000.0, "invalid_type"),
        ("1000.00", "invalid_type"),
        (True, "invalid_type"),
    ],
    ids=[
        "negative",
        "nan",
        "snan",
        "infinity",
        "minus_infinity",
        "sub_cent",
        "above_max",
        "huge",
        "int",
        "float",
        "str",
        "bool",
    ],
)
def test_c49_invalid_budget_rejected_before_any_query(value, code):
    error = search_error(max_price_brl=value)
    assert (error.field, error.code) == ("max_price_brl", code)


# --- C51: text types and parameter order --------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [("query", 123), ("query", b"x"), ("query", ["x"]), ("category", 1)],
    ids=["query_int", "query_bytes", "query_list", "category_int"],
)
def test_c51_text_must_be_str(field, value):
    error = search_error(**{field: value})
    assert (error.field, error.code) == (field, "invalid_type")


def test_c51_first_invalid_argument_in_parameter_order():
    error = search_error(query=123, offset=-1)
    assert (error.field, error.code) == ("query", "invalid_type")


# --- Slice C ------------------------------------------------------------------------


def quote_error(product_id: object, **arguments) -> CatalogInputError:
    with pytest.raises(CatalogInputError) as caught:
        quote_product(closed_connection(), product_id, **arguments)
    error = caught.value
    assert isinstance(error, ValueError)
    assert str(error) == f"{error.field}: {error.code}"
    assert error.args == (str(error),)
    return error


# --- C63 (closed-connection part): product_id validated before any query -----------


@pytest.mark.parametrize(
    ("value", "code"),
    [
        (True, "invalid_type"),
        (0, "out_of_range"),
        (2**63, "out_of_range"),
        ("201", "invalid_type"),
        (None, "invalid_type"),
    ],
    ids=["true", "zero", "two_pow_63", "str", "none"],
)
def test_c63_invalid_product_id_rejected_before_any_query(value, code):
    error = quote_error(value, payment_method="pix")
    assert (error.field, error.code) == ("product_id", code)


def test_c63_product_id_checked_before_payment_method():
    error = quote_error(True, payment_method="PIX")
    assert (error.field, error.code) == ("product_id", "invalid_type")


# --- C64: payment_method exact, required, no default and no case folding -----------


def test_c64_payment_method_type():
    assert typing.get_args(PaymentMethod.__value__) == ("regular", "pix")


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("PIX", "unknown_value"),
        ("", "unknown_value"),
        ("credit_3x", "unknown_value"),
        (None, "invalid_type"),
        (True, "invalid_type"),
    ],
    ids=["upper_pix", "empty", "credit_3x", "none", "true"],
)
def test_c64_invalid_payment_method_rejected_before_any_query(value, code):
    error = quote_error(201, payment_method=value)
    assert (error.field, error.code) == ("payment_method", code)


def test_c64_payment_method_is_required():
    with pytest.raises(TypeError) as caught:
        quote_product(closed_connection(), 201)
    assert not isinstance(caught.value, CatalogInputError)
    assert "payment_method" in str(caught.value)


def test_c64_payment_method_is_keyword_only():
    with pytest.raises(TypeError) as caught:
        quote_product(closed_connection(), 201, "pix")
    assert not isinstance(caught.value, CatalogInputError)
