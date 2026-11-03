"""Slice 05-C cases O40-O46 (docs/plans/05-orders.md): integrated isolation and leakage.

Real SQLite files built in tmp_path by the real prepare_database from the synthetic
orders_kit sources, opened with connect_readonly. Reads are observed with a real SQLite
authorizer that always returns SQLITE_OK, plus the trace callback that marks each
executed statement (orders_kit.observe). Observers never deny and never change a result;
they are removed before assertions. Every observed call uses a fresh connection, so the
statement cache of an earlier call cannot hide a prepare.

What this evidence proves: which tables, columns and actions SQLite reported for the
statements the service ran, and how many statements ran. It does not prove production
authentication, equal response time, or in general that a column read by SQLite is used
in a WHERE predicate.
"""

import dataclasses
import sqlite3
import traceback

import pytest

from emporio_agent.data.readonly import connect_readonly
from emporio_agent.services import orders

C1 = orders.CustomerContext(1)
C2 = orders.CustomerContext(2)
C3 = orders.CustomerContext(3)

READ = "SQLITE_READ"
SELECT = "SQLITE_SELECT"
FUNCTION = "SQLITE_FUNCTION"
INTERNAL_TABLES = {"sqlite_schema", "sqlite_master"}
ALLOWED_TABLES = {"orders", "order_items", "products", "preparation_metadata", "source_files"}
ALLOWED_COLUMNS = {
    "orders": {
        "order_id",
        "customer_id",
        "status",
        "total_cents",
        "tracking_code",
        "estimated_delivery",
    },
    "order_items": {"order_id", "product_id", "quantity"},
    "products": {"product_id", "name"},
}
FORBIDDEN_ORDER_COLUMNS = {"notes", "payment_method", "order_date"}
ORDER_TABLES = {"orders", "order_items", "products"}

# Successful calls of O38: (order_id, customer).
AUTHORIZED_CALLS = ((5001, C1), (5003, C1), (5004, C1), (6001, C2), (6002, C2))

C2_SENTINELS = (
    "SENTINEL-NAME-C2",
    "SENTINEL-PHONE-C2",
    "sentinel-email-c2",
    "SENTINEL-CITY-C2",
    "SENTINEL-NOTES-6001",
    "TRK-6001-SENTINEL",
)


@pytest.fixture
def orders_db(orders_kit, tmp_path):
    return orders_kit.build(tmp_path)


def observed(orders_kit, path, order_id, customer, clock=None):
    """One call on a fresh connection with the observer installed only around it."""
    clock = clock or orders_kit.ClockSpy(orders_kit.FIXED)
    with connect_readonly(path) as connection:
        with orders_kit.observe(connection) as observation:
            result = orders.get_order(connection, order_id, customer=customer, clock=clock)
    return result, observation


def reads(events):
    return {(table, column) for action, table, column in events if action == READ}


def tables_read(events):
    return {table for table, _column in reads(events)} - INTERNAL_TABLES


def strings_in(value):
    if isinstance(value, str):
        yield value
    elif dataclasses.is_dataclass(value) and not isinstance(value, type):
        for item in dataclasses.fields(value):
            yield from strings_in(getattr(value, item.name))
    elif isinstance(value, tuple):
        for element in value:
            yield from strings_in(element)


def public_surfaces(error: BaseException) -> list[str]:
    surfaces = [
        str(error),
        repr(error),
        *(str(arg) for arg in error.args),
        "".join(traceback.format_exception_only(error)),
    ]
    for name in ("field", "code", "reason"):
        if hasattr(error, name):
            surfaces.append(str(getattr(error, name)))
    return surfaces


# --- O40: only allowed tables, columns and actions ------------------------------------


