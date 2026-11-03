"""Read-only, customer-scoped order lookup (Slice 05, part A: order header and dates).

Reads a prepared SQLite database through a connection opened by the caller with
connect_readonly. Authorization is part of the SQL filter: one fixed statement binds the
requested order_id and the customer_id of the application-built CustomerContext. No
session, a missing order and another customer's order all return the same None.

The caller owns the connection: this module never opens, closes or commits it and never
changes its row_factory. Errors carry codes only. CustomerContext is a demonstration
convention chosen by the application, not production authentication.
See docs/specs/05-orders.md.
"""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Final, Literal

from emporio_agent.data.parsing import SQLITE_MAX_INTEGER, cents_to_decimal, parse_date
from emporio_agent.data.schema import ORDER_STATUSES

type OrderStatus = Literal["pending", "confirmed", "shipped", "delivered", "cancelled"]
type EstimateStatus = Literal["not_registered", "passed", "due_today", "upcoming"]

# Fixed offset (spec P-09): Brazil has had no daylight saving time since 2019, and
# ZoneInfo("America/Sao_Paulo") needs tzdata, which is not a dependency. Wrong if daylight
# saving time returns.
BUSINESS_TIMEZONE: Final = timezone(timedelta(hours=-3), "UTC-03:00")

# Fixed statement. Only order_id and customer.customer_id are ever bound. customer_id is
# read by SQLite to evaluate the filter and is never selected.
_ORDER_SQL: Final = (
    "SELECT order_id, status, total_cents, tracking_code, estimated_delivery"
    " FROM orders"
    " WHERE order_id = ? AND customer_id = ?"
)


@dataclass(frozen=True, slots=True)
class CustomerContext:
    """Customer bound to the session by application code, never by model arguments."""

    customer_id: int

    def __post_init__(self) -> None:
        if type(self.customer_id) is not int:
            raise TypeError("customer_id must be an int")
        if not 1 <= self.customer_id <= SQLITE_MAX_INTEGER:
            raise ValueError("customer_id out of range")


@dataclass(frozen=True)
class OrderView:
    order_id: int
    status: OrderStatus
    total: Decimal  # historical, from total_cents
    tracking_code: str | None  # untrusted data
    estimated_delivery: date | None  # registered estimate
    estimate_status: EstimateStatus
    business_date: date


class OrderInputError(ValueError):
    """An argument was rejected before any database access.

    Carries the argument name and a code only, never the value. args == (str(self),).
    """

    field: str
    code: str

    def __init__(self, field: str, code: str) -> None:
        self.field = field
        self.code = code
        super().__init__(f"{field}: {code}")


class OrderDataError(Exception):
    """A row read for the authorized order failed validation (invalid_row).

    Carries the reason only: no path, SQL, cell value or original message.
    args == (str(self),).
    """

    reason: str

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _check_order_id(order_id: object) -> int:
    if type(order_id) is not int:
        raise OrderInputError("order_id", "invalid_type")
    if not 1 <= order_id <= SQLITE_MAX_INTEGER:
        raise OrderInputError("order_id", "out_of_range")
    return order_id


def _business_date(clock: Callable[[], datetime]) -> date:
    now = clock()
    if not isinstance(now, datetime):
        raise TypeError("clock must return a datetime")
    if now.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return now.astimezone(BUSINESS_TIMEZONE).date()


def _estimate_status(estimate: date | None, business_date: date) -> EstimateStatus:
    if estimate is None:
        return "not_registered"
    if estimate < business_date:
        return "passed"
    if estimate == business_date:
        return "due_today"
    return "upcoming"


# Row checks raise TypeError or ValueError (FieldError included) with fixed text;
# get_order turns them into OrderDataError("invalid_row"). No message contains a cell
# value. The clock is read outside this conversion, so a clock defect is never
# reported as invalid_row.


def _integer(value: object) -> int:
    if type(value) is not int:
        raise TypeError("integer cell expected")
    return value


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if type(value) is not str or not value.strip():
        raise ValueError("non-blank text cell expected")
    return value


def _optional_date(value: object) -> date | None:
    if value is None:
        return None
    if type(value) is not str:
        raise TypeError("text cell expected")
    return date.fromisoformat(parse_date(value))


def _header(
    row: tuple[object, ...], order_id: int
) -> tuple[int, OrderStatus, Decimal, str | None, date | None]:
    stored_id, status, total_cents, tracking_code, estimated_delivery = row
    if _integer(stored_id) != order_id:
        raise ValueError("unexpected order")
    if type(status) is not str or status not in ORDER_STATUSES:
        raise ValueError("unknown status")
    cents = _integer(total_cents)
    if cents < 0:
        raise ValueError("negative total")
    return (
        stored_id,
        status,  # type: ignore[return-value]
        cents_to_decimal(cents),
        _optional_text(tracking_code),
        _optional_date(estimated_delivery),
    )


def get_order(
    connection: sqlite3.Connection,
    order_id: int,
    *,
    customer: CustomerContext | None,
    clock: Callable[[], datetime],
) -> OrderView | None:
    """Return the order of the session customer, or None when it is not accessible.

    order_id is validated first, then the session; neither touches the database. A
    missing order and another customer's order run the same query and return None
    without reading the clock. The clock is read exactly once for an authorized order.
    """
    order_id = _check_order_id(order_id)
    if customer is None:
        return None
    if type(customer) is not CustomerContext:
        raise TypeError("customer must be a CustomerContext or None")

    cursor = connection.cursor()
    cursor.row_factory = None
    row = cursor.execute(_ORDER_SQL, (order_id, customer.customer_id)).fetchone()
    if row is None:
        return None

    business_date = _business_date(clock)
    try:
        stored_id, status, total, tracking_code, estimated_delivery = _header(row, order_id)
    except TypeError, ValueError:  # FieldError is a ValueError
        raise OrderDataError("invalid_row") from None
    return OrderView(
        order_id=stored_id,
        status=status,
        total=total,
        tracking_code=tracking_code,
        estimated_delivery=estimated_delivery,
        estimate_status=_estimate_status(estimated_delivery, business_date),
        business_date=business_date,
    )
