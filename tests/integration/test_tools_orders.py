"""Slice 06-C integration cases TL45-TL55 (docs/plans/06-tools.md).

get_order through execute_tool against real SQLite files built in tmp_path by the real
prepare_database from the synthetic orders_kit sources, opened through the real
connect_readonly and observed by the delegating tools_kit spy. The customer, clock and
paths come only from a real ToolContext. Expected values are literals from Spec 05/06
and the orders fixture, independent SQL reads and SHA-256 of the written source bytes.
No production serializer, authorization rule or query is the oracle. Nothing here mocks
authorization, SQL, dates or persistence.
"""

import dataclasses
import decimal
import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from emporio_agent.data.readonly import connect_readonly
from emporio_agent.services.orders import CustomerContext, OrderInputError, get_order

TOOL = "get_order"
ENVELOPE_KEYS = ["tool", "status", "data", "error"]
ORDER_KEYS = [
    "order_id",
    "status",
    "items",
    "total",
    "tracking_code",
    "estimated_delivery",
    "estimate_status",
    "business_date",
    "provenance",
]
ITEM_KEYS = ["product_id", "name", "quantity", "name_source"]
C1 = CustomerContext(1)
C2 = CustomerContext(2)
C3 = CustomerContext(3)
MAX_ID = 2**63 - 1

# Literal no-access payload of Spec 06 (examples): one answer for every no-access case.
NOT_ACCESSIBLE = (
    '{"tool":"get_order","status":"unavailable","data":null,'
    '"error":{"code":"order_not_accessible","field":null}}'
)
DATA_UNAVAILABLE = (
    '{"tool":"get_order","status":"unavailable","data":null,'
    '"error":{"code":"data_unavailable","field":null}}'
)
INTERNAL_ERROR = (
    '{"tool":"get_order","status":"internal_error","data":null,'
    '"error":{"code":"internal_error","field":null}}'
)

# Literal table from Spec 05 P-03 (approved service contract), written by hand. Each
# entry: (raw JSON value for the tool, Python value for the direct service call, code).
INVALID_IDS = [
    ("0", 0, "out_of_range"),
    ("-1", -1, "out_of_range"),
    (str(2**63), 2**63, "out_of_range"),
    (str(-(2**63)), -(2**63), "out_of_range"),
    ("true", True, "invalid_type"),
    ("false", False, "invalid_type"),
    ('"5001"', "5001", "invalid_type"),
    ("5001.0", 5001.0, "invalid_type"),
    ("1e3", Decimal("1e3"), "invalid_type"),
]

# Order date, payment method and notes of every fixture order: never in a payload.
EXCLUDED_TEXT = [
    "SENTINEL-",
    "example.invalid",
    "credit_3x",
    "credit_1x",
    '"pix"',
    "boleto",
    "debit",
    "Rua Exemplo",
    "2026-10-28",
    "2026-10-29",
    "2026-10-20",
    "2026-10-30",
    "2026-10-25",
    "2026-10-26",
    "Cliente Teste",
]
EXCLUDED_KEYS = {
    "customer_id",
    "customer",
    "notes",
    "payment_method",
    "order_date",
    "phone",
    "email",
    "city",
}


def invalid_payload(code):
    return (
        '{"tool":"get_order","status":"invalid_input","data":null,'
        f'"error":{{"code":"{code}","field":"order_id"}}}}'
    )


@pytest.fixture
def database(tmp_path, orders_kit):
    return orders_kit.build(tmp_path)


@pytest.fixture
def ctx(database, tools_kit):
    return tools_kit.context(database_path=database, customer=C1)


@pytest.fixture
def spy(monkeypatch, tools_kit):
    return tools_kit.connect_spy(monkeypatch)


def lookup(tools_kit, context, order_id):
    return tools_kit.run(TOOL, json.dumps({"order_id": order_id}), context)


def keys_of(value):
    if isinstance(value, dict):
        found = set(value)
        for item in value.values():
            found |= keys_of(item)
        return found
    if isinstance(value, list):
        found = set()
        for item in value:
            found |= keys_of(item)
        return found
    return set()


def assert_no_excluded(execution, body):
    for text in EXCLUDED_TEXT:
        assert text not in execution.payload, text
    assert not keys_of(body) & EXCLUDED_KEYS


