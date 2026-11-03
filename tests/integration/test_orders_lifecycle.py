"""Slice 05-C cases O56-O58 (docs/plans/05-orders.md, spec P-14): connection lifecycle and
no write.

The caller owns the connection from connect_readonly. These tests show, on real SQLite
files, that the service neither closes, commits, rolls back nor changes row_factory of
that connection after a success, a None, an input error, invalid_row or query_failed;
that sqlite3.ProgrammingError from a closed connection propagates unchanged; and that the
database file, its directory and every table are byte- and row-identical after the calls.
Snapshots use a separate mode=ro connection (orders_kit.snapshot), never connect_readonly.
"""

import sqlite3

import pytest

from emporio_agent.data.readonly import connect_readonly
from emporio_agent.services import orders

C1 = orders.CustomerContext(1)
C2 = orders.CustomerContext(2)
C3 = orders.CustomerContext(3)


@pytest.fixture
def orders_db(orders_kit, tmp_path):
    return orders_kit.build(tmp_path)


def assert_caller_state(connection: sqlite3.Connection, *, in_transaction: bool) -> None:
    assert connection.row_factory is sqlite3.Row
    assert connection.in_transaction is in_transaction
    assert connection.execute("SELECT 1").fetchone()[0] == 1


# --- O56: reusable connection, row_factory and transaction state preserved -----------


def test_o56_connection_reusable_after_success_none_and_input_error(orders_kit, orders_db):
    clock = orders_kit.ClockSpy(orders_kit.FIXED)
    with connect_readonly(orders_db) as connection:
        first = orders.get_order(connection, 5001, customer=C1, clock=clock)
        assert first is not None
        assert_caller_state(connection, in_transaction=False)

        assert orders.get_order(connection, 5001, customer=C1, clock=clock) == first
        assert_caller_state(connection, in_transaction=False)

        assert orders.get_order(connection, 6001, customer=C1, clock=clock) is None
        assert_caller_state(connection, in_transaction=False)
        assert orders.get_order(connection, 5001, customer=C1, clock=clock) == first

        with pytest.raises(orders.OrderInputError):
            orders.get_order(connection, 0, customer=C1, clock=clock)
        assert_caller_state(connection, in_transaction=False)
        assert orders.get_order(connection, 5001, customer=C1, clock=clock) == first
        assert_caller_state(connection, in_transaction=False)


def test_o56_connection_reusable_after_invalid_row(orders_kit, tmp_path):
    path = orders_kit.build(tmp_path)
    orders_kit.corrupt(path, "status_unknown")
    assert orders_kit.query(path, "SELECT status FROM orders WHERE order_id = ?", (5001,)) == [
        ("lost",)
    ]
    clock = orders_kit.ClockSpy(orders_kit.FIXED)

    with connect_readonly(path) as connection:
        with pytest.raises(orders.OrderDataError) as caught:
            orders.get_order(connection, 5001, customer=C1, clock=clock)
        assert caught.value.reason == "invalid_row"
        assert_caller_state(connection, in_transaction=False)

        view = orders.get_order(connection, 5002, customer=C1, clock=clock)
        assert (view.order_id, view.status, view.items) == (5002, "pending", ())
        assert format(view.total, "f") == "250.00"
        assert_caller_state(connection, in_transaction=False)


def test_o56_connection_reusable_after_query_failed(orders_kit, tmp_path):
    path = orders_kit.build(tmp_path)
    orders_kit.corrupt(path, "drop_items")
    clock = orders_kit.ClockSpy(orders_kit.FIXED)

    with connect_readonly(path) as connection:
        with pytest.raises(orders.OrderDataError) as caught:
            orders.get_order(connection, 5001, customer=C1, clock=clock)
        assert caught.value.reason == "query_failed"
        assert_caller_state(connection, in_transaction=False)

        # The order query still works on the same connection.
        assert orders.get_order(connection, 6001, customer=C1, clock=clock) is None
        assert_caller_state(connection, in_transaction=False)


