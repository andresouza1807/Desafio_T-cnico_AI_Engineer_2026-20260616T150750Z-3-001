"""Lexical retrieval of store policy sections from a loaded policy index.

Slice 04, part B (docs/specs/04-policies.md, decisions P-07 to P-11, P-13, P-15):
validate the query, fold and stem it (P-08), map terms to concepts (P-09), rank the
retrievable sections by coverage and score (P-10), add curated companions and notices
bound to one reviewed manual hash (P-11), and return at most three whole sections.

search_policies works on an in-memory PolicyIndex only: no file, PDF, network,
environment variable or logging. Section text is returned unchanged and is never
evaluated or used as an instruction. Retrieval does not decide eligibility, compute
fees or installments, or act on the notices.
"""

import re
from dataclasses import dataclass
from typing import Final, Literal, get_args

from emporio_agent.data.policy_index import (
    FRONT_MATTER_ID,
    IndexedSection,
    PolicyIndex,
    PolicySource,
    SectionId,
)
from emporio_agent.services.catalog import normalize_search_text

MAX_QUERY_LENGTH: Final = 4000
MAX_RESULTS: Final = 3

type NoticeCode = Literal[
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
]

# Notice order of spec P-11: curated table, then service table, top to bottom.
_NOTICE_ORDER: Final[tuple[str, ...]] = get_args(NoticeCode.__value__)

_TOKEN = re.compile(r"[0-9a-z]+")
_INSTALLMENT_COUNT = re.compile(r"\d{1,2}x")


# --- Types ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompanionRule:
    trigger_section_id: str
    companion_section_id: str
    only_for_concepts: frozenset[str]  # empty: always


@dataclass(frozen=True)
class NoticeRule:
    code: NoticeCode
    trigger_section_ids: frozenset[str]
    section_ids: tuple[str, ...]  # sections the notice refers to
    message: str  # fixed text written in this code base, never copied from the index


@dataclass(frozen=True)
class CuratedNotes:
    source_sha256: str
    companions: tuple[CompanionRule, ...]
    notices: tuple[NoticeRule, ...]


@dataclass(frozen=True)
class PolicySection:
    section_id: SectionId
    title: str | None
    heading_path: tuple[str, ...]  # ancestor titles, then own title; () for front matter
    pages: tuple[int, ...]
    text: str  # "\n".join of the stored line texts, unchanged
    selected_by: Literal["match", "companion"]
    matched_terms: tuple[str, ...]  # folded, stemmed terms; () for a companion


@dataclass(frozen=True)
class PolicyNotice:
    code: NoticeCode
    section_ids: tuple[SectionId, ...]
    message: str


@dataclass(frozen=True)
class PolicySearchResult:
    status: Literal["ok", "not_found"]
    sections: tuple[PolicySection, ...]  # 0 to MAX_RESULTS
    omitted_section_ids: tuple[SectionId, ...]
    notices: tuple[PolicyNotice, ...]
    source: PolicySource


# --- Errors --------------------------------------------------------------------------


class PolicyInputError(ValueError):
    """The query was rejected before the index was read.

    Carries the field name and a code only, never the value. args == (str(self),).
    """

    field: str
    code: str

    def __init__(self, field: str, code: str) -> None:
        self.field = field
        self.code = code
        super().__init__(f"{field}: {code}")


class PolicyDataError(Exception):
    """The curated notes do not fit the index (notes_mismatch). Reason only."""

    reason: str

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


# --- Terms (P-08) and concepts (P-09) --------------------------------------------------

# (suffix, replacement, minimum length + 1); first rule whose suffix and length hold.
_STEM_RULES: Final = (
    ("coes", "cao", 0),
    ("oes", "ao", 0),
    ("aes", "ao", 0),
    ("ais", "al", 5),
    ("eis", "el", 5),
)


def _stem(token: str) -> str:
    if token.isdigit():
        return token
    for suffix, replacement, minimum in _STEM_RULES:
        if token.endswith(suffix) and len(token) >= minimum:
            return token[: -len(suffix)] + replacement
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _raw_terms(text: str) -> list[str]:
    return [_stem(token) for token in _TOKEN.findall(normalize_search_text(text))]


_STOPWORDS: Final = frozenset(
    _raw_terms(
        "a o as os e de da do das dos em no na nos nas um uma uns umas para pra por pelo"
        " pela com sem que qual quais como se ao aos ou eu meu minha voce voces vc vcs me"
        " te isso esse essa este esta tem ter ha sao ser foi posso pode podem quero queria"
        " gostaria saber sobre mais muito onde quando quanto quanta la aqui ai hoje ja nao"
        " sim ola oi bom dia boa tarde noite obrigado obrigada favor preciso r loja emporio"
        " musica cliente produto"
    )
)


