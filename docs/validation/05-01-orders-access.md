# Slice 05, part A validation: context, validation, authorized order query and dates

Date: 2026-11-03. Baseline: `feature/data-preparation`, commit `9870cf8`, clean working tree (confirmed before editing). Last approved validation from the task brief: 1176 passed, 2 skipped on Windows; reproduced here before implementation (see RED). No commit, push, PR or merge.
Contract: `docs/specs/05-orders.md` (rules A1–A16, decisions P-01–P-15) and `docs/plans/05-orders.md` section "Slice A" and test matrix rows O01–O23. Neither document was changed.
Platform: Windows 11 Pro 10.0.26200, Python 3.14.3, SQLite 3.50.4.

## Spec Summary

Implemented only slice A in the new module `emporio_agent.services.orders`:

- Types: `OrderStatus`, `EstimateStatus` (type aliases over `Literal`), `BUSINESS_TIMEZONE = timezone(timedelta(hours=-3), "UTC-03:00")`.
- `CustomerContext(customer_id)`: `@dataclass(frozen=True, slots=True)`; `__post_init__` requires `type(customer_id) is int` (else `TypeError("customer_id must be an int")`) and `1 <= customer_id <= SQLITE_MAX_INTEGER` (else `ValueError("customer_id out of range")`). Fixed messages, never the value.
- `OrderView` (frozen dataclass), partial for slice A: `order_id`, `status`, `total`, `tracking_code`, `estimated_delivery`, `estimate_status`, `business_date`. No `items`, no `provenance`.
- `OrderInputError(field, code)` (`ValueError` subclass, `str` = `"<field>: <code>"`) and `OrderDataError(reason)` (`str` = reason); `args == (str(error),)` for both.
- `get_order(connection, order_id, *, customer, clock) -> OrderView | None`:
  1. `order_id` validated (`invalid_type`, `out_of_range`) before anything else.
  2. `customer is None` returns `None`; any type other than `CustomerContext` raises `TypeError` (caller defect).
  3. One fixed statement on an own cursor with `row_factory = None`:
     `SELECT order_id, status, total_cents, tracking_code, estimated_delivery FROM orders WHERE order_id = ? AND customer_id = ?`, bound with `(order_id, customer.customer_id)`. No row returns `None` without reading the clock.
  4. `clock()` read exactly once; non-`datetime` raises `TypeError("clock must return a datetime")`, naive raises `ValueError("clock must return a timezone-aware datetime")`; business date is `clock().astimezone(BUSINESS_TIMEZONE).date()`. The clock is read outside the `invalid_row` conversion.
  5. Header checks of spec G11 for the fields used (`order_id` int and equal to the request, `status` in `ORDER_STATUSES`, `total_cents` int `>= 0`, `tracking_code` `None` or non-blank `str`, `estimated_delivery` `None` or `str` accepted by `parse_date`). Any `TypeError`/`ValueError` (including `FieldError`) becomes `OrderDataError("invalid_row")` `from None`.
  6. `total = cents_to_decimal(total_cents)`; `estimate_status` by `<`, `==`, `>` against the business date, or `not_registered`.

Not implemented (slices B/C): `OrderItem`, `OrderProvenance`, `items`, `provenance`, items SQL, product reads, `read_preparation_info` use, `query_failed` mapping, `orders_kit` edits `renamed`, `corrupt`, `observe`; the `get_order` tool, Pydantic, session object, CLI, OpenAI. No stub or type for them exists.

## Gap Analysis

No conflict with the approved contract was found and no P-decision was reinterpreted. Points resolved without adding a rule, for review:

