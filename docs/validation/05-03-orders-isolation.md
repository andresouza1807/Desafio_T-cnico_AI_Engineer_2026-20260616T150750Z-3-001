# Slice 05, part C validation: integrated isolation, data errors, privacy and connection lifecycle

Date: 2026-11-03. Baseline: `feature/data-preparation`, HEAD `63da8641677cf723c85d6bdbd1a629fad6cfdb3a`, clean working tree. Both were confirmed before any edit. Approved baseline from the task brief: 1249 passed, 2 skipped; Ruff check and format clean. No commit, push, PR or merge.
Contract: `docs/specs/05-orders.md` (rules A1–A16, decisions P-01–P-15) and `docs/plans/05-orders.md`, section "Slice C" and test matrix rows O40–O58. Neither document was changed. The reports `05-01-orders-access.md` and `05-02-orders-items.md` were read, and their decisions were kept. No `AGENTS.md` file exists in the repository (searched outside `.venv` and `.git`).
Platform: Windows 11 Pro 10.0.26200, Python 3.14.3, SQLite 3.50.4.

## Spec Summary

Slice C proves O40–O58 and keeps O01–O39 unchanged:

- Authorization is part of the SQL filter and uses the application-built `CustomerContext`.
- The service reads no item or product row for an order that the order query did not authorize.
- A foreign order and a missing order both return `None` and produce the same observed reads. With no session, the service runs no query.
- The service reads only allowed tables and columns.
- An invalid row gives `OrderDataError("invalid_row")`.
- A SQLite failure in the order or items query gives `OrderDataError("query_failed")`, raised `from None`. `sqlite3.ProgrammingError` still propagates unchanged.
- `DatabaseUnavailableError` and caller-contract errors (`OrderInputError`, `TypeError`) are unchanged.
- The caller owns the connection. The service does not close, commit or roll back the connection, does not change `row_factory` and does not write.
- No public type or signature changed.

Production change, `src/emporio_agent/services/orders.py`:

- New private helper `_fetch(connection, sql, parameters)`. It opens its own cursor with `row_factory = None` and returns `fetchall()`. It re-raises `sqlite3.ProgrammingError`. It maps every other `sqlite3.Error` to `OrderDataError("query_failed")` `from None`. This is the same pattern as `catalog._fetch_all`.
- The order query and the items query use `_fetch`. The order query now uses `fetchall()` and then takes `rows[0]`, instead of `fetchone()`. The filter is on the primary key, so the query returns zero rows or one row and the result is the same.
- The module docstring and the `OrderDataError` docstring now name `query_failed`.

Nothing else changed: same SQL text, check order, row checks, types, fields and signature.

## Gap Analysis

The matrix O40–O58 was compared with the contract and with the code at `63da864` before any test was written.

Gap found in production: the `query_failed` mapping (spec G13; plan slice C, file table). At `63da864`, a dropped `orders` or `order_items` table raised `sqlite3.OperationalError: no such table: …` from `get_order`. The tests showed this gap (see RED). No other gap was found: header and item checks, `LEFT JOIN`, a single items query and the connection handling already existed from slices A and B.

Ambiguities, resolved without a new rule (listed for review):

