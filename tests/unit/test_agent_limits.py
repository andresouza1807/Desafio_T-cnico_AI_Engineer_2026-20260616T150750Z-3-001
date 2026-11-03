"""Slice 07-C retry, loop limits, input checks and request budget (spec 07 G8-G10b).

Covers AG51-AG64, AG66 and AG109-AG113, AG115. Helpers come from `test_agent_loop.py`.
Byte sizes are literals written by hand; `len(s.encode("utf-8"))` is used only over literal
strings of this file to confirm them. No production size helper computes an oracle.
`MAX_REQUEST_BYTES` and `MAX_STATIC_BYTES` are patched only in AG110 and AG115 (plan).
"""

from pathlib import Path

import pytest
from test_agent_loop import (
    CONFIGURATION_TEXT,
    INPUT_EMPTY_TEXT,
    INPUT_INVALID_TEXT,
    INPUT_TOO_LONG_TEXT,
    NOT_COMPLETED_TEXT,
    ORDER_NOT_ACCESSIBLE,
    TEMPORARY_TEXT,
    TOO_MUCH_TEXT,
    Kit,
    ScriptedProvider,
    answer,
    call_item,
    history_items,
    make_kit,
    make_response,
    message_item,
    seeded_session,
    state,
    tool_round,
)

import emporio_agent.agent as agent_module
from emporio_agent.provider import (
    FunctionCall,
    FunctionCallOutput,
    ProviderError,
    ProviderItem,
    UserMessage,
)
from emporio_agent.session import ConversationSession


@pytest.fixture
def kit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Kit:
    return make_kit(tmp_path, monkeypatch)


def calls_round(prefix: str, count: int) -> object:
    return tool_round(*(call_item(f"{prefix}_{i}") for i in range(count)))


# --- Constants -------------------------------------------------------------------------


def test_limit_constants_are_the_approved_literals() -> None:
    assert agent_module.MAX_MODEL_ATTEMPTS == 8
    assert agent_module.MAX_TOOL_EXECUTIONS == 6
    assert agent_module.MAX_RETRIES_PER_MESSAGE == 1
    assert agent_module.RETRY_DELAY_SECONDS == 1.0
    assert agent_module.MAX_RETRY_AFTER_SECONDS == 5.0
    assert agent_module.MAX_USER_MESSAGE_CHARS == 4_000
    assert agent_module.MAX_FINAL_TEXT_CHARS == 16_000
    assert agent_module.MAX_STATIC_BYTES == 16_384
    assert agent_module.MAX_TURN_CHAIN_BYTES == 262_144
    assert agent_module.MAX_REQUEST_BYTES == 671_744


# --- AG51-AG56: retry ------------------------------------------------------------------


def test_ag51_transient_then_success_retries_same_request(kit: Kit) -> None:
    provider = ScriptedProvider(ProviderError("timeout"), answer("ok"))
    session = ConversationSession()

    result = kit.agent(provider).respond(session, "x")

    assert result.outcome == "answered"
    assert provider.calls == 2
    assert provider.requests[0] is provider.requests[1]
    assert provider.requests[0] == provider.requests[1]
    assert [a.retry for a in result.diagnostics.attempts] == [False, True]
    assert [a.outcome for a in result.diagnostics.attempts] == ["error", "response"]
    assert kit.sleeps == [1.0]
    assert result.diagnostics.retries_used == 1
    assert len(session.history) == 1


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ProviderError("timeout"), "provider_timeout"),
        (ProviderError("connection"), "provider_unavailable"),
        (ProviderError("server_error"), "provider_unavailable"),
        (ProviderError("rate_limited"), "provider_rate_limited"),
        (ProviderError("provider_failed", transient=True), "provider_failed"),
    ],
    ids=["timeout", "connection", "server_error", "rate_limited", "provider_failed"],
)
def test_ag52_two_transient_failures_end_the_turn(
    kit: Kit, error: ProviderError, code: str
) -> None:
    session = seeded_session()
    before = state(session)
    provider = ScriptedProvider(error, error)

    result = kit.agent(provider).respond(session, "x")

    expected_text = NOT_COMPLETED_TEXT if code == "provider_failed" else TEMPORARY_TEXT
    assert (result.outcome, result.failure, result.text) == ("failed", code, expected_text)
    assert provider.calls == 2
    assert kit.sleeps == [1.0]
    assert state(session) == before


