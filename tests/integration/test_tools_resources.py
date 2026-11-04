"""Slice 06-D integration cases TL56-TL60 and TL71 (docs/plans/06-tools.md).

Resources, absence of writes, replacement of prepared files and customer isolation,
through execute_tool against real SQLite files built by the real prepare_database from
synthetic sources and real policy indexes built by the real prepare_policy_index. The
spies of tools_kit delegate to the real connect_readonly and load_policy_index and only
count calls and check closure. Snapshots use independent mode=ro reads, SHA-256 of file
bytes and directory listings. Nothing here mocks SQL, authorization or persistence.
"""

import hashlib
import json
import os

import pytest

from emporio_agent.services.orders import CustomerContext

C1 = CustomerContext(1)
C2 = CustomerContext(2)


@pytest.fixture
def opens(monkeypatch, tools_kit):
    return tools_kit.connect_spy(monkeypatch)


@pytest.fixture
def loads(monkeypatch, tools_kit):
    return tools_kit.index_spy(monkeypatch)


@pytest.fixture
def files(tmp_path, catalog_kit, orders_kit, policy_kit, tools_kit):
    catalog = catalog_kit.build(tmp_path / "catalogo")
    orders = orders_kit.build(tmp_path / "pedidos")
    index = tools_kit.policy_index(policy_kit, tmp_path / "politicas")
    return catalog, orders, index


def call(tools_kit, name, arguments, **context):
    return tools_kit.run(name, json.dumps(arguments), tools_kit.context(**context))


# --- TL56: resource counts per tool and outcome ---------------------------------------


def test_tl56_resource_counts(files, tools_kit, opens, loads):
    catalog, orders, index = files
    # (tool, arguments, context, expected status, expected opens, expected loads)
    cases = [
        ("search_products", {}, {"database_path": catalog}, "ok", 1, 0),
        ("get_product", {"product_id": 202}, {"database_path": catalog}, "ok", 1, 0),
        ("get_product", {"product_id": 999}, {"database_path": catalog}, "not_found", 1, 0),
        (
            "quote_product",
            {"product_id": 202, "payment_method": "pix"},
            {"database_path": catalog},
            "ok",
            1,
            0,
        ),
        (
            "quote_product",
            {"product_id": 202, "payment_method": "boleto"},
            {"database_path": catalog},
            "invalid_input",
            1,
            0,
        ),
        ("search_policies", {"query": "sedex"}, {"policy_index_path": index}, "ok", 0, 1),
        (
            "search_policies",
            {"query": "qual a capital da França?"},
            {"policy_index_path": index},
            "not_found",
            0,
            1,
        ),
        ("search_policies", {"query": 5}, {"policy_index_path": index}, "invalid_input", 0, 0),
        ("get_product", {"product_id": True}, {"database_path": catalog}, "invalid_input", 0, 0),
        ("get_order", {"order_id": 5001}, {"database_path": orders, "customer": C1}, "ok", 1, 0),
        (
            "get_order",
            {"order_id": 6001},
            {"database_path": orders, "customer": C1},
            "unavailable",
            1,
            0,
        ),
        ("get_order", {"order_id": 5001}, {"database_path": orders}, "unavailable", 0, 0),
        (
            "get_order",
            {"order_id": 0},
            {"database_path": orders, "customer": C1},
            "invalid_input",
            0,
            0,
        ),
        ("search_products", {"offset": -1}, {"database_path": catalog}, "invalid_input", 1, 0),
    ]
    for name, arguments, context, status, expected_opens, expected_loads in cases:
        before = (opens.opens, loads.loads)
        execution, _ = call(tools_kit, name, arguments, **context)
        assert execution.status == status, (name, arguments)
        assert opens.opens - before[0] == expected_opens, (name, arguments)
        assert loads.loads - before[1] == expected_loads, (name, arguments)
    assert opens.all_closed()
    # Unknown tool: nothing opened or loaded.
    before = (opens.opens, loads.loads)
    execution, _ = tools_kit.run("drop_tables", "{}", tools_kit.context(database_path=catalog))
    assert execution.error_code == "unknown_tool"
    assert (opens.opens, loads.loads) == before


# --- TL57: every connection closed after every outcome --------------------------------


