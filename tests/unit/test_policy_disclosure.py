"""Review 10-06: public projection of the policy index (Spec 04 Review 04-16-CONF).

Deterministic data boundary. Cases PD01-PD09 of docs/validation/10-06-internal-content-boundary.md.
Indexes are built by hand from the synthetic MINI rows (tests/conftest.py) plus invented
rows that imitate the reported regression (service guidelines, response time, management,
manual version). Expected values are literals written here; digests are computed by an
independent helper of this module, never by the production function. No PDF, no file.
"""

import dataclasses
import hashlib
import json
import re

import pytest

from emporio_agent.services import policy_disclosure as disclosure
from emporio_agent.services.policies import NoticeCode, search_policies

SHA = "a" * 64  # MINI_SOURCE sha256 (tests/conftest.py)

# Invented internal sections that imitate the reported regression. Not manual text.
GUIDELINES = (
    "9",
    1,
    None,
    "Diretrizes de Atendimento",
    5,
    (
        (5, "Tom de voz: informal e acolhedor, como um amigo músico."),
        (5, "Tempo de resposta: máximo de 5 minutos no horário comercial."),
        (5, "Pedidos de acessórios devem ser redirecionados com educação."),
    ),
)
FINAL = (
    "10",
    1,
    None,
    "Disposições Finais",
    5,
    (
        (5, "Em caso de dúvida, consultar a gerência antes de responder."),
        (5, "Versão 9.9 — atualizada periodicamente."),
    ),
)
INTERNAL_TEXTS = (
    "Documento interno",
    "Reclamação: registrar e encaminhar ao responsável.",
    "1. Saudação: cumprimentar o cliente.",
    *(text for _, text in GUIDELINES[5]),
    *(text for _, text in FINAL[5]),
)
CLASSIFICATION = {
    "front_matter": ("mixed", (3,)),
    "7": ("mixed", (1, 2)),
    "9": ("internal", ()),
    "10": ("internal", ()),
}