@pytest.mark.parametrize(
    ("error", "code", "text"),
    [
        (ProviderError("quota"), "provider_quota", CONFIGURATION_TEXT),
        (ProviderError("authentication"), "provider_authentication", CONFIGURATION_TEXT),
        (ProviderError("permission"), "provider_permission", CONFIGURATION_TEXT),
        (ProviderError("model_unavailable"), "provider_model_unavailable", CONFIGURATION_TEXT),
        (ProviderError("bad_request"), "provider_bad_request", CONFIGURATION_TEXT),
        (ProviderError("provider_failed"), "provider_failed", NOT_COMPLETED_TEXT),
        (ProviderError("response_invalid"), "response_invalid", NOT_COMPLETED_TEXT),
        (ProviderError("unexpected"), "unexpected", NOT_COMPLETED_TEXT),
    ],
    ids=lambda value: value if isinstance(value, str) and " " not in value else None,
)
def test_ag53_permanent_error_is_not_retried(
    kit: Kit, error: ProviderError, code: str, text: str
) -> None:
    provider = ScriptedProvider(error)
    result = kit.agent(provider).respond(ConversationSession(), "x")
    assert (result.outcome, result.failure, result.text) == ("failed", code, text)
    assert provider.calls == 1
    assert kit.sleeps == []
    assert result.diagnostics.retries_used == 0


def test_ag53_quota_with_retry_after_is_not_retried(kit: Kit) -> None:
    provider = ScriptedProvider(ProviderError("quota", status_code=429, retry_after_seconds=1.0))
    result = kit.agent(provider).respond(ConversationSession(), "x")
    assert result.failure == "provider_quota"
    assert provider.calls == 1
    assert kit.sleeps == []


@pytest.mark.parametrize(
    ("retry_after", "sleeps"),
    [(5.0, [5.0]), (0.2, [1.0]), (0.0, [1.0]), (None, [1.0]), (3.5, [3.5])],
)
def test_ag54_retry_after_within_limit(
    kit: Kit, retry_after: float | None, sleeps: list[float]
) -> None:
    provider = ScriptedProvider(
        ProviderError("rate_limited", status_code=429, retry_after_seconds=retry_after),
        answer("ok"),
    )
    result = kit.agent(provider).respond(ConversationSession(), "x")
    assert result.outcome == "answered"
    assert provider.calls == 2
    assert kit.sleeps == sleeps


def test_ag54_retry_after_above_limit_is_not_retried(kit: Kit) -> None:
    provider = ScriptedProvider(
        ProviderError("rate_limited", status_code=429, retry_after_seconds=5.01), answer("ok")
    )
    result = kit.agent(provider).respond(ConversationSession(), "x")
    assert (result.outcome, result.failure, result.text) == (
        "failed",
        "provider_rate_limited",
        TEMPORARY_TEXT,
    )
    assert provider.calls == 1
    assert kit.sleeps == []


def test_ag55_retry_after_tool_round_reuses_outputs(kit: Kit) -> None:
    call = call_item("call_1")
    provider = ScriptedProvider(tool_round(call), ProviderError("connection"), answer("ok"))

    result = kit.agent(provider).respond(ConversationSession(), "x")

    assert result.outcome == "answered"
    assert len(kit.spy.calls) == 1
    assert provider.requests[1] is provider.requests[2]
    assert provider.requests[2].items == (
        UserMessage("x"),
        call,
        FunctionCallOutput("call_1", ORDER_NOT_ACCESSIBLE),
    )
    assert [a.retry for a in result.diagnostics.attempts] == [False, False, True]


def test_ag56_second_transient_failure_later_is_not_retried(kit: Kit) -> None:
    session = seeded_session()
    before = state(session)
    provider = ScriptedProvider(
        ProviderError("timeout"),
        tool_round(call_item("call_1")),
        tool_round(call_item("call_2")),
        ProviderError("timeout"),
    )

    result = kit.agent(provider).respond(session, "x")

    assert (result.outcome, result.failure) == ("failed", "provider_timeout")
    assert provider.calls == 4
    assert len(kit.spy.calls) == 2
    assert kit.sleeps == [1.0]
    assert state(session) == before


# --- AG57-AG64: model attempts and tool executions -------------------------------------


def test_ag57_six_rounds_then_text_at_attempt_7(kit: Kit) -> None:
    steps = [tool_round(call_item(f"call_{i}")) for i in range(6)]
    provider = ScriptedProvider(*steps, answer("fim"))

    result = kit.agent(provider).respond(ConversationSession(), "x")

    assert (result.outcome, result.text) == ("answered", "fim")
    assert len(kit.spy.calls) == 6
    assert provider.calls == 7
    assert [r.allow_tool_calls for r in provider.requests] == [True] * 6 + [False]


