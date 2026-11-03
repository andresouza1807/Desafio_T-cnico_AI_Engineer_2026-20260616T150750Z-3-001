# Slice 02, task T4 contract: key uniqueness and references between tables

Status: contract for task T4, written before tests, 2026-11-02.
Source of truth: `docs/specs/02-data-preparation.md` (revision R1 with amendment 02-04-R1), `docs/plans/02-data-preparation.md` (T4), `docs/specs/02-03-data-schema.md` (T2), `docs/specs/02-04-csv-reading.md` (T3).
This document refines the in-memory validation step of `emporio_agent.data.preparation`. It adds no business rule. Where it differs from R1, R1 wins.

## Scope

In scope:

- `src/emporio_agent/data/preparation.py`: one coordinating function, `read_validated_tables`, that reads the six source files with the existing `read_table` and validates key uniqueness, `categories.name` uniqueness and references in memory.
- `tests/integration/test_data_relations.py`.

Out of scope (T5 and later): `prepare_database`, `PreparationResult`, quality notes, SQLite connections, temporary files, replace, metadata, read-only access, CLI, agent and pricing. No change to `read_table`, its types, errors, parsers or schema.

## Public contract

```python
# emporio_agent.data.preparation (addition)
def read_validated_tables(
    source_dir: Path,
    *,
    file_prefix: str = SOURCE_FILE_PREFIX,
) -> tuple[TableReading, ...]: ...
```

- Returns six `TableReading` values, one per table, in `TABLE_ORDER` (`categories`, `customers`, `products`, `promotions`, `orders`, `order_items`). The table metadata is `schema.TABLES`, which is already in `TABLE_ORDER`.
- Each returned `TableReading` is the object returned by `read_table` for that table, unchanged: same `SourceFileInfo` (name, SHA-256, size, row count) and the same records with their values and physical lines. No record is added, removed, merged, reordered or normalized. No new result type exists.
- Side effects: exactly the reads done by `read_table`, so each of the six files is opened once. No file is created or written, no SQLite connection is opened, no log is written, no environment variable is read and no global state changes.
- `file_prefix` is passed to `read_table` unchanged. A prefix with a path component raises the `ValueError` of `read_table`.

## Algorithm

For each `TableSpec` in `TABLES` (that is, in `TABLE_ORDER`):

1. `reading = read_table(source_dir, table, file_prefix=file_prefix)`. Any `DataPreparationError` or `ValueError` from the reader propagates unchanged: same object, same code and location.
2. Uniqueness pass over `reading.records` (section below). The whole pass finishes before step 3.
3. Reference pass over `reading.records` against the key indexes of parent tables already validated (section below).
4. Store the key index of this table: the set of primary-key values of its records. Only then move to the next table.

So no later file is opened before every earlier table has passed its structure, uniqueness and reference checks. A relational error in an earlier table cannot be hidden by a field error or a missing file in a later table. This matches the R1 fail-fast order: file order, then line order, then column order, with the uniqueness pass before the reference pass inside one file (R1, "Validation order and categories").

Inside one file, `read_table` validates every record before step 2 starts. So a field or structure error anywhere in a file wins over a duplicate key or broken reference in the same file, even on an earlier line. This is the R1 order: "After all records of a file: 1. Uniqueness pass … 2. Foreign keys".

The key index of a parent is built from the values already in memory. No parent file is read again.

## Uniqueness pass

