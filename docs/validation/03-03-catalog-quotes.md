# Slice 03, part C validation: quote integrated with pricing

Date: 2026-11-03. Baseline: `feature/data-preparation`, commit `433fb61`, clean working tree, `uv run --frozen pytest -q -rs` before any change: `903 passed, 2 skipped` (exit 0; the two skips are the known Windows `?` file-name cases in `tests/integration/test_readonly_connection.py:599` and `:612`). No divergence from the expected baseline. No commit, push, PR or merge.
Contract: `docs/specs/03-catalog.md` revision R1 (rules A1–A16, decisions P-01–P-15) and `docs/plans/03-catalog.md` section "Slice C" and test matrix rows C56–C67, C76. Neither document was changed.
Platform: Windows 11 Pro 10.0.26200, Python 3.14.3, SQLite 3.50.4.

## Spec Summary

Added slice C to `emporio_agent.services.catalog`:

- `PaymentMethod = Literal["regular", "pix"]` and the frozen dataclass `ProductQuote` (fields as in the spec: `product`, `payment_method`, `list_price`, `final_price`, `discount_percent`, `discount_source`, `promotion`, `pix_not_combined`, `provenance`).
- `quote_product(connection, product_id, *, payment_method) -> ProductQuote | None`:
  - `payment_method` is keyword-only and has no default;
  - `product_id` is validated first (existing `_check_product_id`), then `payment_method` (`type(value) is str`, else `invalid_type`; exactly `"regular"` or `"pix"`, else `unknown_value`; no case folding, no stripping), both before any database access;
  - fixed parameterized `SELECT` statements: product joined with its category (summary columns only, no `description` and no `specs_json`), all promotions of the product, product notes; then `read_preparation_info` for provenance. A missing product returns `None` before promotions, notes or provenance are read (same order as `get_product`);
  - every row read is validated by the shared slice A/B helpers (`_promotion`, `_note`, `_summary`); `TypeError` or `ValueError` becomes `CatalogDataError("invalid_row")` with `from None`;
  - `product` is the shared `ProductSummary` (regular price, availability, notes); `list_price`, `final_price`, `discount_percent` and `discount_source` are copied from the `PriceQuote` returned by `quote_price(summary.list_price, payment_method=..., promotions=all promotions)`; `promotion` is chosen by the shared `_applied_promotion` (largest active, lowest `promotion_id` on a tie); `pix_not_combined = payment_method == "pix" and discount_source == "promotion"`;
  - unavailable products are quoted with their availability fields; nothing refuses them.
- Shared private helper `_pricing_promotions` (extracted from `_summary`): builds the `Promotion` list, active and inactive. Behavior of `_summary` is unchanged.

Not implemented: tools, JSON serialization, Pydantic, CLI, OpenAI, policies. `pricing.py`, the search and availability algorithms and the slice A/B public behavior are unchanged.

## Gap Analysis

Decisions P-01–P-15 were applied unchanged. No blocking ambiguity was found. Points resolved without adding a rule, for review:

1. Query used by the quote. `quote_product` does not reuse `_PRODUCT_SQL` (which reads `description` and `specs_json` for `get_product`). It uses a new fixed statement `_SUMMARY_SQL` with the eight summary columns, so the quote never reads or parses text it does not return (task brief). Spec G15 "rows the call reads" (target product, its category, all its promotions, its notes) still holds. C67 asserts this with the authorizer (one fact beyond the matrix row, see item 5).
2. Two `quote_price` calls per quote. The shared `_summary` prices the product in regular mode (its `effective_price` is always the regular price, as the spec says for `ProductSummary`); the quote then calls `quote_price` once more with the requested method. For `regular` both calls return the same values. No money is computed in `catalog.py`; `summary.list_price` is the `Decimal` already returned by the first `quote_price` call.
3. Test placement. The plan's slice C file table lists C63 only in `tests/integration/test_catalog_quotes.py`; the matrix types it `I + U` and the task brief puts the unit part in `tests/unit/test_catalog_rules.py`. Closed-connection checks are in the unit file, `None` for a missing product is in the quotes file.
4. Product IDs in C63 (unit). The matrix names only `True`. The test also passes `0`, `2**63`, `"201"` and `None`, because the brief requires invalid IDs to fail before the closed connection is touched; the expected codes come from P-05 (same as C03).
5. Facts asserted beyond the matrix rows, all derived from the spec or the brief; no expectation was relaxed:
   - C63: `product_id` is checked before `payment_method` (`True` with `"PIX"` gives `product_id: invalid_type`; P-05 parameter order); `999` and `2**63 - 1` return `None` under both methods.
   - C64: `PaymentMethod` has the arguments `("regular", "pix")`; a positional `payment_method` raises `TypeError` (spec signature with `*`).
   - C56: quote provenance equals `get_product(201).provenance`.
   - C57–C62, C66: `quote.product.effective_price` equals the hand-written regular price and `quote.product.promotion` equals `quote.promotion`.
   - C65: `quote.product == get_product(id).summary` for all 17 IDs.
   - C67: quotes never read `products.description` or `products.specs_json`. The authorizer and the snapshot checks are two test functions.
   - C76: `__context__` is the `ValueError` raised in `quote_price` (as C71); no surface of the error contains `20000`, `200` or `Promo Folk`; snapshot unchanged.
