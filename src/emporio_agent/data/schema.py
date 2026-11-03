"""SQLite schema for the prepared store database.

Constants only. Importing this module opens no connection, creates no file and runs no
SQL. DDL_STATEMENTS holds individual CREATE TABLE statements; the caller owns the
connection, PRAGMA settings and the transaction.

SQL constraints are a second line of defense. The parsers and the importer own the
source grammar and the error codes; see docs/specs/02-03-data-schema.md, section
"Guarantee boundary".
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

SCHEMA_VERSION: Final = 1
SOURCE_FILE_PREFIX: Final = "desafio_tecnico_ai_eng - "
TABLE_ORDER: Final = ("categories", "customers", "products", "promotions", "orders", "order_items")
AUXILIARY_TABLES: Final = ("preparation_metadata", "source_files", "data_quality_notes")

PRODUCT_STATUSES: Final = frozenset({"active", "discontinued", "coming_soon"})
ORDER_STATUSES: Final = frozenset({"pending", "confirmed", "shipped", "delivered", "cancelled"})
PAYMENT_METHODS: Final = frozenset(
    {"pix", "debit", "boleto"} | {f"credit_{count}x" for count in range(1, 13)}
)

QUALITY_ENTITY_PRODUCT: Final = "product"
STATUS_STOCK_CONFLICT: Final = "status_stock_conflict"
CATALOG_TEXT_CONFLICT: Final = "catalog_text_conflict"
QUALITY_NOTE_CODES: Final = frozenset({STATUS_STOCK_CONFLICT, CATALOG_TEXT_CONFLICT})

# Manual review of 2026-11-02: name, description and specs disagree.
KNOWN_CATALOG_TEXT_CONFLICTS: Final = frozenset({135, 137, 139, 140, 142, 144})


class FieldKind(StrEnum):
    ID = "id"
    COUNT = "count"
    QUANTITY = "quantity"
    MONEY = "money"
    PERCENT = "percent"
    DATE = "date"
    OPTIONAL_DATE = "optional_date"
    FLAG = "flag"
    ENUM = "enum"
    JSON_OBJECT = "json_object"
    TEXT = "text"
    OPTIONAL_TEXT = "optional_text"


@dataclass(frozen=True)
class ColumnSpec:
    source_name: str
    column_name: str
    kind: FieldKind
    allowed: frozenset[str] | None = None


@dataclass(frozen=True)
class Reference:
    column_name: str
    parent_table: str


@dataclass(frozen=True)
class TableSpec:
    name: str
    columns: tuple[ColumnSpec, ...]
    primary_key: tuple[str, ...]
    unique: tuple[str, ...] = ()
    references: tuple[Reference, ...] = ()


def _same(name: str, kind: FieldKind, allowed: frozenset[str] | None = None) -> ColumnSpec:
    return ColumnSpec(name, name, kind, allowed)


TABLES: Final = (
    TableSpec(
        "categories",
        (
            _same("category_id", FieldKind.ID),
            _same("name", FieldKind.TEXT),
            _same("description", FieldKind.TEXT),
        ),
        primary_key=("category_id",),
        unique=("name",),
    ),
    TableSpec(
        "customers",
        (
            _same("customer_id", FieldKind.ID),
            _same("name", FieldKind.TEXT),
            _same("phone", FieldKind.TEXT),
            _same("email", FieldKind.TEXT),
            _same("city", FieldKind.TEXT),
        ),
        primary_key=("customer_id",),
    ),
    TableSpec(
        "products",
        (
            _same("product_id", FieldKind.ID),
            _same("category_id", FieldKind.ID),
            _same("name", FieldKind.TEXT),
            _same("description", FieldKind.TEXT),
            ColumnSpec("price_brl", "price_cents", FieldKind.MONEY),
            _same("stock_quantity", FieldKind.COUNT),
            _same("status", FieldKind.ENUM, PRODUCT_STATUSES),
            ColumnSpec("specs", "specs_json", FieldKind.JSON_OBJECT),
            _same("created_at", FieldKind.DATE),
        ),
        primary_key=("product_id",),
        references=(Reference("category_id", "categories"),),
    ),
    TableSpec(
        "promotions",
        (
            _same("promotion_id", FieldKind.ID),
            _same("product_id", FieldKind.ID),
            ColumnSpec("discount_percent", "discount_basis_points", FieldKind.PERCENT),
            _same("description", FieldKind.TEXT),
            _same("is_active", FieldKind.FLAG),
        ),
        primary_key=("promotion_id",),
        references=(Reference("product_id", "products"),),
    ),
    TableSpec(
        "orders",
        (
            _same("order_id", FieldKind.ID),
            _same("customer_id", FieldKind.ID),
            _same("order_date", FieldKind.DATE),
            _same("status", FieldKind.ENUM, ORDER_STATUSES),
            ColumnSpec("total_brl", "total_cents", FieldKind.MONEY),
            _same("payment_method", FieldKind.ENUM, PAYMENT_METHODS),
            _same("tracking_code", FieldKind.OPTIONAL_TEXT),
            _same("estimated_delivery", FieldKind.OPTIONAL_DATE),
            _same("notes", FieldKind.OPTIONAL_TEXT),
        ),
        primary_key=("order_id",),
        references=(Reference("customer_id", "customers"),),
    ),
    TableSpec(
        "order_items",
        (
            _same("order_id", FieldKind.ID),
            _same("product_id", FieldKind.ID),
            _same("quantity", FieldKind.QUANTITY),
        ),
        primary_key=("order_id", "product_id"),
        references=(Reference("order_id", "orders"), Reference("product_id", "products")),
    ),
)


def _sql_list(values: frozenset[str] | tuple[str, ...]) -> str:
    # Literals come from the constants above, never from input.
    return ", ".join(f"'{value}'" for value in sorted(values))


def _required_text(column: str) -> str:
    return f"{column} TEXT NOT NULL CHECK (length(trim({column})) > 0)"


def _optional_text(column: str) -> str:
    return f"{column} TEXT CHECK ({column} IS NULL OR length(trim({column})) > 0)"


def _date(column: str) -> str:
    return f"{column} TEXT NOT NULL CHECK (date({column}) IS {column})"


DDL_STATEMENTS: Final = (
    f"""CREATE TABLE categories (
    category_id INTEGER PRIMARY KEY NOT NULL CHECK (category_id > 0),
    {_required_text("name")} UNIQUE,
    {_required_text("description")}
) STRICT""",
    f"""CREATE TABLE customers (
    customer_id INTEGER PRIMARY KEY NOT NULL CHECK (customer_id > 0),
    {_required_text("name")},
    {_required_text("phone")},
    {_required_text("email")},
    {_required_text("city")}
) STRICT""",
    f"""CREATE TABLE products (
    product_id INTEGER PRIMARY KEY NOT NULL CHECK (product_id > 0),
    category_id INTEGER NOT NULL REFERENCES categories (category_id),
    {_required_text("name")},
    {_required_text("description")},
    price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
    stock_quantity INTEGER NOT NULL CHECK (stock_quantity >= 0),
    status TEXT NOT NULL CHECK (status IN ({_sql_list(PRODUCT_STATUSES)})),
    specs_json TEXT NOT NULL CHECK (
        CASE WHEN json_valid(specs_json) THEN json_type(specs_json) = 'object' ELSE 0 END
    ),
    {_date("created_at")}
) STRICT""",
    f"""CREATE TABLE promotions (
    promotion_id INTEGER PRIMARY KEY NOT NULL CHECK (promotion_id > 0),
    product_id INTEGER NOT NULL REFERENCES products (product_id),
    discount_basis_points INTEGER NOT NULL CHECK (discount_basis_points BETWEEN 0 AND 10000),
    {_required_text("description")},
    is_active INTEGER NOT NULL CHECK (is_active IN (0, 1))
) STRICT""",
    f"""CREATE TABLE orders (
    order_id INTEGER PRIMARY KEY NOT NULL CHECK (order_id > 0),
    customer_id INTEGER NOT NULL REFERENCES customers (customer_id),
    {_date("order_date")},
    status TEXT NOT NULL CHECK (status IN ({_sql_list(ORDER_STATUSES)})),
    total_cents INTEGER NOT NULL CHECK (total_cents >= 0),
    payment_method TEXT NOT NULL CHECK (payment_method IN ({_sql_list(PAYMENT_METHODS)})),
    {_optional_text("tracking_code")},
    estimated_delivery TEXT CHECK (
        estimated_delivery IS NULL OR date(estimated_delivery) IS estimated_delivery
    ),
    {_optional_text("notes")}
) STRICT""",
    """CREATE TABLE order_items (
    order_id INTEGER NOT NULL REFERENCES orders (order_id),
    product_id INTEGER NOT NULL REFERENCES products (product_id),
    quantity INTEGER NOT NULL CHECK (quantity >= 1),
    PRIMARY KEY (order_id, product_id)
) STRICT""",
    """CREATE TABLE preparation_metadata (
    key TEXT PRIMARY KEY NOT NULL,
    value TEXT NOT NULL
) STRICT""",
    f"""CREATE TABLE source_files (
    table_name TEXT PRIMARY KEY NOT NULL CHECK (table_name IN ({_sql_list(TABLE_ORDER)})),
    file_name TEXT NOT NULL CHECK (
        length(trim(file_name)) > 0
        AND instr(file_name, '/') = 0
        AND instr(file_name, '\\') = 0
    ),
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    row_count INTEGER NOT NULL CHECK (row_count >= 0)
) STRICT""",
    f"""CREATE TABLE data_quality_notes (
    entity TEXT NOT NULL CHECK (entity IN ('{QUALITY_ENTITY_PRODUCT}')),
    entity_id INTEGER NOT NULL CHECK (entity_id > 0),
    code TEXT NOT NULL CHECK (code IN ({_sql_list(QUALITY_NOTE_CODES)})),
    PRIMARY KEY (entity, entity_id, code)
) STRICT""",
)
