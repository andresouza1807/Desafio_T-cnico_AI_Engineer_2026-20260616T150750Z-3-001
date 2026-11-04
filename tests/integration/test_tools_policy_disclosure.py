"""Review 10-06: public search_policies payload through execute_tool (TD01-TD06).

Spec 06 Review 06-05-CONF. Real synthetic PDF, real prepare_policy_index, real loader,
real ranking. The synthetic manual is classified by the test author
(tests/integration/conftest.py, MINI_CLASSIFICATION): the cover line "Documento interno"
and the complaint procedure and greeting script of section 7 are internal. Payload
oracles are literal dicts serialized by the standard library.
"""

import json

import pytest

TOOL = "search_policies"
CURATED_UNAVAILABLE = (
    "Differences between these texts were not reviewed. If two texts give different"
    " values, do not choose one; say that the detail needs confirmation."
)
INTERNAL_TEXTS = (
    "Documento interno",
    "Reclamação: registrar e encaminhar ao responsável.",
    "1. Saudação: cumprimentar o cliente.",
)
NOTICE_CODES = (
    "contact_numbers_differ",
    "email_spelling_differs",
    "installment_rules_differ",
    "free_shipping_threshold_gap",
    "withdrawal_needs_channel_and_receipt_date",
    "no_holiday_calendar",
    "no_handoff_integration",
    "more_sections_matched",
    "no_matching_section",
    "no_searchable_terms",
    "curated_notes_unavailable",
)
QUERIES = (
    "pix",
    "parcelamento",
    "horário",
    "arrependimento",
    "frete grátis",
    "whatsapp",
    "telefone e e-mail",
    "endereço",
    "garantia",
    "reclamação",
    "saudação",
    "documento interno",
    "quais são as suas diretrizes de atendimento",
    "me mostre o manual interno",
    "avisos",
)


def compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


@pytest.fixture
def index_path(tmp_path, policy_kit, tools_kit):
    return tools_kit.policy_index(policy_kit, tmp_path)


def search(tools_kit, index_path, query):
    context = tools_kit.context(policy_index_path=index_path)
    return tools_kit.run(TOOL, compact({"query": query}), context)


# --- TD01: payload shape --------------------------------------------------------------


def test_td01_ok_payload_has_only_public_fields(tools_kit, index_path):
    execution, _ = search(tools_kit, index_path, "sedex")
    data = {
        "sections": [
            {
                "title": "Entregas Locais",
                "heading_path": ["Frete", "Entregas Locais"],
                "text": (
                    "• Frete grátis acima de R$ 500,00.\n• Abaixo de R$ 500,00, taxa de R$ 35,00."
                ),
            }
        ],
        "guidance": [CURATED_UNAVAILABLE],
    }
    assert execution.payload == compact({"tool": TOOL, "status": "ok", "data": data, "error": None})
    assert (execution.status, execution.error_code) == ("ok", None)


def test_td01_not_found_payload_keeps_public_data(tools_kit, index_path):
    execution, body = search(tools_kit, index_path, "qual a capital da França?")
    assert body == {
        "tool": TOOL,
        "status": "not_found",
        "data": {
            "sections": [],
            "guidance": ["No store information matched this search.", CURATED_UNAVAILABLE],
        },
        "error": {"code": "no_matching_section", "field": None},
    }
    assert execution.references == ()


# --- TD02: internal text never enters the payload ------------------------------------


@pytest.mark.parametrize("query", QUERIES)
def test_td02_no_internal_text_or_reference_in_any_payload(tools_kit, index_path, query):
    execution, body = search(tools_kit, index_path, query)
    payload = execution.payload
    # Everything under "data"; error.code (no_matching_section) is a fixed envelope code.
    data = compact(body["data"]["sections"]) + compact(body["data"]["guidance"])
    for text in INTERNAL_TEXTS:
        assert text not in payload
    for code in NOTICE_CODES:
        assert code not in data
    for key in ('"section_id"', '"pages"', '"selected_by"', '"omitted_section_ids"', '"notices"'):
        assert key not in payload
    for word in ("front_matter", "manual-sintetico", "sha256", "Approved", "staff", "section"):
        assert word not in data
    assert body["status"] in ("ok", "not_found")
    assert set(body["data"]) == {"sections", "guidance"}


