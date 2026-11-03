# Slice 05, part B validation: items, current names, historical total and provenance

Date: 2026-11-03. Baseline: `feature/data-preparation`, commit `503956a`, clean working tree, confirmed before editing. The full suite was re-run before any edit: `1229 passed, 2 skipped` (`test_readonly_connection.py:599`, `:612`, Windows `?` file names), which matches the task brief. No commit, push, PR or merge.
Contract: `docs/specs/05-orders.md` (rules A1–A16, decisions P-01–P-15) and `docs/plans/05-orders.md`, section "Slice B" and test matrix rows O24–O39. Neither document was changed. The slice A report (`docs/validation/05-01-orders-access.md`) was read and its decisions were kept.
Platform: Windows 11 Pro 10.0.26200, Python 3.14.3, SQLite 3.50.4.

## Spec Summary

This change implements only slice B, in `emporio_agent.services.orders`:

- `OrderItem(product_id, name, quantity, name_source="current_catalog")`, a frozen dataclass. `name` is the current catalog name and is untrusted data.
- `OrderProvenance(prepared_at, sources)`, a frozen dataclass. `prepared_at` is UTC. `sources` is a tuple of `SourceFileInfo` for `orders`, `order_items` and `products`, in that order.
- `OrderView` gains `items` (after `status`) and `provenance` (last). Field order follows the spec contract: `order_id, status, items, total, tracking_code, estimated_delivery, estimate_status, business_date, provenance`.
- `_ITEMS_SQL` is a fixed statement, equal to the approved text:

  ```sql
  SELECT oi.product_id, oi.quantity, p.name
  FROM order_items AS oi
  JOIN orders AS o ON o.order_id = oi.order_id
  LEFT JOIN products AS p ON p.product_id = oi.product_id
  WHERE oi.order_id = ? AND o.customer_id = ?
  ORDER BY oi.product_id
  ```

  It is bound with `(order_id, customer.customer_id)` and runs on the same own cursor (`row_factory = None`). It runs once, only after the order query returned a row, after the single clock read and after the header checks.
- Item row checks (spec G11): `product_id` and `quantity` must be `int` and `>= 1`. `name` must be a non-blank `str`; `NULL` (missing product row) fails. A failure raises `OrderDataError("invalid_row")` `from None`.
- Provenance comes from `read_preparation_info(connection)`. The service selects the three records by table name, in fixed order. It never includes `customers`, a line number or any other invented reference.
- `total` is unchanged from slice A: `cents_to_decimal(orders.total_cents)`. No price, promotion or product field other than `name` is read. `services.catalog` and `services.pricing` are not imported.

Unchanged from slice A: argument validation and check order, `CustomerContext`, the order SQL, the generic `None`, the fixed UTC−03:00 offset, the estimate comparison, the single clock read for an authorized order, and the connection lifecycle (no open, close, commit or `row_factory` change).

Not implemented (slice C or later): the `query_failed` mapping, `orders_kit.corrupt` and `orders_kit.observe`, invalid-row tests, authorizer-based evidence, the `get_order` tool, Pydantic, the session object, CLI and OpenAI.

## Gap Analysis

The work found no conflict with the approved contract and did not reinterpret any P-decision. These points were resolved without adding a rule and are listed for review:

1. **Item row checks without tests.** The spec (G6, G11) puts item checks in the item reader, and the slice B file table lists "item and provenance readers". These checks are implemented, the same way slice A implemented header checks that only slice C exercises. No slice B case stores an invalid item row, so mutation MB15 (name check removed) survives. Detection is planned in slice C (O51, O52).
2. **Order of steps.** The order query runs first, then the single clock read, then the header checks, then the items query and item checks, then provenance (spec G3, steps 3–5). Because of this order, a damaged header row raises `invalid_row` before the service reads any item.
3. **Provenance errors.** `read_preparation_info` raises `DatabaseUnavailableError("schema_mismatch")` when metadata is malformed. The service lets it propagate unchanged. Spec G13 maps it to `unavailable` / `data_unavailable`. No slice B case tests this path.
4. **SQLite errors from the items query.** `OperationalError` and similar errors propagate unchanged. The `query_failed` mapping is slice C, as in slice A, gap item 4.
5. **RED granularity.** `test_orders_items.py` imports the module (`from emporio_agent.services import orders`) and does not import the new names. Each case therefore fails separately and gives its own cause; one `ImportError` would block the whole file at collection.
6. **`orders_kit.SOURCE_DIR`.** This value was added to the fixture namespace. It is needed by O36, which hashes the written CSV files as a second independent oracle. Data, existing fixtures and behaviour are unchanged. `orders_kit.build(base)` without `edit` builds the same database as before.
7. **Stale docstring.** The module docstring of `tests/integration/test_orders_access.py` still says that slice A covers the header only. The task allowed changes to O01 only, so the docstring was not touched.
8. **Output size.** The future tool still has no output size bound (Q3). This slice adds no limit and no truncation.

