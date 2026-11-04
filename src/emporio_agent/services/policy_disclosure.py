"""Customer-facing projection of the policy index (spec 04 Review 04-16-CONF).

The supplied manual is an internal document: it mixes store rules for customers with
directives to staff. `public_policy_index` keeps only the lines that a content review
classified as public, so internal text never reaches retrieval, the tool payload or the
model. The review is bound to the reviewed manual SHA-256 and to a digest of every
section; any other manual, or a section whose content changed, is withheld (fail closed).

`public_guidance` turns the notices of a search result into fixed customer-safe guidance
for the model: no code, section, page, document name or staff-directed wording.

Pure functions over in-memory values: no file, network, environment or logging. The
original index object is never changed.
"""

import dataclasses
import hashlib
import json
from dataclasses import dataclass
from typing import Final, Literal, get_args

from emporio_agent.data.policy_index import FRONT_MATTER_ID, IndexedSection, PolicyIndex
from emporio_agent.services.policies import NoticeCode, PolicySearchResult

type Disposition = Literal["public", "internal", "mixed"]

_DISPOSITIONS: Final = frozenset(get_args(Disposition.__value__))


@dataclass(frozen=True)
class SectionReview:
    section_id: str
    digest: str  # section_digest of the reviewed section
    disposition: Disposition
    internal_lines: tuple[int, ...] = ()  # 0-based, strictly increasing; only for "mixed"

    def __post_init__(self) -> None:
        if self.disposition not in _DISPOSITIONS:
            raise ValueError("unknown disposition")
        positions = self.internal_lines
        if (self.disposition == "mixed") != bool(positions):
            raise ValueError("internal_lines must be given for mixed sections only")
        if any(type(p) is not int or p < 0 for p in positions) or list(positions) != sorted(
            set(positions)
        ):
            raise ValueError("internal_lines must be increasing non-negative positions")


@dataclass(frozen=True)
class DisclosureReview:
    source_sha256: str
    sections: tuple[SectionReview, ...]


class PolicyContentUnreviewedError(Exception):
    """The index comes from a manual without a disclosure review. No text may be used."""


