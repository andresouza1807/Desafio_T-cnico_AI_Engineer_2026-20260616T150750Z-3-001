"""Read-only catalog service: product detail, availability and search (Slice 03, A and B).

Reads a prepared SQLite database through a connection opened by the caller with
connect_readonly, and prices products only through quote_price. The caller owns the
connection: this module never opens, closes or commits it and never changes its
row_factory. Errors carry codes only. See docs/specs/03-catalog.md.
"""

import json
import sqlite3
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Context, Decimal, localcontext
from types import MappingProxyType
from typing import Final, Literal

from emporio_agent.data.parsing import (
    SQLITE_MAX_INTEGER,
    basis_points_to_percent,
    cents_to_decimal,
)
from emporio_agent.data.preparation import SourceFileInfo
from emporio_agent.data.readonly import read_preparation_info
from emporio_agent.data.schema import (
    CATALOG_TEXT_CONFLICT,
    PRODUCT_STATUSES,
    QUALITY_ENTITY_PRODUCT,
    QUALITY_NOTE_CODES,
)
from emporio_agent.services.pricing import CENT, PriceQuote, Promotion, quote_price

type Availability = Literal["available", "out_of_stock", "discontinued", "coming_soon"]
type QualityNoteCode = Literal["catalog_text_conflict", "status_stock_conflict"]
type CategoryResolution = Literal["not_requested", "resolved", "unknown", "ambiguous"]

PAGE_SIZE: Final = 5
MAX_TEXT_LENGTH: Final = 4000
MAX_PRICE_BRL: Final = Decimal("92233720368547758.07")  # SQLITE_MAX_INTEGER cents

_PROVENANCE_TABLES: Final = ("categories", "products", "promotions")
_WITHHELD_FIELDS: Final = ("description", "specs")

# Fixed statements. Only product_id and the constant entity are ever bound.
_PRODUCT_SQL: Final = (
    "SELECT p.product_id, p.category_id, p.name, p.description, p.price_cents,"
    " p.stock_quantity, p.status, p.specs_json, c.category_id, c.name"
    " FROM products AS p JOIN categories AS c ON c.category_id = p.category_id"
    " WHERE p.product_id = ?"
)
_PROMOTIONS_SQL: Final = (
    "SELECT promotion_id, product_id, discount_basis_points, description, is_active"
    " FROM promotions WHERE product_id = ?"
)
_NOTES_SQL: Final = (
    "SELECT entity_id, code FROM data_quality_notes WHERE entity = ? AND entity_id = ?"
)
# Search reads whole tables and filters in Python. No description or specs_json column.
_ALL_CATEGORIES_SQL: Final = "SELECT category_id, name FROM categories ORDER BY category_id"
_ALL_PRODUCTS_SQL: Final = (
    "SELECT p.product_id, p.category_id, p.name, p.price_cents, p.stock_quantity,"
    " p.status, c.category_id, c.name"
    " FROM products AS p JOIN categories AS c ON c.category_id = p.category_id"
    " ORDER BY p.product_id"
)
_ALL_PROMOTIONS_SQL: Final = (
    "SELECT promotion_id, product_id, discount_basis_points, description, is_active"
    " FROM promotions ORDER BY promotion_id"
)
_ALL_NOTES_SQL: Final = (
    "SELECT entity_id, code FROM data_quality_notes WHERE entity = ? ORDER BY entity_id, code"
)

# Private context for budget checks: the caller's decimal context is never used or changed.
_BUDGET_CONTEXT: Final = Context(prec=40)


class CatalogInputError(ValueError):
    """An argument was rejected before any database access.

    Carries the argument name and a code only, never the value. args == (str(self),).
    """

    field: str
    code: str

    def __init__(self, field: str, code: str) -> None:
        self.field = field
        self.code = code
        super().__init__(f"{field}: {code}")


class CatalogDataError(Exception):
    """A catalog query failed (query_failed) or a row read failed validation (invalid_row).

    Carries the reason only: no path, SQL, cell value or original message.
    args == (str(self),).
    """

    reason: str

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class CategoryRef:
    category_id: int
    name: str


@dataclass(frozen=True)
class AppliedPromotion:
    promotion_id: int
    discount_percent: Decimal
    description: str  # campaign text, untrusted data


@dataclass(frozen=True)
class Provenance:
    prepared_at: datetime  # UTC
    sources: tuple[SourceFileInfo, ...]  # categories, products, promotions


@dataclass(frozen=True)
class ProductSummary:
    product_id: int
    name: str
    category: CategoryRef
    list_price: Decimal
    effective_price: Decimal
    promotion: AppliedPromotion | None
    stock_quantity: int
    status: str
    availability: Availability
    is_available: bool
    quality_notes: tuple[QualityNoteCode, ...]  # sorted