1. **O43 and the items statement.** The matrix says: "every statement that reads `orders` also reads `orders.customer_id`". That rule does not catch an items query without the customer join: such a statement reads `order_items` and not `orders`, so the rule passes (MB6). Spec G4 and G6 require the items query to repeat the customer filter through the join. The O43 test therefore also asserts that the one statement that reads `order_items` reads `orders.customer_id`. This rule comes from the spec. It is not a new rule.
2. **N+1 detection and the statement cache.** SQLite calls the authorizer only when a statement is prepared, and `sqlite3` caches prepared statements. A per-item query with the same SQL text is prepared only once, so the authorizer alone cannot count executions. `orders_kit.observe` therefore installs two real callbacks. The authorizer records `(action, table, column)` and always returns `SQLITE_OK`. The trace callback (`set_trace_callback`) runs for every executed statement, cached ones included, and closes the group of authorizer events of that statement. The trace callback is a second real observer, not part of the plan text. It changes no result.
3. **The trace text contains bound values.** In this Python version the trace callback receives expanded SQL, for example `… WHERE order_id = 6001 AND customer_id = 1`. O42 therefore compares authorizer events and statement counts, not trace text. The trace text is never asserted.
4. **O40 action set.** The observed actions were only `SQLITE_SELECT` and `SQLITE_READ`. `SQLITE_FUNCTION` is still allowed, as the plan permits. Reads of `sqlite_schema` and `sqlite_master` are tolerated, but none occurred.
5. **Provenance reads.** `read_preparation_info` reads `preparation_metadata` and `source_files`. Its SQL text differs from the schema check in `connect_readonly`, so the statement cache does not hide these reads. O40 lists both tables as allowed.
6. **Customer ID in error surfaces.** The spec says that no surface contains a customer ID. The tests check this more strictly: no public surface of an order error contains any digit. This holds for `order_id: invalid_type`, `order_id: out_of_range`, `invalid_row` and `query_failed`.
7. **O55 path check.** `DatabaseUnavailableError` contains the database base name by its own contract (Slice 02, T6). O55 checks that the directory path (`str(tmp_path)`, `orders_kit.DB_DIR`) is absent. It does not check the base name.
8. **Privacy scope.** The surfaces checked are those of spec G13: `str`, `repr`, `args`, the documented attributes and `traceback.format_exception_only`. The full traceback and `__context__` are outside this guarantee. They were not inspected for text, and this report does not claim that they are clean.
9. **Authorizer limits.** The authorizer proves the accesses that SQLite reported for the statements that ran. It is not production authentication. It does not prove equal response time. It does not prove in general that a column read by SQLite is used in a `WHERE` predicate (see MC2b below).
10. **`schema_v2`.** This is the only variant that touches `preparation_metadata`. It is applied with `orders_kit.corrupt`, as the plan's variant table requires.

## Implementation Plan (executed)

1. Confirmed the baseline: branch `feature/data-preparation`, HEAD `63da8641677cf723c85d6bdbd1a629fad6cfdb3a`, empty `git status --porcelain`.
2. Read the spec, the plan, reports 05-01 and 05-02, `orders.py`, `readonly.py`, `errors.py`, the DDL in `schema.py`, `catalog._fetch_all`, the conftest `catalog_kit` and `orders_kit` blocks, and `test_catalog_invalid_rows.py`.
3. Ran a scratch probe (outside the repository) to see the real authorizer and trace events for `get_order` on SQLite 3.50.4. No repository file changed in this step.
4. Extended `tests/integration/conftest.py`. The change is append-only, except for three added lines in the `orders_kit` namespace (`DB_DIR`, `corrupt`, `observe`). It adds `_ORDERS_VARIANTS`, `orders_corrupt`, `_AUTHORIZER_ACTION_NAMES`, `ReadObservation` and `OrdersObserver`. No existing fixture or helper changed.
5. Wrote `test_orders_isolation.py`, `test_orders_invalid_rows.py` and `test_orders_lifecycle.py` before any production change.
6. Ran the tests (RED, below).
7. Added the `query_failed` mapping only.
8. Ran GREEN, the mutation checks, the restoration check and the full gates. Then wrote this report and ran Ruff again.

## Test Checklist

51 new collected items in 23 test functions: `test_orders_isolation.py` 20, `test_orders_invalid_rows.py` 18, `test_orders_lifecycle.py` 13.