1. `ClockSpy` lives in `tests/integration/conftest.py` as the plan says and is exposed as `orders_kit.ClockSpy`. Unit tests in `tests/unit/` cannot see that conftest, so `tests/unit/test_order_rules.py` has a local four-line `CountingClock` with the same behavior (fixed value, counted calls). No conftest was created under `tests/unit/`.
2. `orders_kit.build(base)` takes no `edit` argument in this slice: the only planned edit (`renamed`) belongs to slice B and was not added. `orders_kit.query` and `orders_kit.snapshot` reuse the existing `catalog_query` and `catalog_snapshot` (same independent `mode=ro` reader; not `connect_readonly`). `snapshot` is not used by any slice A test (O58 is slice C).
3. Clock type errors. The plan (O22) requires `TypeError` for a clock returning `date` and `ValueError` with the `prepare_database` text for a naive `datetime`. `prepare_database` raises `ValueError` for both. The service follows the plan: `TypeError("clock must return a datetime")` for a non-`datetime`, `ValueError("clock must return a timezone-aware datetime")` for a naive one.
4. `query_failed`. Spec G13 maps `sqlite3.Error` other than `ProgrammingError` to `OrderDataError("query_failed")`; the plan places that mapping in slice C ("only what C tests prove missing (for example `query_failed` mapping …)"). In slice A an `OperationalError` from the order query (for example a dropped `orders` table) propagates unchanged. Not tested here.
5. Header row checks (G11) are implemented in slice A as the plan's file table requires, but no slice A case damages a row: they are exercised only by slice C (O47–O50). Mutation M10 below confirms that removing the status check survives slice A.
6. `customer` type check is `type(customer) is not CustomerContext` (exact type), consistent with the `type(x) is int` rule used for IDs.
7. Business time zone: fixed UTC−03:00, as approved (Q4). Wrong if Brazil reintroduces daylight saving time; tested only against fixed instants.
8. Output size of the future tool stays pending (Q3): no limit and no truncation were introduced. No quality note (Q5) is read or returned.

## Implementation Plan (executed)

1. Baseline confirmed: branch `feature/data-preparation`, HEAD `9870cf8`, `git status --short` empty.
2. Read spec, plan, `schema.py`, `readonly.py`, `parsing.py`, `errors.py`, `preparation.py` (clock rule), `services/catalog.py` (conventions), `tests/integration/conftest.py`, `tests/unit/test_catalog_rules.py`.
3. Appended `orders_kit` (rows, `build`, `query`, `snapshot`, `ClockSpy`, constants) to `tests/integration/conftest.py`. No import added; the block imports no order module. `git diff --numstat`: 167 added, 0 removed.
4. Wrote `tests/unit/test_order_rules.py` and `tests/integration/test_orders_access.py` before the module.
5. RED.
6. Wrote `src/emporio_agent/services/orders.py`.
7. GREEN, mutation checks, restoration check, full gates, this report, Ruff again.

## Test Checklist

53 collected items in 33 test functions: `tests/unit/test_order_rules.py` 32 items (15 functions), `tests/integration/test_orders_access.py` 21 items (18 functions).