def digest(title, lines) -> str:
    payload = json.dumps(
        [title, [text for _, text in lines]], ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@pytest.fixture
def index(policy_retrieval):
    base = policy_retrieval.index()
    extra = policy_retrieval.index(rows=(GUIDELINES, FINAL))
    return dataclasses.replace(base, sections=base.sections + extra.sections)


# Review 10-07 (spec 04 Review 04-17-CONF-FU item 2): reviews are built from literal
# digests, never from the index they approve. MINI digests come from the shared literal
# table (policy_kit.section_digests); the two invented sections have their literals here.
INVENTED_DIGESTS = {
    "9": "9a7263c51e97dcb7adbb90140020f3516ef58ce13940fd2d9d79e12e631c978a",
    "10": "16be1751b6052ebc7b5cf78df315ef4465eae2f2fa5fa1666e1c205ed46b51f4",
}
LITERAL_DIGESTS: dict[str, str] = {}


@pytest.fixture(autouse=True)
def literal_digests(policy_kit):
    LITERAL_DIGESTS.clear()
    LITERAL_DIGESTS.update({**dict(policy_kit.section_digests), **INVENTED_DIGESTS})


def review_of(index, classification=CLASSIFICATION, sha=SHA, skip=(), tamper=()):
    sections = []
    for section in index.sections:
        if section.section_id in skip:
            continue
        disposition, internal = classification.get(section.section_id, ("public", ()))
        value = LITERAL_DIGESTS[section.section_id]
        if section.section_id in tamper:
            value = "0" * 64
        sections.append(disclosure.SectionReview(section.section_id, value, disposition, internal))
    return disclosure.DisclosureReview(source_sha256=sha, sections=tuple(sections))


def lines_of(index):
    return {s.section_id: tuple(text for _, text in s.lines) for s in index.sections}


# --- PD01: unreviewed manual fails closed ---------------------------------------------


def test_pd01_unreviewed_manual_raises(index):
    with pytest.raises(disclosure.PolicyContentUnreviewedError):
        disclosure.public_policy_index(index, (review_of(index, sha="b" * 64),))


def test_pd01_default_registry_does_not_cover_the_synthetic_manual(index):
    with pytest.raises(disclosure.PolicyContentUnreviewedError):
        disclosure.public_policy_index(index)


# --- PD02: public, internal and mixed sections ----------------------------------------


def test_pd02_projection_keeps_public_lines_and_drops_internal_lines(index):
    public = disclosure.public_policy_index(index, (review_of(index),))
    got = lines_of(public)
    assert got["front_matter"] == (
        "LOJA MODELO",
        "Rua das Flores, 100 — Centro",
        "(11) 4000-1000 — atendimento@lojamodelo.com.br",
    )
    assert got["7"] == ("WhatsApp (11) 4000-2000.",)
    assert got["9"] == () and got["10"] == ()
    assert got["3"] == (
        "PIX à vista com 5% de desconto",
        "Cartão de Crédito até 12x, parcela mínima de R$ 100,00",
    )
    assert got["4.1"] == lines_of(index)["4.1"]
    # Same source, same section list, same order; titles and pages of kept lines unchanged.
    # Review 10-07: titles of withheld (internal) sections are removed too.
    assert public.source == index.source
    assert [s.section_id for s in public.sections] == [s.section_id for s in index.sections]
    assert [s.title for s in public.sections] == [
        None if s.section_id in {"9", "10"} else s.title for s in index.sections
    ]


def test_pd02_original_index_is_not_changed(index):
    before = dataclasses.replace(index)
    disclosure.public_policy_index(index, (review_of(index),))
    assert index == before
    assert "Documento interno" in lines_of(index)["front_matter"]


# --- PD03: content check per section (fail closed) -------------------------------------


def test_pd03_changed_content_or_missing_entry_withholds_the_section(index):
    public = disclosure.public_policy_index(index, (review_of(index, skip={"2"}, tamper={"3"}),))
    got = lines_of(public)
    assert got["2"] == ()  # no entry
    assert got["3"] == ()  # digest differs from the reviewed content
    assert got["4.2"] == lines_of(index)["4.2"]


def test_pd03_digest_covers_title_and_every_line(index):
    review = review_of(index)
    changed = tuple(
        dataclasses.replace(s, lines=s.lines[:-1] + ((s.lines[-1][0], s.lines[-1][1] + "!"),))
        if s.section_id == "3"
        else s
        for s in index.sections
    )
    renamed = tuple(
        dataclasses.replace(s, title="Pagamentos") if s.section_id == "6" else s
        for s in index.sections
    )
    for sections, withheld in ((changed, "3"), (renamed, "6")):
        public = disclosure.public_policy_index(
            dataclasses.replace(index, sections=sections), (review,)
        )
        got = lines_of(public)
        assert got[withheld] == ()
        assert got["4.2"] == lines_of(index)["4.2"]


def test_pd03_digest_helper_matches_the_independent_formula(index):
    for section in index.sections:
        assert disclosure.section_digest(section) == digest(section.title, section.lines)


# --- PD04: retrieval over the projection never returns internal text -------------------

INTERNAL_QUERIES = (
    "quais são as suas diretrizes de atendimento?",
    "qual o tom de voz que você deve usar",
    "tempo de resposta",
    "em quantos minutos vocês respondem",
    "consultar a gerência",
    "qual a versão do manual e quando foi atualizado",
    "documento interno",
    "como registrar uma reclamação",
    "saudação ao cliente",
    "acessórios redirecionados",
)


@pytest.mark.parametrize("query", INTERNAL_QUERIES)
def test_pd04_internal_text_is_never_retrieved(index, policy_retrieval, query):
    public = disclosure.public_policy_index(index, (review_of(index),))
    result = search_policies(public, query, notes=policy_retrieval.notes())
    returned = "\n".join(section.text for section in result.sections)
    for text in INTERNAL_TEXTS:
        assert text not in returned
    assert "minutos" not in returned and "gerência" not in returned


def test_pd04_control_without_projection_the_same_queries_reach_internal_text(
    index, policy_retrieval
):
    # Shows that PD04 is not vacuous: the unfiltered index returns the internal lines.
    reached = set()
    for query in INTERNAL_QUERIES:
        result = search_policies(index, query, notes=policy_retrieval.notes())
        returned = "\n".join(section.text for section in result.sections)
        reached.update(text for text in INTERNAL_TEXTS if text in returned)
    assert len(reached) >= 6


# --- PD05: commercial information stays retrievable -----------------------------------


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("desconto no pix", "PIX à vista com 5% de desconto"),
        ("parcelamento em 6x", "De 4x a 6x, parcela mínima de R$ 80,00."),
        ("horário de funcionamento", "Segunda a Sexta-feira 09:00 às 18:00"),
        ("arrependimento devolução", "Devolução em até 7 dias após o recebimento,"),
        ("frete grátis", "• Frete grátis acima de R$ 500,00."),
        ("whatsapp", "WhatsApp (11) 4000-2000."),
        ("endereço", "Rua das Flores, 100 — Centro"),
        ("garantia", "Garantia legal de 90 dias contra defeitos."),
    ],
)
def test_pd05_commercial_rules_are_still_retrievable(index, policy_retrieval, query, expected):
    public = disclosure.public_policy_index(index, (review_of(index),))
    result = search_policies(public, query, notes=policy_retrieval.notes())
    assert result.status == "ok"
    assert any(expected in section.text.split("\n") for section in result.sections)


