# Slice 02, task T2 contract: SQLite schema and constraints

Status: contract for task T2, written before tests, 2026-11-02.
Source of truth: `docs/specs/02-data-preparation.md` (revision R1, approved) and `docs/plans/02-data-preparation.md` (T2).
This document refines only the module layout and constants of `emporio_agent.data.schema`. It adds no business rule. Where it differs from R1, R1 wins and the difference is a defect of this document.

## Scope

In scope: `src/emporio_agent/data/schema.py` (constants, column metadata, DDL text) and `tests/integration/test_data_schema.py`.

Out of scope: CSV reading, preparation, transactions, PRAGMA settings, read-only connection, CLI, agent, pricing changes, availability, discounts, order totals, catalog and order authorization.

## Module rules

- `schema.py` contains only immutable constants, frozen dataclasses and one `StrEnum`.
- Import of `schema.py` opens no connection, creates no file, executes no SQL, reads no environment variable and changes no global state.
- `schema.py` imports only the standard library (`dataclasses`, `enum`, `typing`). It does not import `sqlite3`.
- DDL is a tuple of individual `CREATE TABLE` statements. Each statement is executable alone with `Connection.execute`. The tuple contains no `BEGIN`, `COMMIT`, `ROLLBACK` or `PRAGMA`. Transaction control and `PRAGMA foreign_keys = ON` belong to the future preparation code (T5).
- Statements do not use `IF NOT EXISTS`. Preparation always builds a new file; a second execution on the same database must fail, not pass silently.
- `SQLITE_MAX_INTEGER` stays defined once, in `emporio_agent.data.parsing` (T1). `schema.py` does not need it: the `INTEGER` storage class already limits values to 64-bit signed integers. No reorganization is done in T2.

## Public constants

| Name | Type | Value |
| --- | --- | --- |
| `SCHEMA_VERSION` | `Final[int]` | `1` |
| `SOURCE_FILE_PREFIX` | `Final[str]` | `"desafio_tecnico_ai_eng - "` |
| `TABLE_ORDER` | `Final[tuple[str, ...]]` | `("categories", "customers", "products", "promotions", "orders", "order_items")` — import order, parents first |
| `AUXILIARY_TABLES` | `Final[tuple[str, ...]]` | `("preparation_metadata", "source_files", "data_quality_notes")` |
| `PRODUCT_STATUSES` | `Final[frozenset[str]]` | `active`, `discontinued`, `coming_soon` |
| `ORDER_STATUSES` | `Final[frozenset[str]]` | `pending`, `confirmed`, `shipped`, `delivered`, `cancelled` |
| `PAYMENT_METHODS` | `Final[frozenset[str]]` | `pix`, `debit`, `boleto`, `credit_1x` … `credit_12x` (15 values) |
| `QUALITY_ENTITY_PRODUCT` | `Final[str]` | `"product"` |
| `STATUS_STOCK_CONFLICT` | `Final[str]` | `"status_stock_conflict"` |
| `CATALOG_TEXT_CONFLICT` | `Final[str]` | `"catalog_text_conflict"` |
| `QUALITY_NOTE_CODES` | `Final[frozenset[str]]` | the two codes above |
| `KNOWN_CATALOG_TEXT_CONFLICTS` | `Final[frozenset[int]]` | `{135, 137, 139, 140, 142, 144}` (manual review of 2026-11-02) |
| `TABLES` | `Final[tuple[TableSpec, ...]]` | column metadata of the six domain tables, in `TABLE_ORDER` |
| `DDL_STATEMENTS` | `Final[tuple[str, ...]]` | nine `CREATE TABLE` statements: six domain tables in `TABLE_ORDER`, then `AUXILIARY_TABLES` in order |

File name of a source table: `f"{SOURCE_FILE_PREFIX}{table}.csv"`. Prefix override stays a parameter of the future `prepare_database`.

## Column metadata

```python
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
    source_name: str  # CSV header name
    column_name: str  # SQLite column name
    kind: FieldKind  # grammar from spec "Field grammars"
    allowed: frozenset[str] | None = None  # allowlist, only for FieldKind.ENUM


@dataclass(frozen=True)
class Reference:
    column_name: str  # referencing column
    parent_table: str  # parent key is the parent's single primary key column


@dataclass(frozen=True)
class TableSpec:
    name: str
    columns: tuple[ColumnSpec, ...]  # header-definition order = validation order
    primary_key: tuple[str, ...]  # column names
    unique: tuple[str, ...] = ()  # extra single-column unique keys
    references: tuple[Reference, ...] = ()
```

Content of `TABLES` (R1 table "Table schema"):

| Table | Columns (`source_name` → `column_name`: kind) | Primary key | Unique | References |
| --- | --- | --- | --- | --- |
| categories | category_id: id; name: text; description: text | category_id | name | — |
| customers | customer_id: id; name, phone, email, city: text | customer_id | — | — |
| products | product_id: id; category_id: id; name, description: text; price_brl → price_cents: money; stock_quantity: count; status: enum `PRODUCT_STATUSES`; specs → specs_json: json_object; created_at: date | product_id | — | category_id → categories |
| promotions | promotion_id: id; product_id: id; discount_percent → discount_basis_points: percent; description: text; is_active: flag | promotion_id | — | product_id → products |
| orders | order_id: id; customer_id: id; order_date: date; status: enum `ORDER_STATUSES`; total_brl → total_cents: money; payment_method: enum `PAYMENT_METHODS`; tracking_code: optional_text; estimated_delivery: optional_date; notes: optional_text | order_id | — | customer_id → customers |
| order_items | order_id: id; product_id: id; quantity: quantity | (order_id, product_id) | — | order_id → orders; product_id → products |