def expected_provenance(tmp_path, orders_kit):
    """Independent oracle: hash and size of the source bytes written by the fixture."""
    sources = []
    for table, row_count in (("orders", 6), ("order_items", 6), ("products", 5)):
        written = (
            tmp_path / orders_kit.SOURCE_DIR / f"desafio_tecnico_ai_eng - {table}.csv"
        ).read_bytes()
        sources.append(
            {
                "table_name": table,
                "file_name": f"desafio_tecnico_ai_eng - {table}.csv",
                "sha256": hashlib.sha256(written).hexdigest(),
                "size_bytes": len(written),
                "row_count": row_count,
            }
        )
    return {"prepared_at": "2026-11-02T12:00:00+00:00", "sources": sources}


def item(product_id, name, quantity):
    return {
        "product_id": product_id,
        "name": name,
        "quantity": quantity,
        "name_source": "current_catalog",
    }


def order_5001(tmp_path, orders_kit):
    return {
        "order_id": 5001,
        "status": "shipped",
        "items": [item(10, "Violão Teste Alfa", 2), item(11, "Teclado Teste Beta", 1)],
        "total": "1500.00",
        "tracking_code": "TRK-5001",
        "estimated_delivery": "2026-11-03",
        "estimate_status": "due_today",
        "business_date": "2026-11-03",
        "provenance": expected_provenance(tmp_path, orders_kit),
    }


def assert_ok(execution, body):
    assert execution.tool == TOOL
    assert execution.status == "ok"
    assert execution.error_code is None
    assert list(body) == ENVELOPE_KEYS
    assert body["error"] is None
    assert list(body["data"]) == ORDER_KEYS
    for entry in body["data"]["items"]:
        assert list(entry) == ITEM_KEYS
    assert_no_excluded(execution, body)


# --- TL45: full projection of order 5001 ----------------------------------------------


def test_tl45_order_5001_full_projection(tmp_path, database, ctx, orders_kit, tools_kit, spy):
    execution, body = lookup(tools_kit, ctx, 5001)

    assert_ok(execution, body)
    assert body["data"] == order_5001(tmp_path, orders_kit)
    assert spy.opens == 1
    assert spy.all_closed()
    # Independent SQL: stored total and current prices differ (historical total kept).
    assert orders_kit.query(database, "SELECT total_cents FROM orders WHERE order_id = 5001") == [
        (150000,)
    ]
    current = orders_kit.query(
        database,
        "SELECT SUM(oi.quantity * p.price_cents) FROM order_items AS oi"
        " JOIN products AS p ON p.product_id = oi.product_id WHERE oi.order_id = 5001",
    )
    assert current == [(220000,)]
    again, _ = lookup(tools_kit, ctx, 5001)
    assert again.payload == execution.payload
    assert '"Violão Teste Alfa"' in execution.payload


def test_tl45_current_catalog_name(tmp_path, orders_kit, tools_kit):
    renamed = orders_kit.build(tmp_path / "renomeado", edit="renamed")
    execution, body = lookup(tools_kit, tools_kit.context(database_path=renamed, customer=C1), 5001)
    assert_ok(execution, body)
    assert body["data"]["items"][0] == item(10, "Violão Teste Alfa Renomeado", 2)
    assert body["data"]["total"] == "1500.00"


# --- TL46: orders without items, tracking or estimate; estimate states ----------------


def test_tl46_order_states(ctx, tools_kit, spy):
    results = {order_id: lookup(tools_kit, ctx, order_id) for order_id in (5002, 5003, 5004)}
    for execution, body in results.values():
        assert_ok(execution, body)
        for word in ("delay", "late", "new_date", "atraso"):
            assert not any(word in key for key in keys_of(body))

    data = {order_id: body["data"] for order_id, (_, body) in results.items()}
    assert {key: data[5002][key] for key in ORDER_KEYS[:-1]} == {
        "order_id": 5002,
        "status": "pending",
        "items": [],
        "total": "250.00",
        "tracking_code": None,
        "estimated_delivery": None,
        "estimate_status": "not_registered",
        "business_date": "2026-11-03",
    }
    assert data[5003]["estimated_delivery"] == "2026-11-02"
    assert data[5003]["estimate_status"] == "passed"
    assert data[5003]["status"] == "delivered"
    assert data[5003]["total"] == "300.00"
    assert {key: data[5004][key] for key in ORDER_KEYS[:-1]} == {
        "order_id": 5004,
        "status": "cancelled",
        "items": [item(13, "Violão Teste Gama", 3)],
        "total": "89.70",
        "tracking_code": None,
        "estimated_delivery": "2026-11-04",
        "estimate_status": "upcoming",
        "business_date": "2026-11-03",
    }
    assert spy.opens == 3
    assert spy.all_closed()


# --- TL47: order_id type and range before any resource --------------------------------


