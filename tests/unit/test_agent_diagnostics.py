"""Slice 07-C outcomes and diagnostics (spec 07 G11, G14, G15).

Covers AG85-AG88 and AG116, plus the public interface of `emporio_agent.agent`. Helpers come
from `test_agent_loop.py`. Tool results are the real `execute_tool` envelopes with absent
files (never `ok`). Reference extraction for `ok` envelopes is checked on the private pure
helper with literal envelopes shaped like spec 06 results; real `ok` envelopes from real
data are slice D (AG93).
"""

import dataclasses
import json
import os
import subprocess
import sys
import textwrap
import typing
from pathlib import Path

import pytest
from test_agent_loop import (
    CONFIGURATION_TEXT,
    CONFIGURED_MODEL,
    INPUT_EMPTY_TEXT,
    INPUT_INVALID_TEXT,
    INPUT_TOO_LONG_TEXT,
    NOT_COMPLETED_TEXT,
    ORDER_ARGS,
    ORDER_NOT_ACCESSIBLE,
    POLICIES_ARGS,
    REFUSED_TEXT,
    REPORTED_MODEL,
    TEMPORARY_TEXT,
    TOO_MUCH_TEXT,
    Kit,
    ScriptedProvider,
    Ticker,
    answer,
    call_item,
    history_items,
    make_kit,
    make_response,
    message_item,
    reasoning_item,
    seeded_session,
    state,
    tool_round,
)

import emporio_agent.agent as agent_module
from emporio_agent.agent import (
    Agent,
    AgentResources,
    AttemptRecord,
    ToolRunRecord,
    TurnDiagnostics,
    TurnResult,
)
from emporio_agent.provider import FunctionCallOutput, ProviderError, Usage, UserMessage
from emporio_agent.services.orders import CustomerContext
from emporio_agent.session import ConversationSession
from emporio_agent.tools import ToolExecution


@pytest.fixture
def kit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Kit:
    return make_kit(tmp_path, monkeypatch)


# --- Public interface (G11, G14, G15) --------------------------------------------------


def _fields(cls: type) -> list[str]:
    return [f.name for f in dataclasses.fields(cls)]


def test_interface_dataclasses_have_the_contract_fields() -> None:
    assert _fields(AgentResources) == ["database_path", "policy_index_path", "clock"]
    assert _fields(TurnResult) == ["outcome", "text", "failure", "diagnostics"]
    assert _fields(ToolRunRecord) == [
        "index",
        "tool",
        "status",
        "error_code",
        "duration_ms",
        "references",
    ]
    assert _fields(AttemptRecord) == [
        "index",
        "retry",
        "outcome",
        "error_kind",
        "reported_model",
        "request_id",
        "duration_ms",
        "request_bytes",
        "usage",
        "usage_invalid",
    ]
    assert _fields(TurnDiagnostics) == [
        "configured_model",
        "attempts",
        "tool_runs",
        "retries_used",
        "budget_failure",
        "usage_reported_attempts",
        "usage_missing_attempts",
        "usage_invalid_attempts",
        "input_tokens_reported",
        "output_tokens_reported",
        "history_turns_after",
        "history_pruned_turns",
    ]
    for cls in (AgentResources, TurnResult, ToolRunRecord, AttemptRecord, TurnDiagnostics):
        params = cls.__dataclass_params__  # type: ignore[attr-defined]
        assert params.frozen is True
        assert hasattr(cls, "__slots__")


def test_interface_turn_outcome_literal() -> None:
    assert typing.get_args(agent_module.TurnOutcome.__value__) == (
        "answered",
        "refused",
        "failed",
        "rejected_input",
    )


def test_interface_turn_result_is_frozen(kit: Kit) -> None:
    result = kit.agent(ScriptedProvider(answer("ok"))).respond(ConversationSession(), "x")
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.text = "outro"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.diagnostics.retries_used = 5  # type: ignore[misc]