def section_digest(section: IndexedSection) -> str:
    """SHA-256 of the compact JSON [title, [line texts]], UTF-8."""
    texts = [text for _, text in section.lines]
    payload = json.dumps([section.title, texts], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --- Reviewed manual (spec 04 Review 04-16-CONF, table of item 5) ------------------------
#
# Public: commercial facts and public company data. Internal: directives to staff, service
# flow, internal procedures, management, manual identity, version and updates.

_P: Final = "public"

REVIEWED_MANUAL_DISCLOSURE: Final = DisclosureReview(
    source_sha256="b7527f96896929f4ba929de117651c6717e25b7a3b92b34bc54d5ba32f6156f7",
    sections=(
        # Cover: manual title, subtitle, version and "internal document" lines withheld.
        SectionReview(
            FRONT_MATTER_ID,
            "13a599688284e943d3bcb54d115d1d97373e75c37c1fa29859e8b7d4515a5bba",
            "mixed",
            (2, 3, 7, 8),
        ),
        SectionReview("1", "80714dfcbdfb0287b77987d4b6b24096474649079d42478db977e0a8734d4073", _P),
        SectionReview(
            "1.1", "37ab6661dd1d182c56b556a6f719689e53b583c82c0bf223ed790e13dc91e4f6", _P
        ),
        SectionReview(
            "1.2", "25e46377447ba796c380bb88acf3f223fdb6567a95f7e890167ccffe5677e69d", _P
        ),
        SectionReview("2", "db1c03fa9ab4a6ced025bcdd29887fb0e97de88aeedac9195c99341cbb4246c9", _P),
        SectionReview("3", "33ecec84c3e93f6128a649f8740a1c21cb2391aafb65c9203f110235eb38a918", _P),
        SectionReview(
            "3.1", "d0c8ba5e4da360901678264661c09d170e3b3481e635a8afb63ed137a354155d", _P
        ),
        SectionReview("4", "f2c0ce97929ed24d9a4287a79b8c96385fb33246233998f886f520b7f04fba35", _P),
        SectionReview(
            "4.1", "f86563bf0dd151ad77435f2a9b59c9190c76dcdb78875d97427aa7ee279e3933", _P
        ),
        SectionReview(
            "4.2", "b4772ecf3e1bd63e733e16343a0c552c4267e01681caa5fce87d23efb668476b", _P
        ),
        SectionReview(
            "4.3", "b33208e47486b275dbb04dc78a48be7903930cea6c743f3fd95d756ceb46ed17", _P
        ),
        SectionReview(
            "4.4", "7876d70d4d0eabc4ffaa32217e422fb049bd1c987ec0eb8d77856c6257022efd", _P
        ),
        SectionReview("5", "718f5d5f8d0a849fd24ac6cd68f3979977436a61cbfcc354311275bf99cba3bc", _P),
        SectionReview(
            "5.1", "f64cbc106f3113750953d1d5ee3191cd969bbd1998af2f9e0259294dc45119b4", _P
        ),
        SectionReview(
            "5.2", "c95e1e8d2be4656befbc42ae6ef050c06062855424778f2cbb4d0064b94f40ff", _P
        ),
        SectionReview(
            "5.3", "700477f25643929cfe9887628945bbf4788f1209f132e26608fd1ab341a92b86", _P
        ),
        SectionReview("6", "74fd4f5b88b05dd3a9ad6aba7a01e2c38531a7ee8afb51584141b6369574cf0d", _P),
        SectionReview(
            "6.1", "d8a79f2e71f3ac78e5ddf3ec1c0cf57550877e7df9b0829037a1db7688e6fece", _P
        ),
        # Promotion rules: the staff directive on how to present promotional prices withheld.
        SectionReview(
            "6.2",
            "7fbb1fe607ec6faf1de46ca5985a0ec62d14b826f239724c528f610bfa962e36",
            "mixed",
            (4, 5),
        ),
        SectionReview("7", "49bda8a1e756aab92fc51d594f7548eea8367db1c37c146a00bddfb5bbb643d7", _P),
        # Service guidelines, standard service flow, special situations: staff directives.
        SectionReview(
            "7.1", "51c5c6f1b4bfbe9945fd95906bda3d86f0f37f5b46a144816b4e52fa7ac241c7", "internal"
        ),
        SectionReview(
            "7.2", "7fc6a3e8a41fc8dfe3c6ba61b649b29b7ed755f0968709ff7c4e7398a0320ad0", "internal"
        ),
        SectionReview(
            "7.3", "5030c740c63df38c957e66e026f39a3255f8724a21d834b4e524d6828b9c957b", "internal"
        ),
        SectionReview("8", "49e2a655f9ef81fe528e2a88a5bdca63caf650ae162b906c74b8394cd0e89974", _P),
        SectionReview(
            "8.1", "4291c9737043d6dce8b5e834d3d0053c5b33aba9d959e05ed5d4c6965098cf5c", _P
        ),
        SectionReview(
            "8.2", "188a9ab83d9cd3a67906eb0d50a94b54d8407bacaa13bae56c66372bffad274a", _P
        ),
        SectionReview(
            "8.3", "7a816b89d0605cee87731f06645e69b7be0e94300966c8b1a99ca8687207407c", _P
        ),
        SectionReview("9", "09fef1ea378fd565a57f5a14083960f541f8633b013f807c60c075431f775db6", _P),
        # Final provisions: internal document, management, updates, version.
        SectionReview(
            "10", "38bd10db4665b26a8cda85edfa8d6ea6c77d4e1611f3d78329e03c511d38c02f", "internal"
        ),
    ),
)

# Read at call time, so the application has one place to register reviews.
REVIEWED_DISCLOSURES: tuple[DisclosureReview, ...] = (REVIEWED_MANUAL_DISCLOSURE,)


def _public_section(section: IndexedSection, entry: SectionReview | None) -> IndexedSection:
    # Spec 04 Review 04-17-CONF-FU item 1: a withheld section loses its title too, so the
    # title never reaches a descendant's heading_path or its ranking.
    if entry is None or entry.digest != section_digest(section) or entry.disposition == "internal":
        return dataclasses.replace(section, title=None, lines=())
    if entry.disposition == "public":
        return section
    if entry.internal_lines[-1] >= len(section.lines):
        return dataclasses.replace(section, lines=())
    withheld = set(entry.internal_lines)
    lines = tuple(line for position, line in enumerate(section.lines) if position not in withheld)
    return dataclasses.replace(section, lines=lines)


def public_policy_index(
    index: PolicyIndex, reviews: tuple[DisclosureReview, ...] | None = None
) -> PolicyIndex:
    """Return a copy of index with only the reviewed public lines of every section.

    Raises PolicyContentUnreviewedError when no review is bound to the index source.
    Sections keep their ID, parent and heading page; a section left without lines is not
    retrievable. Only a matching public or mixed section keeps its title.
    """
    available = REVIEWED_DISCLOSURES if reviews is None else reviews
    review = next((r for r in available if r.source_sha256 == index.source.sha256), None)
    if review is None:
        raise PolicyContentUnreviewedError
    by_id = {entry.section_id: entry for entry in review.sections}
    sections = tuple(
        _public_section(section, by_id.get(section.section_id)) for section in index.sections
    )
    return PolicyIndex(source=index.source, sections=sections)


# --- Guidance (spec 06 Review 06-05-CONF, item 3) -----------------------------------------

PUBLIC_GUIDANCE: Final[dict[str, str]] = {
    "contact_numbers_differ": (
        "Phone and WhatsApp numbers differ between the returned texts. Give the general"
        " phone listed with the company details and the WhatsApp number listed with the"
        " WhatsApp service. Do not mention the difference or where each number comes from."
    ),
    "email_spelling_differs": (
        "The store e-mail address appears with two different spellings. Do not choose or"
        " correct one. If the customer needs the e-mail, say that the address needs"
        " confirmation and offer the phone or WhatsApp."
    ),
    "installment_rules_differ": (
        "The returned texts differ on credit card installments. Use the ranges of the"
        " installment rules. Other installment conditions need confirmation."
    ),
    "free_shipping_threshold_gap": (
        "The local delivery rule covers orders above and below R$ 500,00 only. For a total"
        " of exactly R$ 500,00, say that this case needs confirmation; do not choose a fee."
    ),
    "withdrawal_needs_channel_and_receipt_date": (
        "The right of withdrawal applies to online purchases and counts from the actual"
        " receipt. Ask for the purchase channel and the actual receipt date when missing."
        " An estimated delivery date is not proof of receipt."
    ),
    "no_holiday_calendar": (
        "The store gives regular hours and closure on holidays, with no holiday calendar or"
        " exception dates. Do not state a specific opening or next business day for an"
        " uncertain date."
    ),
    "no_handoff_integration": (
        "This assistant cannot open tickets, register complaints or forward requests. Point"
        " the customer to the store's public contact channels. Never say that a request was"
        " forwarded and never promise a response time or a contact by the team."
    ),
    "more_sections_matched": (
        "More store information matched than was returned. A more specific search can reach it."
    ),
    "no_matching_section": "No store information matched this search.",
    "no_searchable_terms": "The search had no searchable word.",
    "curated_notes_unavailable": (
        "Differences between these texts were not reviewed. If two texts give different"
        " values, do not choose one; say that the detail needs confirmation."
    ),
}

if set(PUBLIC_GUIDANCE) != set(get_args(NoticeCode.__value__)):  # pragma: no cover
    raise AssertionError("every notice code needs one guidance text")


def public_guidance(result: PolicySearchResult) -> tuple[str, ...]:
    """Guidance texts for the notices of result, in the result order."""
    return tuple(PUBLIC_GUIDANCE[notice.code] for notice in result.notices)