@pytest.mark.parametrize(("raw", "python_value", "code"), INVALID_IDS)
def test_tl47_invalid_ids_in_three_contexts(
    raw, python_value, code, database, tools_kit, spy, orders_kit
):
    contexts = {
        "a": tools_kit.context(customer=None),
        "b": tools_kit.context(customer=C1),
        "c": tools_kit.context(database_path=database, customer=C1),
    }
    arguments = f'{{"order_id": {raw}}}'
    payloads = {}
    for label, context in contexts.items():
        execution, body = tools_kit.run(TOOL, arguments, context)
        assert execution.payload == invalid_payload(code), label
        assert (execution.tool, execution.status, execution.error_code) == (
            TOOL,
            "invalid_input",
            code,
        )
        payloads[label] = execution.payload
    assert payloads["a"] == payloads["b"] == payloads["c"]
    assert spy.opens == 0

    # Bound equivalence: the unchanged service gives the same field and code.
    with connect_readonly(database) as connection:
        with pytest.raises(OrderInputError) as raised:
            get_order(connection, python_value, customer=C1, clock=lambda: orders_kit.FIXED)
    assert (raised.value.field, raised.value.code) == ("order_id", code)


@pytest.mark.parametrize("order_id", [1, MAX_ID])
def test_tl47_valid_bounds(order_id, database, tools_kit, spy, orders_kit):
    anonymous, _ = lookup(tools_kit, tools_kit.context(customer=None), order_id)
    assert anonymous.payload == NOT_ACCESSIBLE
    assert spy.opens == 0
    present, _ = lookup(tools_kit, tools_kit.context(database_path=database, customer=C1), order_id)
    assert present.payload == NOT_ACCESSIBLE
    assert spy.opens == 1
    assert spy.all_closed()
    with connect_readonly(database) as connection:
        assert get_order(connection, order_id, customer=C1, clock=lambda: orders_kit.FIXED) is None


def test_tl47_literal_table_matches_service_constants():
    from emporio_agent.data.parsing import SQLITE_MAX_INTEGER

    assert SQLITE_MAX_INTEGER == MAX_ID == 9223372036854775807


# --- TL48: every no-access case is the same payload -----------------------------------


def test_tl48_no_access_cases_are_identical(database, tools_kit, spy):
    cases = [
        (C1, 6001, 1),
        (C1, 9999, 1),
        (None, 5001, 0),
        (None, 6001, 0),
        (C3, 5001, 1),
    ]
    payloads = []
    for customer, order_id, expected_opens in cases:
        before = spy.opens
        execution, _ = lookup(
            tools_kit, tools_kit.context(database_path=database, customer=customer), order_id
        )
        assert spy.opens - before == expected_opens
        assert (execution.tool, execution.status, execution.error_code) == (
            TOOL,
            "unavailable",
            "order_not_accessible",
        )
        payloads.append(execution.payload)
    assert payloads == [NOT_ACCESSIBLE] * 5
    assert spy.all_closed()


# --- TL49: unavailable database versus invalid IDs ------------------------------------


@pytest.mark.parametrize("state", ["missing", "schema_v2"])
def test_tl49_unavailable_database(state, tmp_path, orders_kit, tools_kit, spy):
    if state == "missing":
        path = tmp_path / "pasta-sem-banco" / "pedidos.sqlite3"
    else:
        path = orders_kit.build(tmp_path)
        orders_kit.corrupt(path, "schema_v2")
    context = tools_kit.context(database_path=path, customer=C1)

    valid = [lookup(tools_kit, context, order_id)[0] for order_id in (5001, 6001, 9999)]
    assert [execution.payload for execution in valid] == [DATA_UNAVAILABLE] * 3
    assert spy.opens == 3
    for fragment in ("pedidos", "sqlite", "schema", "pasta", "missing"):
        assert fragment not in valid[0].payload

    zero, _ = tools_kit.run(TOOL, '{"order_id": 0}', context)
    boolean, _ = tools_kit.run(TOOL, '{"order_id": true}', context)
    assert zero.payload == invalid_payload("out_of_range")
    assert boolean.payload == invalid_payload("invalid_type")
    assert spy.opens == 3
    assert spy.all_closed()


# --- TL50: identity never comes from arguments ----------------------------------------


