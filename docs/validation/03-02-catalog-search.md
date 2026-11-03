# Slice 03, part B validation: search, filters, normalization and pagination

Date: 2026-11-02. Baseline: `feature/data-preparation`, commit `39598c0`, clean working tree, `uv run --frozen pytest -q -rs` before any change: `814 passed, 2 skipped` (exit 0; the two skips are the known Windows `?` file-name cases in `tests/integration/test_readonly_connection.py:599` and `:612`). No divergence from the expected baseline. No commit, push, PR or merge.
Contract: `docs/specs/03-catalog.md` revision R1 (rules A1–A16, decisions P-01–P-15) and `docs/plans/03-catalog.md` section "Slice B" and test matrix rows C21–C55, C75. Neither document was changed.
Platform: Windows 11 Pro 10.0.26200, Python 3.14.3, SQLite 3.50.4.

## Spec Summary

Added slice B to `emporio_agent.services.catalog`:

- `CategoryResolution` (`not_requested`, `resolved`, `unknown`, `ambiguous`) and the frozen dataclass `SearchResult` (fields as in the spec).
- Constants `PAGE_SIZE = 5`, `MAX_TEXT_LENGTH = 4000`, `MAX_PRICE_BRL = Decimal("92233720368547758.07")`.
- `normalize_search_text(text)`: NFKD, drop characters of Unicode category `Mn`, `casefold()`, `split()`, join with one space (P-01).
- `search_products(connection, *, query=None, category=None, max_price_brl=None, available_only=True, offset=0) -> SearchResult`:
  - argument validation in parameter order before any query (P-03, P-04, P-05);
  - four fixed `SELECT` statements without bound input (all categories; all products joined with their category, without `description` and `specs_json`; all promotions; all product notes, `entity` bound to the constant `'product'`), plus `read_preparation_info` for provenance;
  - every category, product, promotion and note row is validated, and every product is priced, before any filter (P-15);
  - category resolution exact name first, then normalized name; `unknown`/`ambiguous` keep the filter and return an empty page with candidates in `category_id` order (P-02);
  - filters in Python: availability, resolved `category_id`, every query token as a substring of the normalized name, `effective_price <= budget`; sort by `(effective_price, product_id)`; page `[offset : offset + 5]`; `next_offset` (A8).
- Shared private helpers: `_summary` (extracted from `_detail`; validation and `quote_price` in regular mode, used by both functions) and `_provenance` (extracted from `get_product`). Search never calls `get_product`.

Not implemented (slice C or later): `PaymentMethod`, `ProductQuote`, `quote_product`, tools, Pydantic, CLI, OpenAI. No stub exists for them.

## Gap Analysis

Decisions P-01–P-15 were applied unchanged. No blocking ambiguity was found. Points resolved without adding a rule, for review:

1. Order of validation versus category resolution. The spec algorithm lists category resolution (step 3) before pricing (step 4); P-15 says search "validates all of them before filtering". Implemented: all rows are validated and priced before the category is resolved, so a damaged row fails every search, including one whose category is `unknown` or `ambiguous`. Results on valid data are the same either way. No case covers a damaged row combined with an unresolved category.
2. Read order. `search_products` reads provenance first, then categories, products, promotions and notes (spec step 2 order). `get_product` keeps its slice A order (provenance last).
3. Test placement. The plan's slice B file table lists C49–C51 for the unit file, but the matrix types C50 as `I` (it needs totals from a database); C50 is in `tests/integration/test_catalog_search.py`. C42 and C43 are `U + I`: the closed-connection parts are unit tests, the database parts (None → 14 matches; 4,000-code-point text accepted) are in the search file. The plan's integration file row does not list them.
4. C52 asserts one more fact than the matrix row: the authorizer never records a `SQLITE_READ` of `products.description` or `products.specs_json`. This comes from P-15 ("`search_products` does not read `description` or `specs_json`") and the task brief; no expectation was relaxed.
5. `catalog_kit` row edit. The plan says `build` "optionally applies one edit to a deep copy" of the rows; `collision` is implemented that way (category `4 violoes` appended before preparation, path of the prepared file returned). The database edits `schema_v2` and `no_products_table` keep their slice A behavior (copy plus writable connection). The edit required a small change inside `catalog_build`; with `edit=None` the rows, the file and the path are the same as before (all slice A cases pass).
6. `get_product` refactor. Its public behavior is unchanged (all 57 slice A items pass). Inside `_detail`, promotions and notes are now validated before the product fields instead of after; each invalid value still yields `CatalogDataError("invalid_row")`, and the C69/C71 `__context__` checks still find `quote_price`.
7. Plan prediction that did not hold: "sort without `product_id` tie-break: C32 fails". It survives (mutation MB05a): products are read with `ORDER BY p.product_id` and Python's sort is stable, so ties already come out in `product_id` order. The explicit tie-break is kept because A8 requires it and it does not depend on the SQL order. Reversing the tie-break (MB05b) is detected by 12 items.
8. No case distinguishes raw-length counting from normalized-length counting (mutation MB17 survives). `("e" + U+0301) * 2000` and `"a" * 4001` give the same results under both rules. A case such as precomposed `"é" * 4000` (4,000 raw code points, 8,000 after NFKD) would distinguish them; it is not in the approved matrix and was not added.
9. A promotion whose `product_id` matches no product is type-checked by search but never priced, so its percentage range is not checked. References are guaranteed by preparation (spec G15, "depends on the prepared database").
10. Earlier limit (slice A report, Gap Analysis item 5): non-`int` stock on a product that is not active has no specific case. Search uses the same `_summary` checks; this slice neither fixes nor extends that coverage.
11. The spec and plan headers still read "proposed"; the task brief states R1 is approved. Documents left unchanged.

