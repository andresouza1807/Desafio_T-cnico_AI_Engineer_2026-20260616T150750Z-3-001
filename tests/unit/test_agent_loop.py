"""Slice 07-C loop and protocol cases (docs/plans/07-agent.md, spec 07 G5, G6, G11, G15).

Covers AG37-AG50, AG65, AG84 and AG114. The provider is a scripted `ModelProvider`; tools
run through the real `execute_tool` with absent synthetic files under `tmp_path`, observed
by a delegating spy. Oracles are literal item sequences, codes, texts and envelopes.

This module also holds the shared helpers used by `test_agent_limits.py` and
`test_agent_diagnostics.py` (no conftest is added by this slice). A scripted provider does
not prove language quality, real tool choice or real resistance to prompt injection.
"""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

import emporio_agent.agent as agent_module
from emporio_agent.agent import Agent, AgentResources, TurnResult
from emporio_agent.instructions import AGENT_INSTRUCTIONS, session_instructions
from emporio_agent.provider import (
    FunctionCall,
    FunctionCallOutput,
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderItem,
    Usage,
    UserMessage,
)
from emporio_agent.services.orders import CustomerContext
from emporio_agent.session import CompletedTurn, ConversationSession
from emporio_agent.tools import ToolContext, execute_tool, provider_tool_definitions

# --- Shared literals --------------------------------------------------------------------

CONFIGURED_MODEL = "gpt-test-configured"
REPORTED_MODEL = "gpt-test-reported-2026-01-01"
FIXED_NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)

REFUSED_TEXT = (
    "Não posso ajudar com esse pedido. Posso ajudar com produtos, preços, pedidos e "
    "políticas da loja."
)
INPUT_INVALID_TEXT = (
    "Não consegui ler a sua mensagem porque ela contém caracteres inválidos. "
    "Escreva a mensagem novamente."
)
INPUT_EMPTY_TEXT = (
    "A sua mensagem está vazia. Escreva a sua pergunta sobre produtos, preços, pedidos ou "
    "políticas da loja."
)
INPUT_TOO_LONG_TEXT = (
    "A sua mensagem é longa demais. Envie uma pergunta mais curta, com até 4.000 caracteres."
)
TEMPORARY_TEXT = "O assistente está temporariamente indisponível. Tente novamente em instantes."
CONFIGURATION_TEXT = (
    "O assistente está indisponível por um problema de configuração. "
    "Avise o responsável pela aplicação."
)
NOT_COMPLETED_TEXT = "Não consegui concluir a resposta. Tente novamente ou reformule a pergunta."
TOO_MUCH_TEXT = (
    "A pergunta exigiu consultas ou contexto demais. Faça uma pergunta mais específica ou "
    "inicie uma nova conversa."
)

# Real envelopes of execute_tool with absent files (literal, measured once by hand).
ORDER_NOT_ACCESSIBLE = (
    '{"tool":"get_order","status":"unavailable","data":null,'
    '"error":{"code":"order_not_accessible","field":null}}'
)
ORDER_DATA_UNAVAILABLE = (
    '{"tool":"get_order","status":"unavailable","data":null,'
    '"error":{"code":"data_unavailable","field":null}}'
)
PRODUCTS_DATA_UNAVAILABLE = (
    '{"tool":"search_products","status":"unavailable","data":null,'
    '"error":{"code":"data_unavailable","field":null}}'
)
POLICIES_DATA_UNAVAILABLE = (
    '{"tool":"search_policies","status":"unavailable","data":null,'
    '"error":{"code":"data_unavailable","field":null}}'
)
UNKNOWN_TOOL = (
    '{"tool":null,"status":"invalid_input","data":null,'
    '"error":{"code":"unknown_tool","field":null}}'
)
INVALID_JSON = (
    '{"tool":"get_order","status":"invalid_input","data":null,'
    '"error":{"code":"invalid_json","field":null}}'
)
UNEXPECTED_FIELD = (
    '{"tool":"get_order","status":"invalid_input","data":null,'
    '"error":{"code":"unexpected_field","field":null}}'
)

ORDER_ARGS = '{"order_id":1}'
PRODUCTS_ARGS = (
    '{"query":"violão","category":null,"max_price_brl":null,"available_only":false,"offset":0}'
)
POLICIES_ARGS = '{"query":"troca"}'


# --- Item and response builders (test input, not oracles) -------------------------------