Column order inside each table is the R1 table order. Database column order equals metadata column order. The future importer maps header names to `source_name`; CSV column order stays free (R1 "Header handling").

## Database contract

All nine tables are `STRICT`. Every column listed as "Null: no" in R1 is declared `NOT NULL`. Foreign keys use default `NO ACTION` actions and are not deferred. They are enforced only on a connection with `PRAGMA foreign_keys = ON`.

Domain constraints (R1 "Constraint" column):

- Positive IDs: `CHECK (<id> > 0)` on each single-column primary key.
- `categories.name`: `UNIQUE`, default `BINARY` collation (exact text).
- `customers.email`: no `UNIQUE`.
- Required text: `CHECK (length(trim(<column>)) > 0)`.
- Optional text: `CHECK (<column> IS NULL OR length(trim(<column>)) > 0)`.
- Money (`price_cents`, `total_cents`) and `stock_quantity`: `CHECK (<column> >= 0)`.
- `discount_basis_points`: `CHECK (discount_basis_points BETWEEN 0 AND 10000)`.
- `order_items.quantity`: `CHECK (quantity >= 1)`.
- `is_active`: `CHECK (is_active IN (0, 1))`.
- Enums: `CHECK (<column> IN (...))` with the allowlist literals.
- Dates: `CHECK (date(<column>) IS <column>)`; optional date adds `<column> IS NULL OR`.
- `specs_json`: `CHECK (CASE WHEN json_valid(specs_json) THEN json_type(specs_json) = 'object' ELSE 0 END)`. The `CASE` is needed: `json_type` raises `malformed JSON` (an `OperationalError`, not a constraint failure) on invalid text, and SQL `AND` does not guarantee evaluation order.

Auxiliary tables (R1 "Metadata tables"):

| Table | Columns | Constraints |
| --- | --- | --- |
| preparation_metadata | key TEXT PRIMARY KEY NOT NULL, value TEXT NOT NULL | none beyond types and keys |
| source_files | table_name TEXT PRIMARY KEY NOT NULL, file_name TEXT NOT NULL, sha256 TEXT NOT NULL, size_bytes INTEGER NOT NULL, row_count INTEGER NOT NULL | `table_name IN` the six domain tables; `file_name` non-blank and without `/` or `\`; `sha256` length 64 and only `0-9a-f`; `size_bytes >= 0`; `row_count >= 0` |
| data_quality_notes | entity TEXT NOT NULL, entity_id INTEGER NOT NULL, code TEXT NOT NULL, PRIMARY KEY (entity, entity_id, code) | `entity IN ('product')`; `entity_id > 0`; `code IN` `QUALITY_NOTE_CODES` |

The auxiliary constraints restate R1 content descriptions ("one row per source file", "base name only", "64 lowercase hex", entity `product`, the two rule codes). `data_quality_notes.entity_id` has no foreign key, because `entity` names the target table. `preparation_metadata.key` has no allowlist; the three keys are written by preparation.

## Guarantee boundary

Guaranteed by SQLite 3.50.4 with this DDL (tested in T2):

- Storage class per column (`STRICT`). A value that SQLite cannot convert losslessly is rejected with `sqlite3.IntegrityError` (`datatype mismatch` for a rowid key, `cannot store <type> value in <type> column` otherwise; message corrected after the first GREEN run, see validation report).
- `NOT NULL`, primary keys, composite key of `order_items`, `UNIQUE` on `categories.name`.
- Foreign keys, only when the connection enables `PRAGMA foreign_keys = ON`.
- Every `CHECK` listed above, with the SQL semantics stated there.
- DDL and DML inside a transaction controlled by the caller are undone by `ROLLBACK`.

Not guaranteed by SQL; the parsers (T1) and the future importer (T3–T5) own these rules:

- Source grammar. `STRICT` converts losslessly: text `'5'` and real `6.0` are stored as INTEGER 5 and 6; an INTEGER bound to a TEXT column is stored as text. Leading zeros, signs and whitespace in source text are rejected only by the parsers.
- `NULL` bound to a single-column `INTEGER PRIMARY KEY` is replaced by a new rowid (documented SQLite rowid-alias behavior), even with `NOT NULL`. The importer never binds `NULL` there.
- Blank text: SQL `trim` removes only spaces. Text made only of tabs, line breaks or other Unicode whitespace passes the `CHECK`; `parse_text` and `parse_optional_text` reject it.
- JSON: `json_valid` accepts duplicate object keys. `parse_json_object` rejects them.
- Dates: `date(x) IS x` accepts year `0000`, which `datetime.date.fromisoformat` rejects.
- Values above `SQLITE_MAX_INTEGER` cannot be bound at all (`OverflowError` in the `sqlite3` module, before SQLite). The `money_out_of_range` and `out_of_range` codes come from the parsers.
- Error codes, file, line and column of `DataPreparationError`. SQLite raises only `sqlite3.IntegrityError` or `sqlite3.OperationalError`.
- Foreign keys when `foreign_keys` is off (SQLite default).
- Semantic observations (status with stock, historical totals, text conflicts, campaign names). The schema stores them unchanged and decides nothing.

## Plan cases touched

T2 provides the schema used by D01, D09 and D50. Those cases need `prepare_database` and `connect_readonly` and stay open. T2 tests have no D ID; they are named `test_schema_*` in `tests/integration/test_data_schema.py`.