def test_interface_agent_constructor_keywords(tmp_path: Path) -> None:
    resources = AgentResources(tmp_path / "a", tmp_path / "b", clock=lambda: None)  # type: ignore[arg-type,return-value]
    provider = ScriptedProvider()
    Agent(provider, resources)  # defaults: time.sleep, time.monotonic
    with pytest.raises(TypeError):
        Agent(provider, resources, lambda _s: None)  # type: ignore[misc]


@pytest.mark.parametrize(
    ("database", "index", "clock"),
    [
        ("db.sqlite3", Path("i"), lambda: None),
        (Path("d"), "index.json", lambda: None),
        (Path("d"), Path("i"), 5),
    ],
    ids=["database_str", "index_str", "clock_not_callable"],
)
def test_interface_resources_reject_wrong_types(
    database: object, index: object, clock: object
) -> None:
    with pytest.raises(TypeError):
        AgentResources(database, index, clock)  # type: ignore[arg-type]


def test_interface_agent_rejects_wrong_collaborators(tmp_path: Path) -> None:
    resources = AgentResources(tmp_path / "a", tmp_path / "b", clock=lambda: None)  # type: ignore[arg-type,return-value]
    with pytest.raises(TypeError):
        Agent(object(), resources)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        Agent(ScriptedProvider(), object())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        Agent(ScriptedProvider(), resources, sleep=5)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        Agent(ScriptedProvider(), resources, monotonic=None)  # type: ignore[arg-type]


_CHILD = textwrap.dedent(
    """
    import sys
    import emporio_agent.agent
    print("openai" in sys.modules)
    print(any(name.startswith("openai") for name in sys.modules))
    """
)


