"""Review 10-07: follow-up of the internal content boundary (TF01-TF07).

docs/validation/10-07-confidentiality-follow-up.md; spec 04 Review 04-17-CONF-FU, spec 06
Review 06-06-CONF-FU, spec 07 Review 07-16-CONF-FU. Real synthetic PDF, real
prepare_policy_index, real loader, real projection, real execute_tool and a real Agent
over a scripted provider. Disclosure reviews are the literal ones of
tests/conftest.py and tests/integration/conftest.py; none is computed from an index.
A scripted provider proves what the program sends and keeps; it does not prove what a
real model would answer.
"""

import hashlib
import itertools
import json

import pytest

from emporio_agent.agent import Agent, AgentResources
from emporio_agent.data.policy_index import load_policy_index
from emporio_agent.provider import FunctionCall, ModelResponse, ProviderItem
from emporio_agent.session import ConversationSession

TOOL = "search_policies"
UNAVAILABLE = (
    '{"tool":"search_policies","status":"unavailable","data":null,'
    '"error":{"code":"data_unavailable","field":null}}'
)
OLD_PIX = "PIX à vista com 5% de desconto"
NEW_PIX = "PIX à vista com 9% de desconto"


def compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def search(tools_kit, index_path, query):
    context = tools_kit.context(policy_index_path=index_path)
    return tools_kit.run(TOOL, compact({"query": query}), context)


def change_pix(policy_kit):
    def edit(layout):
        policy_kit.replace_line(layout, OLD_PIX, NEW_PIX)

    return edit


