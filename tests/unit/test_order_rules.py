"""Slice 05-A unit cases O08-O15 and O23 (docs/plans/05-orders.md).

Customer context, signature, argument validation, check order, error surfaces and the
business time zone. No database file. Validation tests pass a closed connection: if any
query ran before validation, sqlite3.ProgrammingError would escape instead of the
expected error.
"""

import dataclasses
import inspect
import sqlite3
import traceback
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from emporio_agent.services.orders import (
    BUSINESS_TIMEZONE,
    CustomerContext,
    OrderInputError,
    get_order,
)

FIXED = datetime(2026, 11, 3, 15, 0, tzinfo=UTC)
SENTINEL = "SENTINEL-7781"


class CountingClock:
    """Local twin of the integration ClockSpy: fixed value, counted calls."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> datetime:
        self.calls += 1
        return FIXED


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


# --- O08: signature; identity only through CustomerContext ----------------------------


def test_o08_signature_has_no_customer_id():
    parameters = inspect.signature(get_order).parameters

    assert list(parameters) == ["connection", "order_id", "customer", "clock"]
    assert "customer_id" not in parameters
    for name in ("customer", "clock"):
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
        assert parameters[name].default is inspect.Parameter.empty


def test_o08_extra_customer_id_keyword_is_rejected():
    clock = CountingClock()

    with pytest.raises(TypeError):
        get_order(  # type: ignore[call-arg]
            closed_connection(), 5001, customer=CustomerContext(1), clock=clock, customer_id=2
        )
    assert clock.calls == 0


def test_o08_customer_and_clock_are_required():
    with pytest.raises(TypeError):
        get_order(closed_connection(), 5001, clock=CountingClock())  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        get_order(closed_connection(), 5001, customer=CustomerContext(1))  # type: ignore[call-arg]


# --- O09: CustomerContext validation --------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["1", True, 1.0, None, Decimal(1)],
    ids=["str", "true", "float", "none", "decimal"],
)
def test_o09_customer_id_wrong_type(value):
    with pytest.raises(TypeError):
        CustomerContext(value)


@pytest.mark.parametrize("value", [0, -1, 2**63], ids=["zero", "minus_one", "two_pow_63"])
def test_o09_customer_id_out_of_range(value):
    with pytest.raises(ValueError) as caught:
        CustomerContext(value)

    assert not isinstance(caught.value, TypeError)
    for surface in public_surfaces(caught.value):
        assert str(value) not in surface


def test_o09_customer_context_error_never_contains_the_value():
    with pytest.raises(TypeError) as caught:
        CustomerContext(SENTINEL)  # type: ignore[arg-type]

    for surface in public_surfaces(caught.value):
        assert SENTINEL not in surface
        assert "SENTINEL" not in surface


def test_o09_customer_context_accepts_bounds():
    assert CustomerContext(1).customer_id == 1
    assert CustomerContext(2**63 - 1).customer_id == 2**63 - 1


# --- O10: frozen context; wrong customer type is a caller defect ----------------------


def test_o10_customer_context_is_frozen_with_slots():
    context = CustomerContext(1)

    with pytest.raises(dataclasses.FrozenInstanceError):
        context.customer_id = 2  # type: ignore[misc]
    assert CustomerContext.__slots__ == ("customer_id",)
    assert not hasattr(context, "__dict__")


@pytest.mark.parametrize(
    "customer",
    [1, {"customer_id": 1}, "1"],
    ids=["int", "dict", "str"],
)
def test_o10_wrong_customer_type_raises_type_error(customer):
    clock = CountingClock()

    with pytest.raises(TypeError) as caught:
        get_order(closed_connection(), 5001, customer=customer, clock=clock)

    assert not isinstance(caught.value, sqlite3.ProgrammingError)
    assert clock.calls == 0


# --- O11, O12: order_id validation before any query -----------------------------------


@pytest.mark.parametrize(
    "value",
    [True, False, "5001", 5001.0, Decimal(5001), None],
    ids=["true", "false", "str", "float", "decimal", "none"],
)
def test_o11_order_id_wrong_type(value):
    clock = CountingClock()

    with pytest.raises(OrderInputError) as caught:
        get_order(closed_connection(), value, customer=CustomerContext(1), clock=clock)

    error = caught.value
    assert isinstance(error, ValueError)
    assert error.field == "order_id"
    assert error.code == "invalid_type"
    assert clock.calls == 0


@pytest.mark.parametrize("value", [0, -1, 2**63], ids=["zero", "minus_one", "two_pow_63"])
def test_o12_order_id_out_of_range(value):
    clock = CountingClock()

    with pytest.raises(OrderInputError) as caught:
        get_order(closed_connection(), value, customer=CustomerContext(1), clock=clock)

    assert caught.value.field == "order_id"
    assert caught.value.code == "out_of_range"
    assert clock.calls == 0


# --- O13: order_id is checked before the session --------------------------------------


def test_o13_order_id_checked_before_missing_session():
    with pytest.raises(OrderInputError) as caught:
        get_order(closed_connection(), True, customer=None, clock=CountingClock())

    assert caught.value.code == "invalid_type"


def test_o13_order_id_checked_before_wrong_customer_type():
    with pytest.raises(OrderInputError) as caught:
        get_order(closed_connection(), 0, customer="1", clock=CountingClock())  # type: ignore[arg-type]

    assert caught.value.code == "out_of_range"


# --- O14: no session returns None without database or clock ---------------------------


def test_o14_no_session_returns_none_without_query_or_clock():
    clock = CountingClock()

    assert get_order(closed_connection(), 5001, customer=None, clock=clock) is None
    assert clock.calls == 0


# --- O15: fixed error surfaces --------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "code"),
    [(SENTINEL, "invalid_type"), (2**63 + 7781, "out_of_range")],
    ids=["sentinel_str", "sentinel_int"],
)
def test_o15_order_input_error_never_contains_the_value(value, code):
    with pytest.raises(OrderInputError) as caught:
        get_order(closed_connection(), value, customer=CustomerContext(1), clock=CountingClock())

    error = caught.value
    assert str(error) == f"order_id: {code}"
    assert error.args == (str(error),)
    for surface in public_surfaces(error):
        assert SENTINEL not in surface
        assert "7781" not in surface
        assert str(value) not in surface


# --- O23: fixed business time zone ----------------------------------------------------


def test_o23_business_timezone_is_fixed_utc_minus_three():
    assert type(BUSINESS_TIMEZONE) is timezone
    assert BUSINESS_TIMEZONE.utcoffset(None) == timedelta(hours=-3)
