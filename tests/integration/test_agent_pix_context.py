"""PIX grounding context and references (plan 08-10 R1, spec 07 Review 07-11-DF01).

Deterministic reference cases (PX06, agent side) use literal `ToolExecution` envelopes.
Orchestration cases (OR01-OR10) use a scripted `ModelProvider`; everything below it is
real: `Agent`, `ConversationSession`, `execute_tool`, argument validation, pricing and SQL
over the synthetic `catalog_kit` database built in `tmp_path`.

The script decides what the "model" does. These tests therefore prove what reaches the
model (instructions, retained envelopes) and what the application records (references,
statuses, history). They do not prove that a real model obeys the PIX rules: an
instruction is not a deterministic guarantee of obedience.

Oracles are literals: catalog_kit product 201 "Violão Clássico Alfa" (list 899.90, no
promotion) -> PIX 899.90 x 0.95 = 854.905 -> 854.91 (ROUND_HALF_UP); product 212
"Violão Mu" (list 333.33, active 15% and 20%, inactive 50%) -> 20% wins -> 266.66 for
both methods, PIX not combined; product 999 does not exist. No network, no key, no `.env`.
"""

import itertools
import json
import socket
from datetime import UTC, datetime
from pathlib import Path

import pytest

import emporio_agent.agent as agent_module
from emporio_agent.agent import Agent, AgentResources, TurnResult
from emporio_agent.provider import (
    FunctionCall,
    FunctionCallOutput,
    ModelRequest,
    ModelResponse,
    ProviderItem,
    UserMessage,
)
from emporio_agent.services.orders import CustomerContext
from emporio_agent.session import ConversationSession
from emporio_agent.tools import ToolExecution

MODEL = "modelo-roteirizado"
CLOCK = datetime(2026, 11, 4, 15, 0, tzinfo=UTC)
PIX_201 = '{"product_id":201,"payment_method":"pix"}'
REGULAR_201 = '{"product_id":201,"payment_method":"regular"}'
PIX_212 = '{"product_id":212,"payment_method":"pix"}'
PIX_999 = '{"product_id":999,"payment_method":"pix"}'
LINKED_LINE = (
    "Session: the application linked a demonstration customer to this session. "
    "Order lookups apply only to that customer."
)
ANONYMOUS_LINE = (
    "Session: no customer is linked to this session. Order lookups are unavailable; "
    "do not ask the customer to identify themselves by message."
)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def guard(*args, **kwargs):
        raise AssertionError("network access attempted")

    for name in ("create_connection", "getaddrinfo", "gethostbyname", "gethostbyname_ex"):
        monkeypatch.setattr(socket, name, guard)
    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket.socket, "connect_ex", guard)


# --- Scripted model and helpers (test-only) --------------------------------------------


