"""Slice 07-B session cases AG67-AG80 (docs/plans/07-agent.md, spec 07 G10, G12).

Turns are built from the real provider.py types and the real CustomerContext. Every
expected size is a literal or is computed here from literal strings with
`len(s.encode("utf-8"))`; the production `item_size_bytes` never computes an oracle.
"""

import dataclasses
import json

import pytest

from emporio_agent.provider import FunctionCall, FunctionCallOutput, ProviderItem, UserMessage
from emporio_agent.services.orders import CustomerContext
from emporio_agent.session import (
    MAX_HISTORY_BYTES,
    MAX_HISTORY_TURNS,
    CompletedTurn,
    ConversationSession,
)

C1 = CustomerContext(1)
C2 = CustomerContext(2)


def _b(text: str) -> int:
    """Independent oracle: UTF-8 byte length of a literal string."""
    return len(text.encode("utf-8"))


def _call(call_id: str, arguments: str = '{"order_id":5001}') -> FunctionCall:
    raw = json.dumps(
        {
            "type": "function_call",
            "call_id": call_id,
            "name": "get_order",
            "arguments": arguments,
        },
        separators=(",", ":"),
    )
    return FunctionCall(call_id=call_id, name="get_order", arguments=arguments, raw=raw)


def _output(call_id: str, output: str = '{"status":"ok"}') -> FunctionCallOutput:
    return FunctionCallOutput(call_id=call_id, output=output)


def _answer(text: str = "ok") -> ProviderItem:
    raw = json.dumps(
        {
            "type": "message",
            "role": "assistant",
            "phase": "final_answer",
            "content": [{"type": "output_text", "text": text}],
        },
        separators=(",", ":"),
    )
    return ProviderItem("assistant_message", raw)


def _text_turn(text: str) -> CompletedTurn:
    return CompletedTurn((UserMessage(text),))


def _sized_turn(label: str, total: int) -> CompletedTurn:
    """ASCII turn of exactly `total` bytes: 8-byte user text plus an assistant raw string."""
    text = f"{label:<8}"[:8]
    assert _b(text) == 8
    return CompletedTurn((UserMessage(text), ProviderItem("assistant_message", "x" * (total - 8))))


def _state(session: ConversationSession) -> tuple:
    return (session.history, session.history_bytes, session.customer, session.pruned_turns)


def _user_texts(session: ConversationSession) -> list[str]:
    return [turn.items[0].text for turn in session.history]


def _assert_paired(turn: CompletedTurn) -> None:
    calls = [item.call_id for item in turn.items if isinstance(item, FunctionCall)]
    outputs = [item.call_id for item in turn.items if isinstance(item, FunctionCallOutput)]
    assert sorted(calls) == sorted(outputs)
    assert len(set(calls)) == len(calls)


# --- constants --------------------------------------------------------------------------


def test_history_limits_are_the_approved_literals():
    assert MAX_HISTORY_TURNS == 20
    assert MAX_HISTORY_BYTES == 393216


# --- AG67: new session and isolation ------------------------------------------------------


@pytest.mark.parametrize("customer", [None, C1])
def test_ag67_new_session_is_empty_with_given_customer(customer):
    session = ConversationSession(customer=customer)
    assert session.history == ()
    assert session.history_bytes == 0
    assert session.pruned_turns == 0
    assert session.customer is customer


def test_ag67_default_session_is_anonymous():
    assert ConversationSession().customer is None


def test_ag67_customer_is_keyword_only():
    with pytest.raises(TypeError):
        ConversationSession(C1)  # type: ignore[misc]


def test_ag67_two_sessions_share_no_state():
    first = ConversationSession(customer=C1)
    second = ConversationSession(customer=C1)
    first.commit(_text_turn("hello"))
    assert len(first.history) == 1
    assert first.history_bytes == 5
    assert second.history == ()
    assert second.history_bytes == 0
    assert second.pruned_turns == 0


# --- AG68: no setter, constructor type check ---------------------------------------------


def test_ag68_customer_has_no_setter():
    session = ConversationSession(customer=C1)
    with pytest.raises(AttributeError):
        session.customer = C2  # type: ignore[misc]
    assert session.customer is C1


@pytest.mark.parametrize("attribute", ["history", "history_bytes", "pruned_turns"])
def test_ag68_read_only_views(attribute):
    session = ConversationSession()
    with pytest.raises(AttributeError):
        setattr(session, attribute, 1)