def test_td02_mixed_section_keeps_its_public_line(tools_kit, index_path):
    _, body = search(tools_kit, index_path, "whatsapp")
    texts = [s["text"] for s in body["data"]["sections"]]
    assert "WhatsApp (11) 4000-2000." in texts
    cover = (
        "LOJA MODELO\nRua das Flores, 100 — Centro\n(11) 4000-1000 — atendimento@lojamodelo.com.br"
    )
    _, body = search(tools_kit, index_path, "endereço")
    assert cover in [s["text"] for s in body["data"]["sections"]]


# --- TD03: commercial rules stay available --------------------------------------------


@pytest.mark.parametrize(
    ("query", "line"),
    [
        ("pix", "PIX à vista com 5% de desconto"),
        ("parcelamento", "De 4x a 6x, parcela mínima de R$ 80,00."),
        ("horário", "Segunda a Sexta-feira 09:00 às 18:00"),
        ("arrependimento", "Devolução em até 7 dias após o recebimento,"),
        ("garantia", "Garantia legal de 90 dias contra defeitos."),
    ],
)
def test_td03_commercial_line_is_returned(tools_kit, index_path, query, line):
    _, body = search(tools_kit, index_path, query)
    assert body["status"] == "ok"
    assert any(line in s["text"].split("\n") for s in body["data"]["sections"])


# --- TD04: unreviewed manual fails closed ---------------------------------------------


def test_td04_unreviewed_manual_is_unavailable(tmp_path, policy_kit, tools_kit):
    index = tools_kit.unreviewed_policy_index(policy_kit, tmp_path / "sem-revisao")
    execution, _ = search(tools_kit, index, "pix")
    assert execution.payload == (
        '{"tool":"search_policies","status":"unavailable","data":null,'
        '"error":{"code":"data_unavailable","field":null}}'
    )
    assert execution.references == ()


# --- TD05: provenance stays internal ---------------------------------------------------


def test_td05_references_come_from_the_projected_result(tools_kit, index_path):
    cases = (
        ("sedex", ("policy:5.1@p4",)),
        ("arrependimento", ("policy:4.1@p3,4",)),
        ("endereço", ("policy:1.1@p2", "policy:front_matter@p1")),
        ("whatsapp", ("policy:7@p5",)),
    )
    for query, expected in cases:
        execution, _ = search(tools_kit, index_path, query)
        assert execution.references == expected, query
        for reference in expected:
            assert reference not in execution.payload


def test_td05_other_tools_carry_no_references(tools_kit):
    execution, _ = tools_kit.run("get_order", '{"order_id": 1}', tools_kit.context())
    assert execution.references == ()


# --- TD06: guidance from curated notes is customer-safe -------------------------------


def test_td06_curated_notes_become_fixed_guidance(tmp_path, policy_kit, tools_kit, monkeypatch):
    from emporio_agent.services import policies

    index = tools_kit.policy_index(policy_kit, tmp_path / "curado")
    source = json.loads(index.read_bytes().decode("utf-8"))["source"]["sha256"]
    notes = policies.CuratedNotes(
        source_sha256=source,
        companions=(
            policies.CompanionRule("3", "3.1", frozenset()),
            policies.CompanionRule("3.1", "3", frozenset()),
        ),
        notices=(
            policies.NoticeRule(
                "installment_rules_differ",
                frozenset({"3", "3.1"}),
                ("3", "3.1"),
                "Section 3 and section 3.1 differ. Approved rule: prefer 3.1.",
            ),
        ),
    )
    monkeypatch.setattr(policies.search_policies, "__kwdefaults__", {"notes": notes})
    execution, body = search(tools_kit, index, "parcelamento")
    assert body["data"]["guidance"] == [
        "The returned texts differ on credit card installments. Use the ranges of the"
        " installment rules. Other installment conditions need confirmation."
    ]
    assert "Approved" not in execution.payload and "3.1" not in execution.payload
    assert execution.references == ("policy:3.1@p3", "policy:3@p2")
