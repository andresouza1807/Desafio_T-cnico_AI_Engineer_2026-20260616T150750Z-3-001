"""Slice 05-A integration cases O01-O07 and O16-O22 (docs/plans/05-orders.md).

Real SQLite files built in tmp_path by the real prepare_database from the synthetic
orders_kit sources, opened with connect_readonly. Expected values are literals
calculated by hand in the plan or read with independent parameterized SQL through a
separate mode=ro connection; get_order, cents_to_decimal and parse_date are never the
oracle. Nothing here mocks authorization, SQL, dates, relations or persistence.

Slice A covers the order header only: items and provenance belong to slice B, the
read observer and invalid-row variants to slice C.
"""

import dataclasses
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from emporio_agent.data.readonly import connect_readonly
from emporio_agent.services.orders import CustomerContext, OrderView, get_order

C1 = CustomerContext(1)
C2 = CustomerContext(2)
C3 = CustomerContext(3)


@pytest.fixture
def orders_db(orders_kit, tmp_path):
    return orders_kit.build(tmp_path)


def lookup(path, order_id, customer, clock):
    with connect_readonly(path) as connection:
        return get_order(connection, order_id, customer=customer, clock=clock)


# --- O01: authorized header -----------------------------------------------------------


def test_o01_authorized_order_header(orders_kit, orders_db):
    view = lookup(orders_db, 5001, C1, orders_kit.ClockSpy(orders_kit.FIXED))
    # Slice B: source records read with independent SQL, not through the service.
    sources = orders_kit.query(
        orders_db,
        "SELECT table_name, file_name, sha256, size_bytes, row_count FROM source_files"
        " WHERE table_name IN (?, ?, ?)",
        ("orders", "order_items", "products"),
    )
    columns = ("table_name", "file_name", "sha256", "size_bytes", "row_count")
    by_table = {row[0]: dict(zip(columns, row, strict=True)) for row in sources}

    assert type(view) is OrderView
    assert dataclasses.asdict(view) == {
        "order_id": 5001,
        "status": "shipped",
        "items": (
            {
                "product_id": 10,
                "name": "Violão Teste Alfa",
                "quantity": 2,
                "name_source": "current_catalog",
            },
            {
                "product_id": 11,
                "name": "Teclado Teste Beta",
                "quantity": 1,
                "name_source": "current_catalog",
            },
        ),
        "total": Decimal("1500.00"),
        "tracking_code": "TRK-5001",
        "estimated_delivery": date(2026, 11, 3),
        "estimate_status": "due_today",
        "business_date": date(2026, 11, 3),
        "provenance": {
            "prepared_at": datetime(2026, 11, 2, 12, 0, tzinfo=UTC),
            "sources": (by_table["orders"], by_table["order_items"], by_table["products"]),
        },
    }


# --- O02-O07: no access returns the same None -----------------------------------------


def test_o02_other_customers_order_is_none(orders_kit, orders_db):
    assert lookup(orders_db, 6001, C1, orders_kit.ClockSpy(orders_kit.FIXED)) is None


def test_o03_missing_order_is_none(orders_kit, orders_db):
    assert lookup(orders_db, 9999, C1, orders_kit.ClockSpy(orders_kit.FIXED)) is None


def test_o04_no_session_is_none(orders_kit, orders_db):
    clock = orders_kit.ClockSpy(orders_kit.FIXED)

    assert lookup(orders_db, 5001, None, clock) is None
    assert lookup(orders_db, 6001, None, clock) is None
    assert clock.calls == 0


def test_o05_second_customer_sees_only_own_order(orders_kit, orders_db):
    clock = orders_kit.ClockSpy(orders_kit.FIXED)

    view = lookup(orders_db, 6001, C2, clock)
    assert view is not None
    assert view.order_id == 6001
    assert view.status == "shipped"
    assert view.total == Decimal("777.77")
    assert view.tracking_code == "TRK-6001-SENTINEL"
    assert view.estimated_delivery == date(2026, 11, 5)
    assert view.estimate_status == "upcoming"

    assert lookup(orders_db, 5001, C2, clock) is None


def test_o06_customer_without_orders_and_unknown_customer(orders_kit, orders_db):
    clock = orders_kit.ClockSpy(orders_kit.FIXED)

    for customer in (C3, CustomerContext(424242)):
        for order_id in (5001, 6001):
            assert lookup(orders_db, order_id, customer, clock) is None
    assert clock.calls == 0


def test_o07_largest_integer_order_id_is_none(orders_kit, orders_db):
    assert lookup(orders_db, 2**63 - 1, C1, orders_kit.ClockSpy(orders_kit.FIXED)) is None


# --- O16, O17: historical total and stored status -------------------------------------


def test_o16_total_is_the_stored_decimal(orders_kit, orders_db):
    clock = orders_kit.ClockSpy(orders_kit.FIXED)
    stored = orders_kit.query(
        orders_db,
        "SELECT order_id, total_cents FROM orders WHERE order_id IN (?, ?) ORDER BY order_id",
        (5001, 5004),
    )
    assert stored == [(5001, 150000), (5004, 8970)]

    first = lookup(orders_db, 5001, C1, clock)
    assert type(first.total) is Decimal
    assert first.total == Decimal("1500.00")
    assert str(first.total) == "1500.00"
    # Hand-calculated current sums of the plan: table 2200.00, effective 2100.00.
    assert first.total != Decimal("2200.00")
    assert first.total != Decimal("2100.00")

    second = lookup(orders_db, 5004, C1, clock)
    assert type(second.total) is Decimal
    assert str(second.total) == "89.70"