def test_ag58_call_after_six_executions_fails(kit: Kit) -> None:
    session = seeded_session()
    before = state(session)
    steps = [tool_round(call_item(f"call_{i}")) for i in range(7)]
    provider = ScriptedProvider(*steps)

    result = kit.agent(provider).respond(session, "x")

    assert (result.outcome, result.failure, result.text) == (
        "failed",
        "tool_limit_reached",
        TOO_MUCH_TEXT,
    )
    assert len(kit.spy.calls) == 6
    assert provider.calls == 7
    assert state(session) == before


def test_ag59_batch_exceeding_remaining_budget_runs_nothing(kit: Kit) -> None:
    steps = [tool_round(call_item(f"call_{i}")) for i in range(5)]
    provider = ScriptedProvider(*steps, calls_round("batch", 2))

    result = kit.agent(provider).respond(ConversationSession(), "x")

    assert result.failure == "tool_limit_reached"
    assert len(kit.spy.calls) == 5
    assert provider.calls == 6


def test_ag59_batch_exactly_filling_budget_runs(kit: Kit) -> None:
    steps = [tool_round(call_item(f"call_{i}")) for i in range(4)]
    provider = ScriptedProvider(*steps, calls_round("batch", 2), answer("ok"))
    result = kit.agent(provider).respond(ConversationSession(), "x")
    assert result.outcome == "answered"
    assert len(kit.spy.calls) == 6
    assert provider.requests[-1].allow_tool_calls is False


def test_ag60_seven_calls_in_first_response_run_nothing(kit: Kit) -> None:
    provider = ScriptedProvider(calls_round("c", 7))
    result = kit.agent(provider).respond(ConversationSession(), "x")
    assert (result.outcome, result.failure) == ("failed", "tool_limit_reached")
    assert kit.spy.calls == []
    assert provider.calls == 1


def test_ag61_retry_six_rounds_and_text_at_attempt_8(kit: Kit) -> None:
    steps = [tool_round(call_item(f"call_{i}")) for i in range(6)]
    provider = ScriptedProvider(ProviderError("timeout"), *steps, answer("fim"))

    result = kit.agent(provider).respond(ConversationSession(), "x")

    assert (result.outcome, result.text) == ("answered", "fim")
    assert provider.calls == 8
    assert len(kit.spy.calls) == 6
    assert [r.allow_tool_calls for r in provider.requests] == [True] * 7 + [False]
    assert [a.index for a in result.diagnostics.attempts] == [1, 2, 3, 4, 5, 6, 7, 8]


def test_ag62_call_at_attempt_8_is_not_executed(kit: Kit) -> None:
    session = seeded_session()
    before = state(session)
    steps = [tool_round(call_item(f"call_{i}")) for i in range(7)]
    provider = ScriptedProvider(ProviderError("timeout"), *steps)

    result = kit.agent(provider).respond(session, "x")

    assert (result.outcome, result.failure, result.text) == (
        "failed",
        "model_call_limit_reached",
        TOO_MUCH_TEXT,
    )
    assert provider.calls == 8
    assert provider.overcalled is False
    assert len(kit.spy.calls) == 6
    assert state(session) == before


def test_ag62_allow_false_on_attempt_8_after_transient_at_7(kit: Kit) -> None:
    steps = [tool_round(call_item(f"call_{i}")) for i in range(5)]
    provider = ScriptedProvider(
        *steps, ProviderError("timeout"), tool_round(call_item("call_5")), answer("ok")
    )
    # attempts: 5 rounds, transient at 6, retry at 7 (call 6), final at 8.
    result = kit.agent(provider).respond(ConversationSession(), "x")
    assert result.outcome == "answered"
    assert provider.calls == 8
    assert [r.allow_tool_calls for r in provider.requests] == [True] * 7 + [False]


E = "E"  # transient error
P = "P"  # permanent error
T = "T"  # final text


def _step(token: str | int, index: int) -> object:
    if token == E:
        return ProviderError("timeout")
    if token == P:
        return ProviderError("authentication")
    if token == T:
        return answer("fim")
    return tool_round(*(call_item(f"r{index}_c{i}") for i in range(int(token))))