6. Surviving extra mutation MC10 (quote prices with active promotions only). It is equivalent with the current structure: `_summary` passes all promotions, active and inactive, to `quote_price` in regular mode before the requested-method call, so an invalid inactive promotion already fails the quote, and inactive promotions never change a price. No quote case combines `percent_over_inactive` with `quote_product`; such a case is not in the approved matrix and was not added.
7. Earlier limits kept, neither fixed nor extended: non-`int` stock on a product that is not active has no specific case (the quote uses the same `_summary` checks, so the limit applies to quotes too); MB17 (raw versus normalized text length) is not distinguished by the matrix; a damaged row combined with an unresolved category has no case; orphan references depend on preparation.
8. The spec and plan headers still read "proposed"; the task brief states R1 is approved. Documents left unchanged.

## Implementation Plan (executed)

1. Baseline checked: branch, `433fb61`, clean tree, `903 passed, 2 skipped`.
2. Read spec R1, plan, `catalog.py`, `pricing.py`, the slice A and B reports, `tests/integration/conftest.py` (`catalog_kit`) and the slice A/B tests.
3. Wrote the tests before the implementation: C63 (unit part) and C64 appended to `tests/unit/test_catalog_rules.py`, new `tests/integration/test_catalog_quotes.py`, C76 appended to `tests/integration/test_catalog_invalid_rows.py`. `conftest.py` unchanged (the `percent_over` variant already exists).
4. RED.
5. Implemented slice C in `src/emporio_agent/services/catalog.py`.
6. GREEN, mutation checks, restoration check, full gates, this report, Ruff again.

## Test Checklist

35 new collected items in 22 new test functions: `tests/unit/test_catalog_rules.py` +14 items (55 → 69), `tests/integration/test_catalog_quotes.py` 20 items (new), `tests/integration/test_catalog_invalid_rows.py` +1 item (24 → 25). `tests/integration/test_catalog_products.py` (18) and `tests/integration/test_catalog_search.py` (49) unchanged.

