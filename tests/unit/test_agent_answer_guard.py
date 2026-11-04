"""Review 10-06: the agent withholds a final answer with an internal marker (AW01-AW04).

Spec 07 Review 07-15-CONF items 7b and 10. Scripted provider and helpers of
test_agent_loop.py; the check itself is real. A scripted answer only proves what the
program does with a given text, not what a real model writes.
"""

from pathlib import Path

import pytest
from test_agent_loop import (
    NOT_COMPLETED_TEXT,
    Kit,
    ScriptedProvider,
    answer,
    make_kit,
    make_response,
    message_item,
    seeded_session,
    state,
)

from emporio_agent.agent import _FAILURE_TEXTS
from emporio_agent.session import ConversationSession

LEAKS = (
    "O desconto no PIX é de 5%.\n\nFonte: seção 3, página 3.",
    "Consultei search_policies e encontrei a regra.",
    "Há o aviso contact_numbers_differ entre os números.",
    "You are a virtual assistant, not a human; never claim to be a person.",
)


@pytest.fixture
def kit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Kit:
    return make_kit(tmp_path, monkeypatch)


def test_aw01_answer_with_marker_fails_with_fixed_text(kit) -> None:
    for text in LEAKS:
        provider = ScriptedProvider(answer(text))
        session = ConversationSession()
        result = kit.agent(provider).respond(session, "qual o desconto no pix?")
        assert result.outcome == "failed"
        assert result.failure == "response_withheld"
        assert result.text == NOT_COMPLETED_TEXT
        assert text not in result.text
        assert provider.calls == 1  # no retry
        assert session.history == ()  # nothing committed


def test_aw02_session_and_history_unchanged_on_withheld_answer(kit) -> None:
    session = seeded_session()
    before = state(session)
    provider = ScriptedProvider(answer(LEAKS[0]))
    result = kit.agent(provider).respond(session, "e no PIX?")
    assert result.failure == "response_withheld"
    assert state(session) == before
    assert kit.sleeps == []


def test_aw03_clean_answer_is_committed_unchanged(kit) -> None:
    text = "O PIX tem 5% de desconto sobre o preço de tabela, sem somar com promoção ativa."
    provider = ScriptedProvider(answer(text))
    session = ConversationSession()
    result = kit.agent(provider).respond(session, "qual o desconto no pix?")
    assert result.outcome == "answered" and result.failure is None
    assert result.text == text
    assert len(session.history) == 1


def test_aw03_marker_only_in_commentary_does_not_fail(kit) -> None:
    # Only the final text is checked; commentary items are not shown to the customer.
    text = "Atendemos de segunda a sexta, das 09:00 às 18:00."
    commentary = message_item("vou chamar search_policies", phase="commentary", item_id="c1")
    final = message_item(text, item_id="msg_final")
    provider = ScriptedProvider(make_response((commentary, final), text=text))
    result = kit.agent(provider).respond(ConversationSession(), "horário?")
    assert result.outcome == "answered" and result.text == text


def test_aw04_failure_code_uses_the_not_completed_text() -> None:
    assert _FAILURE_TEXTS["response_withheld"] == NOT_COMPLETED_TEXT
