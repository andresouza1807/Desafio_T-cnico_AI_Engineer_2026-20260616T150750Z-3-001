# Slice 03, part A validation: product detail and availability

Date: 2026-11-02. Baseline: `feature/data-preparation`, commit `43dfea0`, clean working tree, `uv run --frozen pytest -q -rs` before any change: `757 passed, 2 skipped` (exit 0; the two skips are the known Windows `?` file-name cases in `tests/integration/test_readonly_connection.py:599` and `:612`). No commit, push, PR or merge.
Contract: `docs/specs/03-catalog.md` revision R1 (rules A1–A16, decisions P-01–P-15) and `docs/plans/03-catalog.md` section "Slice A" and test matrix rows C01–C20, C68–C74. Neither document was changed.
Platform: Windows 11 Pro 10.0.26200, Python 3.14.3, SQLite 3.50.4.

## Spec Summary

Implemented only slice A in the new module `emporio_agent.services.catalog`:

- Types: `Availability`, `QualityNoteCode`, `CategoryRef`, `AppliedPromotion`, `Provenance`, `ProductSummary`, `ProductDetail` (frozen dataclasses, fields as in the spec).
- Errors: `CatalogInputError(field, code)` (`ValueError` subclass, `str` = `"<field>: <code>"`) and `CatalogDataError(reason)` (`str` = reason); `args == (str(error),)` for both.
- `availability_of(status, stock_quantity)`: table P-07; status outside `PRODUCT_STATUSES` raises `CatalogDataError("invalid_row")`.
- `get_product(connection, product_id) -> ProductDetail | None`: P-05 validation of `product_id` before any query; three fixed `SELECT` statements with `?` parameters (product joined with its category, all promotions of the product, product quality notes) on cursors with `row_factory = None`; provenance from `read_preparation_info` filtered to `categories`, `products`, `promotions`; P-15 checks of every used field; price through `cents_to_decimal`, `basis_points_to_percent` and `quote_price` with active and inactive promotions; shown promotion per P-09; P-10 projection for `catalog_text_conflict`.

Not implemented (slices B/C): `search_products`, `normalize_search_text`, pagination, `CategoryResolution`, `SearchResult`, `PAGE_SIZE`, `MAX_TEXT_LENGTH`, `MAX_PRICE_BRL`, `PaymentMethod`, `ProductQuote`, `quote_product`, tools, Pydantic, CLI, OpenAI. No stub or type for them exists.

## Gap Analysis

No conflict with the approved contract was found and no P-decision was reinterpreted. Points the contract leaves open, resolved without adding a rule, for review:

1. Error constructors. The spec fixes attributes and `str()` but not signatures. Chosen: `CatalogInputError(field, code)` and `CatalogDataError(reason)`, positional, without validating the code against a list (the spec states no such rule).
2. Read order inside `get_product`. Product row first; when it is absent, `None` is returned before promotions, notes or provenance are read. Otherwise promotions, notes and `read_preparation_info` are read, then all rows are validated. `DatabaseUnavailableError` from `read_preparation_info` propagates unchanged (G12).
3. `availability_of` raises `CatalogDataError("invalid_row") from None` although it is not inside an `except` block, so every `CatalogDataError` has `__cause__ is None` and `__suppress_context__ is True` (G12) and none has a `__context__` there.
4. `specs` is a `MappingProxyType` over the parsed `dict`: the top level is read-only, nested values are not (spec: "read-only top level").
5. Plan expectation that did not hold (mutation M08a below). The plan predicts that removing the catalog type check on `price_cents` or `stock_quantity` makes C68 fail. It does not: `cents_to_decimal` (reused, as required) rejects non-`int` values with `TypeError`, and `str > int` in `availability_of` raises `TypeError`; P-15 converts both to `invalid_row`. C68 does detect an escaping `TypeError` (M08b). Residual: a non-`int` stock on a `discontinued` or `coming_soon` product is caught only by the catalog check, because `availability_of` does not compare stock for those states; no slice A case stores such a value (C68 damages active product 201). The catalog check is implemented; adding a case is a reviewer decision, not done here.
6. `catalog_kit` covers only what slice A uses. `build(base, edit=None)` supports the database edits `schema_v2` and `no_products_table` (applied to a copy of the built file, as the plan says). The row edit `collision` and the variants `price_text_other` and `specs_other_product` belong to slices B (C45, C75) and were not added.
7. The spec header still reads "proposed"; the task brief states R1 is approved. Documents were left unchanged.

## Implementation Plan (executed)