AG63_ROWS = [
    # script, attempts, executions, failure (None: answered)
    ([T], 1, 0, None),
    ([E, T], 2, 0, None),
    ([E, E], 2, 0, "provider_timeout"),
    ([P], 1, 0, "provider_authentication"),
    ([1, 1, 1, 1, 1, 1, T], 7, 6, None),
    ([1, 1, 1, 1, 1, 1, 1], 7, 6, "tool_limit_reached"),
    ([E, 1, 1, 1, 1, 1, 1, T], 8, 6, None),
    ([E, 1, 1, 1, 1, 1, 1, 1], 8, 6, "model_call_limit_reached"),
    ([2, 2, 2, T], 4, 6, None),
    ([2, 2, 2, 1], 4, 6, "tool_limit_reached"),
    ([2, 2, 1, 2], 4, 5, "tool_limit_reached"),
    ([1, E, 1, E], 4, 2, "provider_timeout"),
    ([7], 1, 0, "tool_limit_reached"),
    ([1, 1, 1, 1, 1, 1, E, T], 8, 6, None),
    ([E, 1, 1, 1, 1, 1, 1, E], 8, 6, "provider_timeout"),
    ([3, E, 3, T], 4, 6, None),
]


@pytest.mark.parametrize(("script", "attempts", "executions", "failure"), AG63_ROWS)
def test_ag63_bounds_hold_for_mixed_scripts(
    kit: Kit, script: list[object], attempts: int, executions: int, failure: str | None
) -> None:
    provider = ScriptedProvider(*(_step(token, i) for i, token in enumerate(script)))  # type: ignore[arg-type]

    result = kit.agent(provider).respond(ConversationSession(), "x")

    assert provider.calls == attempts
    assert provider.calls <= 8
    assert provider.overcalled is False
    assert len(kit.spy.calls) == executions
    assert len(kit.spy.calls) <= 6
    assert result.failure == failure
    assert result.outcome == ("answered" if failure is None else "failed")
    assert len(result.diagnostics.attempts) == attempts
    assert len(result.diagnostics.tool_runs) == executions


def test_ag64_transient_failure_at_attempt_8_is_not_retried(kit: Kit) -> None:
    steps = [tool_round(call_item(f"call_{i}")) for i in range(6)]
    provider = ScriptedProvider(ProviderError("timeout"), *steps, ProviderError("server_error"))

    result = kit.agent(provider).respond(ConversationSession(), "x")

    assert (result.outcome, result.failure) == ("failed", "provider_unavailable")
    assert provider.calls == 8
    assert provider.overcalled is False
    assert kit.sleeps == [1.0]


# --- AG66/AG109: input checks ----------------------------------------------------------


@pytest.mark.parametrize("text", ["a" * 4_000, "🎸" * 4_000, "ç" * 4_000])
def test_ag66_user_text_of_4000_code_points_is_accepted(kit: Kit, text: str) -> None:
    provider = ScriptedProvider(answer("ok"))
    result = kit.agent(provider).respond(ConversationSession(), text)
    assert result.outcome == "answered"
    assert provider.requests[0].items == (UserMessage(text),)


@pytest.mark.parametrize(
    ("text", "code", "message"),
    [
        ("a" * 4_001, "input_too_long", INPUT_TOO_LONG_TEXT),
        ("🎸" * 4_001, "input_too_long", INPUT_TOO_LONG_TEXT),
        ("", "input_empty", INPUT_EMPTY_TEXT),
        ("  \n", "input_empty", INPUT_EMPTY_TEXT),
        (" " * 4_001, "input_empty", INPUT_EMPTY_TEXT),
    ],
    ids=["ascii_4001", "four_byte_4001", "empty", "blank", "blank_long"],
)
def test_ag66_invalid_user_text_is_rejected_before_any_attempt(
    kit: Kit, text: str, code: str, message: str
) -> None:
    session = seeded_session()
    before = state(session)
    provider = ScriptedProvider()

    result = kit.agent(provider).respond(session, text)

    assert (result.outcome, result.failure, result.text) == ("rejected_input", code, message)
    assert provider.calls == 0
    assert state(session) == before
    assert result.diagnostics.attempts == ()


@pytest.mark.parametrize("value", [None, b"oi", 5])
def test_ag66_non_str_input_is_a_caller_defect(kit: Kit, value: object) -> None:
    session = seeded_session()
    before = state(session)
    provider = ScriptedProvider()
    with pytest.raises(TypeError):
        kit.agent(provider).respond(session, value)  # type: ignore[arg-type]
    assert provider.calls == 0
    assert state(session) == before