def test_tl57_connections_closed_on_every_outcome(
    tmp_path, catalog_kit, tools_kit, tools_big, opens
):
    good = catalog_kit.build(tmp_path / "bom")
    damaged = catalog_kit.build(tmp_path / "linha")
    catalog_kit.corrupt(damaged, "price_text")
    nan = catalog_kit.build(tmp_path / "nan")
    catalog_kit.corrupt(nan, "specs_nan")
    big = tools_big.catalog(tmp_path / "grande", 40_000)
    cases = [
        (good, "get_product", {"product_id": 202}, "ok", None),
        (good, "get_product", {"product_id": 999}, "not_found", "product_not_found"),
        (
            good,
            "quote_product",
            {"product_id": 202, "payment_method": "boleto"},
            "invalid_input",
            "unknown_value",
        ),
        (damaged, "get_product", {"product_id": 201}, "internal_error", "internal_error"),
        (nan, "get_product", {"product_id": 202}, "internal_error", "internal_error"),
        (big, "get_product", {"product_id": 202}, "unavailable", "result_too_large"),
    ]
    for database, name, arguments, status, code in cases:
        before = opens.opens
        execution, _ = call(tools_kit, name, arguments, database_path=database)
        assert (execution.status, execution.error_code) == (status, code), (name, database)
        assert opens.opens == before + 1
        assert opens.all_closed(), (name, database)
    assert len(opens.connections) == len(cases)


# --- TL58: index replaced after a call is read at the next call -----------------------


def test_tl58_index_replaced_between_calls(tmp_path, policy_kit, tools_kit, loads):
    index = tools_kit.policy_index(policy_kit, tmp_path / "v1")
    context = tools_kit.context(policy_index_path=index)
    old_line = "• Frete grátis acima de R$ 500,00."
    new_line = "• Frete grátis acima de R$ 700,00."

    first, body = tools_kit.run("search_policies", '{"query": "sedex"}', context)
    assert first.status == "ok"
    assert old_line in body["data"]["sections"][0]["text"]

    def edit(layout):
        policy_kit.replace_line(layout, old_line, new_line)

    replacement = tools_kit.policy_index(policy_kit, tmp_path / "v2", edit=edit)
    new_sha = hashlib.sha256((tmp_path / "v2" / "manual-sintetico.pdf").read_bytes()).hexdigest()
    os.replace(replacement, index)  # fails on Windows if a handle were still open

    second, body = tools_kit.run("search_policies", '{"query": "sedex"}', context)
    assert second.status == "ok"
    text = body["data"]["sections"][0]["text"]
    assert new_line in text and old_line not in text
    # Review 10-06: the source is internal; the v2 text is served under the v2 review.
    assert new_sha not in second.payload
    assert second.references == ("policy:5.1@p4",)
    assert loads.loads == 2


# --- TL59: no write, no temporary file -------------------------------------------------


def tree(root):
    """Every file under root with its SHA-256; directories listed too."""
    entries = {}
    for directory, subdirectories, names in os.walk(root):
        for name in subdirectories:
            entries[os.path.relpath(os.path.join(directory, name), root)] = "dir"
        for name in names:
            path = os.path.join(directory, name)
            with open(path, "rb") as handle:
                entries[os.path.relpath(path, root)] = hashlib.sha256(handle.read()).hexdigest()
    return entries