def policy_terms(text: str) -> tuple[str, ...]:
    """Folded, stemmed terms without stopwords; duplicates removed, first kept (P-08)."""
    return tuple(dict.fromkeys(term for term in _raw_terms(text) if term not in _STOPWORDS))


_SYNONYMS: Final[dict[str, str]] = {
    "endereco": "endereço localização localizada localizado fica rua",
    "horario": (
        "horário funcionamento expediente abre abrem aberto aberta fecha fecham fechado"
        " feriado domingo sábado"
    ),
    "pagamento": "pagamento pagar pago forma",
    "pix": "pix",
    "parcelamento": "parcelamento parcelar parcela parcelado vezes juros",
    "cartao": "cartão crédito débito bandeira",
    "boleto": "boleto",
    "frete": (
        "frete entrega entregar envio enviar correios sedex pac jadlog transportadora"
        " motoboy chega chegar"
    ),
    "rastreamento": "rastreamento rastrear rastreio",
    "troca": "troca trocar substituir substituição",
    "devolucao": (
        "devolução devolver arrependimento arrepender arrependi desistir desistência"
        " reembolso estorno"
    ),
    "defeito": "defeito defeituoso quebrado quebrou estragado",
    "garantia": "garantia assistência conserto reparo",
    "telefone": "telefone fone ligar ligação",
    "whatsapp": "whatsapp zap",
    "email": "email mail",
    "humano": "humano atendente pessoa gerente gerência",
    "reclamacao": "reclamação reclamar queixa",
    "promocao": "promoção desconto oferta liquidação",
    "privacidade": "privacidade lgpd excluir exclusão",
}


def _concept_table() -> dict[str, str]:
    table: dict[str, str] = {}
    for concept, words in _SYNONYMS.items():
        for word in words.split():
            (term,) = policy_terms(word)  # one term per word, never a stopword
            if table.setdefault(term, concept) != concept:
                raise AssertionError("synonym groups overlap")
    return table


_CONCEPTS: Final = _concept_table()


def concept_of(term: str) -> str:
    """Concept key of a folded, stemmed term (P-09)."""
    concept = _CONCEPTS.get(term)
    if concept is not None:
        return concept
    if _INSTALLMENT_COUNT.fullmatch(term):
        return "parcelamento"
    return term


# --- Curated notes registry (P-11) ----------------------------------------------------

_TEL_WHATSAPP: Final = frozenset({"telefone", "whatsapp"})
_EMAIL: Final = frozenset({"email"})

REVIEWED_MANUAL_NOTES: Final = CuratedNotes(
    source_sha256="b7527f96896929f4ba929de117651c6717e25b7a3b92b34bc54d5ba32f6156f7",
    companions=(
        CompanionRule("3", "3.1", frozenset()),
        CompanionRule("3.1", "3", frozenset()),
        CompanionRule("4.2", "4.4", frozenset()),
        CompanionRule("4.3", "4.4", frozenset()),
        CompanionRule("8.1", "8.3", frozenset()),
        CompanionRule("8.2", "8.3", frozenset()),
        CompanionRule("1.2", "7", _TEL_WHATSAPP),
        CompanionRule("7", "1.2", _TEL_WHATSAPP),
        CompanionRule("1.2", FRONT_MATTER_ID, _EMAIL),
        CompanionRule(FRONT_MATTER_ID, "1.2", _EMAIL),
    ),
    notices=(
        NoticeRule(
            "contact_numbers_differ",
            frozenset({FRONT_MATTER_ID, "1.2", "7"}),
            (FRONT_MATTER_ID, "1.2", "7"),
            "Phone and WhatsApp numbers differ between sections. Approved rule: general"
            " phone from section 1.2, WhatsApp from section 7. Explain the origin if asked.",
        ),
        NoticeRule(
            "email_spelling_differs",
            frozenset({FRONT_MATTER_ID, "1.2"}),
            (FRONT_MATTER_ID, "1.2"),
            "The e-mail address is spelled differently on page 1 and in section 1.2. Do"
            " not correct it; report both origins if confirmation is requested and offer"
            " the documented phone or WhatsApp.",
        ),
        NoticeRule(
            "installment_rules_differ",
            frozenset({"3", "3.1"}),
            ("3", "3.1"),
            "Section 3 and section 3.1 differ on credit card installments. Approved rule:"
            " prefer the ranges of section 3.1; other conditions need staff confirmation.",
        ),
        NoticeRule(
            "free_shipping_threshold_gap",
            frozenset({"5.1"}),
            ("5.1",),
            "Section 5.1 defines orders above and below R$ 500,00 only. A total of"
            " exactly R$ 500,00 is not defined and needs staff confirmation.",
        ),
        NoticeRule(
            "withdrawal_needs_channel_and_receipt_date",
            frozenset({"4.1"}),
            ("4.1",),
            "Section 4.1 applies to online purchases and counts from actual receipt. Ask"
            " the purchase channel and the actual receipt date when missing. An estimated"
            " delivery date is not proof of receipt.",
        ),
        NoticeRule(
            "no_holiday_calendar",
            frozenset({"2", "7.1"}),
            ("2",),
            "The manual gives regular hours and closure on holidays, with no holiday"
            " calendar or exception dates. Do not state a specific opening or next"
            " business day for an uncertain date.",
        ),
        NoticeRule(
            "no_handoff_integration",
            frozenset({"4.2", "7", "7.3", "10"}),
            ("4.2", "7", "7.3", "10"),
            "This system cannot open tickets, register complaints or forward requests."
            " Guide the customer to contact the store; never say a request was forwarded.",
        ),
    ),
)