| Case | Test (items) | Oracle / evidence |
| --- | --- | --- |
| O40 | `test_o40_only_allowed_tables_columns_and_actions` (5: 5001, 5003, 5004 with C1; 6001, 6002 with C2) | Authorizer: actions ⊆ {SELECT, READ, FUNCTION}. Tables ⊆ {orders, order_items, products, preparation_metadata, source_files}. Never `customers` or `promotions`. `orders` columns ⊆ allowed set, with no `notes`, `payment_method` or `order_date`. `products` columns exactly {`product_id`, `name`}. Positive guards: `orders.customer_id`, `order_items.quantity` and `products.name` are observed, so the pass cannot be vacuous. |
| O41 | `test_o41_no_item_read_for_foreign_order` (1) | 6001 with C1: one statement, one `SQLITE_SELECT`, only `orders` read, no read of `order_items` or `products`, clock not called |
| O42 | `test_o42_foreign_and_missing_orders_run_identical_reads` (1) | Precondition by independent SQL. One fresh connection per call. 6001 and 9999 with C1 give identical non-empty event lists and one statement each. 5001 with no session gives zero statements and no clock call. |
| O43 | `test_o43_every_order_statement_reads_customer_id` (5, the O38 calls) | Exactly two statements touch order tables. Each statement that reads `orders` reads `orders.customer_id`. The single `order_items` statement reads `orders.customer_id` (gap item 1). |
| O43 (N+1) | `test_o43_items_loaded_by_one_statement` (3: 5001 with 2 items, 5003, 5004) | Item count by independent SQL. Trace counts: one statement reads `order_items`, one reads `products`, two touch order tables. |
| O44 | `test_o44_text_order_id_cannot_select_identity` (2), `test_o44_customer_id_keyword_is_rejected` (1) | `invalid_type` for both strings and `TypeError` for `customer_id=2`. No statement observed and no clock call. The next call, `get_order(6001, C1)`, returns `None`. The surfaces contain no digit, `customer_id` or `cliente`. |
| O45 | `test_o45_customer_two_data_never_reaches_customer_one` (1) | Every C1 call (5001–5004, 6001, 6002, 9999), the `renamed` database and four input errors. No C2 sentinel and no `Baixo Teste Delta` appears in `repr` or in any reachable string. Independent SQL shows that the C2 data exists. Errors from O47–O54 are checked against the same sentinels in `assert_clean_surfaces`. |
| O46 | `test_o46_input_error_surfaces_are_codes_only` (1). Data errors: `assert_clean_surfaces` in every O47–O54 test | The surfaces contain no `tmp_path`, `DB_DIR`, `DB_NAME`, `SELECT`, `WHERE`, `FROM`, `JOIN`, `no such`, `order_items`, `sqlite`, sentinel, damaged value or digit |
| O47–O51, O52 (`name_blank`) | `test_o47_o52_damaged_value_is_invalid_row` (12: `status_unknown`; `total_negative`, `total_text`, `total_real`; `tracking_blank`, `tracking_int`; `estimate_month`, `estimate_format`, `estimate_int`; `quantity_zero`, `quantity_text`; `name_blank`) | Damage proved by independent `typeof(...)` and value. Then `invalid_row`, `args`, `__cause__ is None`, `__suppress_context__ is True`, not a `TypeError` or `ValueError`, and clean surfaces. |
| O52 (`product_missing`) | `test_o52_missing_product_is_invalid_row_not_a_shorter_view` (1) | Independent SQL: product 11 is gone, and the item `(5001, 11)` remains. Result: `invalid_row`, never a one-item view. |
| O53 | `test_o53_foreign_damage_does_not_affect_or_reveal` (1) | Damage proved for 6001. C1→5001 returns the expected view (`shipped`, `1500.00`, items `(10, 2), (11, 1)`). C1→6001 returns `None`. C2→6001 raises `invalid_row`. `SENTINEL-FOREIGN` is absent everywhere. |
| O54 | `test_o54_sqlite_failure_is_query_failed` (2: `drop_items`, `drop_orders`) | Missing table proved by an independent `sqlite_schema` query. Result: `query_failed`, `__cause__ is None`, `__suppress_context__ is True`, clean surfaces. |
| O55 | `test_o55_missing_file_fails_before_service`, `test_o55_schema_v2_fails_before_service` (2) | `DatabaseUnavailableError` with `missing` / `schema_mismatch`. The `with` body is never entered and the clock is not called. No directory path in the surfaces. |
| O56 | `test_o56_connection_reusable_after_success_none_and_input_error`, `…_after_invalid_row`, `…_after_query_failed` (3). `test_o56_caller_transaction_is_neither_committed_nor_rolled_back` (5: view, `None`, `invalid_input`, `invalid_row`, `query_failed`) | After every call: `row_factory is sqlite3.Row`, `in_transaction` as expected and `SELECT 1` works. Follow-up calls equal the first result. 5002 is returned after `invalid_row`. A caller `BEGIN` stays open across every outcome. |
| O57 | `test_o57_service_never_closes_the_connection`, `test_o57_closed_connection_programming_error_is_not_converted` (2) | The connection is usable until `connect_readonly` exits, then raises `ProgrammingError`. On a closed connection, `type(error) is sqlite3.ProgrammingError`, it is not an `OrderDataError`, and the clock is not called. |
| O58 | `test_o58_lookups_leave_database_unchanged` (1), `test_o58_failed_lookups_leave_database_unchanged` (2: `invalid_row`, `query_failed`) | `orders_kit.snapshot` (SHA-256, directory listing, ordered rows of every table) is equal before and after the calls. The listing is exactly `[pedidos.sqlite3]`: no journal, WAL or temporary file. The calls cover 8 IDs × 5 contexts, input errors, a wrong `customer` type, a closed connection and the two data errors. |

