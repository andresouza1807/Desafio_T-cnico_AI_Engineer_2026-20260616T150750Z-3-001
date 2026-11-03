"""Slice 05-C cases O47-O55 (docs/plans/05-orders.md, spec P-11 and P-13): data errors.

Each case builds a real database with orders_kit, damages one value (or drops one table)
with orders_kit.corrupt through a separate writable connection, proves the damage with
independent SQL (typeof and value) and only then opens the file with connect_readonly.
The public surfaces checked are those of spec G13: str, repr, args, the reason attribute
and traceback.format_exception_only. The full traceback and __context__ are outside the
guarantee and are not inspected for text.
"""

import traceback
from pathlib import Path

import pytest

from emporio_agent.data.readonly import DatabaseUnavailableError, connect_readonly
from emporio_agent.services import orders

C1 = orders.CustomerContext(1)
C2 = orders.CustomerContext(2)

SENTINELS = (
    "SENTINEL-NAME-C1",
    "SENTINEL-PHONE-C1",
    "sentinel-email-c1",
    "SENTINEL-CITY-C1",
    "SENTINEL-NAME-C2",
    "SENTINEL-PHONE-C2",
    "sentinel-email-c2",
    "SENTINEL-CITY-C2",
    "SENTINEL-NOTES-5001",
    "SENTINEL-NOTES-6001",
    "TRK-6001-SENTINEL",
    "TRK-5001",
    "SENTINEL-TOTAL",
    "SENTINEL-FOREIGN",
)
SQL_FRAGMENTS = ("SELECT", "WHERE", "FROM", "JOIN", "no such", "order_items", "sqlite")


def corrupted(orders_kit, tmp_path: Path, variant: str) -> Path:
    path = orders_kit.build(tmp_path)
    orders_kit.corrupt(path, variant)
    return path


def public_surfaces(error: BaseException) -> list[str]:
    return [
        str(error),
        repr(error),
        *(str(arg) for arg in error.args),
        str(error.reason),
        "".join(traceback.format_exception_only(error)),
    ]


def assert_clean_surfaces(error: BaseException, tmp_path: Path, orders_kit, *values: str):
    for surface in public_surfaces(error):
        assert str(tmp_path) not in surface
        assert orders_kit.DB_DIR not in surface
        assert orders_kit.DB_NAME not in surface
        for text in (*SENTINELS, *SQL_FRAGMENTS, *values):
            assert text not in surface
        # No order ID, customer ID or numeric cell value.
        assert not any(character.isdigit() for character in surface)


def data_error(orders_kit, path: Path, order_id: int, customer, reason: str):
    clock = orders_kit.ClockSpy(orders_kit.FIXED)
    with connect_readonly(path) as connection:
        with pytest.raises(orders.OrderDataError) as caught:
            orders.get_order(connection, order_id, customer=customer, clock=clock)
    error = caught.value
    assert type(error) is orders.OrderDataError
    assert error.reason == reason
    assert str(error) == reason
    assert error.args == (reason,)
    assert error.__cause__ is None
    assert error.__suppress_context__ is True
    assert not isinstance(error, (TypeError, ValueError))
    return error


ORDER_CELL = "SELECT typeof({column}), {column} FROM orders WHERE order_id = ?"
ITEM_CELL = (
    "SELECT typeof(quantity), quantity FROM order_items WHERE order_id = ? AND product_id = ?"
)
PRODUCT_CELL = "SELECT typeof(name), name FROM products WHERE product_id = ?"

# variant: (independent SQL, parameters, stored rows, text that must not leak)
INVALID_ROWS = {
    # O47
    "status_unknown": (ORDER_CELL.format(column="status"), (5001,), [("text", "lost")], "lost"),
    # O48
    "total_negative": (
        ORDER_CELL.format(column="total_cents"),
        (5001,),
        [("integer", -1)],
        "-1",
    ),
    "total_text": (
        ORDER_CELL.format(column="total_cents"),
        (5001,),
        [("text", "SENTINEL-TOTAL")],
        "SENTINEL-TOTAL",
    ),
    "total_real": (
        ORDER_CELL.format(column="total_cents"),
        (5001,),
        [("real", 150000.5)],
        "150000",
    ),
    # O49
    "tracking_blank": (
        ORDER_CELL.format(column="tracking_code"),
        (5001,),
        [("text", "   ")],
        "TRK",
    ),
    "tracking_int": (
        ORDER_CELL.format(column="tracking_code"),
        (5001,),
        [("integer", 5001)],
        "5001",
    ),
    # O50
    "estimate_month": (
        ORDER_CELL.format(column="estimated_delivery"),
        (5001,),
        [("text", "2026-13-01")],
        "2026-13-01",
    ),
    "estimate_format": (
        ORDER_CELL.format(column="estimated_delivery"),
        (5001,),
        [("text", "03/11/2026")],
        "03/11/2026",
    ),
    "estimate_int": (
        ORDER_CELL.format(column="estimated_delivery"),
        (5001,),
        [("integer", 20261103)],
        "20261103",
    ),
    # O51
    "quantity_zero": (ITEM_CELL, (5001, 10), [("integer", 0)], "quantity"),
    "quantity_text": (ITEM_CELL, (5001, 10), [("text", "2")], "quantity"),
    # O52
    "name_blank": (PRODUCT_CELL, (10,), [("text", "   ")], "Violão"),
}