1. Baseline checked: branch, `43dfea0`, clean tree, 757 passed, 2 skipped.
2. Read spec R1, plan, `pricing.py`, `parsing.py`, `schema.py`, `preparation.py`, `readonly.py`, `errors.py`, `tests/integration/conftest.py` and the T6 tests and report.
3. Appended `catalog_kit` (with `build`, `corrupt`, `query`, `snapshot`) to `tests/integration/conftest.py`; four import lines added to its import block (`hashlib`, `shutil`, `datetime`/`UTC`, `prepare_database`). No existing fixture, constant or helper changed (`git diff --numstat`: 327 added, 0 removed). The arrangement was checked once with a scratch script outside the repository: all 20 variants apply, 17 products, 9 promotions, notes 135 `catalog_text_conflict`, 208 and 209 `status_stock_conflict`.
4. Wrote the three test files before the module.
5. RED.
6. Wrote `src/emporio_agent/services/catalog.py`.
7. GREEN, `ruff format` on the new module only, mutation checks, restoration check, full gates, this report, Ruff again.

## Test Checklist

57 collected items in 30 test functions: `tests/unit/test_catalog_rules.py` 17 items (4 functions), `tests/integration/test_catalog_products.py` 18 items (17 functions), `tests/integration/test_catalog_invalid_rows.py` 22 items (9 functions).

| Case | Test (items) |
| --- | --- |
| C01 | `test_c01_product_detail` (1): all fields; specs read-only; provenance equals an independent `source_files` query and SHA-256 of the written CSV bytes |
| C02 | `test_c02_missing_product_returns_none[999, 2**63-1]` (2) |
| C03 | `test_c03_invalid_product_id_rejected_before_any_query` (9): `0`, `-1`, `2**63` `out_of_range`; `True`, `False`, `"201"`, `201.0`, `Decimal(201)`, `None` `invalid_type`; closed connection |
| C04 | `test_c04_active_without_stock_is_out_of_stock` (1) |
| C05 | `test_c05_discontinued_keeps_stock_and_is_not_available` (1) |
| C06 | `test_c06_coming_soon_keeps_stock_and_is_not_available` (1) |
| C07 | `test_c07_availability_states` (6), `test_c07_unknown_status_is_invalid_row` (1) |
| C08 | `test_c08_active_promotion_and_tie_by_lowest_id` (1): 202 990.00 / 301; 135 1350.00 / 308 |
| C09 | `test_c09_inactive_promotions_never_returned` (1): 217 None / 2500.00; 212 305 / 266.66 |
| C10 | `test_c10_conflicting_text_is_withheld` (1): withheld; stored text unchanged (independent query) |
| C11 | `test_c11_injection_text_returned_unchanged` (1) |
| C12 | `test_c12_provenance_has_base_names_only` (1) |
| C13 | `test_c13_missing_database_never_reaches_catalog` (1) |
| C14 | `test_c14_other_schema_version_is_unavailable` (1) |
| C15 | `test_c15_missing_products_table_is_query_failed` (1) |
| C16 | `test_c16_connection_unchanged_after_success` (1) |
| C17 | `test_c17_calls_leave_database_unchanged` (1): 11 calls; SHA-256, listing and all tables equal |
| C18 | `test_c18_authorizer_sees_only_catalog_reads` (1) |
| C19 | `test_c19_closed_connection_propagates_programming_error` (1) |
| C20 | `test_c20_sentinel_product_id_never_reaches_the_error` (1) |
| C68 | `test_c68_wrong_type_is_invalid_row` (4): `price_text`, `price_real`, `price_null`, `stock_text`; 202 still 990.00 |
| C69 | `test_c69_invalid_value_is_invalid_row` (3): `price_negative` (`__context__` raised in `quote_price`), `stock_negative`, `status_unknown` |
| C70 | `test_c70_bad_specs_is_invalid_row` (5), `test_c70_withheld_specs_are_not_parsed` (1) |
| C71 | `test_c71_bad_promotion_is_invalid_row` (4): `percent_over`, `percent_over_inactive` (both `__context__` in `quote_price`), `active_two`, `percent_text` |
| C72 | `test_c72_unknown_note_is_invalid_row` (1): 208 `invalid_row`; 201 returned |
| C73 | `test_c73_invalid_row_error_is_sanitized` (2): `price_sentinel`, `specs_sentinel` |
| C74 | `test_c74_connection_usable_after_invalid_row` (1), `test_c74_connection_usable_after_query_failed` (1) |

Every invalid-row case first asserts `typeof(...)` and the stored value through an independent `mode=ro` connection, then calls the service.

## Test Strategy

- Real SQLite files in `tmp_path`, built by the real `prepare_database` from the synthetic `catalog_kit` rows (17 products, 9 promotions, header-only customers, orders and order_items), fixed clock `2026-11-02T12:00:00+00:00`, opened with `connect_readonly`.
- Invalid states built before the read-only open with a separate writable connection (`isolation_level=None`, explicit `BEGIN`/`COMMIT`): `PRAGMA foreign_keys = OFF` before `BEGIN`; `CHECK` variants with `PRAGMA ignore_check_constraints = ON` and one parameterized `UPDATE`; `STRICT`/`NOT NULL` variants by rebuilding the table without declared types or constraints, then one `UPDATE`. Each `UPDATE` must change exactly one row.
- Oracles: literals from the plan (hand-calculated prices, IDs, texts), independent parameterized SQL, SHA-256 of the written CSV bytes. `quote_price` and `availability_of` are never used to compute an expectation. `__context__` is used only to prove the raising frame is `quote_price` in `pricing.py`.
- Read-only proof: real SQLite authorizer (records, never denies) and `snapshot` (SHA-256, listing, ordered rows of every table).
- No mock of SQL, pricing, availability, relations or persistence. No private data, no OpenAI, no network.