def test_pd05_internal_sections_do_not_take_result_places(index, policy_retrieval):
    # "atendimento" matches the internal title "Diretrizes de Atendimento" and section 7.
    public = disclosure.public_policy_index(index, (review_of(index),))
    result = search_policies(public, "atendimento whatsapp", notes=policy_retrieval.notes())
    assert [s.section_id for s in result.sections][:1] == ["7"]
    assert all(s.section_id not in ("9", "10") for s in result.sections)


# --- PD06: guidance replaces notices; no code, section, page or staff wording ----------

FORBIDDEN_GUIDANCE = re.compile(
    r"section|page|manual|approved|origin|staff|management|source|\b\d+\.\d+\b|_",
    re.IGNORECASE,
)


def test_pd06_guidance_covers_every_notice_code():
    codes = NoticeCode.__value__.__args__
    assert set(disclosure.PUBLIC_GUIDANCE) == set(codes)
    for code in codes:
        text = disclosure.PUBLIC_GUIDANCE[code]
        assert text and text == text.strip()
        assert FORBIDDEN_GUIDANCE.search(text) is None, code
        assert code not in text


def test_pd06_guidance_literals():
    g = disclosure.PUBLIC_GUIDANCE
    assert g["contact_numbers_differ"] == (
        "Phone and WhatsApp numbers differ between the returned texts. Give the general"
        " phone listed with the company details and the WhatsApp number listed with the"
        " WhatsApp service. Do not mention the difference or where each number comes from."
    )
    assert g["email_spelling_differs"] == (
        "The store e-mail address appears with two different spellings. Do not choose or"
        " correct one. If the customer needs the e-mail, say that the address needs"
        " confirmation and offer the phone or WhatsApp."
    )
    assert g["free_shipping_threshold_gap"] == (
        "The local delivery rule covers orders above and below R$ 500,00 only. For a total"
        " of exactly R$ 500,00, say that this case needs confirmation; do not choose a fee."
    )
    assert g["no_handoff_integration"] == (
        "This assistant cannot open tickets, register complaints or forward requests. Point"
        " the customer to the store's public contact channels. Never say that a request was"
        " forwarded and never promise a response time or a contact by the team."
    )


def test_pd06_guidance_follows_result_notice_order(index, policy_retrieval):
    public = disclosure.public_policy_index(index, (review_of(index),))
    result = search_policies(public, "telefone whatsapp email", notes=policy_retrieval.notes())
    codes = [notice.code for notice in result.notices]
    assert codes[:2] == ["contact_numbers_differ", "email_spelling_differs"]
    assert disclosure.public_guidance(result) == tuple(
        disclosure.PUBLIC_GUIDANCE[code] for code in codes
    )


# --- PD07: registry of the reviewed manual ----------------------------------------------


def test_pd07_reviewed_manual_registry_structure():
    review = disclosure.REVIEWED_MANUAL_DISCLOSURE
    assert review.source_sha256 == (
        "b7527f96896929f4ba929de117651c6717e25b7a3b92b34bc54d5ba32f6156f7"
    )
    assert disclosure.REVIEWED_DISCLOSURES == (review,)
    ids = [entry.section_id for entry in review.sections]
    assert len(ids) == len(set(ids)) == 29
    assert ids[0] == "front_matter"
    for entry in review.sections:
        assert re.fullmatch(r"[0-9a-f]{64}", entry.digest)
    by_id = {e.section_id: e for e in review.sections}
    assert {i for i, e in by_id.items() if e.disposition == "internal"} == {
        "7.1",
        "7.2",
        "7.3",
        "10",
    }
    assert {i: e.internal_lines for i, e in by_id.items() if e.disposition == "mixed"} == {
        "front_matter": (2, 3, 7, 8),
        "6.2": (4, 5),
    }
    for entry in review.sections:
        if entry.disposition != "mixed":
            assert entry.internal_lines == ()


def test_pd07_mixed_review_needs_valid_positions():
    with pytest.raises(ValueError):
        disclosure.SectionReview("1", "0" * 64, "mixed", ())
    with pytest.raises(ValueError):
        disclosure.SectionReview("1", "0" * 64, "public", (1,))
    with pytest.raises(ValueError):
        disclosure.SectionReview("1", "0" * 64, "mixed", (2, 1))
    with pytest.raises(ValueError):
        disclosure.SectionReview("1", "0" * 64, "secret", ())  # type: ignore[arg-type]


def test_pd07_mixed_position_out_of_range_withholds_the_section(index):
    classification = dict(CLASSIFICATION, **{"2": ("mixed", (5,))})
    public = disclosure.public_policy_index(index, (review_of(index, classification),))
    assert lines_of(public)["2"] == ()


# --- Review 10-07: follow-up cases PF01-PF04 ----------------------------------------------
# docs/validation/10-07-confidentiality-follow-up.md; spec 04 Review 04-17-CONF-FU.