## Implementation Plan (executed)

1. Confirmed the baseline: branch, HEAD `503956a`, empty `git status --porcelain`, full suite `1229 passed, 2 skipped`.
2. Read the spec, the plan, `orders.py`, `readonly.py`, `preparation.SourceFileInfo`, `schema.TABLE_ORDER` and promotions columns, the `orders_kit` block of `conftest.py`, `test_orders_access.py` and the slice A report.
3. Added the `renamed` edit to `orders_kit.build` and `SOURCE_DIR` to the namespace in `tests/integration/conftest.py`.
4. Extended the O01 expected value with `items` and `provenance`. Wrote `tests/integration/test_orders_items.py` (O24–O39) before the implementation.
5. Ran RED (see below).
6. Implemented slice B in `src/emporio_agent/services/orders.py`.
7. Ran GREEN, the mutation checks and the restoration check, then the full gates. Wrote this report and ran Ruff again.

## Test Checklist

`tests/integration/test_orders_items.py`: 20 collected items in 16 test functions. `test_orders_access.py`: O01 extended; the item count is unchanged.

| Case | Test (items) | Oracle |
| --- | --- | --- |
| O01 (extended) | `test_o01_authorized_order_header` (1): full `asdict` with the two items and provenance | literals; source records from independent `source_files` query |
| O24 | `test_o24_items_sorted_by_product_id` (1): precondition that the CSV lists 11 before 10; exact item tuple | literals |
| O25 | `test_o25_every_item_name_is_current_catalog` (1): 5001, 5003, 5004; 4 items | literal |
| O26 | `test_o26_order_without_items_keeps_total` (1): precondition `count(*) = 0`; `items == ()`, `total == 250.00` | independent SQL, literals |
| O27 | `test_o27_quantity_preserved_and_repeatable` (1): `(13, "Violão Teste Gama", 3)`, `int` quantity, two equal calls | literals |
| O28 | `test_o28_total_is_historical_not_current_prices` (1): preconditions for prices (50000, 120000) and promotion (1000 bp, active); `1500.00`, `!= 2200.00`, `!= 2100.00` | hand-calculated sums from the plan |
| O29 | `test_o29_projection_has_exactly_the_approved_fields` (1): exact field tuples of `OrderView`, `OrderItem`, `OrderProvenance`; no price, subtotal, customer, notes, payment, order date or line field | literals |
| O30 | `test_o30_renamed_product_shows_current_name` (1): `renamed` build; precondition from independent query; renamed name, `current_catalog`, total `1500.00` | literals, independent SQL |
| O31 | `test_o31_no_customer_data_in_views` (1): orders 5001–5004; the four C1 sentinels are absent from `repr` and from every reachable `str` | literals |
| O32 | `test_o32_notes_never_returned` (1) | literals |
| O33 | `test_o33_payment_method_and_order_date_never_returned` (1): `repr` text, reachable strings, and no `date(2026, 10, 28)` value | literals |
| O34 | `test_o34_hostile_product_name_is_data` (1): exact name; then `6001` with C1 is `None` | literal |
| O35 | `test_o35_hostile_tracking_code_is_data` (1): exact text; then `6001` with C1 is `None` | literal |
| O36 | `test_o36_provenance_from_preparation_metadata` (1): `prepared_at` `2026-11-02T12:00:00+00:00`, UTC; tables, file names and row counts (6, 6, 5); `astuple` equals the `source_files` row; SHA-256 and size equal the written CSV bytes; no `customers` | literals, independent SQL, `hashlib` of the source files |
| O37 | `test_o37_other_customers_items_never_returned` (1): no product 14 and no `Baixo Teste Delta` in any C1 order | literals |
| O38 | `test_o38_items_equal_independent_query` (5): 5001, 5003, 5004 (C1), 6001, 6002 (C2) | independent `SELECT oi.product_id, p.name, oi.quantity ... ORDER BY oi.product_id` |
| O39 | `test_o39_no_authorization_carried_between_calls` (1): one connection, C1→5001, C2→5001, C2→6001, C1→6001, C1→5001; view, `None`, view, `None`, view equal to the first; `clock.calls == 3` | literals |

## Test Strategy