@pytest.mark.parametrize(
    "arguments",
    [
        '{"order_id": 6001, "customer_id": 2}',
        '{"order_id": 6001, "customer": 2}',
        '{"order_id": 6001, "customerId": 2}',
        '{"order_id": 6001, "session": {"customer_id": 2}}',
        '{"order_id": 6001, "as_customer": 2}',
        '{"order_id": 6001, "cliente": 2}',
        '{"order_id": 6001, "CUSTOMER_ID": 2}',
        '{"orderId": 6001}',
        '{"ORDER_ID": 6001}',
        '{"order_id ": 6001}',
    ],
)
def test_tl50_identity_fields_rejected(arguments, ctx, tools_kit, spy):
    copy = dataclasses.replace(ctx)
    execution, _ = tools_kit.run(TOOL, arguments, ctx)

    assert execution.payload == (
        '{"tool":"get_order","status":"invalid_input","data":null,'
        '"error":{"code":"unexpected_field","field":null}}'
    )
    for text in ("customer", "cliente", "session", "orderId", "ORDER", "6001"):
        assert text not in execution.payload
    assert spy.opens == 0
    assert ctx == copy
    assert ctx.customer is C1
    still, _ = lookup(tools_kit, ctx, 6001)
    assert still.payload == NOT_ACCESSIBLE


@pytest.mark.parametrize(
    "value", ['"6001 customer 2"', '"6001"', '{"order_id": 6001, "customer_id": 2}', "[6001, 2]"]
)
def test_tl50_identity_in_text_is_invalid_type(value, ctx, tools_kit, spy):
    execution, _ = tools_kit.run(TOOL, f'{{"order_id": {value}}}', ctx)
    assert execution.payload == invalid_payload("invalid_type")
    assert "customer" not in execution.payload
    assert spy.opens == 0
    assert ctx.customer is C1


# --- TL51: stored adversarial text stays data -----------------------------------------


def test_tl51_tracking_injection_does_not_change_identity(
    tmp_path, ctx, orders_kit, tools_kit, spy
):
    injected, body = lookup(tools_kit, ctx, 5003)
    assert_ok(injected, body)
    assert body["data"]["tracking_code"] == orders_kit.INJECTION_TRACKING
    assert injected.payload.count(orders_kit.INJECTION_TRACKING) == 1
    assert body["data"]["items"] == [item(12, orders_kit.INJECTION_NAME, 1)]
    assert injected.payload.count(orders_kit.INJECTION_NAME) == 1
    assert list(body) == ENVELOPE_KEYS

    foreign, _ = lookup(tools_kit, ctx, 6001)
    assert foreign.payload == NOT_ACCESSIBLE
    own, own_body = lookup(tools_kit, ctx, 5001)
    assert own_body["data"] == order_5001(tmp_path, orders_kit)
    assert ctx.customer is C1
    assert spy.opens == 3


# --- TL52: excluded data never appears ------------------------------------------------


def test_tl52_no_excluded_data_in_any_payload(database, tools_kit, spy):
    authorized = set()
    for customer in (C1, C2, C3, None):
        context = tools_kit.context(database_path=database, customer=customer)
        for order_id in (5001, 5002, 5003, 5004, 6001, 6002, 9999):
            execution, body = lookup(tools_kit, context, order_id)
            assert_no_excluded(execution, body)
            if execution.status == "ok":
                authorized.add((customer, order_id))
    assert authorized == {(C1, 5001), (C1, 5002), (C1, 5003), (C1, 5004), (C2, 6001), (C2, 6002)}
    # Authorized texts are kept: the tracking code of 6001 is returned to C2 unchanged.
    _, body = lookup(tools_kit, tools_kit.context(database_path=database, customer=C2), 6001)
    assert body["data"]["tracking_code"] == "TRK-6001-SENTINEL"
    assert body["data"]["items"] == [item(14, "Baixo Teste Delta", 1)]
    assert body["data"]["total"] == "777.77"


# --- TL53: business date at the UTC-03:00 boundary; invalid clocks --------------------


@pytest.mark.parametrize(
    ("now", "business_date", "estimate_status"),
    [
        (datetime(2026, 11, 3, 2, 59, 59, tzinfo=UTC), "2026-11-02", "upcoming"),
        (datetime(2026, 11, 3, 3, 0, 0, tzinfo=UTC), "2026-11-03", "due_today"),
        (datetime(2026, 11, 4, 2, 59, 59, tzinfo=UTC), "2026-11-03", "due_today"),
        (datetime(2026, 11, 4, 3, 0, 0, tzinfo=UTC), "2026-11-04", "passed"),
    ],
)
def test_tl53_business_date_boundary(
    now, business_date, estimate_status, database, orders_kit, tools_kit
):
    clock = orders_kit.ClockSpy(now)
    context = tools_kit.context(database_path=database, customer=C1, clock=clock)
    execution, body = lookup(tools_kit, context, 5001)
    assert_ok(execution, body)
    assert body["data"]["business_date"] == business_date
    assert body["data"]["estimate_status"] == estimate_status
    assert body["data"]["estimated_delivery"] == "2026-11-03"
    assert clock.calls == 1