## Compliance Report

### RED

`uv run --frozen pytest tests/unit/test_catalog_rules.py tests/integration/test_catalog_products.py tests/integration/test_catalog_invalid_rows.py -q` — exit 2, `3 errors in 0.18s`. Each of the three files: `E   ModuleNotFoundError: No module named 'emporio_agent.services.catalog'`. Same cause as the plan's expected RED. Existing suite with the new files ignored: `757 passed, 2 skipped`.

### GREEN

Same command after the implementation: exit 0, `57 passed`. First GREEN run needed no change to tests or code; the only later change was `ruff format` on `catalog.py` (one wrapped constant, PEP 758 `except` form).

### Mutation checks (after GREEN, one at a time, then restored)

Run by a scratch script outside the repository that rewrites `catalog.py`, runs the three slice A files and restores the original bytes. SHA-256 of `catalog.py` before and after all mutations: `09f50dd537dbfd3a78493069b4135879ca4408cd814c6322f531048d0c6fd8f4` (equal).

| ID | Mutation (plan behavior) | Observed failures |
| --- | --- | --- |
| M01 | availability by stock only | 6: C05, C06, C07 ×4 |
| M02 | availability by status only | 2: C04, C07 (`active`, 0) |
| M03 | description and specs returned for flagged products | 2: C10, C70 withheld |
| M04 | inactive or largest campaign shown | 2: C09 (212 shows 306), C17 (217 hits the no-match check) |
| M05 | equal promotions resolved by highest ID | 1: C08 |
| M06 | argument value in the message | 7: C03 ×6 (`invalid_type` items), C20 |
| M07 | validation after the first query | 10: C03 ×9 (`ProgrammingError`), C20 |
| M08a | no catalog type check on `price_cents` and `stock_quantity` (negative stock check removed with it) | 1: C69 `stock_negative`. C68 passes: see Gap Analysis item 5 |
| M08b | M08a plus `TypeError` no longer converted | 8: C68 ×4, C69 `stock_negative`, C71 `percent_text`, C73 `price_sentinel`, C74 `invalid_row` |
| M09 | `bool(is_active)` instead of the 0/1 check | 1: C71 `active_two` |
| M10 | only active promotions passed to `quote_price` | 1: C71 `percent_over_inactive` |
| M11a | `raise ... from error` | 18: C68–C73 every invalid-row item |
| M11b | message built from the original error and the cell | 19: M11a items plus C74 `invalid_row` |
| M12 | connection closed on error | 2: C74 ×2 |

### Final gates (after restoration)

| Command | Exit | Result |
| --- | --- | --- |
| `uv run --frozen pytest tests/unit/test_catalog_rules.py tests/integration/test_catalog_products.py tests/integration/test_catalog_invalid_rows.py -q` | 0 | `57 passed` |
| `uv run --frozen pytest tests/unit/test_pricing.py -q` | 0 | `37 passed` |
| `uv run --frozen pytest -q -rs` | 0 | `814 passed, 2 skipped` (757 earlier passed + 57 new; skips: `test_readonly_connection.py:599` and `:612`, Windows does not allow `?` in file names) |
| `uv run --frozen ruff check .` | 0 | `All checks passed!` |
| `uv run --frozen ruff format --check .` | 0 | `47 files already formatted` |

The 759 earlier items are preserved: 757 passed and the same 2 skipped.

### Files

Created: `src/emporio_agent/services/catalog.py`, `tests/unit/test_catalog_rules.py`, `tests/integration/test_catalog_products.py`, `tests/integration/test_catalog_invalid_rows.py`, this report.
Changed: `tests/integration/conftest.py` (additions only).
Unchanged (checked with `git status` and `git diff`): `src/emporio_agent/services/pricing.py`, everything under `src/emporio_agent/data/`, earlier tests, `tests/fixtures/`, README, specs, plans, earlier reports, `pyproject.toml`, `uv.lock`.

### Limits

- Synthetic data on one Windows machine; the real catalog is not used.
- Invalid-row cases cover the slice A fields of spec G15 only. Uniqueness, references, non-blank text, dates, duplicate JSON keys and `source_files` content remain guaranteed by preparation. Physical file corruption is not tested.
- Gap Analysis item 5: non-`int` stock on a non-active product has no case.
- The prompt-injection case proves the service returns stored text unchanged; it proves nothing about model behavior.
- The authorizer proof covers one `get_product` call on a fresh connection.

Status: slice A GREEN, awaiting review. Slices B and C not started.