@pytest.mark.parametrize(
    "text",
    ["oi \ud800", "\ud800", "   \udfff   ", "\ud800" * 4_001, "a" * 4_000 + "\udc80"],
    ids=["mixed", "alone", "blank_around", "too_long", "over_limit_tail"],
)
def test_ag109_lone_surrogate_rejected_first(
    kit: Kit, monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    built: list[str] = []
    real_user_message = agent_module.UserMessage

    def user_message_spy(value: str) -> UserMessage:
        built.append(value)
        return real_user_message(value)

    monkeypatch.setattr(agent_module, "UserMessage", user_message_spy)
    session = seeded_session()
    before = state(session)
    retained = history_items(session)
    provider = ScriptedProvider(answer("ok"))

    result = kit.agent(provider).respond(session, text)

    assert (result.outcome, result.failure, result.text) == (
        "rejected_input",
        "input_invalid_text",
        INPUT_INVALID_TEXT,
    )
    assert provider.calls == 0
    assert built == []
    assert state(session) == before
    assert INPUT_INVALID_TEXT.encode("utf-8")  # the fixed message itself is encodable

    follow_up = kit.agent(provider).respond(session, "oi")
    assert follow_up.outcome == "answered"
    assert provider.requests[0].items == (*retained, UserMessage("oi"))


# --- AG110-AG113, AG115: request budget ------------------------------------------------

# Literal parts of the budget chain (sizes checked against the literals below).
USER_TEXT = "pedido 1?"  # 9 bytes
REASONING_PREFIX = '{"type":"reasoning","id":"rs_pad","summary":[],"encrypted_content":"'  # 68
REASONING_SUFFIX = '"}'  # 2
PAD_CALL_RAW = (
    '{"type":"function_call","id":"fc_pad","call_id":"call_pad","name":"get_order",'
    '"arguments":"{\\"order_id\\":1}","status":"completed"}'
)  # 130
PAD_CALL = FunctionCall("call_pad", "get_order", '{"order_id":1}', PAD_CALL_RAW)
PAD_OUTPUT = FunctionCallOutput("call_pad", ORDER_NOT_ACCESSIBLE)  # 8 + 108 = 116
# chain after one tool round = 9 + (68 + n + 2) + 130 + 116 = 325 + n
STATIC_ANONYMOUS = 6_711  # session_instructions(False) + compact tool schemas, measured once


def padded_reasoning(padding: str) -> ProviderItem:
    return ProviderItem("reasoning", REASONING_PREFIX + padding + REASONING_SUFFIX)


def test_budget_literals_match_their_strings() -> None:
    assert len(USER_TEXT.encode("utf-8")) == 9
    assert len(REASONING_PREFIX.encode("utf-8")) == 68
    assert len(PAD_CALL_RAW.encode("utf-8")) == 130
    assert len(ORDER_NOT_ACCESSIBLE.encode("utf-8")) == 108
    assert len(b"call_pad") == 8


def test_ag110_chain_of_exactly_262144_bytes_is_sent(kit: Kit) -> None:
    reasoning = padded_reasoning("x" * 261_819)
    provider = ScriptedProvider(tool_round(reasoning, PAD_CALL), answer("ok"))

    result = kit.agent(provider).respond(ConversationSession(), USER_TEXT)

    assert provider.calls == 2
    assert provider.requests[1].items == (UserMessage(USER_TEXT), reasoning, PAD_CALL, PAD_OUTPUT)
    assert result.diagnostics.attempts[1].request_bytes == STATIC_ANONYMOUS + 262_144
    # Review 07-04-R1: the 139-byte final message would make the chain 262,283 bytes.
    assert (result.outcome, result.failure, result.diagnostics.budget_failure) == (
        "failed",
        "request_too_large",
        "chain",
    )


def test_ag110_chain_of_262145_bytes_is_not_sent(kit: Kit) -> None:
    session = seeded_session()
    before = state(session)
    history_bytes = session.history_bytes
    reasoning = padded_reasoning("x" * 261_820)
    provider = ScriptedProvider(tool_round(reasoning, PAD_CALL), answer("ok"))

    result = kit.agent(provider).respond(session, USER_TEXT)

    assert (result.outcome, result.failure, result.text) == (
        "failed",
        "request_too_large",
        TOO_MUCH_TEXT,
    )
    assert result.diagnostics.budget_failure == "chain"
    assert provider.calls == 1
    assert len(kit.spy.calls) == 1
    assert len(result.diagnostics.attempts) == 1
    assert state(session) == before
    assert session.history_bytes == history_bytes


def test_ag110_total_budget_exact_is_sent(kit: Kit, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_module, "MAX_REQUEST_BYTES", STATIC_ANONYMOUS + 2)
    provider = ScriptedProvider(answer("ok"))
    result = kit.agent(provider).respond(ConversationSession(), "oi")
    assert result.outcome == "answered"
    assert result.diagnostics.attempts[0].request_bytes == 6_713


def test_ag110_total_budget_minus_one_is_not_sent(
    kit: Kit, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(agent_module, "MAX_REQUEST_BYTES", 6_712)
    session = ConversationSession()
    provider = ScriptedProvider(answer("ok"))

    result = kit.agent(provider).respond(session, "oi")

    assert (result.outcome, result.failure, result.text) == (
        "failed",
        "request_too_large",
        TOO_MUCH_TEXT,
    )
    assert result.diagnostics.budget_failure == "total"
    assert result.diagnostics.attempts == ()
    assert provider.calls == 0
    assert session.history == ()


# Seeded history (test_agent_loop.seeded_session), by hand: turns h2..h9 are 2 + 133 bytes
# (8 x 135), turns h10..h20 are 3 + 134 (11 x 137), turn h21 is 3 + 137 + (9 + 108) + 134
# = 391; total 1,080 + 1,507 + 391 = 2,978.
SEEDED_HISTORY_BYTES = 2_978


@pytest.mark.parametrize(("limit", "sent"), [(9_691, True), (9_690, False)])
def test_ag110_total_budget_with_retained_history(
    kit: Kit, monkeypatch: pytest.MonkeyPatch, limit: int, sent: bool
) -> None:
    session = seeded_session()
    assert session.history_bytes == SEEDED_HISTORY_BYTES
    before = state(session)
    monkeypatch.setattr(agent_module, "MAX_REQUEST_BYTES", limit)  # 6,711 + 2,978 + 2
    provider = ScriptedProvider(answer("ok"))

    result = kit.agent(provider).respond(session, "oi")

    if sent:
        assert result.outcome == "answered"
        assert result.diagnostics.attempts[0].request_bytes == 9_691
    else:
        assert (result.failure, result.diagnostics.budget_failure) == (
            "request_too_large",
            "total",
        )
        assert provider.calls == 0
        assert state(session) == before


def test_ag111_growth_after_tool_output_blocks_next_attempt(kit: Kit) -> None:
    session = seeded_session()
    before = state(session)
    # Before tools: 9 + 70 + n + 130 = 262,094 (limit - 50); after the 116-byte output: 262,210.
    reasoning = padded_reasoning("x" * 261_885)
    provider = ScriptedProvider(tool_round(reasoning, PAD_CALL), answer("ok"))

    result = kit.agent(provider).respond(session, USER_TEXT)

    assert len(kit.spy.calls) == 1
    assert provider.calls == 1
    assert (result.outcome, result.failure) == ("failed", "request_too_large")
    assert result.diagnostics.budget_failure == "chain"
    assert len(result.diagnostics.tool_runs) == 1
    assert state(session) == before


@pytest.mark.parametrize(
    ("cedillas", "ascii", "sent"),
    [(130_909, 1, True), (130_910, 0, False)],
    ids=["262144_bytes", "262145_bytes"],
)
def test_ag112_multibyte_budget_counts_utf8_bytes(
    kit: Kit, cedillas: int, ascii: int, sent: bool
) -> None:
    padding = "ç" * cedillas + "x" * ascii
    reasoning = padded_reasoning(padding)
    chain = (UserMessage(USER_TEXT), reasoning, PAD_CALL, PAD_OUTPUT)
    code_points = 9 + len(reasoning.raw) + len(PAD_CALL_RAW) + 8 + len(ORDER_NOT_ACCESSIBLE)
    assert code_points < 262_144  # the limit is never reached in code points
    provider = ScriptedProvider(tool_round(reasoning, PAD_CALL), answer("ok"))

    result = kit.agent(provider).respond(ConversationSession(), USER_TEXT)

    if sent:
        assert provider.calls == 2
        assert provider.requests[1].items == chain
        # Review 07-04-R1: the 139-byte final message pushes the chain over the limit.
        assert (result.outcome, result.failure) == ("failed", "request_too_large")
        assert result.diagnostics.attempts[1].request_bytes == STATIC_ANONYMOUS + 262_144
    else:
        assert (result.failure, result.diagnostics.budget_failure) == (
            "request_too_large",
            "chain",
        )
        assert provider.calls == 1


def test_ag113_retry_keeps_request_and_size(kit: Kit) -> None:
    reasoning = padded_reasoning("x" * 261_819)
    provider = ScriptedProvider(
        tool_round(reasoning, PAD_CALL), ProviderError("timeout"), answer("ok")
    )

    result = kit.agent(provider).respond(ConversationSession(), USER_TEXT)

    # Review 07-04-R1: the request at the limit is sent and retried unchanged; the final
    # message (139 bytes) then exceeds the chain budget before commit.
    assert (result.outcome, result.failure, result.diagnostics.budget_failure) == (
        "failed",
        "request_too_large",
        "chain",
    )
    assert provider.calls == 3
    assert provider.requests[1] is provider.requests[2]
    sizes = [a.request_bytes for a in result.diagnostics.attempts]
    assert sizes == [6_720, 268_855, 268_855]
    assert len(kit.spy.calls) == 1
    assert kit.sleeps == [1.0]


def test_ag115_large_reasoning_item_blocks_its_calls(kit: Kit) -> None:
    session = seeded_session()
    before = state(session)
    reasoning = padded_reasoning("x" * 299_930)  # raw of 300,000 bytes
    assert len(reasoning.raw.encode("utf-8")) == 300_000
    provider = ScriptedProvider(tool_round(reasoning, PAD_CALL), answer("ok"))

    result = kit.agent(provider).respond(session, USER_TEXT)

    assert kit.spy.calls == []
    assert provider.calls == 1
    assert (result.outcome, result.failure, result.text) == (
        "failed",
        "request_too_large",
        TOO_MUCH_TEXT,
    )
    assert result.diagnostics.budget_failure == "chain"
    assert result.diagnostics.tool_runs == ()
    assert state(session) == before


def test_ag115_chain_exactly_at_limit_after_response_runs_calls(kit: Kit) -> None:
    # V4c boundary: 9 + 70 + n + 130 = 262,144 before tools -> calls run, then the output
    # pushes the chain over the limit before the next attempt.
    reasoning = padded_reasoning("x" * 261_935)
    provider = ScriptedProvider(tool_round(reasoning, PAD_CALL), answer("ok"))
    result = kit.agent(provider).respond(ConversationSession(), USER_TEXT)
    assert len(kit.spy.calls) == 1
    assert (result.failure, result.diagnostics.budget_failure) == ("request_too_large", "chain")
    assert provider.calls == 1


def test_ag115_chain_one_over_limit_after_response_runs_nothing(kit: Kit) -> None:
    reasoning = padded_reasoning("x" * 261_936)
    provider = ScriptedProvider(tool_round(reasoning, PAD_CALL), answer("ok"))
    result = kit.agent(provider).respond(ConversationSession(), USER_TEXT)
    assert kit.spy.calls == []
    assert (result.failure, result.diagnostics.budget_failure) == ("request_too_large", "chain")


def test_ag115_static_budget_exact_is_sent(kit: Kit, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_module, "MAX_STATIC_BYTES", STATIC_ANONYMOUS)
    provider = ScriptedProvider(answer("ok"))
    result = kit.agent(provider).respond(ConversationSession(), "oi")
    assert result.outcome == "answered"
    assert provider.calls == 1


def test_ag115_static_budget_minus_one_is_not_sent(
    kit: Kit, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(agent_module, "MAX_STATIC_BYTES", STATIC_ANONYMOUS - 1)
    session = seeded_session()
    before = state(session)
    provider = ScriptedProvider(answer("ok"))

    result = kit.agent(provider).respond(session, "oi")

    assert (result.outcome, result.failure, result.text) == (
        "failed",
        "request_too_large",
        TOO_MUCH_TEXT,
    )
    assert result.diagnostics.budget_failure == "static"
    assert provider.calls == 0
    assert result.diagnostics.attempts == ()
    assert state(session) == before


def test_ag115_static_budget_linked_session_literal(
    kit: Kit, monkeypatch: pytest.MonkeyPatch
) -> None:
    from emporio_agent.services.orders import CustomerContext

    monkeypatch.setattr(agent_module, "MAX_STATIC_BYTES", 6_690)  # linked line is shorter
    provider = ScriptedProvider(answer("ok"))
    session = ConversationSession(customer=CustomerContext(1))
    result = kit.agent(provider).respond(session, "oi")
    assert result.outcome == "answered"
    assert result.diagnostics.attempts[0].request_bytes == 6_692


def test_budget_never_prunes_history_at_request_time(kit: Kit) -> None:
    session = seeded_session()
    retained = history_items(session)
    provider = ScriptedProvider(tool_round(call_item("call_1")), answer("ok"))
    kit.agent(provider).respond(session, "x")
    for request in provider.requests:
        assert request.items[: len(retained)] == retained


# --- Review 07-04-R1: chain budget also applies to the final answer (G10b) -------------

# Final message item of `answer("ok")`: literal raw, 139 bytes.
FINAL_OK_RAW = (
    '{"type":"message","id":"msg_final","role":"assistant","status":"completed",'
    '"content":[{"type":"output_text","text":"ok","annotations":[]}]}'
)
# chain of a final answer = 9 (user) + (68 + n + 2) (reasoning) + 139 (message) = 218 + n


def test_r1_final_answer_literals_match_their_strings() -> None:
    assert len(FINAL_OK_RAW.encode("utf-8")) == 139
    assert answer("ok").output[-1].raw == FINAL_OK_RAW  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("padding", "sent"),
    [
        ("x" * 261_926, True),  # 262,144 bytes
        ("x" * 261_927, False),  # 262,145 bytes
        ("ç" * 130_963, True),  # 261,926 bytes, 130,963 code points
        ("ç" * 130_963 + "x", False),  # 261,927 bytes
    ],
    ids=["ascii_exact", "ascii_plus_one", "multibyte_exact", "multibyte_plus_one"],
)
def test_r1_final_answer_with_reasoning_respects_chain_budget(
    kit: Kit, padding: str, sent: bool
) -> None:
    session = seeded_session()
    before = state(session)
    reasoning = padded_reasoning(padding)
    expected_bytes = 9 + 68 + len(padding.encode("utf-8")) + 2 + 139
    assert expected_bytes == (262_144 if sent else 262_145)
    if "ç" in padding:  # multibyte rows never reach the limit in code points
        assert 9 + len(reasoning.raw) + 139 < 262_144
    provider = ScriptedProvider(answer("ok", reasoning), answer("nunca"))

    result = kit.agent(provider).respond(session, USER_TEXT)

    assert provider.calls == 1
    assert kit.spy.calls == []
    assert len(result.diagnostics.attempts) == 1
    if sent:
        assert (result.outcome, result.text, result.failure) == ("answered", "ok", None)
        assert result.diagnostics.budget_failure is None
        assert session.history[-1].items == (
            UserMessage(USER_TEXT),
            reasoning,
            ProviderItem("assistant_message", FINAL_OK_RAW),
        )
    else:
        assert (result.outcome, result.failure, result.text) == (
            "failed",
            "request_too_large",
            TOO_MUCH_TEXT,
        )
        assert result.diagnostics.budget_failure == "chain"
        assert state(session) == before
        assert (
            result.diagnostics.history_turns_after,
            result.diagnostics.history_pruned_turns,
        ) == (
            20,
            1,
        )


@pytest.mark.parametrize(("padding", "sent"), [(261_680, True), (261_681, False)])
def test_r1_final_answer_after_tool_round_respects_chain_budget(
    kit: Kit, padding: int, sent: bool
) -> None:
    # Chain: 9 (user) + 130 (call) + 116 (output) + (70 + n) (reasoning) + 139 (message)
    # = 464 + n.
    session = seeded_session()
    before = state(session)
    reasoning = padded_reasoning("x" * padding)
    provider = ScriptedProvider(tool_round(PAD_CALL), answer("ok", reasoning), answer("nunca"))

    result = kit.agent(provider).respond(session, USER_TEXT)

    assert provider.calls == 2
    assert len(kit.spy.calls) == 1
    if sent:
        assert result.outcome == "answered"
    else:
        assert (result.failure, result.diagnostics.budget_failure) == (
            "request_too_large",
            "chain",
        )
        assert state(session) == before


def test_r1_other_final_checks_keep_precedence_over_chain_budget(kit: Kit) -> None:
    # Over the chain budget and empty text: V5 still wins (spec G5 order).
    reasoning = padded_reasoning("x" * 300_000)
    provider = ScriptedProvider(make_response((reasoning, message_item("")), text=""))
    result = kit.agent(provider).respond(ConversationSession(), USER_TEXT)
    assert (result.failure, result.diagnostics.budget_failure) == ("empty_response", None)


def test_r1_refusal_keeps_precedence_over_chain_budget(kit: Kit) -> None:
    reasoning = padded_reasoning("x" * 300_000)
    provider = ScriptedProvider(make_response((reasoning, message_item("")), refusal=True))
    result = kit.agent(provider).respond(ConversationSession(), USER_TEXT)
    assert (result.outcome, result.diagnostics.budget_failure) == ("refused", None)