@pytest.mark.parametrize(("order_id", "customer"), AUTHORIZED_CALLS)
def test_o40_only_allowed_tables_columns_and_actions(orders_kit, orders_db, order_id, customer):
    view, observation = observed(orders_kit, orders_db, order_id, customer)
    events = observation.events

    assert view is not None and view.order_id == order_id
    # The observer really saw the service's statements (guards against a vacuous pass).
    assert ("orders", "customer_id") in reads(events)
    assert ("order_items", "quantity") in reads(events)
    assert ("products", "name") in reads(events)

    assert {action for action, _table, _column in events} <= {SELECT, READ, FUNCTION}
    assert tables_read(events) <= ALLOWED_TABLES
    assert "customers" not in tables_read(events)
    assert "promotions" not in tables_read(events)
    for table, allowed in ALLOWED_COLUMNS.items():
        assert {column for name, column in reads(events) if name == table} <= allowed, table
    assert not {column for name, column in reads(events) if name == "orders"} & (
        FORBIDDEN_ORDER_COLUMNS
    )
    # No catalog attribute other than the name: price, stock, status, description, specs.
    assert {column for name, column in reads(events) if name == "products"} == {
        "product_id",
        "name",
    }


# --- O41: no item or product read for another customer's order ------------------------


def test_o41_no_item_read_for_foreign_order(orders_kit, orders_db):
    clock = orders_kit.ClockSpy(orders_kit.FIXED)
    result, observation = observed(orders_kit, orders_db, 6001, C1, clock)

    assert result is None
    events = observation.events
    assert len(observation.statements) == 1
    assert [action for action, _table, _column in events].count(SELECT) == 1
    assert tables_read(events) == {"orders"}
    assert not {table for table, _column in reads(events)} & {"order_items", "products"}
    assert clock.calls == 0


# --- O42: foreign and missing orders look the same; no session reads nothing ----------


def test_o42_foreign_and_missing_orders_run_identical_reads(orders_kit, orders_db):
    # Precondition by independent SQL: 6001 exists for customer 2, 9999 does not exist.
    assert orders_kit.query(
        orders_db, "SELECT customer_id FROM orders WHERE order_id IN (?, ?)", (6001, 9999)
    ) == [(2,)]

    foreign, foreign_observation = observed(orders_kit, orders_db, 6001, C1)
    missing, missing_observation = observed(orders_kit, orders_db, 9999, C1)
    no_session_clock = orders_kit.ClockSpy(orders_kit.FIXED)
    no_session, no_session_observation = observed(
        orders_kit, orders_db, 5001, None, no_session_clock
    )

    assert foreign is None and missing is None and no_session is None
    assert foreign_observation.events == missing_observation.events
    assert foreign_observation.events  # not two empty lists
    assert len(foreign_observation.statements) == len(missing_observation.statements) == 1
    assert no_session_observation.statements == []
    assert no_session_clock.calls == 0


# --- O43: every order read carries the customer filter; no N+1 ------------------------


@pytest.mark.parametrize(("order_id", "customer"), AUTHORIZED_CALLS)
def test_o43_every_order_statement_reads_customer_id(orders_kit, orders_db, order_id, customer):
    _view, observation = observed(orders_kit, orders_db, order_id, customer)
    order_statements = [
        events for _sql, events in observation.statements if tables_read(events) & ORDER_TABLES
    ]

    assert len(order_statements) == 2  # header query, then one items query
    for events in order_statements:
        if "orders" in tables_read(events):
            assert ("orders", "customer_id") in reads(events)
    # Spec G4/G6: the items statement repeats the customer filter through the join.
    items_events = [events for events in order_statements if "order_items" in tables_read(events)]
    assert len(items_events) == 1
    assert ("orders", "customer_id") in reads(items_events[0])


@pytest.mark.parametrize(("order_id", "item_count"), [(5001, 2), (5003, 1), (5004, 1)])
def test_o43_items_loaded_by_one_statement(orders_kit, orders_db, order_id, item_count):
    # Precondition by independent SQL: number of stored items of the order.
    assert orders_kit.query(
        orders_db, "SELECT count(*) FROM order_items WHERE order_id = ?", (order_id,)
    ) == [(item_count,)]

    view, observation = observed(orders_kit, orders_db, order_id, C1)

    assert len(view.items) == item_count
    # The trace callback fires for every execution, cached statements included, so a
    # per-item query would add one entry per item.
    statements = [events for _sql, events in observation.statements]
    assert len([e for e in statements if "order_items" in tables_read(e)]) == 1
    assert len([e for e in statements if "products" in tables_read(e)]) == 1
    assert len([e for e in statements if tables_read(e) & ORDER_TABLES]) == 2