# Service notices, independent of the registry. Fixed text; never holds query or index text.
_SERVICE_MESSAGES: Final[dict[str, str]] = {
    "more_sections_matched": (
        "More sections matched than can be returned. Their IDs are listed; a more"
        " specific query can reach them."
    ),
    "no_matching_section": "No policy section matched the query. The information was not found.",
    "no_searchable_terms": (
        "The query has no searchable term after normalization. The information was not found."
    ),
    "curated_notes_unavailable": (
        "The policy index comes from a manual that was not reviewed for this version."
        " No companion section or divergence notice was applied."
    ),
}


# --- Search (P-07, P-10, P-11) -----------------------------------------------------------


def _check_query(query: object) -> str:
    """P-07: type, raw length in code points, then blank after normalization."""
    if type(query) is not str:
        raise PolicyInputError("query", "invalid_type") from None
    if len(query) > MAX_QUERY_LENGTH:
        raise PolicyInputError("query", "too_long") from None
    if not normalize_search_text(query):
        raise PolicyInputError("query", "blank") from None
    return query


def _concepts(text: str) -> frozenset[str]:
    return frozenset(concept_of(term) for term in policy_terms(text))


def _check_notes(notes: CuratedNotes, known: set[str]) -> None:
    referenced = {
        section_id
        for rule in notes.companions
        for section_id in (rule.trigger_section_id, rule.companion_section_id)
    } | {
        section_id
        for rule in notes.notices
        for section_id in (*rule.trigger_section_ids, *rule.section_ids)
    }
    if not referenced <= known:
        raise PolicyDataError("notes_mismatch") from None


@dataclass(frozen=True)
class _Ranked:
    section: IndexedSection
    position: int
    score: int
    matched_terms: tuple[str, ...]


def _heading_path(section: IndexedSection, by_id: dict[str, IndexedSection]) -> tuple[str, ...]:
    titles: list[str] = []
    current: IndexedSection | None = section
    while current is not None:
        if current.title is not None:
            titles.append(current.title)
        current = by_id.get(current.parent_id) if current.parent_id is not None else None
    return tuple(reversed(titles))


def _rank(
    index: PolicyIndex, terms: tuple[str, ...], by_id: dict[str, IndexedSection]
) -> list[_Ranked]:
    """Coverage filter, score threshold and order of P-10 steps 1 to 4."""
    query_concepts = list(dict.fromkeys(concept_of(term) for term in terms))
    scored: list[tuple[int, _Ranked]] = []
    for position, section in enumerate(index.sections):
        if not section.lines:
            continue
        own = _concepts(section.title or "")
        ancestors: list[str] = []
        parent = section.parent_id
        while parent is not None and parent in by_id:
            ancestors.append(by_id[parent].title or "")
            parent = by_id[parent].parent_id
        rest = _concepts("\n".join([*(text for _, text in section.lines), *ancestors]))
        weights = {c: 3 if c in own else 1 if c in rest else 0 for c in query_concepts}
        coverage = sum(1 for weight in weights.values() if weight > 0)
        if coverage == 0:
            continue
        matched = tuple(term for term in terms if weights[concept_of(term)] > 0)
        ranked = _Ranked(section, position, sum(weights.values()), matched)
        scored.append((coverage, ranked))
    if not scored:
        return []
    best_coverage = max(coverage for coverage, _ in scored)
    kept = [ranked for coverage, ranked in scored if coverage == best_coverage]
    top = max(ranked.score for ranked in kept)
    kept = [ranked for ranked in kept if 2 * ranked.score >= top]
    kept.sort(key=lambda ranked: (-ranked.score, -ranked.section.level, ranked.position))
    return kept


