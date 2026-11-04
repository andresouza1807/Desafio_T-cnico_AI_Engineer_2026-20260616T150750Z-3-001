"""Review 10-10 tool cases TI01-TI06 (Spec 06 Review 06-07-INSTALLMENTS).

Real SQLite files built in tmp_path from the synthetic catalog_kit sources; execute_tool
runs the real argument pipeline, catalog service and pricing. Expected money values are
literals from the catalog fixture divided by hand in integer cents.

TI06 uses a scripted ModelProvider: it proves only integration (what reaches the model
and what the application records). It does not prove that a real model uses the
simulation, explains the cent or avoids "PIX parcelado".
"""

import json
from pathlib import Path

import pytest
from test_agent_pix_context import (
    ScriptedProvider,
    assert_answered,
    call,
    final,
    make_agent,
    outputs,
    response,
)

from emporio_agent.provider import FunctionCallOutput
from emporio_agent.session import ConversationSession

QUOTE_KEYS = [
    "product",
    "payment_method",
    "list_price",
    "final_price",
    "discount_percent",
    "discount_source",
    "promotion",
    "pix_not_combined",
    "installments",
    "provenance",
]
SIMULATION_KEYS = ["count", "status", "total", "minimum_installment", "schedule"]


@pytest.fixture
def database(tmp_path, catalog_kit):
    return catalog_kit.build(tmp_path)


@pytest.fixture
def ctx(database, tools_kit):
    return tools_kit.context(database_path=database)


def run(tools_kit, context, arguments):
    return tools_kit.run("quote_product", json.dumps(arguments), context)


def error_payload(code, field):
    return (
        '{"tool":"quote_product","status":"invalid_input","data":null,'
        f'"error":{{"code":"{code}","field":"{field}"}}}}'
    )


# --- TI01: payload shape ----------------------------------------------------------------


@pytest.mark.parametrize("extra", [{}, {"installments": None}])
@pytest.mark.parametrize("method", ["regular", "pix"])
def test_ti01_without_count_the_key_is_null(tools_kit, ctx, extra, method):
    execution, payload = run(tools_kit, ctx, {"product_id": 202, "payment_method": method, **extra})
    assert execution.status == "ok"
    assert list(payload["data"]) == QUOTE_KEYS
    assert payload["data"]["installments"] is None


def test_ti01_simulation_with_cent_remainder(tools_kit, ctx):
    # 135: table 1500.00, active 10% promotion, regular 1350.00; 135000 = 7 * 19285 + 5.
    execution, payload = run(
        tools_kit, ctx, {"product_id": 135, "payment_method": "regular", "installments": 7}
    )
    assert execution.status == "ok"
    data = payload["data"]
    assert data["final_price"] == "1350.00"
    assert data["discount_source"] == "promotion"
    assert list(data["installments"]) == SIMULATION_KEYS
    assert data["installments"] == {
        "count": 7,
        "status": "simulated",
        "total": "1350.00",
        "minimum_installment": "100.00",
        "schedule": [
            {"installments": 5, "amount": "192.86"},
            {"installments": 2, "amount": "192.85"},
        ],
    }


def test_ti01_exact_division_and_short_range(tools_kit, ctx):
    _, six = run(
        tools_kit, ctx, {"product_id": 202, "payment_method": "regular", "installments": 6}
    )
    assert six["data"]["installments"]["schedule"] == [{"installments": 6, "amount": "165.00"}]
    assert six["data"]["installments"]["minimum_installment"] == "80.00"
    _, three = run(
        tools_kit, ctx, {"product_id": 201, "payment_method": "regular", "installments": 3}
    )
    # 89990 = 3 * 29996 + 2.
    assert three["data"]["installments"] == {
        "count": 3,
        "status": "simulated",
        "total": "899.90",
        "minimum_installment": None,
        "schedule": [
            {"installments": 2, "amount": "299.97"},
            {"installments": 1, "amount": "299.96"},
        ],
    }


@pytest.mark.parametrize(
    ("product_id", "count", "status", "minimum"),
    [
        (202, 12, "below_minimum", "100.00"),  # 990.00 / 12 = 82.50
        (211, 2, "needs_confirmation", None),  # 0.00
    ],
)
def test_ti01_no_amounts_without_simulation(tools_kit, ctx, product_id, count, status, minimum):
    _, payload = run(
        tools_kit,
        ctx,
        {"product_id": product_id, "payment_method": "regular", "installments": count},
    )
    simulation = payload["data"]["installments"]
    assert (simulation["status"], simulation["minimum_installment"]) == (status, minimum)
    assert simulation["schedule"] == []