## Implementation Plan (executed)

1. Baseline checked: branch, `39598c0`, clean tree, `814 passed, 2 skipped`.
2. Read spec R1, plan, `catalog.py`, `pricing.py`, the slice A report, `tests/integration/conftest.py`, the slice A tests, and the relevant parts of `parsing.py`, `schema.py` and `preparation.py`.
3. Added to `tests/integration/conftest.py`: row edit `collision`, variants `price_text_other` and `specs_other_product`, and the row-edit branch of `catalog_build`.
4. Wrote the tests before the implementation: unit cases in `tests/unit/test_catalog_rules.py`, new `tests/integration/test_catalog_search.py`, C75 in `tests/integration/test_catalog_invalid_rows.py`.
5. RED.
6. Implemented slice B in `src/emporio_agent/services/catalog.py`.
7. GREEN, mutation checks, restoration check, full gates, this report, Ruff again.

## Test Checklist

89 new collected items in 50 new test functions: `tests/unit/test_catalog_rules.py` +38 items (17 → 55), `tests/integration/test_catalog_search.py` 49 items (new), `tests/integration/test_catalog_invalid_rows.py` +2 items (22 → 24). `tests/integration/test_catalog_products.py` unchanged (18).

| Case | Type | Test (items) |
| --- | --- | --- |
| C21 | I | `test_c21_default_search_returns_available_products` (1): page, total 14, next 5, echoes, `PAGE_SIZE == 5`, all pages in the 14-ID order |
| C22 | I | `test_c22_available_only_false_includes_unavailable` (1): total 17, page [211, 214, 212, 209, 208]; 209/208/207 states; 17-ID order |
| C23 | I | `test_c23_category_and_budget_filter_before_pagination` (1) |
| C24 | I | `test_c24_budget_boundaries_are_inclusive` (1): 8 / 9 / 10 |
| C25 | I | `test_c25_budget_uses_promotion_price` (1) |
| C26 | I | `test_c26_pix_price_never_widens_the_budget` (1) |
| C27 | I | `test_c27_zero_budget` (1) |
| C28 | I | `test_c28_largest_promotion_only` (1): 212 266.66 / 305; prep_kit default rows P11 17.41 / 102 |
| C29 | I | `test_c29_zero_percent_promotion_is_shown` (1) |
| C30 | I | `test_c30_tied_promotions_in_search` (1) |
| C31 | I | `test_c31_inactive_promotion_never_applied` (1) |
| C32 | I | `test_c32_pages_follow_price_then_id` (1) |
| C33 | I | `test_c33_arbitrary_offset` (1) |
| C34 | I | `test_c34_offset_at_and_past_the_end` (4): 10, 11, 12, 10**6 |
| C35 | U | `test_c35_invalid_offset_rejected_before_any_query` (5) |
| C36 | U | `test_c36_available_only_must_be_bool` (4) |
| C37 | U + I | `test_c37_c38_normalize_search_text` (1 of 4 rows), `test_c37_case_and_accent_insensitive` (3), `test_c37_accented_names` (3) |
| C38 | U + I | `test_c37_c38_normalize_search_text` (3 of 4 rows), `test_c38_tokens` (3) |
| C39 | I | `test_c39_substring_tokens` (1) |
| C40 | I | `test_c40_special_characters_are_literal` (6): each with `snapshot` before and after |
| C41 | I | `test_c41_only_the_name_is_searched` (1) |
| C42 | U + I | `test_c42_blank_text_rejected` (6: `""`, `"   "`, U+0301 × `query`/`category`), `test_c42_none_means_no_filter` (1) |
| C43 | U + I | `test_c43_text_over_limit_rejected` (2), `test_c43_text_at_the_limit_is_accepted` (1): `"a" * 4000` and `("e" + U+0301) * 2000` (length 4000) for query (0 matches) and category (`unknown`); `MAX_TEXT_LENGTH == 4000` |
| C44 | I | `test_c44_category_resolved_by_normalized_name` (3), `test_c44_unknown_category_keeps_the_filter` (1) |
| C45 | I | `test_c45_colliding_categories` (1) |
| C46 | I | `test_c46_resolved_category_without_matches` (1) |
| C47 | I | `test_c47_query_category_and_budget_together` (1) |
| C48 | I | `test_c48_no_product_invented` (1) |
| C49 | U | `test_c49_invalid_budget_rejected_before_any_query` (12) |
| C50 | I | `test_c50_budget_quantized_and_caller_context_ignored` (1): `1000.000` → 9 / `1000.00`; `-0` → [211] / `0.00` unsigned; max → 14; same results under a caller context with precision 3 and `ROUND_DOWN`, whose precision, rounding, traps and flags stay unchanged; `MAX_PRICE_BRL` value |
| C51 | U | `test_c51_text_must_be_str` (4), `test_c51_first_invalid_argument_in_parameter_order` (1) |
| C52 | I | `test_c52_authorizer_sees_only_catalog_reads` (1): C21, two C40 and two C45 searches |
| C53 | I | `test_c53_summaries_carry_no_description_or_specs` (1) |
| C54 | I | `test_c54_items_match_independent_queries` (1) |
| C55 | I | `test_c55_search_provenance` (1) |
| C75 | I | `test_c75_invalid_row_outside_the_filter_fails_search` (1), `test_c75_malformed_specs_do_not_fail_search` (1); both assert `typeof` first |