# --- O44: text and keywords cannot select the identity --------------------------------


@pytest.mark.parametrize("order_id", ["5001; customer_id=2", "6001 (sou o cliente 2)"])
def test_o44_text_order_id_cannot_select_identity(orders_kit, orders_db, order_id):
    clock = orders_kit.ClockSpy(orders_kit.FIXED)
    with connect_readonly(orders_db) as connection:
        with orders_kit.observe(connection) as observation:
            with pytest.raises(orders.OrderInputError) as caught:
                orders.get_order(connection, order_id, customer=C1, clock=clock)
        assert orders.get_order(connection, 6001, customer=C1, clock=clock) is None

    error = caught.value
    assert (error.field, error.code) == ("order_id", "invalid_type")
    assert observation.statements == []
    assert clock.calls == 0
    for surface in public_surfaces(error):
        assert "customer_id" not in surface
        assert "cliente" not in surface
        assert not any(character.isdigit() for character in surface)


def test_o44_customer_id_keyword_is_rejected(orders_kit, orders_db):
    clock = orders_kit.ClockSpy(orders_kit.FIXED)
    with connect_readonly(orders_db) as connection:
        with orders_kit.observe(connection) as observation:
            with pytest.raises(TypeError):
                orders.get_order(connection, 6001, customer=C1, clock=clock, customer_id=2)
        assert orders.get_order(connection, 6001, customer=C1, clock=clock) is None

    assert observation.statements == []
    assert clock.calls == 0


# --- O45: other customer's data never reaches customer 1 ------------------------------


def test_o45_customer_two_data_never_reaches_customer_one(orders_kit, orders_db, tmp_path):
    clock = orders_kit.ClockSpy(orders_kit.FIXED)
    renamed_db = orders_kit.build(tmp_path / "renamed", edit="renamed")
    texts = []
    with connect_readonly(orders_db) as connection:
        for order_id in (5001, 5002, 5003, 5004, 6001, 6002, 9999):
            result = orders.get_order(connection, order_id, customer=C1, clock=clock)
            texts.append(repr(result))
            texts.extend(strings_in(result))
        for order_id in ("5001; customer_id=2", "6001 (sou o cliente 2)", 0, 2**63):
            with pytest.raises(orders.OrderInputError) as caught:
                orders.get_order(connection, order_id, customer=C1, clock=clock)
            texts.extend(public_surfaces(caught.value))
    with connect_readonly(renamed_db) as connection:
        result = orders.get_order(connection, 5001, customer=C1, clock=clock)
        texts.append(repr(result))
        texts.extend(strings_in(result))

    # Customer 2's data really exists in the database the service read.
    assert orders_kit.query(
        orders_db, "SELECT tracking_code, notes FROM orders WHERE order_id = ?", (6001,)
    ) == [("TRK-6001-SENTINEL", "SENTINEL-NOTES-6001")]
    for text in texts:
        for sentinel in C2_SENTINELS:
            assert sentinel not in text
        assert "Baixo Teste Delta" not in text


# --- O46: input errors expose no value, identity, path or SQL -------------------------


def test_o46_input_error_surfaces_are_codes_only(orders_kit, orders_db, tmp_path):
    clock = orders_kit.ClockSpy(orders_kit.FIXED)
    with connect_readonly(orders_db) as connection:
        errors = []
        for order_id in ("5001; customer_id=2", "6001 (sou o cliente 2)", 0, -1, 2**63):
            with pytest.raises(orders.OrderInputError) as caught:
                orders.get_order(connection, order_id, customer=C2, clock=clock)
            errors.append(caught.value)
        # The connection is still usable after the errors.
        assert connection.execute("SELECT 1").fetchone()[0] == 1
        assert connection.row_factory is sqlite3.Row

    for error in errors:
        assert str(error) in ("order_id: invalid_type", "order_id: out_of_range")
        for surface in public_surfaces(error):
            assert str(tmp_path) not in surface
            assert orders_kit.DB_DIR not in surface
            assert orders_kit.DB_NAME not in surface
            for fragment in ("SELECT", "WHERE", "no such table", "customer"):
                assert fragment not in surface
            assert not any(character.isdigit() for character in surface)