| Case | Type | Test (items) |
| --- | --- | --- |
| C56 | I | `test_c56_regular_quote_without_promotion` (1): 201 regular, list and final 899.90, 0, `none`, promotion None, flag False, available, provenance |
| C57 | I | `test_c57_pix_discount_without_active_promotion` (3): 201 854.91, 214 18.91, 217 2375.00 (inactive 307 ignored); 5, `pix`, flag False; summary keeps the regular price |
| C58 | I | `test_c58_promotion_is_not_combined_with_pix` (2): 202 `pix` 990.00 / 10 / `promotion` / 301 / flag True; `regular` same price, flag False |
| C59 | I | `test_c59_zero_percent_promotion_blocks_pix` (1): 210 700.00, 0, `promotion`, 302, flag True |
| C60 | I | `test_c60_full_promotion_under_pix` (1): 211 0.00, 100, `promotion`, 303 |
| C61 | I | `test_c61_largest_promotion_only_under_pix` (1): 212 266.66, 20, 305 |
| C62 | I | `test_c62_unavailable_product_is_quoted` (3): 208 regular 450.00 `discontinued`; 207 pix 475.00 `out_of_stock`; 209 regular 300.00 `coming_soon`; all `is_available` False; notes |
| C63 | U + I | unit: `test_c63_invalid_product_id_rejected_before_any_query` (5), `test_c63_product_id_checked_before_payment_method` (1); integration: `test_c63_missing_product_returns_none` (4) |
| C64 | U | `test_c64_payment_method_type` (1), `test_c64_invalid_payment_method_rejected_before_any_query` (5: `"PIX"`, `""`, `"credit_3x"` `unknown_value`; `None`, `True` `invalid_type`), `test_c64_payment_method_is_required` (1), `test_c64_payment_method_is_keyword_only` (1) |
| C65 | I | `test_c65_search_detail_and_quote_agree` (1): offsets 0, 5, 10, 15; `next_offset` 5, 10, 15, None; 17 distinct IDs in the plan order, including 207, 208, 209; search, `get_product` and regular quote prices equal the literal table; list prices equal the fixture table |
| C66 | I | `test_c66_flagged_product_quote_has_no_text` (1): 135 1350.00, 308, notes (`catalog_text_conflict`,); no text field in `ProductQuote` or `ProductSummary`; stored description and specs absent from `repr` |
| C67 | I | `test_c67_quotes_read_only_catalog_tables` (1): authorizer around all C56–C66 quotes plus the C65 calls; `test_c67_quotes_leave_database_unchanged` (1): snapshot |
| C76 | I | `test_c76_invalid_promotion_fails_the_quote` (1): `typeof` first; 202 `pix` `invalid_row` sanitized; connection open; 201 `pix` 854.91 |

13 planned IDs, all covered.

## Test Strategy

- Real SQLite files in `tmp_path`, built by the real `prepare_database` from the synthetic `catalog_kit` rows, opened with `connect_readonly`. `conftest.py` reused unchanged.
- Oracles: money values and IDs written literally from the plan ("Hand-calculated effective prices", the PIX line, the fixture table, "Expected orderings"). `quote_price` and `availability_of` never compute an expectation. Money is compared as exact text (`format(value, "f")`) so the two decimal places are checked; percentages by `Decimal` value.
- Validation tests use a closed connection, so any query before validation raises `ProgrammingError` instead of `CatalogInputError`.
- Read-only proof: real SQLite authorizer (records, never denies) and `snapshot` (SHA-256, listing, ordered rows of every table).
- Invalid row (C76): existing variant `percent_over` applied by `catalog_kit.corrupt` before the read-only open; `typeof` checked through an independent `mode=ro` connection.
- No mock of SQL, pricing, availability, relations or persistence. No private data, no OpenAI, no network, no new dependency.

## Compliance Report

### RED

`uv run --frozen pytest tests/unit/test_catalog_rules.py tests/integration/test_catalog_products.py tests/integration/test_catalog_search.py tests/integration/test_catalog_invalid_rows.py tests/integration/test_catalog_quotes.py -q` — exit 2, `3 errors in 0.19s`, all at collection:

- `tests/unit/test_catalog_rules.py`: `ImportError: cannot import name 'PaymentMethod' from 'emporio_agent.services.catalog'`
- `tests/integration/test_catalog_invalid_rows.py`: `ImportError: cannot import name 'quote_product' from 'emporio_agent.services.catalog'`
- `tests/integration/test_catalog_quotes.py`: `ImportError: cannot import name 'ProductQuote' from 'emporio_agent.services.catalog'`

Same cause as the plan's expected RED (missing names; each file fails on the first missing name in its sorted import list). `test_catalog_products.py` and `test_catalog_search.py`: `67 passed`. Full suite with the three blocked files ignored: `824 passed, 2 skipped` (903 − 55 − 24 earlier items in the blocked files).

### GREEN

Same command after the implementation: exit 0, `181 passed` (146 slice A/B + 35 slice C). The first GREEN run needed no change to tests or code.

### Mutation checks (after GREEN, one at a time, then restored)

Run by a scratch script outside the repository that rewrites `catalog.py` for one mutation, runs the five catalog files, and writes the original bytes back. SHA-256 of `catalog.py` before and after all mutations: `e5c910a49f331c3bc3e46b008ce3d79d4b7e9b10e726a599ff1303ebdef2641d` (equal; also checked with `sha256sum` outside the script).