class _SecretClockError(ValueError):
    pass


def _raising_clock():
    raise _SecretClockError("SECRET C:\\x SELECT")


def _type_error_clock():
    raise TypeError("SECRET-TYPE C:\\y FROM orders")


@pytest.mark.parametrize(
    "clock",
    [
        lambda: datetime(2026, 11, 3, 15, 0),  # naive
        _raising_clock,
        _type_error_clock,
        lambda: "2026-11-03T15:00:00+00:00",  # not a datetime
        lambda: None,
    ],
)
def test_tl53_invalid_clock_is_internal_error(clock, database, tools_kit, spy):
    context = tools_kit.context(database_path=database, customer=C1, clock=clock)
    execution, _ = lookup(tools_kit, context, 5001)
    assert execution.payload == INTERNAL_ERROR
    assert (execution.status, execution.error_code) == ("internal_error", "internal_error")
    for text in ("SECRET", "SELECT", "C:\\", "naive", "timezone", "datetime", "Error"):
        assert text not in execution.payload
    assert spy.opens == 1
    assert spy.all_closed()


def test_tl53_clock_not_read_without_access(database, orders_kit, tools_kit):
    clock = orders_kit.ClockSpy(orders_kit.FIXED)
    for customer, order_id in ((None, 5001), (C1, 6001), (C1, 9999), (C1, 0)):
        context = tools_kit.context(database_path=database, customer=customer, clock=clock)
        lookup(tools_kit, context, order_id)
    assert clock.calls == 0


# --- TL54: KeyboardInterrupt propagates after the connection is closed ----------------


def _interrupt():
    raise KeyboardInterrupt


def _exit():
    raise SystemExit(3)


@pytest.mark.parametrize(("clock", "error"), [(_interrupt, KeyboardInterrupt), (_exit, SystemExit)])
def test_tl54_base_exceptions_propagate_with_connection_closed(
    clock, error, database, tools_kit, spy
):
    from importlib import import_module

    context = tools_kit.context(database_path=database, customer=C1, clock=clock)
    with pytest.raises(error):
        import_module("emporio_agent.tools").execute_tool(TOOL, '{"order_id": 5001}', context)
    assert spy.opens == 1
    assert spy.all_closed()


# --- TL55: damaged rows, one consistent setup per variant -----------------------------


@pytest.mark.parametrize("variant", ["status_unknown", "drop_items"])
def test_tl55_damaged_authorized_order_is_internal_error(
    variant, tmp_path, orders_kit, tools_kit, spy
):
    path = orders_kit.build(tmp_path / variant)
    orders_kit.corrupt(path, variant)
    execution, _ = lookup(tools_kit, tools_kit.context(database_path=path, customer=C1), 5001)
    assert execution.payload == INTERNAL_ERROR
    for text in ("lost", "order_items", "invalid_row", "query_failed", "SELECT", "sqlite"):
        assert text not in execution.payload
    assert spy.opens == 1
    assert spy.all_closed()


def test_tl55_foreign_damage_does_not_affect_own_order(tmp_path, orders_kit, tools_kit, spy):
    base = tmp_path / "foreign_status"
    path = orders_kit.build(base)
    orders_kit.corrupt(path, "foreign_status")
    context = tools_kit.context(database_path=path, customer=C1)

    own, body = lookup(tools_kit, context, 5001)
    assert_ok(own, body)
    assert body["data"] == order_5001(base, orders_kit)
    foreign, _ = lookup(tools_kit, context, 6001)
    assert foreign.payload == NOT_ACCESSIBLE
    assert "SENTINEL-FOREIGN" not in foreign.payload
    assert spy.opens == 2


# --- Pipeline reuse: extreme exponents and caller decimal context ---------------------


@pytest.mark.parametrize(
    "arguments",
    [
        '{"order_id": 1e999999999999999999999}',
        '{"order_id": 5001, "order_id": -1e999999999999999999999}',
        '{"order_id": 1e-1999999999999999998}',
    ],
)
def test_orders_extreme_exponent_stays_invalid_json(arguments, ctx, tools_kit, spy):
    with decimal.localcontext() as caller:
        caller.traps[decimal.InvalidOperation] = False
        caller.clear_flags()
        execution, _ = tools_kit.run(TOOL, arguments, ctx)
        assert execution.payload == (
            '{"tool":"get_order","status":"invalid_input","data":null,'
            '"error":{"code":"invalid_json","field":null}}'
        )
        assert not caller.flags[decimal.InvalidOperation]
        assert decimal.getcontext() is caller
    assert spy.opens == 0