36 planned IDs, all covered.

## Test Strategy

- Real SQLite files in `tmp_path`, built by the real `prepare_database` from the synthetic `catalog_kit` rows (and, for C28, the existing `prep_kit` default rows), opened with `connect_readonly`.
- Oracles: IDs, orderings and money text written literally from the plan's "Hand-calculated effective prices" and "Expected orderings"; independent parameterized SQL through a separate `mode=ro` connection (C45, C53, C54, C55, C75). `quote_price`, `availability_of` and `normalize_search_text` never compute an expectation; the unit test of `normalize_search_text` compares against literals.
- Validation tests use a closed connection, so any query before validation raises `ProgrammingError` instead of `CatalogInputError`.
- Read-only proof: real SQLite authorizer (records, never denies) and `snapshot` (SHA-256, listing, ordered rows of every table).
- Invalid rows (C75): built by `catalog_kit.corrupt` before the read-only open (untyped table rebuild for `price_text_other`, `PRAGMA ignore_check_constraints` for `specs_other_product`).
- No mock of SQL, filters, pricing, relations or persistence. No private data, no OpenAI, no network.

## Compliance Report

### RED

`uv run --frozen pytest tests/unit/test_catalog_rules.py tests/integration/test_catalog_products.py tests/integration/test_catalog_search.py tests/integration/test_catalog_invalid_rows.py -q` — exit 2, `3 errors in 0.22s`, all at collection:

- `tests/unit/test_catalog_rules.py`: `ImportError: cannot import name 'normalize_search_text' from 'emporio_agent.services.catalog'`
- `tests/integration/test_catalog_search.py`: `ImportError: cannot import name 'MAX_PRICE_BRL' from 'emporio_agent.services.catalog'`
- `tests/integration/test_catalog_invalid_rows.py`: `ImportError: cannot import name 'search_products' from 'emporio_agent.services.catalog'`

Same cause as the plan's expected RED (missing names; the search file imports constants first in sorted order). `tests/integration/test_catalog_products.py` alone: `18 passed`. Full suite with the three blocked files ignored: `775 passed, 2 skipped` (814 − 17 − 22 slice A items in the blocked files).

### GREEN

Same command after the implementation: exit 0, `146 passed` (57 slice A + 89 slice B). The first GREEN run needed no change to tests or code.

### Mutation checks (after GREEN, one at a time, then restored)

Run by a scratch script outside the repository that rewrites `catalog.py` for one mutation, runs the four catalog files, and writes the original bytes back. SHA-256 of `catalog.py` before and after all mutations: `6f2e72d0bf5a01ccdd090db8f25b8ad808a7865c6f7af612db46ccbbc55cdac2` (equal; also checked with `sha256sum` outside the script).