@pytest.mark.parametrize("bad", [1, "1", {"customer_id": 1}, True, 1.0])
def test_ag68_constructor_rejects_non_customer_context(bad):
    with pytest.raises(TypeError):
        ConversationSession(customer=bad)


# --- AG69: reset ---------------------------------------------------------------------------


def _pruned_session(customer: CustomerContext | None) -> ConversationSession:
    session = ConversationSession(customer=customer)
    for index in range(1, 22):
        session.commit(_text_turn(f"t{index}"))
    assert session.pruned_turns == 1
    return session


def test_ag69_reset_without_customer_returns_to_anonymous_empty_session():
    session = _pruned_session(C1)
    session.reset()
    assert _state(session) == ((), 0, None, 0)


def test_ag69_reset_with_new_customer_clears_everything_first():
    session = _pruned_session(C1)
    session.reset(customer=C2)
    assert session.history == ()
    assert session.history_bytes == 0
    assert session.pruned_turns == 0
    assert session.customer is C2


def test_ag69_reset_with_same_customer_still_clears_history():
    session = _pruned_session(C1)
    session.reset(customer=C1)
    assert _state(session) == ((), 0, C1, 0)


def test_ag69_session_works_after_reset():
    session = _pruned_session(C1)
    session.reset(customer=C2)
    session.commit(_text_turn("again"))
    assert _user_texts(session) == ["again"]
    assert session.history_bytes == 5


@pytest.mark.parametrize("bad", [2, "2", {"customer_id": 2}, False])
def test_ag69_invalid_reset_changes_nothing(bad):
    session = _pruned_session(C1)
    before = _state(session)
    with pytest.raises(TypeError):
        session.reset(customer=bad)
    assert _state(session) == before


def test_ag69_reset_is_keyword_only():
    session = ConversationSession()
    with pytest.raises(TypeError):
        session.reset(C2)  # type: ignore[misc]


# --- AG70: invalid turns are rejected atomically -----------------------------------------


def _baseline_session() -> ConversationSession:
    """Session with retained history, a pruning count and a retained call `h-1`."""
    session = ConversationSession(customer=C1)
    for index in range(1, 21):
        session.commit(_text_turn(f"t{index}"))
    session.commit(CompletedTurn((UserMessage("t21"), _call("h-1"), _output("h-1"), _answer())))
    assert session.pruned_turns == 1
    assert len(session.history) == 20
    return session


_INVALID_TURNS = {
    "empty": (),
    "first_not_user_provider_item": (_answer(), UserMessage("q")),
    "first_not_user_call": (_call("c1"), _output("c1"), UserMessage("q")),
    "first_not_user_output": (_output("c1"),),
    "call_without_output": (UserMessage("q"), _call("c1"), _answer()),
    "output_without_call": (UserMessage("q"), _output("c1"), _answer()),
    "output_before_call": (UserMessage("q"), _output("c1"), _call("c1"), _answer()),
    "output_for_other_call": (UserMessage("q"), _call("c1"), _output("c2")),
    "two_outputs_for_one_call": (UserMessage("q"), _call("c1"), _output("c1"), _output("c1")),
    "duplicate_call_id_in_turn": (
        UserMessage("q"),
        _call("c1"),
        _output("c1"),
        _call("c1"),
        _output("c1"),
    ),
    "duplicate_call_id_before_outputs": (
        UserMessage("q"),
        _call("c1"),
        _call("c1"),
        _output("c1"),
        _output("c1"),
    ),
    "call_id_in_retained_history": (UserMessage("q"), _call("h-1"), _output("h-1")),
    "second_call_unpaired": (UserMessage("q"), _call("c1"), _output("c1"), _call("c2")),
}


@pytest.mark.parametrize("items", list(_INVALID_TURNS.values()), ids=list(_INVALID_TURNS))
def test_ag70_invalid_turn_raises_and_leaves_session_unchanged(items):
    session = _baseline_session()
    before = _state(session)
    with pytest.raises(ValueError):
        session.commit(CompletedTurn(items))
    assert _state(session) == before
    # The next valid turn commits on top of the same retained history.
    session.commit(_text_turn("next"))
    assert _user_texts(session) == [f"t{i}" for i in range(3, 22)] + ["next"]
    assert session.pruned_turns == 2


def test_ag70_invalid_turn_on_empty_session_leaves_it_empty():
    session = ConversationSession()
    with pytest.raises(ValueError):
        session.commit(CompletedTurn((UserMessage("q"), _call("c1"))))
    assert _state(session) == ((), 0, None, 0)