| ID | Mutation (plan behaviors first) | Observed failures |
| --- | --- | --- |
| MC01 | PIX applied on top of the promotion price | 3: C58 `pix`, C59, C61 |
| MC02 | active 0% promotion treated as absent in the quote | 1: C59 |
| MC03a | active promotions summed | 5: C61, C65, C66, C67 ×2 |
| MC03b | active promotions chained | 3: C61, C65, C66 |
| MC04 | unavailable product not quoted (`None`) | 4: C62 ×3, C65 |
| MC05a | `payment_method` default `"regular"` | 1: C64 required |
| MC05b | `payment_method` stripped and case-folded | 1: C64 `"PIX"` |
| MC06a | quote path drops promotions that fail validation | 1: C76 |
| MC06b | quote path without the `invalid_row` conversion | 1: C76 |
| MC07 | `pix_not_combined` ignores the discount source (extra) | 4: C57 ×3, C62 207 |
| MC08 | `payment_method` validated after the first query (extra) | 5: C64 ×5 (`ProgrammingError`) |
| MC09 | `payment_method` validated before `product_id` (extra) | 1: C63 order |
| MC10 | quote call with active promotions only (extra) | **survives** (0): equivalent, see Gap Analysis item 6 |
| MC11 | quote reads `description` and `specs_json` (extra) | 1: C67 authorizer |
| MC12 | summary priced with the requested method (extra) | 4: C57 ×3, C62 207 |

Every behavior the plan lists for slice C is detected. No expectation was changed to kill a mutation.

### Final gates (after restoration)

| Command | Exit | Result |
| --- | --- | --- |
| `uv run --frozen pytest tests/unit/test_catalog_rules.py tests/integration/test_catalog_products.py tests/integration/test_catalog_search.py tests/integration/test_catalog_invalid_rows.py tests/integration/test_catalog_quotes.py -q` | 0 | `181 passed` |
| `uv run --frozen pytest tests/unit/test_pricing.py -q` | 0 | `37 passed` |
| `uv run --frozen pytest -q -rs` | 0 | `938 passed, 2 skipped` (903 earlier passed + 35 new; skips: `test_readonly_connection.py:599` and `:612`, Windows does not allow `?` in file names) |
| `uv run --frozen ruff check .` | 0 | `All checks passed!` |
| `uv run --frozen ruff format --check .` | 0 | `51 files already formatted` |

The 905 earlier items are preserved: 903 passed and the same 2 skipped.

Ruff repeated after this report was written: `ruff check .` exit 0, `All checks passed!`; `ruff format --check .` exit 0, `52 files already formatted` (the count includes this new Markdown file).

### Files

Created: `tests/integration/test_catalog_quotes.py`, this report.
Changed: `src/emporio_agent/services/catalog.py` (module docstring, `PaymentMethod`, `_PAYMENT_METHODS`, `_SUMMARY_SQL`, `ProductQuote`, `_pricing_promotions` extracted from `_summary`, `_check_payment_method`, `_quote`, `quote_product`), `tests/unit/test_catalog_rules.py` (docstring, two imports, slice C cases appended), `tests/integration/test_catalog_invalid_rows.py` (docstring, one import, C76 appended). Removed lines in the diff: only the old docstrings, the old import line and the inline `Promotion` list in `_summary`. No earlier expectation changed.
Unchanged (checked with `git status` and `git diff`): `src/emporio_agent/services/pricing.py`, everything under `src/emporio_agent/data/`, `tests/integration/conftest.py`, `tests/integration/test_catalog_products.py`, `tests/integration/test_catalog_search.py`, other earlier tests, `tests/fixtures/`, README, specs, plans, earlier reports, `pyproject.toml`, `uv.lock`.

### Limits

- Synthetic data on one Windows machine; the real catalog is not used.
- A quote is a unit price for one product; it creates no purchase and is not an offer to sell. Quantity, installments, shipping and other payment methods are out of scope.
- Surviving mutation MC10 (equivalent with the current structure). No case quotes a product whose inactive promotion is invalid.
- Earlier limits kept: non-`int` stock on a product that is not active has no case; MB17 survives; a damaged row combined with an unresolved category, and orphan references, have no case.
- The authorizer proof covers the C56–C66 calls on one connection. It says nothing about the future tool layer, JSON money text or model behavior.

Status: slice C GREEN, awaiting review. No later slice started.