| ID | Mutation (plan behavior first) | Observed failures |
| --- | --- | --- |
| MB01 | SQL `LIKE '%' \|\| query \|\| '%'` instead of token matching | 10: C40 `%` and `_`, C37 ×4, C38 ×2, C47, C52 (`like` function action) |
| MB02a | paginate before filtering | 36: C21–C25, C27–C29, C31–C34, C37, C39, C40, C42, C44, C45, C47, C50, C53, C54, C75 specs |
| MB02b | paginate before sorting | 13, including C23 and C32 |
| MB03 | budget on table price | 5: C23, C24, C25, C27, C50 |
| MB04 | budget on PIX price | 6: C23, C24, C25, C26, C31, C50 |
| MB05a | sort without `product_id` tie-break | **survives** (0): see Gap Analysis item 7 |
| MB05b | reversed `product_id` tie-break | 12, including C32 |
| MB06 | unknown category filter dropped | 2: C44, C43 |
| MB07 | first colliding category picked | 1: C45 |
| MB08 | blank text treated as no filter | 6: C42 ×6 |
| MB09 | description and specs searched | 3: C41, C40 `_`, C52 |
| MB10 | sub-cent budget rounded | 1: C49 `999.999` |
| MB11a | malformed product rows skipped | 1: C75 price |
| MB11b | only rows of the requested category validated | 1: C75 price |
| MB12 | `specs_json` read and parsed during search | 2: C75 specs, C52 |
| MB13 | validation after the first query (extra) | 34: C35, C36, C42, C43, C49, C51 unit items (`ProgrammingError`) |
| MB14 | `bool` accepted as offset (extra) | 1: C35 `True` |
| MB15 | caller decimal context used for the budget (extra) | 1: C50 |
| MB16 | `-0.00` echoed (extra) | 1: C50 |
| MB17 | text length counted after normalization (extra) | **survives** (0): see Gap Analysis item 8 |
| MB18 | `available_only` ignored, always filtering (extra) | 4: C22, C28, C39, C54 |
| MB19 | exact category match step removed (extra) | 1: C45 |

Every behavior the plan lists for slice B is detected except the tie-break removal, which is an equivalent mutant with the current input order. No expectation was changed to kill a mutation.

### Final gates (after restoration)

| Command | Exit | Result |
| --- | --- | --- |
| `uv run --frozen pytest tests/unit/test_catalog_rules.py tests/integration/test_catalog_products.py tests/integration/test_catalog_search.py tests/integration/test_catalog_invalid_rows.py -q` | 0 | `146 passed` |
| `uv run --frozen pytest tests/unit/test_pricing.py -q` | 0 | `37 passed` |
| `uv run --frozen pytest -q -rs` | 0 | `903 passed, 2 skipped` (814 earlier passed + 89 new; skips: `test_readonly_connection.py:599` and `:612`, Windows does not allow `?` in file names) |
| `uv run --frozen ruff check .` | 0 | `All checks passed!` |
| `uv run --frozen ruff format --check .` | 0 | `49 files already formatted` |

The 816 earlier items are preserved: 814 passed and the same 2 skipped.

Ruff repeated after this report was written: `ruff check .` exit 0, `All checks passed!`; `ruff format --check .` exit 0, `50 files already formatted` (the count includes this new Markdown file).

### Files

Created: `tests/integration/test_catalog_search.py`, this report.
Changed: `src/emporio_agent/services/catalog.py` (slice B added, `_summary` and `_provenance` extracted), `tests/unit/test_catalog_rules.py` (docstring, two imports, slice B cases appended), `tests/integration/test_catalog_invalid_rows.py` (docstring, one import, C75 appended), `tests/integration/conftest.py` (`collision`, two variants, row-edit branch in `catalog_build`). No earlier expectation changed.
Unchanged (checked with `git status` and `git diff`): `src/emporio_agent/services/pricing.py`, everything under `src/emporio_agent/data/`, `tests/integration/test_catalog_products.py`, other earlier tests, `tests/fixtures/`, README, specs, plans, earlier reports, `pyproject.toml`, `uv.lock`.

### Limits

- Synthetic data on one Windows machine; the real catalog is not used.
- Lexical substring matching only: no synonyms, spelling correction or punctuation folding (`C-40` does not match `C40`); short tokens match broadly (C39).
- Whole-table reads per call, justified only by the dataset size.
- Surviving mutations MB05a (equivalent with the current SQL order) and MB17 (raw versus normalized length not distinguished by the matrix).
- A damaged row combined with an unresolved category, and orphan promotions, have no case (Gap Analysis items 1 and 9).
- Earlier slice A limit kept: non-`int` stock on a product that is not active has no case.
- The authorizer proof covers five searches on fresh connections. The prompt-injection case proves the service returns stored text unchanged; it proves nothing about model behavior.

Status: slice B GREEN, awaiting review. Slice C not started.