CASE_OF = {
    "status_unknown": "O47",
    "total_negative": "O48",
    "total_text": "O48",
    "total_real": "O48",
    "tracking_blank": "O49",
    "tracking_int": "O49",
    "estimate_month": "O50",
    "estimate_format": "O50",
    "estimate_int": "O50",
    "quantity_zero": "O51",
    "quantity_text": "O51",
    "name_blank": "O52",
}


# --- O47-O52: every damaged value used by the service is invalid_row -----------------


@pytest.mark.parametrize(
    "variant", list(INVALID_ROWS), ids=[f"{CASE_OF[v]}-{v}" for v in INVALID_ROWS]
)
def test_o47_o52_damaged_value_is_invalid_row(orders_kit, tmp_path, variant):
    path = corrupted(orders_kit, tmp_path, variant)
    sql, parameters, stored, leaked = INVALID_ROWS[variant]
    assert orders_kit.query(path, sql, parameters) == stored

    error = data_error(orders_kit, path, 5001, C1, "invalid_row")

    assert_clean_surfaces(error, tmp_path, orders_kit, leaked)


def test_o52_missing_product_is_invalid_row_not_a_shorter_view(orders_kit, tmp_path):
    path = corrupted(orders_kit, tmp_path, "product_missing")
    # The product row is gone while its order item remains: an inner join would hide it.
    assert orders_kit.query(path, "SELECT count(*) FROM products WHERE product_id = ?", (11,)) == [
        (0,)
    ]
    assert orders_kit.query(
        path,
        "SELECT product_id, quantity FROM order_items WHERE order_id = ? ORDER BY product_id",
        (5001,),
    ) == [(10, 2), (11, 1)]

    error = data_error(orders_kit, path, 5001, C1, "invalid_row")

    assert_clean_surfaces(error, tmp_path, orders_kit, "Teclado")


# --- O53: damage in another customer's order stays there ------------------------------


def test_o53_foreign_damage_does_not_affect_or_reveal(orders_kit, tmp_path):
    path = corrupted(orders_kit, tmp_path, "foreign_status")
    assert orders_kit.query(
        path, "SELECT typeof(status), status, customer_id FROM orders WHERE order_id = ?", (6001,)
    ) == [("text", "SENTINEL-FOREIGN", 2)]
    clock = orders_kit.ClockSpy(orders_kit.FIXED)

    with connect_readonly(path) as connection:
        own = orders.get_order(connection, 5001, customer=C1, clock=clock)
        foreign = orders.get_order(connection, 6001, customer=C1, clock=clock)

    assert own is not None
    assert (own.order_id, own.status, format(own.total, "f")) == (5001, "shipped", "1500.00")
    assert [(item.product_id, item.quantity) for item in own.items] == [(10, 2), (11, 1)]
    assert foreign is None
    assert "SENTINEL-FOREIGN" not in repr(own)
    assert "SENTINEL-FOREIGN" not in repr(foreign)

    error = data_error(orders_kit, path, 6001, C2, "invalid_row")
    assert_clean_surfaces(error, tmp_path, orders_kit)


# --- O54: SQLite failures become query_failed with the chain suppressed --------------


@pytest.mark.parametrize(
    ("variant", "table"), [("drop_items", "order_items"), ("drop_orders", "orders")]
)
def test_o54_sqlite_failure_is_query_failed(orders_kit, tmp_path, variant, table):
    path = corrupted(orders_kit, tmp_path, variant)
    assert orders_kit.query(
        path, "SELECT count(*) FROM sqlite_schema WHERE type = 'table' AND name = ?", (table,)
    ) == [(0,)]

    error = data_error(orders_kit, path, 5001, C1, "query_failed")

    # Not the bare table name: the qualified class name contains "orders".
    assert_clean_surfaces(error, tmp_path, orders_kit, f"table: {table}", f" {table}:")


# --- O55: unusable databases fail in connect_readonly, before the service -------------


def test_o55_missing_file_fails_before_service(orders_kit, tmp_path):
    path = tmp_path / orders_kit.DB_DIR / orders_kit.DB_NAME
    assert not path.exists()
    entered = False

    with pytest.raises(DatabaseUnavailableError) as caught:
        with connect_readonly(path) as connection:
            entered = True
            orders.get_order(connection, 5001, customer=C1, clock=orders_kit.ClockSpy(None))

    assert entered is False
    assert caught.value.reason == "missing"
    for surface in (str(caught.value), repr(caught.value), *map(str, caught.value.args)):
        assert str(tmp_path) not in surface
        assert orders_kit.DB_DIR not in surface


def test_o55_schema_v2_fails_before_service(orders_kit, tmp_path):
    path = corrupted(orders_kit, tmp_path, "schema_v2")
    assert orders_kit.query(
        path, "SELECT value FROM preparation_metadata WHERE key = ?", ("schema_version",)
    ) == [("2",)]
    clock = orders_kit.ClockSpy(orders_kit.FIXED)
    entered = False

    with pytest.raises(DatabaseUnavailableError) as caught:
        with connect_readonly(path) as connection:
            entered = True
            orders.get_order(connection, 5001, customer=C1, clock=clock)

    assert entered is False
    assert clock.calls == 0
    assert caught.value.reason == "schema_mismatch"
    for surface in (str(caught.value), repr(caught.value), *map(str, caught.value.args)):
        assert str(tmp_path) not in surface
        assert orders_kit.DB_DIR not in surface