## Test Strategy

- Integration only. Each test uses real SQLite files in `tmp_path`, built by the real `prepare_database` from the synthetic `orders_kit` CSV. The service always gets a connection from `connect_readonly`.
- Damage is applied only to the test's own built file, with a separate writable connection, before `connect_readonly`. Every `UPDATE` or `DELETE` must change exactly one row. Each case proves its damage with independent SQL (`typeof` and value, `count(*)` or `sqlite_schema`) through a separate `mode=ro` connection.
- The observers are a real authorizer that always returns `SQLITE_OK` and the real trace callback. Both are installed only around the observed call and removed before assertions and verification queries. Each observed call uses a fresh connection. No result or authorization is simulated.
- Oracles are literals from the plan and independent SQL. `get_order`, `cents_to_decimal`, `parse_date` and `read_preparation_info` never compute an expected value.
- No mock of rules, SQL, relations or persistence. No private data, network, OpenAI, tool, agent, session or CLI.

## Compliance Report

### Map O40–O58 → tests

See the Test Checklist. Every case from O40 to O58 has at least one test. O45 and O46 are also enforced inside every O47–O54 test through `assert_clean_surfaces`.

### RED (before any production change)

`uv run --frozen pytest tests/integration/test_orders_isolation.py tests/integration/test_orders_invalid_rows.py tests/integration/test_orders_lifecycle.py -q -rs`. Exit 1, `5 failed, 46 passed`.

| Failed (5) | Real cause |
| --- | --- |
| `test_o54_sqlite_failure_is_query_failed[drop_items-order_items]` | `sqlite3.OperationalError: no such table: order_items` (`orders.py:258`) |
| `test_o54_sqlite_failure_is_query_failed[drop_orders-orders]` | `sqlite3.OperationalError: no such table: orders` (`orders.py:248`) |
| `test_o56_connection_reusable_after_query_failed` | `OperationalError: no such table: order_items` |
| `test_o56_caller_transaction_is_neither_committed_nor_rolled_back[drop_items-5001-query_failed]` | `OperationalError: no such table: order_items` |
| `test_o58_failed_lookups_leave_database_unchanged[drop_items-query_failed]` | `OperationalError: no such table: order_items` |

46 items passed on the first run. They check behaviour that slices A and B already implemented: O40–O46 (isolation, statement shape, leakage), O47–O53 (row checks, `LEFT JOIN`), O55 (`connect_readonly`), and O56–O58 for success, `None`, input errors and `invalid_row`. No stub was used and no protection was removed to make them fail. The mutation checks below show that these tests detect the behaviours they target.

**Test defect found after the implementation.** The first GREEN run gave `1 failed, 123 passed`. `test_o54…[drop_orders-orders]` checked that the bare table name `orders` was absent. But `traceback.format_exception_only` contains the qualified class name `emporio_agent.services.orders.OrderDataError`. The check was changed to the SQLite message fragments `table: orders` and ` orders:`. All other checks of that test stayed the same (`no such`, `FROM`, digits and others). Before the fix, during RED, this item had failed for the real cause (`OperationalError`).