| Case | Test (items) |
| --- | --- |
| O01 | `test_o01_authorized_order_header` (1): `type is OrderView`; `asdict` equals the literal dict of the seven slice A fields |
| O02 | `test_o02_other_customers_order_is_none` (1) |
| O03 | `test_o03_missing_order_is_none` (1) |
| O04 | `test_o04_no_session_is_none` (1): 5001 and 6001; clock not called |
| O05 | `test_o05_second_customer_sees_only_own_order` (1): 6001 fields for C2; 5001 `None` |
| O06 | `test_o06_customer_without_orders_and_unknown_customer` (1): C3 and 424242 × 5001, 6001; clock not called |
| O07 | `test_o07_largest_integer_order_id_is_none` (1) |
| O08 | `test_o08_signature_has_no_customer_id` (1), `test_o08_extra_customer_id_keyword_is_rejected` (1), `test_o08_customer_and_clock_are_required` (1) |
| O09 | `test_o09_customer_id_wrong_type` (5), `test_o09_customer_id_out_of_range` (3, value absent from surfaces), `test_o09_customer_context_error_never_contains_the_value` (1), `test_o09_customer_context_accepts_bounds` (1) |
| O10 | `test_o10_customer_context_is_frozen_with_slots` (1), `test_o10_wrong_customer_type_raises_type_error` (3: `1`, dict, `"1"`; closed connection; clock 0) |
| O11 | `test_o11_order_id_wrong_type` (6): `True`, `False`, `"5001"`, `5001.0`, `Decimal(5001)`, `None`; closed connection |
| O12 | `test_o12_order_id_out_of_range` (3): `0`, `-1`, `2**63`; closed connection |
| O13 | `test_o13_order_id_checked_before_missing_session` (1), `test_o13_order_id_checked_before_wrong_customer_type` (1) |
| O14 | `test_o14_no_session_returns_none_without_query_or_clock` (1) |
| O15 | `test_o15_order_input_error_never_contains_the_value` (2): `"SENTINEL-7781"`, `2**63 + 7781`; `str`, `args`, `repr`, `format_exception_only` |
| O16 | `test_o16_total_is_the_stored_decimal` (1): independent `SELECT total_cents` equals `[(5001, 150000), (5004, 8970)]`; `Decimal("1500.00")`, `"1500.00"`, `!= 2200.00`, `!= 2100.00`; 5004 `"89.70"` |
| O17 | `test_o17_status_exactly_as_stored` (1) |
| O18 | `test_o18_absent_tracking_and_estimate` (1) |
| O19 | `test_o19_estimate_comparison_keeps_registered_date` (1): `passed`/`due_today`/`upcoming`; date-valued fields exactly `estimated_delivery`, `business_date` |
| O20 | `test_o20_no_receipt_eligibility_or_deadline_field` (1) |
| O21 | `test_o21_business_date_and_estimate_status` (6): the six clocks of the plan table × 5001–5004 (C1) and 6001 (C2) |
| O22 | `test_o22_naive_clock_is_rejected` (1), `test_o22_clock_returning_date_is_rejected` (1), `test_o22_clock_read_once_and_only_for_authorized_order` (1) |
| O23 | `test_o23_business_timezone_is_fixed_utc_minus_three` (1) |

## Test Strategy

- Integration: real SQLite file in `tmp_path`, built by the real `prepare_database` from the synthetic `orders_kit` rows of the plan (3 customers with sentinels, 5 products, 1 promotion, orders 5001–5004 of customer 1 and 6001–6002 of customer 2, 6 order items), fixed preparation clock `2026-11-02T12:00:00+00:00`, opened with `connect_readonly` per call.
- Unit: closed `sqlite3` connection for every validation case, so a query before validation would raise `ProgrammingError`.
- Oracles: literals from the plan (hand-calculated totals, dates and estimate statuses) and an independent parameterized `SELECT total_cents` through a separate `mode=ro` connection. `get_order`, `cents_to_decimal` and `parse_date` never compute an expected value.
- Clocks: `ClockSpy` / `CountingClock` with fixed aware datetimes; no test reads the real clock.
- No mock of authorization, SQL, dates, relations or persistence. No private CSV, network, OpenAI or new dependency.

## Compliance Report

### RED

Before `orders.py` existed:

- `uv run --frozen pytest tests/unit/test_order_rules.py tests/integration/test_orders_access.py -q` → exit 2; `2 errors during collection`, both `ModuleNotFoundError: No module named 'emporio_agent.services.orders'`. Matches the plan's expected RED.
- `uv run --frozen pytest -q --ignore=tests/unit/test_order_rules.py --ignore=tests/integration/test_orders_access.py` → `1176 passed, 2 skipped`: the appended fixture did not block collection of earlier tests.

### GREEN

- `uv run --frozen pytest tests/unit/test_order_rules.py tests/integration/test_orders_access.py -q` → exit 0, `53 passed`.

### Mutation checks

Run after GREEN, one at a time, by a scratch script outside the repository: replace exact text in `orders.py` (each replacement asserted unique), run the two new test files, restore the original bytes, assert the SHA-256 again.