def message_item(text: str, *, phase: str | None = None, item_id: str = "msg_1") -> ProviderItem:
    body: dict[str, object] = {"type": "message", "id": item_id, "role": "assistant"}
    body["status"] = "completed"
    if phase is not None:
        body["phase"] = phase
    body["content"] = [{"type": "output_text", "text": text, "annotations": []}]
    raw = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    return ProviderItem("assistant_message", raw)


def reasoning_item(item_id: str = "rs_1", encrypted: str = "enc-1") -> ProviderItem:
    raw = (
        '{"type":"reasoning","id":"'
        + item_id
        + '","summary":[],"encrypted_content":"'
        + encrypted
        + '"}'
    )
    return ProviderItem("reasoning", raw)


def call_item(call_id: str, name: str = "get_order", arguments: str = ORDER_ARGS) -> FunctionCall:
    body = {
        "type": "function_call",
        "id": "fc_" + call_id,
        "call_id": call_id,
        "name": name,
        "arguments": arguments,
        "status": "completed",
    }
    raw = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    return FunctionCall(call_id, name, arguments, raw)


def make_response(
    output: tuple[ProviderItem | FunctionCall, ...],
    *,
    text: str = "",
    status: str = "completed",
    incomplete_reason: str | None = None,
    commentary: bool = False,
    refusal: bool = False,
    usage: Usage | None = None,
    usage_invalid: bool = False,
    request_id: str | None = None,
    response_id: str | None = "resp_1",
    model: str = REPORTED_MODEL,
) -> ModelResponse:
    return ModelResponse(
        response_id=response_id,
        model=model,
        status=status,  # type: ignore[arg-type]
        incomplete_reason=incomplete_reason,
        output=output,
        text=text,
        commentary_present=commentary,
        refusal=refusal,
        usage=usage,
        usage_invalid=usage_invalid,
        request_id=request_id,
    )


def answer(text: str, *before: ProviderItem, usage: Usage | None = None) -> ModelResponse:
    """Final answer: optional items, then one final-eligible message with `text`."""
    return make_response((*before, message_item(text, item_id="msg_final")), text=text, usage=usage)


def tool_round(*items: ProviderItem | FunctionCall, usage: Usage | None = None) -> ModelResponse:
    return make_response(tuple(items), usage=usage)


# --- Scripted provider, spies and clocks ------------------------------------------------