@dataclass(frozen=True)
class ProductDetail:
    summary: ProductSummary
    description: str | None  # None when withheld
    specs: Mapping[str, object] | None  # read-only top level; None when withheld
    withheld_fields: tuple[str, ...]  # () or ("description", "specs")
    provenance: Provenance


@dataclass(frozen=True)
class SearchResult:
    products: tuple[ProductSummary, ...]  # at most PAGE_SIZE
    total_matches: int
    offset: int
    next_offset: int | None
    available_only: bool
    max_price_brl: Decimal | None  # quantized echo
    category_resolution: CategoryResolution
    category: CategoryRef | None  # set when resolved
    candidate_categories: tuple[CategoryRef, ...]  # unknown or ambiguous only
    provenance: Provenance


@dataclass(frozen=True)
class _PromotionRow:
    promotion_id: int
    discount_percent: Decimal
    description: str
    is_active: bool


def availability_of(status: str, stock_quantity: int) -> Availability:
    """Available only when active with positive stock (spec G7)."""
    if status not in PRODUCT_STATUSES:
        raise CatalogDataError("invalid_row") from None
    if status == "discontinued":
        return "discontinued"
    if status == "coming_soon":
        return "coming_soon"
    return "available" if stock_quantity > 0 else "out_of_stock"


def _check_product_id(product_id: object) -> int:
    if type(product_id) is not int:
        raise CatalogInputError("product_id", "invalid_type")
    if not 1 <= product_id <= SQLITE_MAX_INTEGER:
        raise CatalogInputError("product_id", "out_of_range")
    return product_id


def _fetch_all(
    connection: sqlite3.Connection, sql: str, parameters: tuple[object, ...]
) -> list[tuple[object, ...]]:
    try:
        cursor = connection.cursor()
        cursor.row_factory = None
        return cursor.execute(sql, parameters).fetchall()
    except sqlite3.ProgrammingError:
        raise
    except sqlite3.Error:
        raise CatalogDataError("query_failed") from None


def _provenance(connection: sqlite3.Connection) -> Provenance:
    info = read_preparation_info(connection)
    return Provenance(
        prepared_at=info.prepared_at,
        sources=tuple(source for source in info.sources if source.table_name in _PROVENANCE_TABLES),
    )


# Row checks raise TypeError or ValueError with fixed text; the public functions turn
# them into CatalogDataError("invalid_row"). No message ever contains a cell value.


def _integer(value: object) -> int:
    if type(value) is not int:
        raise TypeError("integer cell expected")
    return value


def _text(value: object) -> str:
    if type(value) is not str:
        raise TypeError("text cell expected")
    return value


def _stock(value: object) -> int:
    if _integer(value) < 0:
        raise ValueError("negative stock")
    return value


def _flag(value: object) -> bool:
    if _integer(value) not in (0, 1):
        raise ValueError("flag cell expected")
    return value == 1


def _promotion(row: tuple[object, ...]) -> _PromotionRow:
    promotion_id, product_id, basis_points, description, is_active = row
    _integer(product_id)
    return _PromotionRow(
        promotion_id=_integer(promotion_id),
        discount_percent=basis_points_to_percent(_integer(basis_points)),
        description=_text(description),
        is_active=_flag(is_active),
    )


def _note(row: tuple[object, ...]) -> QualityNoteCode:
    entity_id, code = row
    _integer(entity_id)
    if code not in QUALITY_NOTE_CODES:
        raise ValueError("unknown quality note")
    return code


def _applied_promotion(
    quote: PriceQuote, promotions: Sequence[_PromotionRow]
) -> AppliedPromotion | None:
    """Identify the active record whose percentage quote_price applied; lowest ID on a tie."""
    if quote.discount_source != "promotion":
        return None
    matching = [
        promotion
        for promotion in promotions
        if promotion.is_active and promotion.discount_percent == quote.discount_percent
    ]
    if not matching:
        raise ValueError("no promotion matches the quote")
    chosen = min(matching, key=lambda promotion: promotion.promotion_id)
    return AppliedPromotion(chosen.promotion_id, chosen.discount_percent, chosen.description)


def _specs(value: object) -> Mapping[str, object]:
    parsed = json.loads(_text(value))
    if not isinstance(parsed, dict):
        raise ValueError("specs must be a JSON object")
    return MappingProxyType(parsed)