@pytest.mark.parametrize("bad", [(UserMessage("q"),), [UserMessage("q")], None, "q"])
def test_ag70_commit_requires_completed_turn(bad):
    session = _baseline_session()
    before = _state(session)
    with pytest.raises(TypeError):
        session.commit(bad)  # type: ignore[arg-type]
    assert _state(session) == before


@pytest.mark.parametrize(
    "items",
    [[UserMessage("q")], (UserMessage("q"), "text"), (UserMessage("q"), {"type": "message"})],
)
def test_ag70_completed_turn_requires_tuple_of_items(items):
    with pytest.raises(TypeError):
        CompletedTurn(items)


def test_ag70_call_id_of_pruned_turn_is_no_longer_reserved():
    session = ConversationSession()
    session.commit(CompletedTurn((UserMessage("t1"), _call("c1"), _output("c1"))))
    for index in range(2, 22):
        session.commit(_text_turn(f"t{index}"))
    assert session.pruned_turns == 1
    session.commit(CompletedTurn((UserMessage("t22"), _call("c1"), _output("c1"))))
    assert session.history[-1].items[1].call_id == "c1"


# --- AG71: valid turn with two calls ------------------------------------------------------


def test_ag71_two_calls_commit_in_given_order_with_opaque_items_unchanged():
    reasoning_raw = '{"type":"reasoning","id":"rs_1","summary":[],"encrypted_content":"gAAAA+/=="}'
    message_raw = (
        '{"type":"message","role":"assistant","phase":"commentary",'
        '"content":[{"type":"output_text","text":"Vou consultar."}]}'
    )
    items = (
        UserMessage("Quanto custa o violão 12 e o pedido 5001?"),
        ProviderItem("reasoning", reasoning_raw),
        ProviderItem("assistant_message", message_raw),
        _call("call_a", '{"product_id":12}'),
        _call("call_b", '{"order_id":5001}'),
        _output("call_b", '{"status":"ok","data":{"order":1}}'),
        _output("call_a", '{"status":"ok","data":{"product":12}}'),
        _answer("Pronto."),
    )
    turn = CompletedTurn(items)
    session = ConversationSession(customer=C1)
    session.commit(turn)

    assert session.history == (CompletedTurn(items),)
    assert session.history[0].items == items
    assert all(a is b for a, b in zip(session.history[0].items, items, strict=True))
    assert session.history[0].items[1].raw == reasoning_raw
    assert session.history[0].items[2].raw == message_raw
    _assert_paired(session.history[0])

    expected = (
        _b("Quanto custa o violão 12 e o pedido 5001?")
        + _b(reasoning_raw)
        + _b(message_raw)
        + _b(items[3].raw)
        + _b(items[4].raw)
        + _b("call_b")
        + _b('{"status":"ok","data":{"order":1}}')
        + _b("call_a")
        + _b('{"status":"ok","data":{"product":12}}')
        + _b(items[7].raw)
    )
    assert session.history_bytes == expected
    assert session.pruned_turns == 0


def test_ag71_history_bytes_literal_for_small_turn():
    session = ConversationSession()
    session.commit(
        CompletedTurn(
            (
                UserMessage("abc"),
                FunctionCall("c1", "get_order", "{}", "0123456789"),
                FunctionCallOutput("c1", "12345"),
                ProviderItem("assistant_message", "xyz"),
            )
        )
    )
    # 3 (text) + 10 (call raw) + 2 (output call_id) + 5 (output) + 3 (message raw)
    assert session.history_bytes == 23


# --- AG72: turn count limit ---------------------------------------------------------------


def test_ag72_twenty_turns_kept_twenty_first_drops_exactly_the_first():
    session = ConversationSession()
    for index in range(1, 21):
        session.commit(_text_turn(f"t{index}"))
    assert len(session.history) == 20
    assert session.pruned_turns == 0
    assert _user_texts(session) == [f"t{i}" for i in range(1, 21)]
    # t1..t9: 2 bytes each; t10..t20: 3 bytes each.
    assert session.history_bytes == 9 * 2 + 11 * 3

    session.commit(_text_turn("t21"))
    assert len(session.history) == 20
    assert session.pruned_turns == 1
    assert _user_texts(session) == [f"t{i}" for i in range(2, 22)]
    assert session.history_bytes == 8 * 2 + 12 * 3


def test_ag72_pruning_count_accumulates():
    session = ConversationSession()
    for index in range(1, 26):
        session.commit(_text_turn(f"t{index}"))
    assert session.pruned_turns == 5
    assert _user_texts(session) == [f"t{i}" for i in range(6, 26)]