# --- TI02: invalid counts ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("arguments", "code"),
    [
        ('{"product_id": 202, "payment_method": "regular", "installments": true}', "invalid_type"),
        ('{"product_id": 202, "payment_method": "regular", "installments": "6"}', "invalid_type"),
        ('{"product_id": 202, "payment_method": "regular", "installments": 6.0}', "invalid_type"),
        ('{"product_id": 202, "payment_method": "regular", "installments": [6]}', "invalid_type"),
        ('{"product_id": 202, "payment_method": "regular", "installments": 1}', "out_of_range"),
        ('{"product_id": 202, "payment_method": "regular", "installments": 13}', "out_of_range"),
        ('{"product_id": 202, "payment_method": "regular", "installments": 0}', "out_of_range"),
        ('{"product_id": 202, "payment_method": "regular", "installments": -3}', "out_of_range"),
        (
            '{"product_id": 202, "payment_method": "pix", "installments": 6}',
            "not_available_for_pix",
        ),
    ],
)
def test_ti02_invalid_count(tools_kit, ctx, arguments, code):
    execution, _ = tools_kit.run("quote_product", arguments, ctx)
    assert execution.status == "invalid_input"
    assert execution.payload == error_payload(code, "installments")


# --- TI03: provider schema --------------------------------------------------------------


def quote_definition():
    from emporio_agent.tools import provider_tool_definitions

    return next(d for d in provider_tool_definitions() if d["name"] == "quote_product")


def test_ti03_schema_has_required_nullable_count():
    parameters = quote_definition()["parameters"]
    assert parameters["required"] == ["product_id", "payment_method", "installments"]
    assert parameters["properties"]["installments"] == {
        "type": ["integer", "null"],
        "minimum": 2,
        "maximum": 12,
    }
    assert parameters["additionalProperties"] is False


@pytest.mark.parametrize(
    "phrase",
    [
        "simulation",
        "sum exactly to the price",
        "one more cent",
        "ask for another count",
        "PIX is paid at once",
    ],
)
def test_ti03_description_states_the_contract(phrase):
    assert phrase in quote_definition()["description"]


# --- TI04: PIX quote unchanged ----------------------------------------------------------


def test_ti04_pix_quote_values_unchanged(tools_kit, ctx):
    _, payload = run(tools_kit, ctx, {"product_id": 202, "payment_method": "pix"})
    data = payload["data"]
    # Active 10% promotion: PIX does not add 5%.
    assert (data["final_price"], data["discount_source"], data["pix_not_combined"]) == (
        "990.00",
        "promotion",
        True,
    )


# --- TI05: no internal provenance in the simulation -------------------------------------


def test_ti05_simulation_has_no_source_or_policy_reference(tools_kit, ctx):
    _, payload = run(
        tools_kit, ctx, {"product_id": 135, "payment_method": "regular", "installments": 7}
    )
    text = json.dumps(payload["data"]["installments"], ensure_ascii=False)
    for word in ("section", "page", "policy", "manual", "3.1", "source", "seção", "página"):
        assert word not in text


# --- TI06: scripted integration only ----------------------------------------------------


@pytest.fixture
def agent_db(tmp_path, catalog_kit) -> Path:
    base = tmp_path / "catalogo"
    catalog_kit.build(base)
    return base / catalog_kit.DB_DIR / catalog_kit.DB_NAME


def test_ti06_simulation_reaches_the_model(agent_db, tmp_path):
    arguments = '{"product_id":135,"payment_method":"regular","installments":7}'
    provider = ScriptedProvider(
        response(call("call_parc", arguments)),
        final("Simulação pronta.", "msg_parc"),
    )
    agent = make_agent(provider, agent_db, tmp_path / "sem-indice" / "politicas.json")
    session = ConversationSession()

    result = agent.respond(session, "em 7x quanto fica?")

    assert_answered(result, "Simulação pronta.")
    envelopes = outputs(provider.requests[1])
    assert [e["status"] for e in envelopes] == ["ok"]
    assert envelopes[0]["data"]["installments"]["schedule"] == [
        {"installments": 5, "amount": "192.86"},
        {"installments": 2, "amount": "192.85"},
    ]
    retained = [i for t in session.history for i in t.items if isinstance(i, FunctionCallOutput)]
    assert len(retained) == 1
    # Diagnostics carry no amount of the simulation.
    runs = repr(result.diagnostics.tool_runs)
    for amount in ("192", "1350", "1500"):
        assert amount not in runs
