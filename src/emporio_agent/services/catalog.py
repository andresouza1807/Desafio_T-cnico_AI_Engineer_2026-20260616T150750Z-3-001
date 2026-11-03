"""Read-only catalog service: product detail and availability (Slice 03, part A).

Reads a prepared SQLite database through a connection opened by the caller with
connect_readonly, and prices products only through quote_price. The caller owns the
connection: this module never opens, closes or commits it and never changes its
row_factory. Errors carry codes only. See docs/specs/03-catalog.md.
"""

import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
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
from emporio_agent.services.pricing import PriceQuote, Promotion, quote_price

type Availability = Literal["available", "out_of_stock", "discontinued", "coming_soon"]
type QualityNoteCode = Literal["catalog_text_conflict", "status_stock_conflict"]

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


# Row checks raise TypeError or ValueError with fixed text; get_product turns them into
# CatalogDataError("invalid_row"). No message ever contains a cell value.


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
    _integer(category_id)
    stock = _stock(stock_quantity)
    availability = availability_of(status, stock)
    category = CategoryRef(_integer(category_ref_id), _text(category_name))
    promotions = [_promotion(row) for row in promotion_rows]
    notes = tuple(sorted(_note(row) for row in note_rows))

    # Active and inactive promotions: quote_price validates both and applies the largest.
    quote = quote_price(
        cents_to_decimal(_integer(price_cents)),
        payment_method="regular",
        promotions=[Promotion(item.discount_percent, item.is_active) for item in promotions],
    )
    summary = ProductSummary(
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
    info = read_preparation_info(connection)
    provenance = Provenance(
        prepared_at=info.prepared_at,
        sources=tuple(source for source in info.sources if source.table_name in _PROVENANCE_TABLES),
    )
    try:
        return _detail(product_rows[0], promotion_rows, note_rows, provenance)
    except TypeError, ValueError:
        raise CatalogDataError("invalid_row") from None