@pytest.mark.parametrize(
    ("variant", "order_id", "outcome"),
    [
        (None, 5001, "view"),
        (None, 6001, "none"),
        (None, 0, "invalid_input"),
        ("status_unknown", 5001, "invalid_row"),
        ("drop_items", 5001, "query_failed"),
    ],
)
def test_o56_caller_transaction_is_neither_committed_nor_rolled_back(
    orders_kit, tmp_path, variant, order_id, outcome
):
    path = orders_kit.build(tmp_path)
    if variant is not None:
        orders_kit.corrupt(path, variant)
    clock = orders_kit.ClockSpy(orders_kit.FIXED)

    with connect_readonly(path) as connection:
        connection.execute("BEGIN")
        assert_caller_state(connection, in_transaction=True)

        if outcome == "view":
            assert orders.get_order(connection, order_id, customer=C1, clock=clock) is not None
        elif outcome == "none":
            assert orders.get_order(connection, order_id, customer=C1, clock=clock) is None
        elif outcome == "invalid_input":
            with pytest.raises(orders.OrderInputError):
                orders.get_order(connection, order_id, customer=C1, clock=clock)
        else:
            with pytest.raises(orders.OrderDataError) as caught:
                orders.get_order(connection, order_id, customer=C1, clock=clock)
            assert caught.value.reason == outcome

        # The caller's transaction is still open: no commit, no rollback by the service.
        assert_caller_state(connection, in_transaction=True)
        connection.rollback()
        assert_caller_state(connection, in_transaction=False)


# --- O57: the service never closes; ProgrammingError propagates unchanged ------------


def test_o57_service_never_closes_the_connection(orders_kit, orders_db):
    clock = orders_kit.ClockSpy(orders_kit.FIXED)
    with connect_readonly(orders_db) as connection:
        first = orders.get_order(connection, 5001, customer=C1, clock=clock)
        assert orders.get_order(connection, 6001, customer=C1, clock=clock) is None
        # Still open inside the block: usable by the caller and by the service.
        assert_caller_state(connection, in_transaction=False)
        assert orders.get_order(connection, 5001, customer=C1, clock=clock) == first

    # Closed by connect_readonly on exit, not before.
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")


def test_o57_closed_connection_programming_error_is_not_converted(orders_kit, orders_db):
    with connect_readonly(orders_db) as connection:
        pass
    clock = orders_kit.ClockSpy(orders_kit.FIXED)

    with pytest.raises(sqlite3.ProgrammingError) as caught:
        orders.get_order(connection, 5001, customer=C1, clock=clock)

    assert type(caught.value) is sqlite3.ProgrammingError
    assert not isinstance(caught.value, orders.OrderDataError)
    assert clock.calls == 0


# --- O58: no write: file, directory and every table unchanged ------------------------


def test_o58_lookups_leave_database_unchanged(orders_kit, orders_db):
    before = orders_kit.snapshot(orders_db)
    assert before["listing"] == [orders_kit.DB_NAME]
    clock = orders_kit.ClockSpy(orders_kit.FIXED)

    with connect_readonly(orders_db) as connection:
        for order_id in (5001, 5002, 5003, 5004, 6001, 6002, 9999, 2**63 - 1):
            for customer in (C1, C2, C3, orders.CustomerContext(424242), None):
                orders.get_order(connection, order_id, customer=customer, clock=clock)
        for order_id in (0, -1, 2**63, True, "5001"):
            with pytest.raises(orders.OrderInputError):
                orders.get_order(connection, order_id, customer=C1, clock=clock)
        with pytest.raises(TypeError):
            orders.get_order(connection, 5001, customer=1, clock=clock)
    # A closed connection: the service raises and writes nothing.
    with pytest.raises(sqlite3.ProgrammingError):
        orders.get_order(connection, 5001, customer=C1, clock=clock)

    after = orders_kit.snapshot(orders_db)
    assert after == before
    assert after["listing"] == [orders_kit.DB_NAME]  # no journal, WAL or temporary file


@pytest.mark.parametrize(
    ("variant", "reason"), [("status_unknown", "invalid_row"), ("drop_items", "query_failed")]
)
def test_o58_failed_lookups_leave_database_unchanged(orders_kit, tmp_path, variant, reason):
    path = orders_kit.build(tmp_path)
    orders_kit.corrupt(path, variant)
    before = orders_kit.snapshot(path)
    clock = orders_kit.ClockSpy(orders_kit.FIXED)

    with connect_readonly(path) as connection:
        with pytest.raises(orders.OrderDataError) as caught:
            orders.get_order(connection, 5001, customer=C1, clock=clock)
    assert caught.value.reason == reason

    after = orders_kit.snapshot(path)
    assert after == before
    assert after["listing"] == [orders_kit.DB_NAME]