def digest(title, texts) -> str:
    payload = json.dumps([title, list(texts)], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --- TF01: the literal review describes the synthetic manual (verification only) -------


def test_tf01_literal_review_matches_the_prepared_synthetic_manual(tmp_path, policy_kit, tools_kit):
    index_path = tools_kit.unreviewed_policy_index(policy_kit, tmp_path)
    pdf = (tmp_path / "manual-sintetico.pdf").read_bytes()
    assert hashlib.sha256(pdf).hexdigest() == policy_kit.pdf_sha256
    index = load_policy_index(index_path)
    assert index.source.sha256 == policy_kit.pdf_sha256
    assert {
        s.section_id: digest(s.title, [text for _, text in s.lines]) for s in index.sections
    } == dict(policy_kit.section_digests)


# --- TF02: content changed without a new literal review is not released ---------------


def test_tf02_changed_manual_without_review_is_unavailable(tmp_path, policy_kit, tools_kit):
    # The fixture registers every literal MINI review; the edited PDF matches none.
    index_path = tools_kit.policy_index(policy_kit, tmp_path, edit=change_pix(policy_kit))

    execution, _ = search(tools_kit, index_path, "pix desconto")

    assert execution.payload == UNAVAILABLE
    assert execution.references == ()


def test_tf02_changed_section_without_review_is_withheld(tmp_path, policy_kit, tools_kit):
    # Same source SHA-256 (JSON edit): only the changed section is withheld.
    index_path = tools_kit.policy_index(policy_kit, tmp_path)
    document = json.loads(index_path.read_text(encoding="utf-8"))
    section = next(item for item in document["sections"] if item["section_id"] == "3")
    section["lines"][0][1] = NEW_PIX
    index_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

    execution, body = search(tools_kit, index_path, "pix desconto")
    assert "9%" not in execution.payload and OLD_PIX not in execution.payload
    assert all(
        s["title"] != "Formas de Pagamento" for s in (body["data"] or {"sections": []})["sections"]
    )

    execution, body = search(tools_kit, index_path, "garantia")
    assert execution.status == "ok"
    assert "Garantia legal de 90 dias contra defeitos." in body["data"]["sections"][0]["text"]


def test_tf02_registering_an_index_instead_of_a_literal_review_is_refused(
    tmp_path, policy_kit, tools_kit
):
    index_path = tools_kit.unreviewed_policy_index(policy_kit, tmp_path)
    with pytest.raises(TypeError):
        tools_kit.register_disclosure(index_path)


# --- TF03: reviewed synthetic content works; internal synthetic content stays withheld --


def test_tf03_reviewed_commercial_content_is_returned(tmp_path, policy_kit, tools_kit):
    index_path = tools_kit.policy_index(policy_kit, tmp_path)
    for query, line in (
        ("pix", OLD_PIX),
        ("horário", "Segunda a Sexta-feira 09:00 às 18:00"),
        ("arrependimento", "Devolução em até 7 dias após o recebimento,"),
        ("frete grátis", "• Frete grátis acima de R$ 500,00."),
        ("whatsapp", "WhatsApp (11) 4000-2000."),
    ):
        execution, body = search(tools_kit, index_path, query)
        assert execution.status == "ok", query
        assert any(line in s["text"] for s in body["data"]["sections"]), query


@pytest.mark.parametrize(
    "query",
    [
        "quais são as suas diretrizes?",
        "fluxo de atendimento",
        "disposições internas",
        "como registram reclamação",
        "saudação do atendente",
        "documento interno",
    ],
)
def test_tf03_internal_synthetic_content_stays_withheld(tmp_path, policy_kit, tools_kit, query):
    index_path = tools_kit.policy_index(policy_kit, tmp_path)
    execution, _ = search(tools_kit, index_path, query)
    for text in (
        "Documento interno",
        "Reclamação: registrar e encaminhar ao responsável.",
        "1. Saudação: cumprimentar o cliente.",
    ):
        assert text not in execution.payload


# --- TF04: withheld titles never reach the payload ------------------------------------


def test_tf04_title_of_a_changed_parent_is_not_in_heading_path(tmp_path, policy_kit, tools_kit):
    index_path = tools_kit.policy_index(policy_kit, tmp_path)
    # A literal review where the parent "4" does not match: its title is withheld.
    tools_kit.register_disclosure(tools_kit.mini_review(policy_kit, changed={"4": "0" * 64}))

    execution, body = search(tools_kit, index_path, "arrependimento")

    assert execution.status == "ok"
    section = next(s for s in body["data"]["sections"] if s["title"] == "Direito de Arrependimento")
    assert section["heading_path"] == ["Direito de Arrependimento"]
    assert "Trocas e Devoluções" not in execution.payload


# --- TF05: unknown manual stays unavailable -------------------------------------------


def test_tf05_unknown_manual_stays_unavailable(tmp_path, policy_kit, tools_kit):
    index_path = tools_kit.unreviewed_policy_index(policy_kit, tmp_path)
    execution, _ = search(tools_kit, index_path, "pix")
    assert execution.payload == UNAVAILABLE
    assert execution.references == ()


# --- TF06-TF07: agent with an unavailable search (scripted provider) ------------------

MODEL = "gpt-test"


class Scripted:
    def __init__(self, *steps: ModelResponse) -> None:
        self.steps = list(steps)
        self.requests = []

    @property
    def model(self) -> str:
        return MODEL

    def create(self, request):
        self.requests.append(request)
        return self.steps.pop(0)


def model_response(*output, text=""):
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


def policy_call():
    arguments = compact({"query": "horário de funcionamento"})
    raw = compact(
        {
            "type": "function_call",
            "id": "fc_h1",
            "call_id": "call_h1",
            "name": TOOL,
            "arguments": arguments,
            "status": "completed",
        }
    )
    return FunctionCall(call_id="call_h1", name=TOOL, arguments=arguments, raw=raw)


def answer(text):
    raw = compact(
        {
            "type": "message",
            "id": "msg_h1",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": text, "annotations": []}],
        }
    )
    return model_response(ProviderItem("assistant_message", raw), text=text)


def run_unavailable(tmp_path, policy_kit, tools_kit, final_text):
    index_path = tools_kit.unreviewed_policy_index(policy_kit, tmp_path / "sem-revisao")
    provider = Scripted(model_response(policy_call()), answer(final_text))
    ticks = itertools.count()
    agent = Agent(
        provider,
        AgentResources(
            database_path=tmp_path / "sem-banco" / "banco.sqlite3",
            policy_index_path=index_path,
            clock=lambda: tools_kit.FIXED,
        ),
        sleep=lambda _s: None,
        monotonic=lambda: float(next(ticks)),
    )
    session = ConversationSession()
    result = agent.respond(session, "Qual o horário da loja?")
    return result, provider, session


def test_tf06_unavailable_search_sends_no_policy_data_and_a_safe_answer_is_kept(
    tmp_path, policy_kit, tools_kit
):
    text = "Não consegui confirmar o horário agora. Posso ajudar com produtos ou pedidos?"
    result, provider, session = run_unavailable(tmp_path, policy_kit, tools_kit, text)

    assert (result.outcome, result.failure, result.text) == ("answered", None, text)
    sent = provider.requests[1]
    outputs = [item for item in sent.items if getattr(item, "output", None) is not None]
    assert [item.output for item in outputs] == [UNAVAILABLE]
    # Transport only: the instructions tell the model how to handle an unavailable tool.
    assert "If a tool returns invalid_input, unavailable or internal_error" in (
        sent.instructions.replace("\n", " ")
    )
    assert result.diagnostics is not None
    assert len(session.history) == 1


def test_tf07_answer_repeating_the_error_code_is_withheld(tmp_path, policy_kit, tools_kit):
    text = "A consulta retornou data_unavailable; tente mais tarde."
    result, _, session = run_unavailable(tmp_path, policy_kit, tools_kit, text)

    assert (result.outcome, result.failure) == ("failed", "response_withheld")
    assert "data_unavailable" not in result.text
    assert session.history == ()
