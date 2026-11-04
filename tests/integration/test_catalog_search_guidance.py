"""Correction 09-02: catalog search by category and budget (docs/validation/09-02).

Two groups, both offline:

- SG10-SG15, controls through execute_tool: the real tool, argument validation, service,
  prices and SQL over the synthetic catalog_kit database built in tmp_path. They pin the
  approved Spec 03/06 behavior that the diagnosis relies on. They are expected to pass
  before and after the correction (born green); no service code changes in 09-02.
  The "Teclas" category of the fixture is used because its product names do not contain
  the category word, like the guitar names of the original store data.
- SG20, agent orchestration with a scripted ModelProvider: what reaches the model on every
  attempt (instructions and tool schemas with the 09-02 guidance) and which envelopes the
  model receives for a generic-word search and a category search. The script decides what
  the "model" does; this does not prove which filters a real model chooses.

Oracles are literals from catalog_kit: Teclas = 216 (1200.00, active, stock 1) and 217
"Piano Digital Sigma" (2500.00, active, stock 1, inactive 30% promotion); Baixos = 135
(1500.00, active 10% promotions -> 1350.00); Violões with budget 1000.00 = 9 available
products, pages 211, 214, 212, 210, 201 then 206, 213, 202, 204. No network, no key.
"""

import itertools
import json
import socket
from datetime import UTC, datetime

import pytest

import emporio_agent.agent as agent_module
from emporio_agent.agent import Agent, AgentResources
from emporio_agent.instructions import session_instructions
from emporio_agent.provider import (
    FunctionCall,
    FunctionCallOutput,
    ModelRequest,
    ModelResponse,
    ProviderItem,
    UserMessage,
)
from emporio_agent.session import ConversationSession
from emporio_agent.tools import provider_tool_definitions

TOOL = "search_products"
MODEL = "modelo-roteirizado"
CLOCK = datetime(2026, 11, 4, 15, 0, tzinfo=UTC)
CATEGORY_CLAUSE = "put an instrument type in category, using a catalog category name"
GENERIC_CLAUSE = (
    "If query held only a generic type word, search the category again with query null before"
    " saying that nothing is available"
)
SCHEMA_CLAUSE = "put the type in category and set query to null"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def guard(*args, **kwargs):
        raise AssertionError("network access attempted")

    for name in ("create_connection", "getaddrinfo", "gethostbyname", "gethostbyname_ex"):
        monkeypatch.setattr(socket, name, guard)
    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket.socket, "connect_ex", guard)


@pytest.fixture
def database(tmp_path, catalog_kit):
    return catalog_kit.build(tmp_path)


@pytest.fixture
def ctx(database, tools_kit):
    return tools_kit.context(database_path=database)


def arguments(query=None, category=None, budget=None, available_only=True, offset=0) -> str:
    return json.dumps(
        {
            "query": query,
            "category": category,
            "max_price_brl": budget,
            "available_only": available_only,
            "offset": offset,
        },
        ensure_ascii=False,
    )


def run(tools_kit, context, **kwargs) -> dict[str, object]:
    execution, envelope = tools_kit.run(TOOL, arguments(**kwargs), context)
    assert (execution.status, envelope["status"], envelope["error"]) == ("ok", "ok", None)
    return envelope["data"]


def page(data) -> tuple[list[int], int, int | None, str]:
    return (
        [product["product_id"] for product in data["products"]],
        data["total_matches"],
        data["next_offset"],
        data["category_resolution"],
    )


# --- SG10: type in category, query null, budget ---------------------------------------


@pytest.mark.parametrize(
    ("budget", "expected"),
    [
        ("3000.00", ([216, 217], 2, None, "resolved")),
        ("2000.00", ([216], 1, None, "resolved")),
        (None, ([216, 217], 2, None, "resolved")),
    ],
)
def test_sg10_category_search_with_null_query(budget, expected, ctx, tools_kit):
    data = run(tools_kit, ctx, category="Teclas", budget=budget)
    assert page(data) == expected
    assert data["category"] == {"category_id": 2, "name": "Teclas"}
    assert data["candidate_categories"] == []
    assert data["max_price_brl"] == budget