- Keys of a table, in check order: the primary key (`TableSpec.primary_key`), then each entry of `TableSpec.unique` as a single-column key. Only `categories` has a `unique` entry (`name`).
- Key value: the tuple of parsed values of the key columns, taken from `SourceRecord.values` by the column position in `table.columns`. For order_items the primary key is the pair `(order_id, product_id)`.
- Comparison is Python equality on the parsed values: `int` for IDs, decoded text for `name`. No trimming, no case folding, no Unicode normalization. `Bebidas`, `bebidas` and `Bebidas ` (trailing space) are three distinct names.
- Records are scanned in source order (ascending physical line). For each record, the primary key is checked first, then each unique key. The first key value already seen raises the error at the record that repeats it (the second occurrence).
- Error: `DataPreparationError(IssueCode.DUPLICATE_KEY, file_name=<base name>, line=<end line of the repeating record>, column=<key column source name>)`. For a single-column key the column is that key column (`category_id`, `customer_id`, `product_id`, `promotion_id`, `order_id`, `name`). For the order_items composite key it is `product_id`, the last key column.
- No other column is unique. Repeated `customers.email` values are valid. Repeated `order_id` in order_items with different `product_id` values is valid. A repeated `(order_id, product_id)` pair is rejected even if the quantities differ; quantities are never summed and no row is dropped.

## Reference pass

References, from `TableSpec.references`:

| Child column | Parent key |
| --- | --- |
| `products.category_id` | `categories.category_id` |
| `promotions.product_id` | `products.product_id` |
| `orders.customer_id` | `customers.customer_id` |
| `order_items.order_id` | `orders.order_id` |
| `order_items.product_id` | `products.product_id` |

- Records are scanned in source order. Inside one record, references are checked in the column order of `table.columns`. For order_items that is `order_id`, then `product_id`.
- The parent key is the single-column primary key of the parent table. Every parent table in R1 has a single-column primary key and comes earlier in `TABLE_ORDER`.
- A value not present in the parent key index raises `DataPreparationError(IssueCode.BROKEN_REFERENCE, file_name=<child base name>, line=<child record end line>, column=<child column source name>)`.
- Only child-to-parent existence is checked. A category without products, a product without promotions or items, a customer without orders and an order without items are valid.
- Header-only files are valid and give an empty key index. A non-empty child table whose records reference an empty parent table fails with `broken_reference` at its first record.
- The line is the physical end line kept by T3. For a record with a quoted multi-line field it is the last physical line of that record.

## Errors and privacy

- Only existing codes are used: `duplicate_key` and `broken_reference`. No new `IssueCode`, no new error type.
- Errors are raised directly, not while handling another exception, so they have no `__cause__` and no `__context__` from this step.
- `str()`, `repr()`, `args`, the four attributes and `traceback.format_exception_only()` contain only the code, the source base name, the line and the schema column name. The repeated key, the repeated category name and the unresolved reference value never appear. The full traceback and `__context__` are outside the guarantee (R1, "Privacy").
- Reader errors are not wrapped or converted.

## Not done in this step

- No data-quality notes, no status or stock rule, no historical total recalculation, no cross-field order checks, no text comparison beyond exact key equality.
- No persistence. D18, D19 and D39 are proved through `read_validated_tables` only. Their run through `prepare_database` (target absent, no connect before validation fails) belongs to T5. Atomicity and persisted import are not claimed.

## Traceability

| Plan case | T4 coverage |
| --- | --- |
| D18 | Duplicate primary key in each of the five single-key tables; repeated `(order_id, product_id)` with equal and with different quantity; second-occurrence line; key column (`product_id` for the pair) |
| D19 | Each of the five references failing alone; child line and column |
| D39 (a) | Same name, different IDs: `duplicate_key`, categories, line 3, column `name`; no SQLite connect during validation |
| D39 (b) | Names differing in case or by a trailing space accepted and kept byte-equal |
| D40–D42 | Not here (T5, atomicity) |

Coordination cases without plan ID: six valid files in `TABLE_ORDER`; precedence between primary key and unique key in one record and between lines; uniqueness before references in one table; record and column order of references; earlier relational error before a later field error or missing file; reader errors preserved; single read per file; data, hashes and totals unchanged; shared email; order without items; parent without children; empty tables; empty parent with non-empty child; multi-line record line; sentinel not in public error surfaces; no write and no SQLite connection.