# --- AG73: byte budget, exact and +1 ------------------------------------------------------


def test_ag73_history_of_exactly_the_byte_budget_is_kept():
    session = ConversationSession()
    turns = [_sized_turn("a", 131072), _sized_turn("b", 131072), _sized_turn("c", 131072)]
    for turn in turns:
        session.commit(turn)
    assert session.history_bytes == 393216
    assert session.pruned_turns == 0
    assert session.history == tuple(turns)


def test_ag73_one_more_byte_drops_the_oldest_whole_turn():
    session = ConversationSession()
    turns = [_sized_turn("a", 131072), _sized_turn("b", 131072), _sized_turn("c", 131073)]
    for turn in turns:
        session.commit(turn)
    assert session.pruned_turns == 1
    assert session.history == (turns[1], turns[2])
    assert session.history_bytes == 262145


def test_ag73_byte_budget_counts_tool_output_call_id():
    """Output size is call_id plus output; the exact budget includes the call_id bytes."""
    call = FunctionCall("k1", "get_order", "{}", "r" * 100)
    first = CompletedTurn((UserMessage("12345678"), call, FunctionCallOutput("k1", "o" * 1000)))
    # first: 8 + 100 + 2 + 1000 = 1110 bytes; second fills the rest exactly.
    second = _sized_turn("b", 393216 - 1110)
    session = ConversationSession()
    session.commit(first)
    session.commit(second)
    assert session.history_bytes == 393216
    assert session.pruned_turns == 0

    over = ConversationSession()
    over.commit(first)
    over.commit(_sized_turn("b", 393216 - 1110 + 1))
    assert over.pruned_turns == 1
    assert over.history_bytes == 393216 - 1110 + 1


def test_ag73_pruning_drops_as_many_old_turns_as_needed():
    session = ConversationSession()
    for label in ("a", "b", "c"):
        session.commit(_sized_turn(label, 100000))
    session.commit(_sized_turn("d", 293217))
    # 3 * 100000 + 293217 > budget; dropping a and b leaves 393217 > budget; drop c too.
    assert session.pruned_turns == 3
    assert _user_texts(session) == ["d       "]
    assert session.history_bytes == 293217


# --- AG74: single turn above the budget -------------------------------------------------


def test_ag74_single_turn_above_budget_is_not_retained():
    session = ConversationSession()
    session.commit(_sized_turn("big", 393217))
    assert session.history == ()
    assert session.history_bytes == 0
    assert session.pruned_turns == 1


def test_ag74_single_turn_at_budget_is_retained():
    session = ConversationSession()
    session.commit(_sized_turn("big", 393216))
    assert len(session.history) == 1
    assert session.history_bytes == 393216


def test_ag74_oversized_turn_empties_existing_history_and_counts_all():
    session = ConversationSession(customer=C1)
    session.commit(_text_turn("t1"))
    session.commit(_text_turn("t2"))
    session.commit(_sized_turn("big", 400000))
    assert session.history == ()
    assert session.history_bytes == 0
    assert session.pruned_turns == 3
    assert session.customer is C1
    session.commit(_text_turn("after"))
    assert _user_texts(session) == ["after"]


# --- AG75: whole turns only ---------------------------------------------------------------


def test_ag75_retained_turns_are_whole_committed_turns():
    session = ConversationSession()
    committed = []
    for index in range(1, 31):
        call_id = f"call-{index}"
        turn = CompletedTurn(
            (
                UserMessage(f"q{index}"),
                ProviderItem("reasoning", "r" * 20000),
                _call(call_id),
                _output(call_id, "o" * 10000),
                _answer(f"a{index}"),
            )
        )
        committed.append(turn)
        session.commit(turn)
        for retained in session.history:
            assert retained in committed
            assert len(retained.items) == 5
            _assert_paired(retained)
    retained = session.history
    assert retained == tuple(committed[-len(retained) :])
    assert session.pruned_turns == 30 - len(retained)
    assert session.history_bytes <= 393216


def test_ag75_byte_pruning_never_splits_a_turn():
    session = ConversationSession()
    big = CompletedTurn(
        (UserMessage("12345678"), _call("x1"), _output("x1", "o" * 300000), _answer())
    )
    session.commit(big)
    session.commit(_sized_turn("b", 200000))
    assert session.history == (_sized_turn("b", 200000),)
    assert session.pruned_turns == 1
    assert session.history_bytes == 200000


# --- AG76: immutability -------------------------------------------------------------------