# --- SG11: the query stays restrictive and combines with the category by AND ----------


@pytest.mark.parametrize("query", ["teclas", "teclado", "tecla"])
def test_sg11_generic_word_absent_from_names_gives_an_empty_resolved_page(query, ctx, tools_kit):
    data = run(tools_kit, ctx, query=query, category="Teclas", budget="3000.00")
    assert page(data) == ([], 0, None, "resolved")
    assert data["category"] == {"category_id": 2, "name": "Teclas"}
    assert data["candidate_categories"] == []


def test_sg11_query_and_category_combine_by_and(ctx, tools_kit):
    assert page(run(tools_kit, ctx, query="alfa")) == ([201], 1, None, "not_requested")
    assert page(run(tools_kit, ctx, query="alfa", category="Teclas")) == ([], 0, None, "resolved")
    assert page(run(tools_kit, ctx, query="sigma", category="Teclas")) == (
        [217],
        1,
        None,
        "resolved",
    )


# --- SG12: unknown singular versus resolved category without products ------------------


@pytest.mark.parametrize("category", ["Tecla", "violão", "violao"])
def test_sg12_unknown_category_is_not_a_resolved_empty_page(category, ctx, tools_kit):
    data = run(tools_kit, ctx, category=category, budget="1000.00")
    assert page(data) == ([], 0, None, "unknown")
    assert data["category"] is None
    assert [item["category_id"] for item in data["candidate_categories"]] == [1, 2, 3]


@pytest.mark.parametrize("category", ["Violões", "violões", "VIOLOES"])
def test_sg12_plural_category_resolves(category, ctx, tools_kit):
    data = run(tools_kit, ctx, category=category, budget="1000.00")
    assert page(data) == ([211, 214, 212, 210, 201], 9, 5, "resolved")


def test_sg12_resolved_category_without_products_in_budget(ctx, tools_kit):
    data = run(tools_kit, ctx, category="Baixos", budget="1000.00")
    assert page(data) == ([], 0, None, "resolved")
    assert data["category"] == {"category_id": 3, "name": "Baixos"}
    assert data["candidate_categories"] == []


# --- SG13: budget, promotion, availability before pagination; order and pages ---------


def test_sg13_category_budget_pages(ctx, tools_kit):
    first = run(tools_kit, ctx, category="Violões", budget="1000.00")
    second = run(tools_kit, ctx, category="Violões", budget="1000.00", offset=5)
    assert page(first) == ([211, 214, 212, 210, 201], 9, 5, "resolved")
    assert page(second) == ([206, 213, 202, 204], 9, None, "resolved")
    prices = [p["effective_price"] for p in first["products"] + second["products"]]
    assert prices == [
        "0.00",
        "19.90",
        "266.66",
        "700.00",
        "899.90",
        "899.90",
        "899.90",
        "990.00",
        "1000.00",
    ]
    # 202: list 1100.00 with an active 10% promotion enters the budget at 990.00.
    folk = second["products"][2]
    assert (folk["product_id"], folk["list_price"], folk["effective_price"]) == (
        202,
        "1100.00",
        "990.00",
    )
    # Every listed product is purchasable; 205 (1000.01), 207 (no stock), 208
    # (discontinued) and 209 (coming soon) are excluded.
    listed = {p["product_id"] for p in first["products"] + second["products"]}
    assert all(p["is_available"] for p in first["products"] + second["products"])
    assert listed.isdisjoint({205, 207, 208, 209})


def test_sg14_unavailable_products_only_with_available_only_false(ctx, tools_kit):
    purchase = run(tools_kit, ctx, category="Violões", budget="1000.00")
    everything = run(tools_kit, ctx, category="Violões", budget="1000.00", available_only=False)
    assert purchase["total_matches"] == 9
    assert everything["total_matches"] == 12  # + 207, 208, 209