class ScriptedProvider:
    def __init__(self, *steps: ModelResponse) -> None:
        self.steps = list(steps)
        self.requests: list[ModelRequest] = []
        self.overflow = 0

    @property
    def model(self) -> str:
        return MODEL

    def create(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self.steps:
            self.overflow += 1
            raise RuntimeError("unscripted attempt")
        return self.steps.pop(0)


def compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def call(call_id: str, arguments: str, name: str = "quote_product") -> FunctionCall:
    raw = compact(
        {
            "type": "function_call",
            "id": f"fc_{call_id}",
            "call_id": call_id,
            "name": name,
            "arguments": arguments,
            "status": "completed",
        }
    )
    return FunctionCall(call_id=call_id, name=name, arguments=arguments, raw=raw)


def response(*output: ProviderItem | FunctionCall, text: str = "") -> ModelResponse:
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


def final(text: str, item_id: str) -> ModelResponse:
    raw = compact(
        {
            "type": "message",
            "id": item_id,
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": text, "annotations": []}],
        }
    )
    return response(ProviderItem("assistant_message", raw), text=text)


def make_agent(provider: ScriptedProvider, database: Path, index: Path) -> Agent:
    ticks = itertools.count()

    def no_sleep(_seconds: float) -> None:
        raise AssertionError("no retry is scripted")

    return Agent(
        provider,
        AgentResources(database_path=database, policy_index_path=index, clock=lambda: CLOCK),
        sleep=no_sleep,
        monotonic=lambda: float(next(ticks)),
    )


def outputs(request: ModelRequest) -> list[dict[str, object]]:
    """Decoded tool envelopes visible to the model in one request."""
    return [json.loads(i.output) for i in request.items if isinstance(i, FunctionCallOutput)]


def quotes(request: ModelRequest) -> list[tuple[int, str]]:
    """(product_id, payment_method) of every ok quote envelope in the request."""
    return [
        (e["data"]["product"]["product_id"], e["data"]["payment_method"])
        for e in outputs(request)
        if e["tool"] == "quote_product" and e["status"] == "ok"
    ]


def references(result: TurnResult) -> list[tuple[str, ...]]:
    return [run.references for run in result.diagnostics.tool_runs]


def tools(result: TurnResult) -> list[tuple[str | None, str]]:
    return [(run.tool, run.status) for run in result.diagnostics.tool_runs]


def assert_answered(result: TurnResult, text: str) -> None:
    assert (result.outcome, result.failure, result.text) == ("answered", None, text)


def assert_diagnostics_safe(result: TurnResult) -> None:
    """No argument, amount, payload field or product name in the diagnostics."""
    shown = repr(result.diagnostics)
    for forbidden in ("payment_method", "product_id", "final_price", "Viol", '{"', "promotion"):
        assert forbidden not in shown
    # Amounts: checked on the tool records (other fields hold byte counts and tokens).
    runs = repr(result.diagnostics.tool_runs)
    for amount in ("854", "266", "899", "333", "5.00", "20.00"):
        assert amount not in runs


@pytest.fixture
def catalog_db(tmp_path, catalog_kit) -> Path:
    base = tmp_path / "catalogo"
    catalog_kit.build(base)
    return base / catalog_kit.DB_DIR / catalog_kit.DB_NAME


@pytest.fixture
def absent_index(tmp_path) -> Path:
    return tmp_path / "sem-indice" / "politicas.json"


@pytest.fixture
def absent_db(tmp_path) -> Path:
    return tmp_path / "sem-banco" / "banco.sqlite3"


@pytest.fixture
def tool_payloads(monkeypatch) -> list[str]:
    """Delegate to the real execute_tool; record only the produced payloads."""
    real = agent_module.execute_tool
    seen: list[str] = []

    def observed(name, arguments, context):
        execution = real(name, arguments, context)
        seen.append(execution.payload)
        return execution

    monkeypatch.setattr(agent_module, "execute_tool", observed)
    return seen


# --- PX06 (agent side): references from literal envelopes ----------------------------


def _envelope(tool: str, status: str, data: object) -> ToolExecution:
    payload = compact({"tool": tool, "status": status, "data": data, "error": None})
    return ToolExecution(tool, status, None, payload)  # type: ignore[arg-type]


def _quote_data(product_id: object, **extra: object) -> dict[str, object]:
    return {"product": {"product_id": product_id, "name": "Violão"}, "final_price": "1.00"} | extra


@pytest.mark.parametrize(
    ("method", "expected"),
    [("pix", ("product:13@pix",)), ("regular", ("product:13@regular",))],
)
def test_px06_ok_quote_reference_carries_validated_payment_method(method, expected):
    execution = _envelope("quote_product", "ok", _quote_data(13, payment_method=method))
    assert agent_module._tool_references(execution) == expected


@pytest.mark.parametrize(
    "data",
    [
        _quote_data(13),  # payment_method absent
        _quote_data(13, payment_method=None),
        _quote_data(13, payment_method=1),
        _quote_data(13, payment_method=True),
        _quote_data(13, payment_method=["pix"]),
        _quote_data(13, payment_method={"pix": True}),
        _quote_data(13, payment_method="PIX"),
        _quote_data(13, payment_method="Pix"),
        _quote_data(13, payment_method=" pix"),
        _quote_data(13, payment_method="pix\n"),
        _quote_data(13, payment_method="pix@regular"),
        _quote_data(13, payment_method="pix;order"),
        _quote_data(13, payment_method="credit"),
        _quote_data(13, payment_method=""),
        _quote_data(13, payment_method="pìx"),
        _quote_data(13, payment_method="\x1b[31mpix"),
        # The method is read only at data.payment_method, never elsewhere.
        {"product": {"product_id": 13, "payment_method": "pix"}, "final_price": "1.00"},
    ],
)
def test_px06_missing_or_invalid_payment_method_keeps_plain_reference(data):
    execution = _envelope("quote_product", "ok", data)
    assert agent_module._tool_references(execution) == ("product:13",)


@pytest.mark.parametrize("status", ["not_found", "invalid_input", "unavailable", "internal_error"])
def test_px06_non_ok_quote_has_no_reference(status):
    execution = _envelope("quote_product", status, _quote_data(13, payment_method="pix"))
    assert agent_module._tool_references(execution) == ()


def test_px06_quote_without_valid_product_id_has_no_reference():
    for product_id in ("13", True, None, 1.5):
        execution = _envelope("quote_product", "ok", _quote_data(product_id, payment_method="pix"))
        assert agent_module._tool_references(execution) == ()


def test_px06_other_tools_never_get_a_payment_method_suffix():
    product = {"product_id": 201, "name": "Violão", "payment_method": "pix"}
    get = _envelope("get_product", "ok", {"product": product, "payment_method": "pix"})
    search = _envelope("search_products", "ok", {"products": [product], "payment_method": "pix"})
    assert agent_module._tool_references(get) == ("product:201",)
    assert agent_module._tool_references(search) == ("product:201",)


def test_px06_reference_extraction_does_not_change_the_payload():
    execution = _envelope("quote_product", "ok", _quote_data(13, payment_method="pix"))
    before = execution.payload
    agent_module._tool_references(execution)
    assert execution.payload == before


# --- Orchestration with real tools (born-green characterization unless noted) ---------


def test_or01_first_pix_quote_reaches_history_and_reference(
    catalog_db, absent_index, tool_payloads
):
    provider = ScriptedProvider(
        response(call("call_p1", PIX_201)), final("No PIX fica R$ 854,91.", "msg_1")
    )
    session = ConversationSession()
    result = make_agent(provider, catalog_db, absent_index).respond(
        session, "Quanto fica o Violão Clássico Alfa no PIX?"
    )

    assert_answered(result, "No PIX fica R$ 854,91.")
    (payload,) = tool_payloads
    envelope = json.loads(payload)
    data = envelope["data"]
    assert (envelope["tool"], envelope["status"]) == ("quote_product", "ok")
    assert (
        data["product"]["product_id"],
        data["payment_method"],
        data["list_price"],
        data["final_price"],
        data["discount_percent"],
        data["discount_source"],
        data["pix_not_combined"],
    ) == (201, "pix", "899.90", "854.91", "5.00", "pix", False)  # 854.905 -> 854.91
    # The envelope sent to the model and retained in history is the tool payload, unchanged.
    assert provider.requests[1].items[-1] == FunctionCallOutput("call_p1", payload)
    assert session.history[0].items[2] == FunctionCallOutput("call_p1", payload)
    assert tools(result) == [("quote_product", "ok")]
    assert references(result) == [("product:201@pix",)]  # RED before D-PIX-03
    assert_diagnostics_safe(result)


def test_or02_only_regular_quote_retained_no_pix_envelope(catalog_db, absent_index, tool_payloads):
    provider = ScriptedProvider(
        response(call("call_r1", REGULAR_201)),
        final("Custa R$ 899,90.", "msg_1"),
        final("Resposta roteirizada.", "msg_2"),
    )
    session = ConversationSession()
    agent = make_agent(provider, catalog_db, absent_index)
    first = agent.respond(session, "Quanto custa o Violão Clássico Alfa?")
    second = agent.respond(session, "E no PIX?")

    assert references(first) == [("product:201@regular",)]  # RED before D-PIX-03
    # Turn 2 sees one quote envelope, and it is regular: no PIX source exists.
    assert quotes(provider.requests[2]) == [(201, "regular")]
    assert json.loads(tool_payloads[0])["data"]["final_price"] == "899.90"
    # The loop allows a turn without tools; it cannot check what the text claims.
    assert_answered(second, "Resposta roteirizada.")
    assert second.diagnostics.tool_runs == ()


def test_or03_reuse_of_retained_pix_quote_without_new_call(catalog_db, absent_index, tool_payloads):
    reuse_text = "Como já vimos nesta conversa, no PIX ele fica R$ 854,91."
    provider = ScriptedProvider(
        response(call("call_p1", PIX_201)),
        final("No PIX fica R$ 854,91.", "msg_1"),
        final(reuse_text, "msg_2"),
    )
    session = ConversationSession()
    agent = make_agent(provider, catalog_db, absent_index)
    agent.respond(session, "Quanto fica o Violão Clássico Alfa no PIX?")
    second = agent.respond(session, "Pode repetir o valor no PIX?")

    (payload,) = tool_payloads
    # The earlier PIX envelope is resent unchanged in the reuse turn.
    assert FunctionCallOutput("call_p1", payload) in provider.requests[2].items
    assert quotes(provider.requests[2]) == [(201, "pix")]
    assert_answered(second, reuse_text)
    assert second.diagnostics.tool_runs == ()  # reuse turn: no tool, no reference
    # Scripted wording does not claim a new check (a script property, not obedience).
    for claim in ("consultei", "consultar de novo", "verifiquei agora", "acabei de"):
        assert claim not in reuse_text.lower()


def test_or04_pix_quote_of_another_product_does_not_cover_this_one(
    catalog_db, absent_index, tool_payloads
):
    provider = ScriptedProvider(
        response(call("call_p1", PIX_212)),
        final("No PIX o Violão Mu fica R$ 266,66.", "msg_1"),
        response(call("call_p2", PIX_201)),
        final("No PIX o Violão Clássico Alfa fica R$ 854,91.", "msg_2"),
    )
    session = ConversationSession()
    agent = make_agent(provider, catalog_db, absent_index)
    first = agent.respond(session, "Quanto fica o Violão Mu no PIX?")
    second = agent.respond(session, "E o Violão Clássico Alfa no PIX?")

    # At the start of turn 2 the only PIX envelope belongs to product 212.
    assert quotes(provider.requests[2]) == [(212, "pix")]
    assert references(first) == [("product:212@pix",)]  # RED before D-PIX-03
    assert references(second) == [("product:201@pix",)]  # RED before D-PIX-03
    assert quotes(provider.requests[3]) == [(212, "pix"), (201, "pix")]


def test_or05_multiple_products_promotion_not_cumulative(catalog_db, absent_index, tool_payloads):
    provider = ScriptedProvider(
        response(call("call_p1", PIX_212)),
        response(call("call_p2", PIX_201)),
        final("Resposta roteirizada.", "msg_1"),
    )
    result = make_agent(provider, catalog_db, absent_index).respond(
        ConversationSession(), "Quanto fica cada um no PIX?"
    )

    assert references(result) == [("product:212@pix",), ("product:201@pix",)]  # RED before
    mu, alfa = (json.loads(p)["data"] for p in tool_payloads)
    # 212: active 20% wins over 15%; PIX does not combine (not 253.33 or 216.66).
    assert (
        mu["final_price"],
        mu["discount_percent"],
        mu["discount_source"],
        mu["promotion"]["promotion_id"],
        mu["pix_not_combined"],
    ) == ("266.66", "20.00", "promotion", 305, True)
    assert (alfa["final_price"], alfa["discount_source"], alfa["pix_not_combined"]) == (
        "854.91",
        "pix",
        False,
    )
    assert_diagnostics_safe(result)


def test_or06_explicit_new_check_adds_a_new_envelope(catalog_db, absent_index, tool_payloads):
    provider = ScriptedProvider(
        response(call("call_p1", PIX_201)),
        final("No PIX fica R$ 854,91.", "msg_1"),
        response(call("call_p2", PIX_201)),
        final("Consultei de novo: R$ 854,91.", "msg_2"),
    )
    session = ConversationSession()
    agent = make_agent(provider, catalog_db, absent_index)
    agent.respond(session, "Quanto fica o Violão Clássico Alfa no PIX?")
    second = agent.respond(session, "Consulta de novo o preço no PIX, quero o valor atualizado.")

    assert references(second) == [("product:201@pix",)]  # RED before D-PIX-03
    first_payload, second_payload = tool_payloads
    retained = [i for t in session.history for i in t.items if isinstance(i, FunctionCallOutput)]
    assert retained == [
        FunctionCallOutput("call_p1", first_payload),
        FunctionCallOutput("call_p2", second_payload),
    ]
    finals = [json.loads(p)["data"]["final_price"] for p in tool_payloads]
    assert finals == ["854.91", "854.91"]


def test_or07_pruned_pix_quote_is_no_longer_sent(catalog_db, absent_index, tool_payloads):
    steps = [response(call("call_p1", PIX_201)), final("R$ 854,91.", "msg_0")]
    steps += [final(f"ok {n}", f"msg_{n}") for n in range(1, 21)]
    steps.append(final("Qual produto?", "msg_21"))
    provider = ScriptedProvider(*steps)
    session = ConversationSession()
    agent = make_agent(provider, catalog_db, absent_index)
    agent.respond(session, "Quanto fica o Violão Clássico Alfa no PIX?")
    for n in range(1, 21):
        agent.respond(session, f"mensagem {n}")
    last = agent.respond(session, "E no PIX?")

    assert session.pruned_turns == 2  # turn 1 dropped at turn 21, turn 2 at turn 22
    assert outputs(provider.requests[-1]) == []
    assert all(not isinstance(i, FunctionCallOutput) for t in session.history for i in t.items)
    assert last.diagnostics.history_pruned_turns == 2
    assert provider.overflow == 0


def test_or08_failed_pix_quote_records_status_without_reference(
    catalog_db, absent_index, absent_db, tool_payloads
):
    provider = ScriptedProvider(
        response(call("call_p1", PIX_999)), final("Não consegui verificar agora.", "msg_1")
    )
    result = make_agent(provider, catalog_db, absent_index).respond(
        ConversationSession(), "Quanto fica o produto 999 no PIX?"
    )
    (payload,) = tool_payloads
    assert json.loads(payload)["status"] == "not_found"
    assert tools(result) == [("quote_product", "not_found")]
    assert references(result) == [()]
    assert_diagnostics_safe(result)


def test_or08b_failure_after_earlier_pix_quote_keeps_history_unchanged(
    catalog_db, absent_index, absent_db, tool_payloads
):
    """The application neither removes the earlier envelope nor blocks its reuse.

    Not reusing it as a substitute for a failed lookup (D-PIX-04) is an instruction rule
    that only a real evaluation can check.
    """
    provider = ScriptedProvider(
        response(call("call_p1", PIX_201)),
        final("No PIX fica R$ 854,91.", "msg_1"),
        response(call("call_p2", PIX_201)),
        final("Não consegui verificar o preço no PIX agora.", "msg_2"),
    )
    session = ConversationSession()
    make_agent(provider, catalog_db, absent_index).respond(session, "PIX do Violão Clássico Alfa?")
    failed = make_agent(provider, absent_db, absent_index).respond(
        session, "Consulta de novo o PIX dele?"
    )

    first_payload, failed_payload = tool_payloads
    failed_status = json.loads(failed_payload)["status"]
    assert failed_status != "ok"
    assert tools(failed) == [("quote_product", failed_status)]
    assert references(failed) == [()]
    # Both envelopes reach the model; the ok one is still there, unchanged.
    assert outputs(provider.requests[3]) == [json.loads(first_payload), json.loads(failed_payload)]
    assert_answered(failed, "Não consegui verificar o preço no PIX agora.")


def test_or09_reset_removes_earlier_pix_quotes(catalog_db, absent_index, tool_payloads):
    provider = ScriptedProvider(
        response(call("call_p1", PIX_201)),
        final("R$ 854,91.", "msg_1"),
        final("Qual produto?", "msg_2"),
        final("Qual produto?", "msg_3"),
    )
    session = ConversationSession(customer=CustomerContext(1))
    agent = make_agent(provider, catalog_db, absent_index)
    agent.respond(session, "PIX do Violão Clássico Alfa?")
    assert provider.requests[0].instructions.endswith("\n\n" + LINKED_LINE)

    session.reset(customer=CustomerContext(2))
    agent.respond(session, "E no PIX?")
    assert provider.requests[2].items == (UserMessage("E no PIX?"),)
    assert provider.requests[2].instructions.endswith("\n\n" + LINKED_LINE)
    assert session.customer == CustomerContext(2)  # identity set by reset, not by any envelope

    session.reset()
    agent.respond(session, "E no PIX?")
    assert provider.requests[3].items == (UserMessage("E no PIX?"),)
    assert provider.requests[3].instructions.endswith("\n\n" + ANONYMOUS_LINE)
    for request in provider.requests:
        assert "201" not in request.instructions


def test_or10_unsupported_amount_is_not_verified_by_the_application(
    catalog_db, absent_index, tool_payloads
):
    """Documented limit: no amount parser or post-check exists in the loop."""
    text = "No PIX fica R$ 123,45."
    provider = ScriptedProvider(final(text, "msg_1"))
    result = make_agent(provider, catalog_db, absent_index).respond(
        ConversationSession(), "Quanto fica o Violão Clássico Alfa no PIX?"
    )
    assert_answered(result, text)
    assert result.diagnostics.tool_runs == ()
    assert tool_payloads == []


def test_or11_pix_instructions_reach_every_attempt(catalog_db, absent_index, tool_payloads):
    provider = ScriptedProvider(response(call("call_p1", PIX_201)), final("R$ 854,91.", "msg_1"))
    make_agent(provider, catalog_db, absent_index).respond(ConversationSession(), "PIX?")
    for request in provider.requests:
        assert "do not say that you checked again" in request.instructions  # RED before G13
        assert "never calculate it" in request.instructions  # RED before G13