- Integration only. Each test uses a real SQLite file in `tmp_path`, built by the real `prepare_database` from the synthetic `orders_kit` rows of the plan, and opens it with `connect_readonly`. O39 uses one connection for all five calls.
- Oracles: literals from the plan (names, quantities, hand-calculated totals), independent parameterized SQL through a separate `mode=ro` connection (`orders_kit.query`), and `hashlib.sha256` of the CSV bytes that the fixture wrote. `get_order`, `cents_to_decimal` and `read_preparation_info` never compute an expected value.
- No mock of authorization, SQL, relations, prices or persistence. No private CSV, network, OpenAI or new dependency.
- No observer, authorizer, trace callback or invalid-row variant was added. Those belong to slice C.

## Compliance Report

### RED

Run after the tests were written and before the implementation:

`uv run --frozen pytest tests/unit/test_order_rules.py tests/integration/test_orders_access.py tests/integration/test_orders_items.py -q`. Exit 1, `17 failed, 56 passed`.

| Failed | Real cause |
| --- | --- |
| O01 | `AssertionError`: `asdict` has no `items` or `provenance` key |
| O24, O25, O26, O27, O30, O34, O37, O38 ×5, O39 (13) | `AttributeError: 'OrderView' object has no attribute 'items'` |
| O36 | `AttributeError: 'OrderView' object has no attribute 'provenance'` |
| O29 | `AttributeError: module 'emporio_agent.services.orders' has no attribute 'OrderItem'` |
| O28 | `sqlite3.OperationalError: no such column: discount_percent`. This was a **test defect**: the precondition query used the CSV column name, but the stored column is `discount_basis_points`. The query was corrected to `discount_basis_points` with expected value `1000`. The expectation was not weakened. After the fix, O28 failed with `AttributeError: ... 'items'` (`1 failed`). |

The 56 passing items are the 52 unchanged slice A items and 4 slice B cases: O31, O32, O33 and O35. These cases check absence (customer data, notes, payment method, order date) or a slice A field (`tracking_code`), so slice A behaviour already satisfies them. No stub was used and no protection was removed to make them fail.

### GREEN

`uv run --frozen pytest tests/unit/test_order_rules.py tests/integration/test_orders_access.py tests/integration/test_orders_items.py -q`. Exit 0, `73 passed`.

### Mutation checks

A scratch script outside the repository ran these checks after GREEN, one mutation at a time. For each mutation it replaced exact text in `orders.py` (each replacement asserted unique), ran the three order test files, restored the original bytes and asserted the SHA-256 again.

| ID | Mutation | Result | Failing tests |
| --- | --- | --- | --- |
| MB1 | `ORDER BY oi.product_id` removed | **survived** | none (see survivors) |
| MB1b | `ORDER BY oi.rowid` (CSV insertion order) | killed (6) | O01, O24, O28, O30, O38[5001-C1], O39 |
| MB1c | `ORDER BY oi.product_id DESC` | killed (6) | O01, O24, O28, O30, O38[5001-C1], O39 |
| MB2 | `LEFT JOIN products` changed to inner `JOIN` | **survived** | none; planned for O52 (slice C) |
| MB3 | total = current table prices × quantity | killed (7) | O01, O05, O16, O26, O28, O30, O39 |
| MB3b | total = current effective prices (active promotion) × quantity | killed (7) | O01, O05, O16, O26, O28, O30, O39 |
| MB4 | item name cached across calls by `product_id` | killed (1) | O30 |
| MB5a | `notes` field added and filled | killed (3) | O01, O29, O32 |
| MB5b | `payment_method` field added and filled | killed (3) | O01, O29, O33 |
| MB5c | `order_date` field added and filled | killed (4) | O01, O19, O29, O33 |
| MB6 | items query without the `orders` join and customer filter | **survived** | none; planned for O41 (slice C) |
| MB7 | items query run before the order query (before authorization) | **survived** | none; planned for O41 (slice C) |
| MB8 | `name_source` default changed to `purchase_time` | killed (3) | O01, O25, O30 |
| MB9a | `customers` added to provenance tables | killed (2) | O01, O36 |
| MB9b | provenance in `TABLE_ORDER` order (products first) | killed (2) | O01, O36 |
| MB10 | `prepared_at` taken from `datetime.now(UTC)` | killed (4) | O01, O27, O36, O39 |
| MB11 | N+1: one product-name query per item | **survived** | none; statement count is observable only in slice C |
| MB12 | order without items returns `None` | killed (10) | O17, O18, O21 ×6, O26, O37 |
| MB13 | quantity forced to 1 | killed (8) | O01, O24, O27, O28, O30, O38[5001-C1], O38[5004-C1], O39 |
| MB14 | view cached by `order_id`, ignoring the customer | killed (12) | O05, O06, O21 ×2, O22 ×3, O30, O34, O35, O37, O39 |
| MB15 | item name check removed (NULL or blank name kept) | **survived** | none; planned for O52 (slice C) |

