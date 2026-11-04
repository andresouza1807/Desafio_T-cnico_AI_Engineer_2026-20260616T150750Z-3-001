"""Deterministic output check of a final answer (spec 07 Review 07-15-CONF item 7b).

A narrow backstop before an answer is committed and shown. It finds fixed internal
markers only: registered tool names, policy notice codes, quality note codes, internal
payload field names and tool protocol statuses and error codes; internal reference syntax
and the phrase "front matter"; a line that starts with "Fonte:", "Fontes:", "Source:" or
"Sources:"; a numbered section citation, also with Markdown emphasis before the number
(Review 07-16-CONF-FU); eight consecutive words of the session instructions. It does not
detect paraphrase, translation or provenance phrases without a fixed marker; those depend
on the data boundary (policy_disclosure) and on the instructions.

Pure function: no logging, no network; the answer text is never stored or returned.
"""

import re
import unicodedata
from typing import Final, Literal, get_args

from emporio_agent.services.policies import NoticeCode

type MarkerKind = Literal[
    "identifier", "reference", "section_citation", "source_label", "instructions"
]

_TOOL_NAMES: Final = (
    "search_products",
    "get_product",
    "quote_product",
    "search_policies",
    "get_order",
)
_INTERNAL_FIELDS: Final = (
    "quality_notes",
    "catalog_text_conflict",
    "status_stock_conflict",
    "withheld_fields",
    "front_matter",
    "selected_by",
    "omitted_section_ids",
    "section_id",
    "heading_path",
    "order_not_accessible",
    # Installment simulation field and statuses (Spec 07 Review 07-18-INSTALLMENTS).
    "minimum_installment",
    "below_minimum",
    "needs_confirmation",
)
# Envelope statuses and error codes used only by the tool protocol (spec 07 Review
# 07-16-CONF-FU item 1).
_TOOL_PROTOCOL_CODES: Final = (
    "not_found",
    "invalid_input",
    "internal_error",
    "data_unavailable",
    "product_not_found",
    "unknown_tool",
    "invalid_json",
    "arguments_not_object",
    "arguments_too_large",
    "duplicate_key",
    "invalid_format",
    "invalid_type",
    "out_of_range",
    "unexpected_field",
    "not_available_for_pix",
)
IDENTIFIERS: Final = frozenset(
    (*_TOOL_NAMES, *get_args(NoticeCode.__value__), *_INTERNAL_FIELDS, *_TOOL_PROTOCOL_CODES)
)
INSTRUCTION_WINDOW: Final = 8  # consecutive words

_SNAKE = re.compile(r"[A-Za-z0-9]+(?:_[A-Za-z0-9]+)+")
_REFERENCE = re.compile(r"\b(?:policy|product):\S")
_SECTION = re.compile(r"\bse[çc](?:[ãa]o|[õo]es)\s*[*_]*\s*\d+(?:\.\d+)*\b", re.IGNORECASE)
_SOURCE_LABEL = re.compile(
    r"^[\s*_>#-]*(?:fontes?|sources?)\s*[*_]*\s*:", re.IGNORECASE | re.MULTILINE
)
_FRONT_MATTER = re.compile(r"\bfront[\s_-]*matter\b", re.IGNORECASE)
_WORD = re.compile(r"[0-9a-z]+")


def _words(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", text.casefold())
    plain = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return _WORD.findall(plain)


def _windows(words: list[str]) -> set[tuple[str, ...]]:
    size = INSTRUCTION_WINDOW
    return {tuple(words[i : i + size]) for i in range(len(words) - size + 1)}


def internal_marker(text: str, instructions: str) -> MarkerKind | None:
    """Kind of the first internal marker found in text, or None."""
    if any(token in IDENTIFIERS for token in _SNAKE.findall(text)):
        return "identifier"
    if _REFERENCE.search(text) is not None:
        return "reference"
    if _SOURCE_LABEL.search(text) is not None:
        return "source_label"
    if _SECTION.search(text) is not None:
        return "section_citation"
    if _FRONT_MATTER.search(text) is not None:
        return "reference"
    if not _windows(_words(text)).isdisjoint(_windows(_words(instructions))):
        return "instructions"
    return None
