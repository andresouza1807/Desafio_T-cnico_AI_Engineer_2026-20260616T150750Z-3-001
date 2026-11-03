"""Read-only, customer-scoped order lookup (Slice 05, parts A and B).

Reads a prepared SQLite database through a connection opened by the caller with
connect_readonly. Authorization is part of the SQL filter: one fixed statement binds the
requested order_id and the customer_id of the application-built CustomerContext. No
session, a missing order and another customer's order all return the same None. Items
are read only after the order matched, by one statement that repeats the customer filter
through a join; item names are the current catalog names. The total is the stored
historical total, never recomputed from current prices.

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
from emporio_agent.data.preparation import SourceFileInfo
from emporio_agent.data.readonly import read_preparation_info
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

# Fixed statement, run only after _ORDER_SQL returned a row. One query for every item (no
# N+1). The join on orders repeats the customer filter. LEFT JOIN: a missing product row
# gives a NULL name, which fails row validation instead of dropping the item. Only
# products.name is read: no price, promotion, description or specs.
_ITEMS_SQL: Final = (
    "SELECT oi.product_id, oi.quantity, p.name"
    " FROM order_items AS oi"
    " JOIN orders AS o ON o.order_id = oi.order_id"
    " LEFT JOIN products AS p ON p.product_id = oi.product_id"
    " WHERE oi.order_id = ? AND o.customer_id = ?"
    " ORDER BY oi.product_id"
)

# Source records returned with an order, in this order. Never customers.
_PROVENANCE_TABLES: Final = ("orders", "order_items", "products")


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
class OrderItem:
    product_id: int
    name: str  # current catalog name, not the name at purchase time; untrusted data
    quantity: int
    name_source: Literal["current_catalog"] = "current_catalog"


@dataclass(frozen=True)
class OrderProvenance:
    prepared_at: datetime  # UTC
    sources: tuple[SourceFileInfo, ...]  # orders, order_items, products


@dataclass(frozen=True)
class OrderView:
    order_id: int
    status: OrderStatus
    items: tuple[OrderItem, ...]  # sorted by product_id; may be empty
    total: Decimal  # historical, from total_cents
    tracking_code: str | None  # untrusted data
    estimated_delivery: date | None  # registered estimate
    estimate_status: EstimateStatus
    business_date: date
    provenance: OrderProvenance


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


def _item(row: tuple[object, ...]) -> OrderItem:
    stored_product_id, stored_quantity, name = row
    product_id = _integer(stored_product_id)
    quantity = _integer(stored_quantity)
    if product_id < 1 or quantity < 1:
        raise ValueError("positive integer cell expected")
    if type(name) is not str or not name.strip():  # NULL: missing product row
        raise ValueError("non-blank product name expected")
    return OrderItem(product_id=product_id, name=name, quantity=quantity)


def _provenance(connection: sqlite3.Connection) -> OrderProvenance:
    info = read_preparation_info(connection)
    by_table = {source.table_name: source for source in info.sources}
    return OrderProvenance(
        prepared_at=info.prepared_at,
        sources=tuple(by_table[table] for table in _PROVENANCE_TABLES),
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
    Items and provenance are read only for an authorized order.
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

    item_rows = cursor.execute(_ITEMS_SQL, (order_id, customer.customer_id)).fetchall()
    try:
        items = tuple(_item(item_row) for item_row in item_rows)
    except TypeError, ValueError:
        raise OrderDataError("invalid_row") from None

    return OrderView(
        order_id=stored_id,
        status=status,
        items=items,
        total=total,
        tracking_code=tracking_code,
        estimated_delivery=estimated_delivery,
        estimate_status=_estimate_status(estimated_delivery, business_date),
        business_date=business_date,
        provenance=_provenance(connection),
    )