Survivors and why they survive:

- **MB1 (`ORDER BY` removed).** `EXPLAIN QUERY PLAN` on this SQLite 3.50.4 shows `SEARCH oi USING INDEX sqlite_autoindex_order_items_1 (order_id=?)`. The primary key index on `(order_id, product_id)` already returns rows sorted by `product_id`, even though the stored rowids follow the CSV order (rowid 1 → product 11, rowid 2 → product 10). No result-level test can see the missing `ORDER BY` with this schema and query plan. O24 still detects any other order (MB1b, MB1c). The explicit `ORDER BY` stays, because the order must not depend on a query plan. The expectation was not weakened.
- **MB2, MB15 (product join and name check).** Valid data always has the product row and a non-blank name. Detection needs a damaged file: `product_missing` and `name_blank` (O52, slice C).
- **MB6, MB7 (customer filter in the items query; items read before authorization).** The items query runs only for an order that the order query already authorized, so the result is the same. Proof by SQLite authorizer (O41, O43) belongs to slice C. This slice does not add that mechanism early.
- **MB11 (N+1).** The result is identical. The number of statements is observable only with the slice C observer.

Restoration: SHA-256 of `src/emporio_agent/services/orders.py` before the run was `8f449bf0ba3eb481418e37683d26df2fb6efc374ac19e23071b69dae8a82eb7a`. After every mutation and at the end, the script asserted the same value. `sha256sum` and `git status --short` checked it again after the run.

### Gates (after restoration)

| Command | Exit | Result |
| --- | --- | --- |
| `uv run --frozen pytest tests/unit/test_order_rules.py tests/integration/test_orders_access.py tests/integration/test_orders_items.py -q` | 0 | 73 passed |
| `uv run --frozen pytest tests/unit/test_pricing.py -q` | 0 | 37 passed |
| `uv run --frozen pytest -q -rs` | 0 | 1249 passed, 2 skipped (`test_readonly_connection.py:599`, `:612`, Windows `?` file names) |
| `uv run --frozen ruff check --no-cache .` | 0 | All checks passed |
| `uv run --frozen ruff format --check --no-cache .` | 0 | 74 files already formatted |

1249 = 1229 earlier passes + 20 new items. The 1231 earlier items are preserved: 1229 passed (O01 included, with its extended expectation) and the same 2 skips. No new failure or skip.

After this report was written, both Ruff commands ran again without cache. The results are recorded in the reply that hands over this slice. Ruff also checks Markdown, so the file count includes this report.

### Files

| Path | Change |
| --- | --- |
| `src/emporio_agent/services/orders.py` | 69 added, 2 removed: `OrderItem`, `OrderProvenance`, `items` and `provenance` fields, `_ITEMS_SQL`, `_PROVENANCE_TABLES`, `_item`, `_provenance`, docstrings |
| `tests/integration/conftest.py` | 18 added, 3 removed: `_ORDERS_ROW_EDITS` (`renamed`), `orders_build(base, edit=None)`, `SOURCE_DIR` in the `orders_kit` namespace |
| `tests/integration/test_orders_access.py` | 27 added, 0 removed: O01 expectation only |
| `tests/integration/test_orders_items.py` | created |
| `docs/validation/05-02-orders-items.md` | created (this report) |

Nothing else changed: no data layer, catalog, pricing, policies, slice A unit tests, other test files, static fixtures, specs, plans, README, configuration or lockfile. Git state: branch `feature/data-preparation`, HEAD `503956a`, uncommitted.

### Limits

- All data is synthetic, and the tests ran on one Windows machine. The supplied orders are not used.
- These tests do not prove real authentication, the future tool schema, the session object or the behaviour of the future model.
- **Integrated isolation is not complete.** This slice shows isolation only at the result level: O37 and O39 (no foreign items, no authorization carried between calls). Statement-level evidence is planned for slice C (O40–O43): which tables and columns are read, that the items query has the customer filter, that no item is read for a foreign order, and that there is no N+1. Mutations MB6, MB7 and MB11 survive until then.
- Product-missing and invalid-item-row behaviour (`LEFT JOIN`, item checks) is implemented but not tested until slice C (O51, O52). MB2 and MB15 survive.
- With this schema, the sort order of items cannot be told apart from the primary key index order (MB1).
- Without the observer, no slice B test proves that the service never reads prices, promotions, description or specs. The total evidence is result-level (O28, MB3, MB3b). Column-level evidence is O40 (slice C).
- The fixed UTC−03:00 offset is unchanged. The output size bound for the future tool remains open (Q3).

Status: slice B GREEN. Slice C not started. Stopped for review.