def test_o17_status_exactly_as_stored(orders_kit, orders_db):
    clock = orders_kit.ClockSpy(orders_kit.FIXED)
    cases = [
        (5001, C1, "shipped"),
        (5002, C1, "pending"),
        (5003, C1, "delivered"),
        (5004, C1, "cancelled"),
        (6002, C2, "confirmed"),
    ]

    for order_id, customer, status in cases:
        assert lookup(orders_db, order_id, customer, clock).status == status


# --- O18-O20: tracking, estimate and no invented date ---------------------------------


def test_o18_absent_tracking_and_estimate(orders_kit, orders_db):
    view = lookup(orders_db, 5002, C1, orders_kit.ClockSpy(orders_kit.FIXED))

    assert view.tracking_code is None
    assert view.estimated_delivery is None
    assert view.estimate_status == "not_registered"


def test_o19_estimate_comparison_keeps_registered_date(orders_kit, orders_db):
    clock = orders_kit.ClockSpy(orders_kit.FIXED)
    cases = [
        (5003, "passed", date(2026, 11, 2)),
        (5001, "due_today", date(2026, 11, 3)),
        (5004, "upcoming", date(2026, 11, 4)),
    ]

    for order_id, status, estimate in cases:
        view = lookup(orders_db, order_id, C1, clock)
        assert view.estimate_status == status
        assert view.estimated_delivery == estimate
        date_fields = {
            field.name
            for field in dataclasses.fields(view)
            if isinstance(getattr(view, field.name), date)
        }
        assert date_fields == {"estimated_delivery", "business_date"}


def test_o20_no_receipt_eligibility_or_deadline_field(orders_kit, orders_db):
    names = {field.name for field in dataclasses.fields(OrderView)}
    forbidden = ("receipt", "received", "delivered", "eligib", "deadline", "return", "confirm")
    assert not [name for name in names if any(word in name for word in forbidden)]

    clock = orders_kit.ClockSpy(orders_kit.FIXED)
    delivered = lookup(orders_db, 5003, C1, clock)
    cancelled = lookup(orders_db, 5004, C1, clock)
    assert delivered.status == "delivered"
    assert delivered.estimate_status == "passed"
    assert cancelled.status == "cancelled"
    assert cancelled.estimate_status == "upcoming"


# --- O21: business date in UTC-03:00 for six clocks -----------------------------------

UTC_MINUS_3 = timezone(timedelta(hours=-3))
UTC_PLUS_14 = timezone(timedelta(hours=14))
# clock, business date, expected status for 5001, 5002, 5003, 5004 (C1) and 6001 (C2).
CLOCK_TABLE = [
    (
        datetime(2026, 11, 3, 15, 0, tzinfo=UTC),
        date(2026, 11, 3),
        ("due_today", "not_registered", "passed", "upcoming", "upcoming"),
    ),
    (
        datetime(2026, 11, 4, 2, 59, 59, tzinfo=UTC),
        date(2026, 11, 3),
        ("due_today", "not_registered", "passed", "upcoming", "upcoming"),
    ),
    (
        datetime(2026, 11, 4, 3, 0, 0, tzinfo=UTC),
        date(2026, 11, 4),
        ("passed", "not_registered", "passed", "due_today", "upcoming"),
    ),
    (
        datetime(2026, 11, 3, 23, 30, tzinfo=UTC_MINUS_3),
        date(2026, 11, 3),
        ("due_today", "not_registered", "passed", "upcoming", "upcoming"),
    ),
    (
        datetime(2026, 11, 4, 10, 0, tzinfo=UTC_PLUS_14),
        date(2026, 11, 3),
        ("due_today", "not_registered", "passed", "upcoming", "upcoming"),
    ),
    (
        datetime(2026, 11, 3, 2, 59, 59, tzinfo=UTC),
        date(2026, 11, 2),
        ("upcoming", "not_registered", "due_today", "upcoming", "upcoming"),
    ),
]


@pytest.mark.parametrize(
    ("instant", "business_date", "statuses"),
    CLOCK_TABLE,
    ids=["fixed", "utc_0259", "utc_0300", "minus3_2330", "plus14_1000", "prev_utc_0259"],
)
def test_o21_business_date_and_estimate_status(
    orders_kit, orders_db, instant, business_date, statuses
):
    requests = [(5001, C1), (5002, C1), (5003, C1), (5004, C1), (6001, C2)]

    for (order_id, customer), status in zip(requests, statuses, strict=True):
        view = lookup(orders_db, order_id, customer, orders_kit.ClockSpy(instant))
        assert view.business_date == business_date, order_id
        assert view.estimate_status == status, order_id


# --- O22: clock contract and call count -----------------------------------------------


def test_o22_naive_clock_is_rejected(orders_kit, orders_db):
    clock = orders_kit.ClockSpy(datetime(2026, 11, 3, 12))

    with pytest.raises(ValueError) as caught:
        lookup(orders_db, 5001, C1, clock)

    assert str(caught.value) == "clock must return a timezone-aware datetime"
    assert type(caught.value) is ValueError


def test_o22_clock_returning_date_is_rejected(orders_kit, orders_db):
    with pytest.raises(TypeError):
        lookup(orders_db, 5001, C1, orders_kit.ClockSpy(date(2026, 11, 3)))


def test_o22_clock_read_once_and_only_for_authorized_order(orders_kit, orders_db):
    clock = orders_kit.ClockSpy(orders_kit.FIXED)

    assert lookup(orders_db, 5001, C1, clock) is not None
    assert clock.calls == 1
    assert lookup(orders_db, 6001, C1, clock) is None
    assert lookup(orders_db, 9999, C1, clock) is None
    assert clock.calls == 1