class ScriptedProvider:
    """`ModelProvider` that returns or raises scripted steps and records every request."""

    def __init__(self, *steps: object, model: str = CONFIGURED_MODEL) -> None:
        self._steps = list(steps)
        self._model = model
        self.requests: list[ModelRequest] = []
        self.overcalled = False

    @property
    def model(self) -> str:
        return self._model

    def create(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if len(self.requests) > len(self._steps):
            self.overcalled = True
            raise AssertionError("provider called more often than scripted")
        step = self._steps[len(self.requests) - 1]
        if isinstance(step, BaseException):
            raise step
        return step  # type: ignore[return-value]

    @property
    def calls(self) -> int:
        return len(self.requests)


@dataclass
class ToolSpy:
    """Delegates to the real execute_tool; records arguments and the real result."""

    calls: list[tuple[str, str, ToolContext, object]] = field(default_factory=list)
    raise_after: BaseException | None = None

    def __call__(self, name: str, arguments: str, context: ToolContext):  # noqa: ANN204
        execution = execute_tool(name, arguments, context)
        self.calls.append((name, arguments, context, execution))
        if self.raise_after is not None:
            raise self.raise_after
        return execution

    @property
    def names(self) -> list[str]:
        return [name for name, _, _, _ in self.calls]


class Ticker:
    """Injected monotonic clock: scripted values, or `calls * step` seconds."""

    def __init__(self, values: list[float] | None = None, step: float = 0.125) -> None:
        self.values = values
        self.step = step
        self.calls = 0

    def __call__(self) -> float:
        self.calls += 1
        if self.values is not None:
            return self.values[self.calls - 1]
        return self.calls * self.step


@dataclass
class Kit:
    resources: AgentResources
    spy: ToolSpy
    sleeps: list[float]
    clock: Ticker

    def agent(self, provider: object) -> Agent:
        return Agent(
            provider,  # type: ignore[arg-type]
            self.resources,
            sleep=self.sleeps.append,
            monotonic=self.clock,
        )


def make_kit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Kit:
    resources = AgentResources(
        database_path=tmp_path / "absent.sqlite3",
        policy_index_path=tmp_path / "absent-index.json",
        clock=lambda: FIXED_NOW,
    )
    spy = ToolSpy()
    monkeypatch.setattr(agent_module, "execute_tool", spy)
    return Kit(resources=resources, spy=spy, sleeps=[], clock=Ticker())


def state(session: ConversationSession) -> tuple[object, ...]:
    return (session.history, session.history_bytes, session.pruned_turns, session.customer)


def seeded_session(customer: CustomerContext | None = None) -> ConversationSession:
    """Session with 20 retained turns, pruned_turns == 1 and call_id "call_hist" retained."""
    session = ConversationSession(customer=customer)
    for index in range(1, 21):
        session.commit(
            CompletedTurn((UserMessage(f"h{index}"), message_item(f"r{index}", item_id="m_h")))
        )
    hist_call = call_item("call_hist")
    session.commit(
        CompletedTurn(
            (
                UserMessage("h21"),
                hist_call,
                FunctionCallOutput("call_hist", ORDER_NOT_ACCESSIBLE),
                message_item("r21", item_id="m_h"),
            )
        )
    )
    assert len(session.history) == 20
    assert session.pruned_turns == 1
    return session


def history_items(session: ConversationSession) -> tuple[object, ...]:
    return tuple(item for turn in session.history for item in turn.items)


@pytest.fixture
def kit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Kit:
    return make_kit(tmp_path, monkeypatch)


# --- AG37: text-only answer ------------------------------------------------------------


def test_ag37_text_only_answer_commits_one_turn(kit: Kit) -> None:
    final = message_item("Olá! Como posso ajudar?", item_id="msg_final")
    provider = ScriptedProvider(make_response((final,), text="Olá! Como posso ajudar?"))
    session = ConversationSession()

    result = kit.agent(provider).respond(session, "oi")

    assert isinstance(result, TurnResult)
    assert (result.outcome, result.text, result.failure) == (
        "answered",
        "Olá! Como posso ajudar?",
        None,
    )
    assert provider.calls == 1
    assert provider.requests[0].items == (UserMessage("oi"),)
    assert provider.requests[0].allow_tool_calls is True
    assert session.history == (CompletedTurn((UserMessage("oi"), final)),)
    assert kit.spy.calls == []
    assert len(result.diagnostics.attempts) == 1


def test_ag37_answer_follows_retained_history(kit: Kit) -> None:
    session = seeded_session()
    before = history_items(session)
    provider = ScriptedProvider(answer("Claro."))

    result = kit.agent(provider).respond(session, "e agora?")

    assert result.outcome == "answered"
    assert provider.requests[0].items == (*before, UserMessage("e agora?"))
    assert session.history[-1] == CompletedTurn(
        (UserMessage("e agora?"), message_item("Claro.", item_id="msg_final"))
    )


# --- AG38: one tool round --------------------------------------------------------------


def test_ag38_one_tool_round_sends_real_output_with_call_id(kit: Kit) -> None:
    call = call_item("call_1")
    provider = ScriptedProvider(tool_round(call), answer("Não consigo acessar esse pedido."))
    session = ConversationSession()

    result = kit.agent(provider).respond(session, "meu pedido 1?")

    assert result.outcome == "answered"
    assert result.text == "Não consigo acessar esse pedido."
    assert provider.calls == 2
    assert provider.requests[1].items == (
        UserMessage("meu pedido 1?"),
        call,
        FunctionCallOutput("call_1", ORDER_NOT_ACCESSIBLE),
    )
    direct = execute_tool(
        "get_order",
        ORDER_ARGS,
        ToolContext(
            kit.resources.database_path, kit.resources.policy_index_path, kit.resources.clock, None
        ),
    )
    assert direct.payload == ORDER_NOT_ACCESSIBLE
    assert len(kit.spy.calls) == 1
    name, arguments, context, _ = kit.spy.calls[0]
    assert (name, arguments) == ("get_order", ORDER_ARGS)
    assert context.database_path == kit.resources.database_path
    assert context.policy_index_path == kit.resources.policy_index_path
    assert context.clock is kit.resources.clock
    assert context.customer is None
    assert session.history == (
        CompletedTurn(
            (
                UserMessage("meu pedido 1?"),
                call,
                FunctionCallOutput("call_1", ORDER_NOT_ACCESSIBLE),
                message_item("Não consigo acessar esse pedido.", item_id="msg_final"),
            )
        ),
    )


# --- AG39: three rounds, three tools ---------------------------------------------------


def test_ag39_three_rounds_literal_sequences(kit: Kit) -> None:
    customer = CustomerContext(1)
    session = ConversationSession(customer=customer)
    c1 = call_item("call_a", "get_order", ORDER_ARGS)
    c2 = call_item("call_b", "search_products", PRODUCTS_ARGS)
    c3 = call_item("call_c", "search_policies", POLICIES_ARGS)
    r1 = reasoning_item("rs_a", "enc-a")
    provider = ScriptedProvider(
        tool_round(r1, c1), tool_round(c2), tool_round(c3), answer("Não consegui consultar.")
    )

    result = kit.agent(provider).respond(session, "várias perguntas")

    user = UserMessage("várias perguntas")
    o1 = FunctionCallOutput("call_a", ORDER_DATA_UNAVAILABLE)
    o2 = FunctionCallOutput("call_b", PRODUCTS_DATA_UNAVAILABLE)
    o3 = FunctionCallOutput("call_c", POLICIES_DATA_UNAVAILABLE)
    assert result.outcome == "answered"
    assert [request.items for request in provider.requests] == [
        (user,),
        (user, r1, c1, o1),
        (user, r1, c1, o1, c2, o2),
        (user, r1, c1, o1, c2, o2, c3, o3),
    ]
    final = message_item("Não consegui consultar.", item_id="msg_final")
    assert session.history == (CompletedTurn((user, r1, c1, o1, c2, o2, c3, o3, final)),)
    assert kit.spy.names == ["get_order", "search_products", "search_policies"]
    contexts = [context for _, _, context, _ in kit.spy.calls]
    assert contexts[0] is contexts[1] is contexts[2]  # one ToolContext per turn
    assert contexts[0].customer is customer


# --- AG40: reasoning and commentary preserved ------------------------------------------


def test_ag40_reasoning_and_commentary_resent_in_order(kit: Kit) -> None:
    reasoning = reasoning_item("rs_1", "enc-SECRET-OPAQUE")
    commentary = message_item("Vou verificar o pedido.", phase="commentary", item_id="msg_c")
    call = call_item("call_1")
    provider = ScriptedProvider(
        make_response((reasoning, commentary, call), commentary=True),
        answer("Não tenho acesso a esse pedido."),
    )
    session = ConversationSession()

    result = kit.agent(provider).respond(session, "pedido 1")

    output = FunctionCallOutput("call_1", ORDER_NOT_ACCESSIBLE)
    assert provider.requests[1].items == (
        UserMessage("pedido 1"),
        reasoning,
        commentary,
        call,
        output,
    )
    assert result.text == "Não tenho acesso a esse pedido."
    assert "Vou verificar" not in result.text
    assert session.history[0].items[:5] == (
        UserMessage("pedido 1"),
        reasoning,
        commentary,
        call,
        output,
    )


# --- AG41: two calls in one response ---------------------------------------------------


def test_ag41_batch_runs_in_order_and_outputs_follow_all_items(kit: Kit) -> None:
    reasoning = reasoning_item()
    call_a = call_item("call_a", "get_order", ORDER_ARGS)
    call_b = call_item("call_b", "search_policies", POLICIES_ARGS)
    provider = ScriptedProvider(
        tool_round(reasoning, call_a, call_b), answer("Não consegui verificar.")
    )

    result = kit.agent(provider).respond(ConversationSession(), "duas coisas")

    assert result.outcome == "answered"
    assert kit.spy.names == ["get_order", "search_policies"]
    assert provider.requests[1].items == (
        UserMessage("duas coisas"),
        reasoning,
        call_a,
        call_b,
        FunctionCallOutput("call_a", ORDER_NOT_ACCESSIBLE),
        FunctionCallOutput("call_b", POLICIES_DATA_UNAVAILABLE),
    )
    assert [run.index for run in result.diagnostics.tool_runs] == [1, 2]


def test_ag41_outputs_matched_by_call_id_not_position(kit: Kit) -> None:
    # Two calls of different tools: each output carries its own call's envelope.
    call_x = call_item("zz_last", "search_policies", POLICIES_ARGS)
    call_y = call_item("aa_first", "get_order", ORDER_ARGS)
    provider = ScriptedProvider(tool_round(call_x, call_y), answer("ok"))

    kit.agent(provider).respond(ConversationSession(), "x")

    assert provider.requests[1].items[-2:] == (
        FunctionCallOutput("zz_last", POLICIES_DATA_UNAVAILABLE),
        FunctionCallOutput("aa_first", ORDER_NOT_ACCESSIBLE),
    )


# --- AG42/AG43: duplicate call_id ------------------------------------------------------


def test_ag42_duplicate_call_id_in_one_response(kit: Kit) -> None:
    session = seeded_session()
    before = state(session)
    provider = ScriptedProvider(
        tool_round(
            call_item("dup", "get_order"), call_item("dup", "search_policies", POLICIES_ARGS)
        )
    )

    result = kit.agent(provider).respond(session, "x")

    assert (result.outcome, result.failure, result.text) == (
        "failed",
        "protocol_error",
        NOT_COMPLETED_TEXT,
    )
    assert kit.spy.calls == []
    assert provider.calls == 1
    assert state(session) == before


def test_ag42_duplicate_call_id_with_valid_call_runs_nothing(kit: Kit) -> None:
    provider = ScriptedProvider(
        tool_round(call_item("ok_1"), call_item("dup"), call_item("dup", "search_policies"))
    )
    result = kit.agent(provider).respond(ConversationSession(), "x")
    assert result.failure == "protocol_error"
    assert kit.spy.calls == []


def test_ag43_call_id_equal_to_retained_history(kit: Kit) -> None:
    session = seeded_session()
    before = state(session)
    provider = ScriptedProvider(tool_round(call_item("call_hist")))

    result = kit.agent(provider).respond(session, "x")

    assert (result.outcome, result.failure) == ("failed", "protocol_error")
    assert kit.spy.calls == []
    assert state(session) == before


def test_ag43_call_id_equal_to_earlier_call_in_same_turn(kit: Kit) -> None:
    session = seeded_session()
    before = state(session)
    provider = ScriptedProvider(
        tool_round(call_item("call_1")),
        tool_round(call_item("call_1", "search_policies", POLICIES_ARGS)),
    )

    result = kit.agent(provider).respond(session, "x")

    assert (result.outcome, result.failure) == ("failed", "protocol_error")
    assert len(kit.spy.calls) == 1  # only the first round; zero new executions
    assert provider.calls == 2
    assert state(session) == before


# --- AG44: adversarial calls go through execute_tool -----------------------------------


def test_ag44_unknown_tool_and_invalid_arguments_are_real_envelopes(kit: Kit) -> None:
    calls = (
        call_item("call_1", "GET_ORDER", ORDER_ARGS),
        call_item("call_2", "get_order", "{"),
        call_item("call_3", "get_order", '{"order_id": 1, "customer_id": 2}'),
    )
    provider = ScriptedProvider(tool_round(*calls), answer("Não consegui verificar."))
    session = ConversationSession(customer=CustomerContext(1))

    result = kit.agent(provider).respond(session, "pedido do cliente 2")

    assert result.outcome == "answered"
    assert provider.requests[1].items[-3:] == (
        FunctionCallOutput("call_1", UNKNOWN_TOOL),
        FunctionCallOutput("call_2", INVALID_JSON),
        FunctionCallOutput("call_3", UNEXPECTED_FIELD),
    )
    assert kit.spy.names == ["GET_ORDER", "get_order", "get_order"]
    runs = result.diagnostics.tool_runs
    assert [(r.index, r.tool, r.status, r.error_code) for r in runs] == [
        (1, None, "invalid_input", "unknown_tool"),
        (2, "get_order", "invalid_input", "invalid_json"),
        (3, "get_order", "invalid_input", "unexpected_field"),
    ]
    assert session.customer == CustomerContext(1)


def test_ag44_empty_name_counts_as_an_execution(kit: Kit) -> None:
    provider = ScriptedProvider(tool_round(call_item("call_1", "", "{}")), answer("ok"))
    result = kit.agent(provider).respond(ConversationSession(), "x")
    assert provider.requests[1].items[-1] == FunctionCallOutput("call_1", UNKNOWN_TOOL)
    assert len(result.diagnostics.tool_runs) == 1


# --- AG45: incomplete responses --------------------------------------------------------


@pytest.mark.parametrize(
    ("output", "text", "reason"),
    [
        (
            (message_item("parcial SENTINEL-PARTIAL"),),
            "parcial SENTINEL-PARTIAL",
            "max_output_tokens",
        ),
        ((call_item("call_1"),), "", "item_incomplete"),
        (
            (message_item("antes SENTINEL-PARTIAL"), call_item("call_1")),
            "antes SENTINEL-PARTIAL",
            "other",
        ),
        ((), "", "other"),
    ],
    ids=["text", "call", "text_and_call", "empty"],
)
def test_ag45_incomplete_response_fails_without_execution(
    kit: Kit, output: tuple[object, ...], text: str, reason: str
) -> None:
    session = seeded_session()
    before = state(session)
    provider = ScriptedProvider(
        make_response(output, text=text, status="incomplete", incomplete_reason=reason)  # type: ignore[arg-type]
    )

    result = kit.agent(provider).respond(session, "x")

    assert (result.outcome, result.failure, result.text) == (
        "failed",
        "response_incomplete",
        NOT_COMPLETED_TEXT,
    )
    assert "SENTINEL-PARTIAL" not in result.text
    assert kit.spy.calls == []
    assert state(session) == before


# --- AG46: refusal ---------------------------------------------------------------------


@pytest.mark.parametrize("with_calls", [False, True], ids=["refusal", "refusal_with_calls"])
def test_ag46_refusal_wins_and_runs_nothing(kit: Kit, with_calls: bool) -> None:
    session = seeded_session()
    before = state(session)
    output: tuple[ProviderItem | FunctionCall, ...] = (message_item(""),)
    if with_calls:
        output = (*output, call_item("call_1"), call_item("call_2"))
    provider = ScriptedProvider(make_response(output, refusal=True))

    result = kit.agent(provider).respond(session, "x")

    assert (result.outcome, result.failure, result.text) == ("refused", None, REFUSED_TEXT)
    assert kit.spy.calls == []
    assert provider.calls == 1
    assert state(session) == before


def test_ag46_refusal_with_text_shows_only_fixed_message(kit: Kit) -> None:
    provider = ScriptedProvider(
        make_response(
            (message_item("SENTINEL-REFUSAL-TEXT"),), text="SENTINEL-REFUSAL-TEXT", refusal=True
        )
    )
    result = kit.agent(provider).respond(ConversationSession(), "x")
    assert result.text == REFUSED_TEXT


# --- AG47: empty responses -------------------------------------------------------------


@pytest.mark.parametrize(
    ("output", "text"),
    [
        ((message_item(""),), ""),
        ((message_item("  \n\t "),), "  \n\t "),
        ((reasoning_item(),), ""),
        ((), ""),
    ],
    ids=["empty_text", "whitespace", "reasoning_only", "no_items"],
)
def test_ag47_empty_response(kit: Kit, output: tuple[object, ...], text: str) -> None:
    session = seeded_session()
    before = state(session)
    provider = ScriptedProvider(make_response(output, text=text))  # type: ignore[arg-type]

    result = kit.agent(provider).respond(session, "x")

    assert (result.outcome, result.failure, result.text) == (
        "failed",
        "empty_response",
        NOT_COMPLETED_TEXT,
    )
    assert state(session) == before


# --- AG48: final text limit ------------------------------------------------------------


@pytest.mark.parametrize("char", ["a", "🎸"], ids=["ascii", "four_byte"])
def test_ag48_final_text_of_exactly_16000_code_points_is_answered(kit: Kit, char: str) -> None:
    text = char * 16_000
    provider = ScriptedProvider(make_response((message_item(text),), text=text))
    result = kit.agent(provider).respond(ConversationSession(), "x")
    assert (result.outcome, result.text) == ("answered", text)


def test_ag48_final_text_of_16001_code_points_fails_untruncated(kit: Kit) -> None:
    text = "a" * 16_001
    session = seeded_session()
    before = state(session)
    provider = ScriptedProvider(make_response((message_item(text),), text=text))

    result = kit.agent(provider).respond(session, "x")

    assert (result.outcome, result.failure, result.text) == (
        "failed",
        "response_too_long",
        NOT_COMPLETED_TEXT,
    )
    assert state(session) == before


# --- AG49/AG50: invalid response and unexpected provider exception ---------------------


def test_ag49_response_invalid_is_not_retried(kit: Kit) -> None:
    provider = ScriptedProvider(ProviderError("response_invalid"))
    result = kit.agent(provider).respond(ConversationSession(), "x")
    assert (result.outcome, result.failure, result.text) == (
        "failed",
        "response_invalid",
        NOT_COMPLETED_TEXT,
    )
    assert provider.calls == 1
    assert kit.sleeps == []


def test_ag50_unexpected_exception_from_provider_fails_closed(kit: Kit) -> None:
    session = seeded_session()
    before = state(session)
    provider = ScriptedProvider(RuntimeError("SENTINEL-RUNTIME"))

    result = kit.agent(provider).respond(session, "x")

    assert (result.outcome, result.failure, result.text) == (
        "failed",
        "unexpected",
        NOT_COMPLETED_TEXT,
    )
    assert provider.calls == 1
    assert kit.sleeps == []
    assert state(session) == before
    assert [a.error_kind for a in result.diagnostics.attempts] == ["unexpected"]


def test_ag50_non_response_return_value_fails_closed(kit: Kit) -> None:
    provider = ScriptedProvider("not a ModelResponse")
    result = kit.agent(provider).respond(ConversationSession(), "x")
    assert (result.outcome, result.failure) == ("failed", "unexpected")
    assert provider.calls == 1


# --- AG65: tool schemas constant across attempts ---------------------------------------


def test_ag65_tools_identical_in_every_attempt(kit: Kit, monkeypatch: pytest.MonkeyPatch) -> None:
    definitions_calls: list[int] = []

    def definitions_spy() -> list[dict[str, object]]:
        definitions_calls.append(1)
        return provider_tool_definitions()

    monkeypatch.setattr(agent_module, "provider_tool_definitions", definitions_spy)
    provider = ScriptedProvider(
        tool_round(call_item("call_1")),
        ProviderError("timeout"),
        tool_round(call_item("call_2")),
        answer("ok"),
    )

    result = kit.agent(provider).respond(ConversationSession(), "x")

    assert result.outcome == "answered"
    assert definitions_calls == [1]  # once per turn
    expected = tuple(provider_tool_definitions())
    first = provider.requests[0].tools
    assert type(first) is tuple
    for request in provider.requests:
        assert request.tools == expected
        assert request.tools is first


def test_ag65_tools_are_requested_again_on_the_next_turn(
    kit: Kit, monkeypatch: pytest.MonkeyPatch
) -> None:
    definitions_calls: list[int] = []

    def definitions_spy() -> list[dict[str, object]]:
        definitions_calls.append(1)
        return provider_tool_definitions()

    monkeypatch.setattr(agent_module, "provider_tool_definitions", definitions_spy)
    provider = ScriptedProvider(answer("a"), answer("b"))
    agent = kit.agent(provider)
    session = ConversationSession()
    agent.respond(session, "1")
    agent.respond(session, "2")
    assert definitions_calls == [1, 1]


# --- AG84: fixed instructions outside the history --------------------------------------


@pytest.mark.parametrize("customer", [None, CustomerContext(7)], ids=["anonymous", "linked"])
def test_ag84_instructions_resent_on_every_attempt(
    kit: Kit, customer: CustomerContext | None
) -> None:
    session = ConversationSession(customer=customer)
    provider = ScriptedProvider(
        tool_round(call_item("call_1")),
        ProviderError("server_error"),
        answer("ok"),
    )

    result = kit.agent(provider).respond(session, "x")

    assert result.outcome == "answered"
    expected = session_instructions(customer is not None)
    assert len(provider.requests) == 3
    for request in provider.requests:
        assert request.instructions == expected
    first_line = AGENT_INSTRUCTIONS.splitlines()[0]
    for request in provider.requests:
        for item in request.items:
            assert first_line not in repr(item)
    assert first_line not in repr(session.history)
    assert all("Session:" not in repr(item) for item in history_items(session))


def test_ag84_reset_to_linked_changes_the_session_line_only(kit: Kit) -> None:
    session = ConversationSession()
    provider = ScriptedProvider(answer("a"), answer("b"))
    agent = kit.agent(provider)
    agent.respond(session, "1")
    session.reset(customer=CustomerContext(3))
    agent.respond(session, "2")
    assert provider.requests[0].instructions == session_instructions(False)
    assert provider.requests[1].instructions == session_instructions(True)
    assert provider.requests[1].items == (UserMessage("2"),)


# --- AG114: phase handling in the loop -------------------------------------------------


def test_ag114_commentary_only_is_no_final_answer(kit: Kit) -> None:
    session = seeded_session()
    before = state(session)
    commentary = message_item("SENTINEL-COMMENTARY pensando", phase="commentary")
    provider = ScriptedProvider(make_response((commentary,), text="", commentary=True))

    result = kit.agent(provider).respond(session, "x")

    assert (result.outcome, result.failure, result.text) == (
        "failed",
        "no_final_answer",
        NOT_COMPLETED_TEXT,
    )
    assert "SENTINEL-COMMENTARY" not in result.text
    assert state(session) == before


def test_ag114_commentary_with_whitespace_final_is_no_final_answer(kit: Kit) -> None:
    provider = ScriptedProvider(
        make_response(
            (message_item("vou ver", phase="commentary"), message_item(" ", phase="final_answer")),
            text=" ",
            commentary=True,
        )
    )
    result = kit.agent(provider).respond(ConversationSession(), "x")
    assert result.failure == "no_final_answer"


def test_ag114_message_without_phase_is_answered(kit: Kit) -> None:
    item = message_item("Resposta sem phase.")
    provider = ScriptedProvider(make_response((item,), text="Resposta sem phase."))
    session = ConversationSession()
    result = kit.agent(provider).respond(session, "x")
    assert (result.outcome, result.text) == ("answered", "Resposta sem phase.")
    assert session.history[0].items == (UserMessage("x"), item)


def test_ag114_commentary_and_final_shows_only_final_and_commits_both(kit: Kit) -> None:
    commentary = message_item("SENTINEL-COMMENTARY", phase="commentary", item_id="m_c")
    final = message_item("Resposta final.", phase="final_answer", item_id="m_f")
    provider = ScriptedProvider(
        make_response((commentary, final), text="Resposta final.", commentary=True)
    )
    session = ConversationSession()

    result = kit.agent(provider).respond(session, "x")

    assert (result.outcome, result.text) == ("answered", "Resposta final.")
    assert session.history[0].items == (UserMessage("x"), commentary, final)


def test_ag114_commentary_with_call_executes_call_and_hides_commentary(kit: Kit) -> None:
    commentary = message_item("SENTINEL-COMMENTARY", phase="commentary", item_id="m_c")
    call = call_item("call_1")
    provider = ScriptedProvider(
        make_response((commentary, call), text="", commentary=True), answer("Pronto.")
    )

    result = kit.agent(provider).respond(ConversationSession(), "x")

    assert (result.outcome, result.text) == ("answered", "Pronto.")
    assert len(kit.spy.calls) == 1
    assert provider.requests[1].items[1:] == (
        commentary,
        call,
        FunctionCallOutput("call_1", ORDER_NOT_ACCESSIBLE),
    )


def test_ag114_text_with_calls_is_never_the_final_answer(kit: Kit) -> None:
    item = message_item("SENTINEL-EARLY-TEXT", item_id="m_early")
    call = call_item("call_1")
    provider = ScriptedProvider(
        make_response((item, call), text="SENTINEL-EARLY-TEXT"), answer("Depois.")
    )
    session = ConversationSession()

    result = kit.agent(provider).respond(session, "x")

    assert (result.outcome, result.text) == ("answered", "Depois.")
    assert len(kit.spy.calls) == 1
    assert session.history[0].items[1:4] == (
        item,
        call,
        FunctionCallOutput("call_1", ORDER_NOT_ACCESSIBLE),
    )


def test_ag114_text_with_calls_at_limit_is_not_shown(kit: Kit) -> None:
    # Text plus calls that cannot run: the turn fails; the text is never shown.
    calls = tuple(call_item(f"call_{i}") for i in range(7))
    provider = ScriptedProvider(
        make_response((message_item("SENTINEL-T"), *calls), text="SENTINEL-T")
    )
    result = kit.agent(provider).respond(ConversationSession(), "x")
    assert (result.outcome, result.failure, result.text) == (
        "failed",
        "tool_limit_reached",
        TOO_MUCH_TEXT,
    )


# --- G15: agent holds no conversation state --------------------------------------------


def test_g15_one_agent_serves_two_sessions_without_crossing_state(kit: Kit) -> None:
    provider = ScriptedProvider(tool_round(call_item("call_s1")), answer("um"), answer("dois"))
    agent = kit.agent(provider)
    first = ConversationSession(customer=CustomerContext(1))
    second = ConversationSession()

    agent.respond(first, "sessão um")
    agent.respond(second, "sessão dois")

    assert provider.requests[2].items == (UserMessage("sessão dois"),)
    assert provider.requests[2].instructions == session_instructions(False)
    assert len(second.history) == 1
    assert len(first.history) == 1
    assert second.history[0].items[0] == UserMessage("sessão dois")


def test_g15_respond_rejects_non_session(kit: Kit) -> None:
    provider = ScriptedProvider()
    with pytest.raises(TypeError):
        kit.agent(provider).respond(object(), "x")  # type: ignore[arg-type]
    assert provider.calls == 0