def test_tl59_no_write_and_no_temporary_file(tmp_path, files, catalog_kit, orders_kit, tools_kit):
    catalog, orders, index = files
    clock_spy = orders_kit.ClockSpy(tools_kit.FIXED)
    before = {
        "tree": tree(tmp_path),
        "catalog": catalog_kit.snapshot(catalog),
        "orders": orders_kit.snapshot(orders),
        "index": index.read_bytes(),
    }
    # Representative calls of TL33-TL55: success, not found, service and pipeline
    # rejections, injection text, no-access cases and the clock path.
    catalog_calls = [
        ("get_product", {"product_id": pid}) for pid in (135, 201, 202, 208, 209, 216, 999)
    ] + [
        ("quote_product", {"product_id": 212, "payment_method": "regular"}),
        ("quote_product", {"product_id": 211, "payment_method": "pix"}),
        ("quote_product", {"product_id": 202, "payment_method": "PIX"}),
        ("search_products", {"query": "violão", "max_price_brl": "1000.00"}),
        ("search_products", {"category": "Teclas", "available_only": False, "offset": 0}),
        ("search_products", {"category": "Inexistente"}),
        ("search_products", {"offset": 5}),
        ("search_products", {"max_price_brl": "1e3"}),
    ]
    for name, arguments in catalog_calls:
        call(tools_kit, name, arguments, database_path=catalog)
    for query in ("sedex", "00", "endereço", "qual a capital da França?", "   ", "?!"):
        call(tools_kit, "search_policies", {"query": query}, policy_index_path=index)
    for customer in (C1, C2, None, CustomerContext(3)):
        for order_id in (5001, 5002, 5003, 5004, 6001, 9999, 0):
            call(
                tools_kit,
                "get_order",
                {"order_id": order_id},
                database_path=orders,
                customer=customer,
                clock=clock_spy,
            )
    after = {
        "tree": tree(tmp_path),
        "catalog": catalog_kit.snapshot(catalog),
        "orders": orders_kit.snapshot(orders),
        "index": index.read_bytes(),
    }
    assert after == before
    suffixes = ("-journal", "-wal", "-shm", ".tmp")
    assert not [name for name in after["tree"] if name.endswith(suffixes)]
    assert clock_spy.calls > 0


# --- TL60: replaced database, no stale result ------------------------------------------


def test_tl60_replaced_database_is_read_again(tmp_path, catalog_kit, tools_big, tools_kit):
    database = catalog_kit.build(tmp_path / "v1")
    context = tools_kit.context(database_path=database)
    first, body = tools_kit.run("get_product", '{"product_id": 202}', context)
    assert body["data"]["product"]["name"] == "Violão Folk Beta"

    renamed = tools_big.catalog_variant(tmp_path / "v2", name_202="Violão Folk Beta Renovado")
    os.replace(renamed, database)

    second, body = tools_kit.run("get_product", '{"product_id": 202}', context)
    assert body["data"]["product"]["name"] == "Violão Folk Beta Renovado"
    assert second.payload != first.payload


def test_tl60_replaced_orders_database_is_read_again(tmp_path, orders_kit, tools_kit):
    database = orders_kit.build(tmp_path / "v1")
    context = tools_kit.context(database_path=database, customer=C1)
    _, body = tools_kit.run("get_order", '{"order_id": 5001}', context)
    assert body["data"]["items"][0]["name"] == "Violão Teste Alfa"

    os.replace(orders_kit.build(tmp_path / "v2", edit="renamed"), database)

    _, body = tools_kit.run("get_order", '{"order_id": 5001}', context)
    assert body["data"]["items"][0]["name"] == "Violão Teste Alfa Renomeado"


# --- TL71: interleaved customers -------------------------------------------------------


def test_tl71_interleaved_customers(tmp_path, orders_kit, tools_kit):
    database = orders_kit.build(tmp_path)

    def lookup(customer, order_id):
        context = tools_kit.context(database_path=database, customer=customer)
        execution, _ = tools_kit.run("get_order", json.dumps({"order_id": order_id}), context)
        return execution.payload

    solo = {
        (customer.customer_id, order_id): lookup(customer, order_id)
        for customer in (C1, C2)
        for order_id in (5001, 6001)
    }
    assert json.loads(solo[(1, 5001)])["status"] == "ok"
    assert json.loads(solo[(2, 6001)])["status"] == "ok"
    assert json.loads(solo[(1, 6001)])["error"]["code"] == "order_not_accessible"
    assert json.loads(solo[(2, 5001)])["error"]["code"] == "order_not_accessible"

    sequence = [(C1, 5001), (C2, 6001), (C1, 6001), (C2, 5001), (C1, 5001)]
    payloads = [lookup(customer, order_id) for customer, order_id in sequence]

    for (customer, order_id), payload in zip(sequence, payloads, strict=True):
        assert payload == solo[(customer.customer_id, order_id)]
    assert payloads[4] == payloads[0]
    seen = {1: set(), 2: set()}
    for (customer, _), payload in zip(sequence, payloads, strict=True):
        data = json.loads(payload)["data"]
        if data is not None:
            seen[customer.customer_id].add(data["order_id"])
    assert seen == {1: {5001}, 2: {6001}}
    assert "TRK-6001" not in payloads[0] + payloads[2] + payloads[4]
    assert "TRK-5001" not in payloads[1] + payloads[3]