### GREEN

`uv run --frozen pytest tests/unit/test_order_rules.py tests/integration/test_orders_access.py tests/integration/test_orders_items.py tests/integration/test_orders_isolation.py tests/integration/test_orders_invalid_rows.py tests/integration/test_orders_lifecycle.py -q -rs`. Exit 0, `124 passed` (73 from A and B, plus 51 new).

### Mutation checks

A scratch script outside the repository ran these checks after GREEN, one mutation at a time. For each mutation it replaced exact text in `orders.py` (each replacement asserted unique) and ran the six order test files. Then it restored the original bytes and asserted the SHA-256 again.

26 mutations: **24 detected, 2 survived**. For comparison, slice B had 21 mutations: 15 detected and 6 survived.

| ID | Mutation | Result | Failing tests (items) |
| --- | --- | --- | --- |
| MB1 | `ORDER BY oi.product_id` removed | **survived** | none |
| MB2 | `LEFT JOIN products` changed to inner `JOIN` | killed (1) | O52 `product_missing` |
| MB6 | items query without the `orders` join and customer filter | killed (5) | O43 ×5 |
| MB7 | items query run before the order query | killed (3) | O41, O42, O56 after `query_failed` |
| MB11 | N+1: one product-name query per item | killed (8) | O43 ×5, O43 N+1 ×3 |
| MB15 | item name check removed | killed (2) | O52 `name_blank`, O52 `product_missing` |
| MC1a | order SQL also selects `notes`, `payment_method`, `order_date` (silently sliced away) | killed (5) | O40 ×5 |
| MC1b | items SQL also reads `products.price_cents` | killed (5) | O40 ×5 |
| MC1c | order SQL joins `customers` | killed (6) | O40 ×5, O41 |
| MC2a | order SQL without the customer filter | killed (24) | O02, O05, O06, O22, O34, O35, O37, O39, O41, O42, O43 ×5, O44 ×3, O45, O53, O56 ×3, O57 |
| MC2b | order read by `order_id` only, `customer_id` selected and checked in Python | **survived** | none |
| MC3a | `query_failed` raised `from error` | killed (2) | O54 ×2 |
| MC3b | `query_failed` message carries the SQLite text | killed (5) | O54 ×2, O56 ×2, O58 |
| MC3c | header `invalid_row` raised `from error` | killed (10) | O47–O50 ×9, O53 |
| MC3d | header `invalid_row` message carries the row | killed (13) | O47–O50 ×9, O53, O56 ×2, O58 |
| MC3e | `query_failed` mapping removed (back to propagation) | killed (5) | O54 ×2, O56 ×2, O58 |
| MC3f | `ProgrammingError` converted to `query_failed` | killed (2) | O57 closed connection, O58 |
| MC4 | service closes the connection (in provenance) | killed (21) | O39, O40 ×5, O43 ×8, O45, O53, O56 ×3, O57, O58 |
| MC5a | service commits after each query | killed (4) | O56 caller transaction ×4 |
| MC5b | service rolls back after each query | killed (4) | O56 caller transaction ×4 |
| MC6 | service sets `connection.row_factory = None` | killed (8) | O56 ×7, O57 |
| MC7 | status check removed | killed (5) | O47, O53, O56 ×2, O58 |
| MC8 | negative total check removed | killed (1) | O48 `total_negative` |
| MC9 | tracking text check removed | killed (2) | O49 ×2 |
| MC10 | invalid estimate becomes `None` | killed (2) | O50 `estimate_month`, `estimate_format` |
| MC11 | quantity range check removed | killed (1) | O51 `quantity_zero` |

Survivors:

- **MB1 (`ORDER BY` removed).** This survives for the same reason as in slice B. On SQLite 3.50.4 the primary key index `(order_id, product_id)` returns the items sorted by `product_id`. O24 still detects any other order (B: MB1b, MB1c). The explicit `ORDER BY` stays as a guarantee that does not depend on the query plan. The schema was not changed and no artificial RED was forced.
- **MC2b (ownership checked in Python).** The statement still reads `orders.customer_id`, but in the select list instead of the `WHERE` clause. For a foreign order, customer 2's row reaches Python and is then discarded. The result is the same `None`. The authorizer reports the same `(READ, orders, customer_id)` event and the same single statement, so O40–O43 cannot tell the two designs apart. This is the limit stated in gap item 9: the authorizer does not prove where a column is used. Spec G4 ("no query by `order_id` alone, followed by a Python ownership check") is kept by the fixed `_ORDER_SQL` constant and by review, not by a test. The bound values appear only in the trace text, and asserting predicate text would test the SQL string, not the behaviour. That was not done.

Restoration: SHA-256 of `src/emporio_agent/services/orders.py` before the run was `fbab64dfda249dedfcec3e0aa172d57aa135bc11356d5792658751fc1f396878`. The script asserted this value after every mutation and at the end. `sha256sum` and `git status --short` checked it again after the run.

### Gates (after restoration)

| Command | Exit | Result |
| --- | --- | --- |
| `uv run --frozen pytest tests/unit/test_order_rules.py tests/integration/test_orders_access.py tests/integration/test_orders_items.py tests/integration/test_orders_isolation.py tests/integration/test_orders_invalid_rows.py tests/integration/test_orders_lifecycle.py -q -rs` | 0 | 124 passed |
| `uv run --frozen pytest tests/unit/test_pricing.py -q` | 0 | 37 passed |
| `uv run --frozen pytest -q -rs` | 0 | 1300 passed, 2 skipped (`test_readonly_connection.py:599`, `:612`, Windows `?` file names) |
| `uv run --frozen ruff check --no-cache .` | 0 | All checks passed |
| `uv run --frozen ruff format --check --no-cache .` | 0 | 78 files already formatted |

1300 = 1249 earlier passes + 51 new items. The same 2 skips. No earlier test failed or changed.

After this report was written, both Ruff commands ran again without cache. The results are recorded in the reply that hands over this slice. Ruff also checks Markdown, so the file count includes this report.

### Files

| Path | Change |
| --- | --- |
| `src/emporio_agent/services/orders.py` | 25 added, 9 removed: `_fetch` with the `query_failed` mapping; order and items queries use it; two docstrings |
| `tests/integration/conftest.py` | 217 added, 0 removed: `_ORDERS_VARIANTS`, `orders_corrupt`, `_AUTHORIZER_ACTION_NAMES`, `ReadObservation`, `OrdersObserver`; `orders_kit` gains `DB_DIR`, `corrupt`, `observe` |
| `tests/integration/test_orders_isolation.py` | created (O40–O46) |
| `tests/integration/test_orders_invalid_rows.py` | created (O47–O55) |
| `tests/integration/test_orders_lifecycle.py` | created (O56–O58) |
| `docs/validation/05-03-orders-isolation.md` | created (this report) |

Nothing else changed: no earlier test, data layer, catalog, pricing, policies, specs, plans, earlier reports, README, configuration, dependencies or lockfile. Git state: branch `feature/data-preparation`, HEAD `63da864`, uncommitted.

### Limits

- All data is synthetic, and the tests ran on one Windows machine with SQLite 3.50.4. The supplied orders are not used. The authorizer event sets may differ with another SQLite version; the assertions use subsets where events are optional.
- These tests do not prove real authentication, the future tool schema, the session object or the behaviour of the future model.
- Equal results for foreign and missing orders are proven at the result, event and statement-count level. Response time is not measured.
- The authorizer proves which columns SQLite read. It does not prove that `customer_id` is used in the `WHERE` clause (MC2b survives).
- Privacy covers the public surfaces of spec G13 only. The full traceback and `__context__` are not covered.
- Invalid-row cases cover the fields of spec G11 only. References, uniqueness, `order_date`, `payment_method` and `notes` stay guaranteed by preparation.
- The item sort order cannot be told apart from the primary key index order (MB1).
- The fixed UTC−03:00 offset and the open output size bound (Q3) are unchanged.

Status: slice C GREEN. Slice 05 complete pending review. Next module not started. Stopped for review.