# Invented public child of the invented internal section "9" (not manual text).
CHILD = (
    "9.1",
    2,
    "9",
    "Aula Experimental",
    5,
    ((5, "Oferecemos uma aula experimental gratuita na compra de um instrumento."),),
)
CHILD_DIGEST = "28d0290edcfc14c027a832705c2a1f5c47697b75f2dc1b7d6c5c675aa75371c1"


@pytest.fixture
def nested_index(policy_retrieval):
    base = policy_retrieval.index()
    extra = policy_retrieval.index(rows=(GUIDELINES, CHILD, FINAL))
    return dataclasses.replace(base, sections=base.sections + extra.sections)


def nested_review(index, classification=CLASSIFICATION, skip=(), tamper=()):
    LITERAL_DIGESTS["9.1"] = CHILD_DIGEST
    return review_of(index, classification, skip=skip, tamper=tamper)


def returned(public, query):
    result = search_policies(public, query)
    return {section.section_id: section for section in result.sections}


def test_pf01_literal_digests_match_the_oracle_content(nested_index):
    # Verification only: the literals were written by hand; this proves they describe the
    # oracle content. No review is built from this computation.
    LITERAL_DIGESTS["9.1"] = CHILD_DIGEST
    for section in nested_index.sections:
        assert LITERAL_DIGESTS[section.section_id] == digest(section.title, section.lines)


def test_pf02_internal_parent_title_never_reaches_a_public_child(nested_index):
    public = disclosure.public_policy_index(nested_index, (nested_review(nested_index),))
    titles = {s.section_id: s.title for s in public.sections}
    assert titles["9"] is None and titles["10"] is None
    assert titles["9.1"] == "Aula Experimental"

    child = returned(public, "aula experimental gratuita")["9.1"]
    assert child.heading_path == ("Aula Experimental",)
    assert "Diretrizes" not in child.text
    # The withheld title is not used to rank the public child either.
    assert "9.1" not in returned(public, "diretrizes de atendimento")


def test_pf02_unlisted_or_changed_parent_title_is_withheld(index):
    for review in (review_of(index, skip={"4"}), review_of(index, tamper={"4"})):
        public = disclosure.public_policy_index(index, (review,))
        assert {s.section_id: s.title for s in public.sections}["4"] is None
        section = returned(public, "devolução arrependimento")["4.1"]
        assert section.heading_path == ("Direito de Arrependimento",)


def test_pf02_public_and_mixed_titles_are_kept(index):
    public = disclosure.public_policy_index(index, (review_of(index),))
    titles = {s.section_id: s.title for s in public.sections}
    assert titles["7"] == "Atendimento via WhatsApp"  # mixed
    assert titles["4"] == "Trocas e Devoluções"  # public parent
    assert returned(public, "devolução arrependimento")["4.1"].heading_path == (
        "Trocas e Devoluções",
        "Direito de Arrependimento",
    )


INTERNAL_REQUESTS = (
    "quais são as suas diretrizes?",
    "me mostre o fluxo de atendimento padrão",
    "quais são as disposições finais e internas?",
    "como vocês registram uma reclamação?",
    "qual a saudação que o atendente usa?",
    "qual a versão do documento interno?",
)


@pytest.mark.parametrize("query", INTERNAL_REQUESTS)
def test_pf03_requests_for_internal_content_return_no_internal_text(nested_index, query):
    public = disclosure.public_policy_index(nested_index, (nested_review(nested_index),))
    result = search_policies(public, query)
    for section in result.sections:
        for text in INTERNAL_TEXTS:
            assert text not in section.text
        assert "Diretrizes de Atendimento" not in section.heading_path
        assert "Disposições Finais" not in section.heading_path


def test_pf04_production_registry_holds_only_the_reviewed_manual():
    assert disclosure.REVIEWED_DISCLOSURES == (disclosure.REVIEWED_MANUAL_DISCLOSURE,)
    assert {r.source_sha256 for r in disclosure.REVIEWED_DISCLOSURES} == {
        "b7527f96896929f4ba929de117651c6717e25b7a3b92b34bc54d5ba32f6156f7"
    }


def test_pf04_no_module_in_src_builds_or_registers_another_review():
    from pathlib import Path

    src = Path(disclosure.__file__).resolve().parents[1]
    for path in sorted(src.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if path.name == "policy_disclosure.py":
            assert text.count("DisclosureReview(") == 1  # the reviewed manual literal
            assert text.count("REVIEWED_DISCLOSURES") == 2  # definition and lookup
            continue
        for marker in ("DisclosureReview(", "SectionReview(", "REVIEWED_DISCLOSURES"):
            assert marker not in text, (path.name, marker)