def _summary(
    product_row: tuple[object, ...],
    promotions: Sequence[_PromotionRow],
    notes: tuple[QualityNoteCode, ...],
) -> ProductSummary:
    """Validate the used product fields and price the product through quote_price.

    product_row: product_id, category_id, name, price_cents, stock_quantity, status,
    joined category_id, joined category name. Shared by get_product and search_products.
    """
    (
        product_id,
        category_id,
        name,
        price_cents,
        stock_quantity,
        status,
        category_ref_id,
        category_name,
    ) = product_row
    _integer(category_id)
    stock = _stock(stock_quantity)
    availability = availability_of(status, stock)
    category = CategoryRef(_integer(category_ref_id), _text(category_name))

    # Active and inactive promotions: quote_price validates both and applies the largest.
    quote = quote_price(
        cents_to_decimal(_integer(price_cents)),
        payment_method="regular",
        promotions=[Promotion(item.discount_percent, item.is_active) for item in promotions],
    )
    return ProductSummary(
        product_id=_integer(product_id),
        name=_text(name),
        category=category,
        list_price=quote.list_price,
        effective_price=quote.final_price,
        promotion=_applied_promotion(quote, promotions),
        stock_quantity=stock,
        status=status,
        availability=availability,
        is_available=availability == "available",
        quality_notes=notes,
    )


def _detail(
    product_row: tuple[object, ...],
    promotion_rows: list[tuple[object, ...]],
    note_rows: list[tuple[object, ...]],
    provenance: Provenance,
) -> ProductDetail:
    (
        product_id,
        category_id,
        name,
        description,
        price_cents,
        stock_quantity,
        status,
        specs_json,
        category_ref_id,
        category_name,
    ) = product_row
    promotions = [_promotion(row) for row in promotion_rows]
    notes = tuple(sorted(_note(row) for row in note_rows))
    summary = _summary(
        (
            product_id,
            category_id,
            name,
            price_cents,
            stock_quantity,
            status,
            category_ref_id,
            category_name,
        ),
        promotions,
        notes,
    )
    if CATALOG_TEXT_CONFLICT in notes:
        # Projection rule only: the stored text is neither read nor changed.
        return ProductDetail(summary, None, None, _WITHHELD_FIELDS, provenance)
    return ProductDetail(summary, _text(description), _specs(specs_json), (), provenance)


def get_product(connection: sqlite3.Connection, product_id: int) -> ProductDetail | None:
    """Return one product with availability, effective price, notes and provenance.

    None when no product has this ID. Raises CatalogInputError for an invalid ID
    before any query, CatalogDataError("query_failed" or "invalid_row") for database
    problems, and lets sqlite3.ProgrammingError and DatabaseUnavailableError propagate.
    """
    product_id = _check_product_id(product_id)
    product_rows = _fetch_all(connection, _PRODUCT_SQL, (product_id,))
    if not product_rows:
        return None
    promotion_rows = _fetch_all(connection, _PROMOTIONS_SQL, (product_id,))
    note_rows = _fetch_all(connection, _NOTES_SQL, (QUALITY_ENTITY_PRODUCT, product_id))
    provenance = _provenance(connection)
    try:
        return _detail(product_rows[0], promotion_rows, note_rows, provenance)
    except TypeError, ValueError:
        raise CatalogDataError("invalid_row") from None


# --- Search (slice B) -----------------------------------------------------------------