| ID | Mutation | Result | Failing tests |
| --- | --- | --- | --- |
| M1 | `WHERE order_id = ?` only, bound `(order_id,)` | killed (4 failed) | O02, O05, O06, O22 (call count) |
| M2 | extra `customer_id` keyword used in the binding | killed (2) | both O08 signature/keyword tests |
| M3 | session check before `order_id` validation | killed (1) | O13 (`True`, `customer=None`) |
| M4 | `order_id` validation after the query | killed (13) | O11, O12, O13 (both), O15 |
| M5 | `args` extended with `repr(order_id)` | killed (1) | O15 |
| M6 | `Decimal(cents / 100)` | killed (2) | O05 (`777.77`), O16 |
| M7 | business date in UTC | killed (3) | O21 `utc_0259`, `minus3_2330`, `prev_utc_0259` |
| M8 | `<=` for `passed` | killed (8) | O01, O19, O21 |
| M9a | extra `clock()` before the row check | killed (2) | O06, O22 (call count) |
| M9b | clock read twice | killed (1) | O22 (call count) |
| M10 | status check removed from the header | **survived** (53 passed) | none; expected, invalid rows are slice C (O47) |
| M11 | clock call moved inside the `invalid_row` conversion | killed (2) | O22 naive and `date` clocks |

Restoration: SHA-256 of `src/emporio_agent/services/orders.py` after the run `af02177a666380c51d2ded669813844ef1e94991669401272e7790cda0da6f01`, equal to the value before the run. M7 was re-run once by hand to list parameter IDs and restored from a copy; the same hash was verified afterwards.

Survivor: M10 (and, by the same reasoning, every other G11 header check) is not detected in slice A because no slice A case stores an invalid row. Detection is planned in slice C (O47–O50).

### Gates (after restoration)

| Command | Exit | Result |
| --- | --- | --- |
| `uv run --frozen pytest tests/unit/test_order_rules.py tests/integration/test_orders_access.py -q` | 0 | 53 passed |
| `uv run --frozen pytest tests/unit/test_pricing.py -q` | 0 | 37 passed |
| `uv run --frozen pytest -q -rs` | 0 | 1229 passed, 2 skipped (`test_readonly_connection.py:599`, `:612`, Windows `?` file names) |
| `uv run --frozen ruff check --no-cache .` | 0 | All checks passed |
| `uv run --frozen ruff format --check --no-cache .` | 0 | 72 files already formatted |

After this report was written, both Ruff commands were run again without cache: exit 0, `All checks passed!` and `73 files already formatted` (Ruff 0.17.3 also checks Markdown files, so the new report is the 73rd file).

1229 = 1176 earlier passes + 53 new items; the 1178 earlier items are preserved (1176 passed, same 2 skips). No new failure or skip.

### Files

| Path | Change |
| --- | --- |
| `src/emporio_agent/services/orders.py` | created |
| `tests/unit/test_order_rules.py` | created |
| `tests/integration/test_orders_access.py` | created |
| `tests/integration/conftest.py` | appended only (167 added, 0 removed) |
| `docs/validation/05-01-orders-access.md` | created (this report) |

Nothing else changed: no data layer, catalog, pricing, policies, earlier tests, static fixtures, specs, plans, README, configuration or lockfile. Git state: branch `feature/data-preparation`, HEAD `9870cf8`, uncommitted.

### Limits

- Synthetic data on one Windows machine. The supplied orders are not used.
- These tests do not prove real authentication, the future tool schema, the session object or the behavior of the future model. `CustomerContext` is a demonstration convention.
- Orders are not complete: items, current names and provenance are slice B; integrated isolation (authorizer), invalid rows, `query_failed`, leakage and no-write evidence are slice C.
- Equal results for foreign and missing orders are shown at the result level only here; statement-level equality is slice C (O42). Timing is not claimed.
- Fixed UTC−03:00 offset tested against fixed instants; daylight saving time out of scope.
- Output size bound for the future tool remains open (Q3).

Status: slice A GREEN. Slices B and C not started. Stopped for review.
