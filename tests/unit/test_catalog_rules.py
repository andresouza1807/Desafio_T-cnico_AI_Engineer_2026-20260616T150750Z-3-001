"""Slice 03-A unit cases C03, C07 and C20 (docs/plans/03-catalog.md).

Pure rules and argument validation, no database file. Validation tests pass a closed
connection: if any query ran before validation, sqlite3.ProgrammingError would escape
instead of CatalogInputError.
"""

import sqlite3
import traceback
from decimal import Decimal

import pytest

from emporio_agent.services.catalog import (
    CatalogDataError,
    CatalogInputError,
    availability_of,
    get_product,
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