def test_interface_agent_module_does_not_import_openai() -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("OPENAI_")}
    src = str(Path(__file__).resolve().parents[2] / "src")
    env["PYTHONPATH"] = src
    completed = subprocess.run(
        [sys.executable, "-c", _CHILD],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    assert completed.stdout.split() == ["False", "False"]


def test_interface_agent_source_has_no_openai_reference() -> None:
    source = Path(agent_module.__file__).read_text(encoding="utf-8")
    assert "openai" not in source.lower()
    assert "import logging" not in source
    assert "from logging" not in source
    assert "os.environ" not in source
    assert "open(" not in source


# --- AG85: tool run records ------------------------------------------------------------


def test_ag85_tool_run_records_literal_with_scripted_clock(tmp_path: Path, monkeypatch) -> None:
    kit = make_kit(tmp_path, monkeypatch)
    # Calls: attempt 1 start/end, tool 1 start/end, tool 2 start/end, attempt 2 start/end.
    kit.clock = Ticker([10.0, 10.5, 11.0, 11.25, 11.25, 11.375, 12.0, 12.125])
    provider = ScriptedProvider(
        tool_round(
            call_item("call_1", "get_order", ORDER_ARGS),
            call_item("call_2", "search_policies", POLICIES_ARGS),
        ),
        answer("ok"),
    )

    result = kit.agent(provider).respond(ConversationSession(), "x")

    assert result.diagnostics.tool_runs == (
        ToolRunRecord(
            index=1,
            tool="get_order",
            status="unavailable",
            error_code="order_not_accessible",
            duration_ms=250,
            references=(),
        ),
        ToolRunRecord(
            index=2,
            tool="search_policies",
            status="unavailable",
            error_code="data_unavailable",
            duration_ms=125,
            references=(),
        ),
    )
    assert [a.duration_ms for a in result.diagnostics.attempts] == [500, 125]
    assert kit.clock.calls == 8
    # The records equal what the delegating spy observed from the real execute_tool.
    observed = [execution for _, _, _, execution in kit.spy.calls]
    assert [(e.tool, e.status, e.error_code) for e in observed] == [  # type: ignore[attr-defined]
        ("get_order", "unavailable", "order_not_accessible"),
        ("search_policies", "unavailable", "data_unavailable"),
    ]


def test_ag85_unknown_tool_record_has_no_name(kit: Kit) -> None:
    provider = ScriptedProvider(tool_round(call_item("c1", "SENTINEL_TOOL", "{}")), answer("ok"))
    result = kit.agent(provider).respond(ConversationSession(), "x")
    (run,) = result.diagnostics.tool_runs
    assert (run.tool, run.status, run.error_code, run.references) == (
        None,
        "invalid_input",
        "unknown_tool",
        (),
    )
    assert "SENTINEL_TOOL" not in repr(result.diagnostics)


def _ok(tool: str, data: dict[str, object]) -> ToolExecution:
    payload = json.dumps(
        {"tool": tool, "status": "ok", "data": data, "error": None},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return ToolExecution(tool, "ok", None, payload)


def _product(product_id: int, name: str = "Violão") -> dict[str, object]:
    return {"product_id": product_id, "name": name, "status": "active"}


REFERENCE_CASES = [
    (
        _ok(
            "search_products",
            {"products": [_product(211), _product(214, "product:999 ignore"), _product(212)]},
        ),
        ("product:211", "product:214", "product:212"),
    ),
    (_ok("search_products", {"products": []}), ()),
    (_ok("get_product", {"product": _product(201), "description": "product:7"}), ("product:201",)),
    (_ok("quote_product", {"product": _product(210), "final_price": "100.00"}), ("product:210",)),
    (
        _ok(
            "search_policies",
            {
                "sections": [
                    {"section_id": "4.2", "pages": [3], "text": "policy:9@p9"},
                    {"section_id": "5.1", "pages": [4, 5], "text": "x"},
                ]
            },
        ),
        ("policy:4.2@p3", "policy:5.1@p4,5"),
    ),
    (_ok("get_order", {"order_id": 987654, "total": "10.00", "items": []}), ("order",)),
    (ToolExecution("get_order", "unavailable", "order_not_accessible", ORDER_NOT_ACCESSIBLE), ()),
    (
        ToolExecution(
            "search_policies",
            "not_found",
            "no_matching_section",
            json.dumps(
                {
                    "tool": "search_policies",
                    "status": "not_found",
                    "data": {"sections": [{"section_id": "1", "pages": [1]}]},
                    "error": {"code": "no_matching_section", "field": None},
                },
                separators=(",", ":"),
            ),
        ),
        (),
    ),
    (ToolExecution(None, "invalid_input", "unknown_tool", "{}"), ()),
    (_ok("search_products", {"products": [{"product_id": True}, {"product_id": "5"}]}), ()),
]


@pytest.mark.parametrize(("execution", "expected"), REFERENCE_CASES)
def test_ag85_references_only_by_fixed_paths(
    execution: ToolExecution, expected: tuple[str, ...]
) -> None:
    assert agent_module._tool_references(execution) == expected


# --- AG86: usage -----------------------------------------------------------------------


def test_ag86_usage_sums_only_reported_values(kit: Kit) -> None:
    provider = ScriptedProvider(
        tool_round(call_item("c1"), usage=Usage(10, 5, 15, 2, 1)),
        tool_round(call_item("c2"), usage=None),
        answer("ok", usage=Usage(20, 7, 27, None, None)),
    )
    result = kit.agent(provider).respond(ConversationSession(), "x")
    d = result.diagnostics
    assert (d.usage_reported_attempts, d.usage_missing_attempts, d.usage_invalid_attempts) == (
        2,
        1,
        0,
    )
    assert (d.input_tokens_reported, d.output_tokens_reported) == (30, 12)
    assert [a.usage for a in d.attempts] == [
        Usage(10, 5, 15, 2, 1),
        None,
        Usage(20, 7, 27, None, None),
    ]


def test_ag86_all_usage_missing_gives_none_sums(kit: Kit) -> None:
    provider = ScriptedProvider(tool_round(call_item("c1")), answer("ok"))
    d = kit.agent(provider).respond(ConversationSession(), "x").diagnostics
    assert (d.usage_reported_attempts, d.usage_missing_attempts) == (0, 2)
    assert (d.input_tokens_reported, d.output_tokens_reported) == (None, None)


def test_ag86_explicit_zero_usage_is_zero(kit: Kit) -> None:
    provider = ScriptedProvider(answer("ok", usage=Usage(0, 0, 0, 0, 0)))
    d = kit.agent(provider).respond(ConversationSession(), "x").diagnostics
    assert (d.usage_reported_attempts, d.usage_missing_attempts) == (1, 0)
    assert (d.input_tokens_reported, d.output_tokens_reported) == (0, 0)


def test_ag86_error_attempts_are_not_usage_attempts(kit: Kit) -> None:
    provider = ScriptedProvider(
        ProviderError("timeout"), answer("ok", usage=Usage(3, 4, 7, None, None))
    )
    d = kit.agent(provider).respond(ConversationSession(), "x").diagnostics
    assert (d.usage_reported_attempts, d.usage_missing_attempts, d.usage_invalid_attempts) == (
        1,
        0,
        0,
    )
    assert (d.input_tokens_reported, d.output_tokens_reported) == (3, 4)
    assert d.attempts[0].usage is None
    assert d.attempts[0].usage_invalid is False


def test_ag86_usage_of_failed_turn_is_still_reported(kit: Kit) -> None:
    provider = ScriptedProvider(
        make_response((message_item(""),), usage=Usage(11, 1, 12, None, None))
    )
    result = kit.agent(provider).respond(ConversationSession(), "x")
    assert result.failure == "empty_response"
    assert result.diagnostics.input_tokens_reported == 11


# --- AG87: no sentinel in diagnostics --------------------------------------------------


def test_ag87_answered_turn_diagnostics_hold_no_content(kit: Kit) -> None:
    session = ConversationSession(customer=CustomerContext(424242))
    provider = ScriptedProvider(
        make_response(
            (
                reasoning_item("rs_s", "SENTINEL-REASONING"),
                message_item("SENTINEL-COMMENTARY", phase="commentary"),
                call_item("call_s", "get_order", '{"order_id":987654,"note":"SENTINEL-ARGS"}'),
                call_item("call_t", "SENTINEL-NAME", '{"SENTINEL-KEY":1}'),
            ),
            commentary=True,
            request_id="req_ALLOWED",
        ),
        answer("SENTINEL-MODEL-TEXT"),
    )

    result = kit.agent(provider).respond(session, "SENTINEL-USER 987654 sk-SENTINEL")

    diagnostics = repr(result.diagnostics)
    for sentinel in (
        "SENTINEL",
        "987654",
        "424242",
        "sk-",
        "order_id",
        "Empório",
        "Session:",
    ):
        assert sentinel not in diagnostics
    assert result.text == "SENTINEL-MODEL-TEXT"
    assert "req_ALLOWED" in diagnostics  # spec G3 explicit exception
    assert REPORTED_MODEL in diagnostics
    assert result.diagnostics.configured_model == CONFIGURED_MODEL


def test_ag87_failed_turn_text_and_diagnostics_hold_no_content(kit: Kit) -> None:
    provider = ScriptedProvider(
        tool_round(call_item("call_s", "get_order", '{"order_id":987654}')),
        RuntimeError("SENTINEL-ERROR sk-SENTINEL"),
    )
    result = kit.agent(provider).respond(ConversationSession(), "SENTINEL-USER")
    text = repr(result)
    assert "SENTINEL" not in text
    assert "987654" not in text
    assert result.text == NOT_COMPLETED_TEXT


def test_ag87_provider_error_metadata_only(kit: Kit) -> None:
    error = ProviderError(
        "rate_limited", status_code=429, request_id="req_1", retry_after_seconds=9.0
    )
    result = kit.agent(ScriptedProvider(error)).respond(ConversationSession(), "x")
    (attempt,) = result.diagnostics.attempts
    assert attempt == AttemptRecord(
        index=1,
        retry=False,
        outcome="error",
        error_kind="rate_limited",
        reported_model=None,
        request_id="req_1",
        duration_ms=125,
        request_bytes=6_712,
        usage=None,
        usage_invalid=False,
    )
    assert "limit" not in result.text.lower()


# --- AG88: every failure leaves the session intact -------------------------------------


def _calls(prefix: str, count: int) -> object:
    return tool_round(*(call_item(f"{prefix}{i}") for i in range(count)))


def _steps_for(code: str) -> list[object]:
    singles = [tool_round(call_item(f"s{i}")) for i in range(7)]
    table: dict[str, list[object]] = {
        "provider_timeout": [ProviderError("timeout")] * 2,
        "provider_unavailable": [ProviderError("connection")] * 2,
        "provider_rate_limited": [ProviderError("rate_limited")] * 2,
        "provider_quota": [ProviderError("quota")],
        "provider_authentication": [ProviderError("authentication")],
        "provider_permission": [ProviderError("permission")],
        "provider_model_unavailable": [ProviderError("model_unavailable")],
        "provider_bad_request": [ProviderError("bad_request")],
        "response_invalid": [ProviderError("response_invalid")],
        "protocol_error": [tool_round(call_item("d"), call_item("d"))],
        "response_incomplete": [
            make_response(
                (message_item("p"),), text="p", status="incomplete", incomplete_reason="other"
            )
        ],
        "empty_response": [make_response((message_item(""),))],
        "no_final_answer": [
            make_response((message_item("c", phase="commentary"),), commentary=True)
        ],
        "response_too_long": [answer("a" * 16_001)],
        "provider_failed": [ProviderError("provider_failed")],
        "unexpected": [RuntimeError("boom")],
        "tool_limit_reached": [_calls("x", 7)],
        "model_call_limit_reached": [ProviderError("timeout"), *singles],
        "request_too_large": [
            tool_round(reasoning_item("rs_big", "x" * 300_000), call_item("big"))
        ],
    }
    return table[code]


FAILURE_TEXTS = {
    "provider_timeout": TEMPORARY_TEXT,
    "provider_unavailable": TEMPORARY_TEXT,
    "provider_rate_limited": TEMPORARY_TEXT,
    "provider_quota": CONFIGURATION_TEXT,
    "provider_authentication": CONFIGURATION_TEXT,
    "provider_permission": CONFIGURATION_TEXT,
    "provider_model_unavailable": CONFIGURATION_TEXT,
    "provider_bad_request": CONFIGURATION_TEXT,
    "response_invalid": NOT_COMPLETED_TEXT,
    "protocol_error": NOT_COMPLETED_TEXT,
    "response_incomplete": NOT_COMPLETED_TEXT,
    "empty_response": NOT_COMPLETED_TEXT,
    "no_final_answer": NOT_COMPLETED_TEXT,
    "response_too_long": NOT_COMPLETED_TEXT,
    "provider_failed": NOT_COMPLETED_TEXT,
    "unexpected": NOT_COMPLETED_TEXT,
    "tool_limit_reached": TOO_MUCH_TEXT,
    "model_call_limit_reached": TOO_MUCH_TEXT,
    "request_too_large": TOO_MUCH_TEXT,
}


@pytest.mark.parametrize("code", list(FAILURE_TEXTS))
def test_ag88_failure_leaves_session_intact(kit: Kit, code: str) -> None:
    session = seeded_session()
    before = state(session)
    retained = history_items(session)
    provider = ScriptedProvider(*_steps_for(code), answer("depois"))

    result = kit.agent(provider).respond(session, "pergunta")

    assert (result.outcome, result.failure, result.text) == ("failed", code, FAILURE_TEXTS[code])
    assert state(session) == before
    assert result.diagnostics.history_turns_after == 20
    assert result.diagnostics.history_pruned_turns == 1
    # The next valid message is sent with the same retained history.
    calls_before = provider.calls
    follow_up = kit.agent(provider).respond(session, "de novo")
    assert follow_up.outcome == "answered"
    assert provider.requests[calls_before].items == (*retained, UserMessage("de novo"))


def test_ag88_refused_leaves_session_intact(kit: Kit) -> None:
    session = seeded_session()
    before = state(session)
    provider = ScriptedProvider(make_response((message_item(""),), refusal=True))
    result = kit.agent(provider).respond(session, "x")
    assert (result.outcome, result.failure, result.text) == ("refused", None, REFUSED_TEXT)
    assert state(session) == before


@pytest.mark.parametrize(
    ("text", "code", "message"),
    [
        ("\ud800", "input_invalid_text", INPUT_INVALID_TEXT),
        ("   ", "input_empty", INPUT_EMPTY_TEXT),
        ("a" * 4_001, "input_too_long", INPUT_TOO_LONG_TEXT),
    ],
)
def test_ag88_rejected_input_leaves_session_intact(
    kit: Kit, text: str, code: str, message: str
) -> None:
    session = seeded_session()
    before = state(session)
    provider = ScriptedProvider()
    result = kit.agent(provider).respond(session, text)
    assert (result.outcome, result.failure, result.text) == ("rejected_input", code, message)
    assert state(session) == before
    d = result.diagnostics
    assert (d.attempts, d.tool_runs, d.retries_used, d.budget_failure) == ((), (), 0, None)
    assert (d.history_turns_after, d.history_pruned_turns) == (20, 1)
    assert d.configured_model == CONFIGURED_MODEL


def test_ag88_fixed_texts_are_distinct_and_safe() -> None:
    texts = {
        REFUSED_TEXT,
        INPUT_INVALID_TEXT,
        INPUT_EMPTY_TEXT,
        INPUT_TOO_LONG_TEXT,
        TEMPORARY_TEXT,
        CONFIGURATION_TEXT,
        NOT_COMPLETED_TEXT,
        TOO_MUCH_TEXT,
    }
    assert len(texts) == 8
    for text in texts:
        lowered = text.lower()
        for forbidden in ("atendente", "humano", "chamado", "protocolo", "req_", "http", "erro "):
            assert forbidden not in lowered


@pytest.mark.parametrize("exception", [KeyboardInterrupt, SystemExit])
def test_ag88_interrupt_from_provider_propagates(kit: Kit, exception: type[BaseException]) -> None:
    session = seeded_session()
    before = state(session)
    provider = ScriptedProvider(tool_round(call_item("c1")), exception())
    with pytest.raises(exception):
        kit.agent(provider).respond(session, "x")
    assert state(session) == before


@pytest.mark.parametrize("exception", [KeyboardInterrupt, SystemExit])
def test_ag88_interrupt_from_tool_propagates(kit: Kit, exception: type[BaseException]) -> None:
    session = seeded_session()
    before = state(session)
    kit.spy.raise_after = exception()
    provider = ScriptedProvider(tool_round(call_item("c1")), answer("ok"))
    with pytest.raises(exception):
        kit.agent(provider).respond(session, "x")
    assert len(kit.spy.calls) == 1
    assert provider.calls == 1
    assert state(session) == before


def test_ag88_unexpected_exception_from_tool_layer_is_not_masked(kit: Kit) -> None:
    session = seeded_session()
    before = state(session)
    kit.spy.raise_after = RuntimeError("agent-side defect")
    provider = ScriptedProvider(tool_round(call_item("c1")), answer("ok"))
    with pytest.raises(RuntimeError, match="agent-side defect"):
        kit.agent(provider).respond(session, "x")
    assert state(session) == before


def test_ag88_interrupt_in_retry_sleep_propagates(tmp_path: Path, monkeypatch) -> None:
    kit = make_kit(tmp_path, monkeypatch)
    session = seeded_session()
    before = state(session)

    def interrupted_sleep(_seconds: float) -> None:
        raise KeyboardInterrupt

    agent = Agent(
        ScriptedProvider(ProviderError("timeout"), answer("ok")),
        kit.resources,
        sleep=interrupted_sleep,
        monotonic=kit.clock,
    )
    with pytest.raises(KeyboardInterrupt):
        agent.respond(session, "x")
    assert state(session) == before


# --- AG116: budget and usage diagnostics -----------------------------------------------


def test_ag116_request_bytes_per_attempt_are_literal(kit: Kit) -> None:
    session = seeded_session()  # history 2,978 bytes (test_agent_limits)
    call = call_item("c1")  # raw 123 bytes; output 2 + 108
    assert len(call.raw.encode("utf-8")) == 123
    provider = ScriptedProvider(tool_round(call), ProviderError("timeout"), answer("ok"))

    result = kit.agent(provider).respond(session, "pedido")

    # static 6,711 + history 2,978 + chain: 6 | 6 + 123 + 110
    assert [a.request_bytes for a in result.diagnostics.attempts] == [9_695, 9_928, 9_928]
    assert result.diagnostics.budget_failure is None
    assert result.diagnostics.retries_used == 1


def test_ag116_invalid_usage_counted_and_excluded(kit: Kit) -> None:
    provider = ScriptedProvider(
        tool_round(call_item("c1"), usage=Usage(5, 5, 10, None, None)),
        make_response((message_item("ok"),), text="ok", usage=None, usage_invalid=True),
    )
    d = kit.agent(provider).respond(ConversationSession(), "x").diagnostics
    assert (d.usage_reported_attempts, d.usage_missing_attempts, d.usage_invalid_attempts) == (
        1,
        0,
        1,
    )
    assert (d.input_tokens_reported, d.output_tokens_reported) == (5, 5)
    assert [a.usage_invalid for a in d.attempts] == [False, True]


def test_ag116_only_invalid_usage_gives_none_sums(kit: Kit) -> None:
    provider = ScriptedProvider(make_response((message_item("ok"),), text="ok", usage_invalid=True))
    d = kit.agent(provider).respond(ConversationSession(), "x").diagnostics
    assert (d.usage_reported_attempts, d.usage_invalid_attempts) == (0, 1)
    assert (d.input_tokens_reported, d.output_tokens_reported) == (None, None)


def test_ag116_budget_failure_records_no_attempt_for_unsent_request(kit: Kit) -> None:
    reasoning = reasoning_item("rs_pad", "x" * 261_885)
    provider = ScriptedProvider(tool_round(reasoning, call_item("call_pad")), answer("ok"))
    result = kit.agent(provider).respond(ConversationSession(), "x")
    assert result.failure == "request_too_large"
    assert len(result.diagnostics.attempts) == 1
    assert result.diagnostics.attempts[0].outcome == "response"
    assert len(result.diagnostics.tool_runs) == 1


def test_ag116_answered_diagnostics_full_literal(tmp_path: Path, monkeypatch) -> None:
    kit = make_kit(tmp_path, monkeypatch)
    session = ConversationSession()
    provider = ScriptedProvider(
        make_response(
            (message_item("Oi!"),),
            text="Oi!",
            usage=Usage(100, 20, 120, 50, 0),
            request_id="req_A",
            response_id="resp_A",
        )
    )

    result = kit.agent(provider).respond(session, "oi")

    assert result == TurnResult(
        outcome="answered",
        text="Oi!",
        failure=None,
        diagnostics=TurnDiagnostics(
            configured_model=CONFIGURED_MODEL,
            attempts=(
                AttemptRecord(
                    index=1,
                    retry=False,
                    outcome="response",
                    error_kind=None,
                    reported_model=REPORTED_MODEL,
                    request_id="req_A",
                    duration_ms=125,
                    request_bytes=6_713,
                    usage=Usage(100, 20, 120, 50, 0),
                    usage_invalid=False,
                ),
            ),
            tool_runs=(),
            retries_used=0,
            budget_failure=None,
            usage_reported_attempts=1,
            usage_missing_attempts=0,
            usage_invalid_attempts=0,
            input_tokens_reported=100,
            output_tokens_reported=20,
            history_turns_after=1,
            history_pruned_turns=0,
        ),
    )


def test_ag116_pruning_reported_after_commit(kit: Kit) -> None:
    session = seeded_session()  # 20 turns, pruned 1
    result = kit.agent(ScriptedProvider(answer("ok"))).respond(session, "x")
    assert (result.diagnostics.history_turns_after, result.diagnostics.history_pruned_turns) == (
        20,
        2,
    )
    assert session.pruned_turns == 2
    assert session.history[-1].items == (UserMessage("x"), message_item("ok", item_id="msg_final"))
    assert FunctionCallOutput("call_hist", ORDER_NOT_ACCESSIBLE) in history_items(session)