def _result_section(
    section: IndexedSection,
    by_id: dict[str, IndexedSection],
    selected_by: Literal["match", "companion"],
    matched_terms: tuple[str, ...],
) -> PolicySection:
    return PolicySection(
        section_id=section.section_id,
        title=section.title,
        heading_path=_heading_path(section, by_id),
        pages=section.pages,
        text="\n".join(text for _, text in section.lines),
        selected_by=selected_by,
        matched_terms=matched_terms,
    )


def _service_notice(code: NoticeCode, section_ids: tuple[str, ...] = ()) -> PolicyNotice:
    return PolicyNotice(code, section_ids, _SERVICE_MESSAGES[code])


def _ordered(notices: list[PolicyNotice]) -> tuple[PolicyNotice, ...]:
    first: dict[str, PolicyNotice] = {}
    for notice in notices:
        first.setdefault(notice.code, notice)
    return tuple(sorted(first.values(), key=lambda notice: _NOTICE_ORDER.index(notice.code)))


def search_policies(
    index: PolicyIndex,
    query: object,
    *,
    notes: CuratedNotes = REVIEWED_MANUAL_NOTES,
) -> PolicySearchResult:
    """Return up to MAX_RESULTS whole sections for query, or an explicit not_found.

    The query is validated before the index is read. The index is never mutated; equal
    inputs give equal results. Raises PolicyInputError for an invalid query and
    PolicyDataError("notes_mismatch") when notes bound to this index name a missing
    section.
    """
    text = _check_query(query)
    terms = policy_terms(text)

    source = index.source
    by_id = {section.section_id: section for section in index.sections}
    curated = notes.source_sha256 == source.sha256
    if curated:
        _check_notes(notes, set(by_id))
    tail = [] if curated else [_service_notice("curated_notes_unavailable")]

    def not_found(code: NoticeCode) -> PolicySearchResult:
        return PolicySearchResult(
            "not_found", (), (), _ordered([_service_notice(code), *tail]), source
        )

    if not terms:
        return not_found("no_searchable_terms")
    ranked = _rank(index, terms, by_id)
    if not ranked:
        return not_found("no_matching_section")

    query_concepts = frozenset(concept_of(term) for term in terms)
    matches = {item.section.section_id: item for item in ranked}
    chosen: list[tuple[IndexedSection, Literal["match", "companion"], tuple[str, ...]]] = []
    seen: set[str] = set()

    def add(section_id: str) -> None:
        if section_id in seen:
            return
        section = by_id.get(section_id)
        if section is None or not section.lines:
            return
        seen.add(section_id)
        match = matches.get(section_id)
        if match is not None:
            chosen.append((section, "match", match.matched_terms))
        else:
            chosen.append((section, "companion", ()))

    for item in ranked:
        add(item.section.section_id)
        if not curated:
            continue
        for rule in notes.companions:
            if rule.trigger_section_id != item.section.section_id:
                continue
            if rule.only_for_concepts and rule.only_for_concepts.isdisjoint(query_concepts):
                continue
            add(rule.companion_section_id)

    returned = chosen[:MAX_RESULTS]
    omitted = tuple(section.section_id for section, _, _ in chosen[MAX_RESULTS:])
    returned_ids = {section.section_id for section, _, _ in returned}

    notices: list[PolicyNotice] = []
    if curated:
        notices.extend(
            PolicyNotice(rule.code, rule.section_ids, rule.message)
            for rule in notes.notices
            if not rule.trigger_section_ids.isdisjoint(returned_ids)
        )
    if omitted:
        notices.append(_service_notice("more_sections_matched", omitted))
    notices.extend(tail)

    return PolicySearchResult(
        status="ok",
        sections=tuple(
            _result_section(section, by_id, selected_by, matched)
            for section, selected_by, matched in returned
        ),
        omitted_section_ids=omitted,
        notices=_ordered(notices),
        source=source,
    )