def normalize_search_text(text: str) -> str:
    """NFKD, drop combining marks (category Mn), casefold, compact whitespace (spec G1)."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
    return " ".join(stripped.casefold().split())


def _check_text(field: str, value: object) -> str | None:
    """None means no filter. Length in raw code points, checked before normalization."""
    if value is None:
        return None
    if type(value) is not str:
        raise CatalogInputError(field, "invalid_type")
    if len(value) > MAX_TEXT_LENGTH:
        raise CatalogInputError(field, "too_long")
    if not normalize_search_text(value):
        raise CatalogInputError(field, "blank")
    return value


def _check_budget(value: object) -> Decimal | None:
    """Exact Decimal in cents from 0 to MAX_PRICE_BRL, never rounded (spec G4)."""
    if value is None:
        return None
    if type(value) is not Decimal:
        raise CatalogInputError("max_price_brl", "invalid_type")
    if not value.is_finite():
        raise CatalogInputError("max_price_brl", "not_finite")
    with localcontext(_BUDGET_CONTEXT):
        if value < 0:
            raise CatalogInputError("max_price_brl", "negative")
        if value > MAX_PRICE_BRL:
            raise CatalogInputError("max_price_brl", "out_of_range")
        quantized = value.quantize(CENT)
        if quantized != value:
            raise CatalogInputError("max_price_brl", "excess_precision")
    return quantized.copy_abs()  # 0.00, never -0.00


def _check_available_only(value: object) -> bool:
    if type(value) is not bool:
        raise CatalogInputError("available_only", "invalid_type")
    return value


def _check_offset(value: object) -> int:
    if type(value) is not int:
        raise CatalogInputError("offset", "invalid_type")
    if value < 0:
        raise CatalogInputError("offset", "out_of_range")
    return value


def _all_summaries(
    product_rows: list[tuple[object, ...]],
    promotion_rows: list[tuple[object, ...]],
    note_rows: list[tuple[object, ...]],
) -> list[ProductSummary]:
    """Validate every promotion, note and product row read, then summarize every product."""
    promotions: dict[int, list[_PromotionRow]] = {}
    for row in promotion_rows:
        promotions.setdefault(_integer(row[1]), []).append(_promotion(row))
    notes: dict[int, list[QualityNoteCode]] = {}
    for row in note_rows:
        notes.setdefault(_integer(row[0]), []).append(_note(row))
    summaries = []
    for row in product_rows:
        product_id = _integer(row[0])
        product_notes = tuple(sorted(notes.get(product_id, ())))
        summaries.append(_summary(row, promotions.get(product_id, ()), product_notes))
    return summaries


def _resolve_category(
    category: str | None, categories: Sequence[CategoryRef]
) -> tuple[CategoryResolution, CategoryRef | None, tuple[CategoryRef, ...]]:
    """Exact name first, then normalized name (spec G2). Candidates in category_id order."""
    if category is None:
        return "not_requested", None, ()
    ordered = sorted(categories, key=lambda item: item.category_id)
    exact = [item for item in ordered if item.name == category]
    if len(exact) == 1:
        return "resolved", exact[0], ()
    wanted = normalize_search_text(category)
    similar = [item for item in ordered if normalize_search_text(item.name) == wanted]
    if len(similar) == 1:
        return "resolved", similar[0], ()
    if similar:
        return "ambiguous", None, tuple(similar)
    return "unknown", None, tuple(ordered)


def search_products(
    connection: sqlite3.Connection,
    *,
    query: str | None = None,
    category: str | None = None,
    max_price_brl: Decimal | None = None,
    available_only: bool = True,
    offset: int = 0,
) -> SearchResult:
    """Filter by name tokens, category, budget and availability; sort; one page of five.

    Arguments are validated in parameter order before any query (CatalogInputError).
    Every category, product, promotion and product note is read and validated before
    filtering (CatalogDataError("invalid_row")); description and specs_json are never
    read. Effective prices come only from quote_price in regular mode; PIX never
    takes part in the budget.
    """
    query = _check_text("query", query)
    category = _check_text("category", category)
    budget = _check_budget(max_price_brl)
    available_only = _check_available_only(available_only)
    offset = _check_offset(offset)

    provenance = _provenance(connection)
    category_rows = _fetch_all(connection, _ALL_CATEGORIES_SQL, ())
    product_rows = _fetch_all(connection, _ALL_PRODUCTS_SQL, ())
    promotion_rows = _fetch_all(connection, _ALL_PROMOTIONS_SQL, ())
    note_rows = _fetch_all(connection, _ALL_NOTES_SQL, (QUALITY_ENTITY_PRODUCT,))
    try:
        categories = [CategoryRef(_integer(row[0]), _text(row[1])) for row in category_rows]
        summaries = _all_summaries(product_rows, promotion_rows, note_rows)
    except TypeError, ValueError:
        raise CatalogDataError("invalid_row") from None

    resolution, resolved, candidates = _resolve_category(category, categories)
    if resolution in ("unknown", "ambiguous"):
        # The filter is kept: nothing matches an unresolved category (spec G2, A3).
        return SearchResult(
            products=(),
            total_matches=0,
            offset=offset,
            next_offset=None,
            available_only=available_only,
            max_price_brl=budget,
            category_resolution=resolution,
            category=None,
            candidate_categories=candidates,
            provenance=provenance,
        )

    tokens = normalize_search_text(query).split() if query is not None else []
    matches = [
        summary
        for summary in summaries
        if (summary.is_available or not available_only)
        and (resolved is None or summary.category.category_id == resolved.category_id)
        and all(token in normalize_search_text(summary.name) for token in tokens)
        and (budget is None or summary.effective_price <= budget)
    ]
    matches.sort(key=lambda summary: (summary.effective_price, summary.product_id))
    end = offset + PAGE_SIZE
    return SearchResult(
        products=tuple(matches[offset:end]),
        total_matches=len(matches),
        offset=offset,
        next_offset=end if end < len(matches) else None,
        available_only=available_only,
        max_price_brl=budget,
        category_resolution=resolution,
        category=resolved,
        candidate_categories=(),
        provenance=provenance,
    )