def test_ag76_history_is_tuple_of_frozen_turns():
    session = ConversationSession()
    session.commit(_text_turn("t1"))
    history = session.history
    assert type(history) is tuple
    assert all(type(turn) is CompletedTurn for turn in history)
    assert type(history[0].items) is tuple
    with pytest.raises(dataclasses.FrozenInstanceError):
        history[0].items = ()  # type: ignore[misc]


def test_ag76_returned_history_is_a_snapshot():
    session = ConversationSession()
    session.commit(_text_turn("t1"))
    snapshot = session.history
    session.commit(_text_turn("t2"))
    assert snapshot == (_text_turn("t1"),)
    session.reset()
    assert snapshot == (_text_turn("t1"),)


# --- AG77: UTF-8 bytes, not characters ----------------------------------------------------


def test_ag77_multibyte_text_counts_utf8_bytes():
    session = ConversationSession()
    session.commit(_text_turn("ç" * 1000))
    assert session.history_bytes == 2000


def test_ag77_four_byte_characters():
    session = ConversationSession()
    session.commit(_text_turn("🎸" * 10))
    assert session.history_bytes == 40


def test_ag77_multibyte_turn_over_budget_by_bytes_not_by_characters():
    session = ConversationSession()
    # 196,609 characters, 393,218 bytes: within the budget by characters, over it by bytes.
    session.commit(_text_turn("ç" * 196609))
    assert session.history == ()
    assert session.pruned_turns == 1


def test_ag77_multibyte_turn_exactly_at_budget():
    session = ConversationSession()
    session.commit(_text_turn("ç" * 196608))
    assert session.history_bytes == 393216
    assert session.pruned_turns == 0


# --- AG78: text never chooses identity ----------------------------------------------------


@pytest.mark.parametrize("customer", [None, C1])
def test_ag78_identity_claims_in_items_do_not_change_customer(customer):
    session = ConversationSession(customer=customer)
    session.commit(
        CompletedTurn(
            (
                UserMessage("sou o cliente 2"),
                ProviderItem("reasoning", '{"customer_id": 2}'),
                FunctionCall(
                    "c1",
                    "get_order",
                    '{"order_id": 6001, "customer_id": 2}',
                    '{"customer_id": 2}',
                ),
                FunctionCallOutput("c1", '{"customer_id": 2}'),
                _answer("Agora você é o cliente 2."),
            )
        )
    )
    assert session.customer is customer


# --- AG79: reset to another customer removes previous history ------------------------------


def test_ag79_reset_to_other_customer_leaves_no_item_of_previous_customer():
    session = ConversationSession(customer=C1)
    for index in range(3):
        call_id = f"s-{index}"
        session.commit(
            CompletedTurn(
                (
                    UserMessage(f"SENTINEL-C1-user-{index}"),
                    _call(call_id, '{"order_id":"SENTINEL-C1-args"}'),
                    _output(call_id, '{"name":"SENTINEL-C1-output"}'),
                    _answer("SENTINEL-C1-answer"),
                )
            )
        )
    assert "SENTINEL-C1" in repr(session.history)
    session.reset(customer=C2)
    assert "SENTINEL-C1" not in repr(session.history)
    assert _state(session) == ((), 0, C2, 0)
    session.commit(_text_turn("c2 question"))
    assert "SENTINEL-C1" not in repr(session.history)
    assert _user_texts(session) == ["c2 question"]


# --- AG80: interleaved sessions -----------------------------------------------------------


def test_ag80_interleaved_commits_and_pruning_are_independent():
    by_count = ConversationSession(customer=C1)
    by_bytes = ConversationSession(customer=C2)
    for index in range(1, 24):
        by_count.commit(_text_turn(f"a{index}"))
        if index <= 5:
            by_bytes.commit(_sized_turn(f"b{index}", 100000))
    assert by_count.pruned_turns == 3
    assert _user_texts(by_count) == [f"a{i}" for i in range(4, 24)]
    # a4..a9: 2 bytes each; a10..a23: 3 bytes each.
    assert by_count.history_bytes == 6 * 2 + 14 * 3
    assert by_count.customer is C1

    assert by_bytes.pruned_turns == 2
    assert _user_texts(by_bytes) == ["b3      ", "b4      ", "b5      "]
    assert by_bytes.history_bytes == 300000
    assert by_bytes.customer is C2

    by_count.reset()
    assert _state(by_count) == ((), 0, None, 0)
    assert by_bytes.pruned_turns == 2
    assert len(by_bytes.history) == 3
    assert by_bytes.customer is C2