def test_sg15_budget_text_is_never_widened(ctx, tools_kit):
    data = run(tools_kit, ctx, category="Teclas", budget="1199.99")
    assert page(data) == ([], 0, None, "resolved")
    assert data["max_price_brl"] == "1199.99"


# --- SG20: guidance sent on every attempt; envelopes available to the model ------------


class ScriptedProvider:
    def __init__(self, *steps: ModelResponse) -> None:
        self.steps = list(steps)
        self.requests: list[ModelRequest] = []

    @property
    def model(self) -> str:
        return MODEL

    def create(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return self.steps.pop(0)


def compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def call(call_id: str, args: str) -> FunctionCall:
    raw = compact(
        {
            "type": "function_call",
            "id": f"fc_{call_id}",
            "call_id": call_id,
            "name": TOOL,
            "arguments": args,
            "status": "completed",
        }
    )
    return FunctionCall(call_id=call_id, name=TOOL, arguments=args, raw=raw)


def response(*output, text: str = "") -> ModelResponse:
    return ModelResponse(
        response_id=None,
        model=MODEL,
        status="completed",
        incomplete_reason=None,
        output=tuple(output),
        text=text,
        commentary_present=False,
        refusal=False,
        usage=None,
        usage_invalid=False,
        request_id=None,
    )


def final(text: str) -> ModelResponse:
    raw = compact(
        {
            "type": "message",
            "id": "msg_sg20",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": text, "annotations": []}],
        }
    )
    return response(ProviderItem("assistant_message", raw), text=text)


def no_sleep(_seconds: float) -> None:
    raise AssertionError("no retry is scripted")


def test_sg20_guidance_and_results_reach_the_model_on_every_attempt(database, tmp_path):
    generic = arguments(query="teclado", category="Teclas", budget="3000.00")
    by_category = arguments(category="Teclas", budget="3000.00")
    provider = ScriptedProvider(
        response(call("call_g", generic)),
        response(call("call_c", by_category)),
        final("Temos duas opções na categoria Teclas."),
    )
    ticks = itertools.count()
    agent = Agent(
        provider,
        AgentResources(
            database_path=database,
            policy_index_path=tmp_path / "sem-indice" / "politicas.json",
            clock=lambda: CLOCK,
        ),
        sleep=no_sleep,
        monotonic=lambda: float(next(ticks)),
    )
    session = ConversationSession()

    result = agent.respond(session, "Quero um teclado até R$ 3.000.")

    assert result.outcome == "answered"
    assert provider.steps == []
    assert len(provider.requests) == 3
    expected_tools = tuple(provider_tool_definitions())
    for request in provider.requests:
        assert request.instructions == session_instructions(False)
        assert CATEGORY_CLAUSE in request.instructions
        assert GENERIC_CLAUSE in request.instructions
        assert request.tools == expected_tools
        assert SCHEMA_CLAUSE in request.tools[0]["description"]

    # The model receives an empty resolved page for the generic word, then the category.
    last = provider.requests[2].items
    assert last[0] == UserMessage("Quero um teclado até R$ 3.000.")
    outputs = [item for item in last if isinstance(item, FunctionCallOutput)]
    assert [o.call_id for o in outputs] == ["call_g", "call_c"]
    pages = [page(json.loads(o.output)["data"]) for o in outputs]
    assert pages == [([], 0, None, "resolved"), ([216, 217], 2, None, "resolved")]
    runs = result.diagnostics.tool_runs
    assert [(r.tool, r.status) for r in runs] == [(TOOL, "ok"), (TOOL, "ok")]
    assert runs[0].references == ()
    assert runs[1].references == ("product:216", "product:217")


def test_sg20_agent_uses_the_module_definitions(monkeypatch):
    # The agent reads the schemas from provider_tool_definitions; no copy elsewhere.
    assert agent_module.provider_tool_definitions is provider_tool_definitions
