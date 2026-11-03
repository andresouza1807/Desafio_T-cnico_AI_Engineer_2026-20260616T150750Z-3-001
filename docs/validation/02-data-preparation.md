# Slice 02 validation: CSV preparation into SQLite (consolidated, T9)

Date: 2026-11-02. Baseline: branch `feature/data-preparation`, commit `13e6e40`, clean working tree. No commit, push, PR or merge.
Platform of the current runs: Windows 11 Pro 10.0.26200, Python 3.14.3 (MSC v.1944 64 bit), SQLite 3.50.4, uv 0.12.5, pytest 9.2.0, Ruff 0.17.3.

Sources:

- `docs/specs/02-data-preparation.md` (R1 with amendment 02-04-R1).
- `docs/plans/02-data-preparation.md` (T1–T9, 59 case IDs).
- Task contracts: `docs/specs/02-03-data-schema.md` (T2), `02-04-csv-reading.md` (T3), `02-05-data-relations.md` (T4), `02-06-database-preparation.md` (T5), `02-07-readonly-connection.md` (T6), `02-08-data-privacy.md` (T7).
- Task reports: `docs/validation/02-02-data-parsing.md` (T1, with 02-02-R1) to `docs/validation/02-09-integrated-gates.md` (T8), including revisions 02-04-R1 and 02-06-R1.
- Current code under `src/emporio_agent/data/` and current tests, read for traceability.

Status: T1–T8 reviewed; T9 submitted for review.

## Spec Summary

Slice 02 delivers an offline preparation step that turns the six store CSV files into one validated SQLite database, and a read-only access helper for later services. Only the `emporio_agent.data` package was added. Pricing code, dependencies and lockfile were not changed.

Delivered behavior:

- Strict pure parsers: IDs, counts, money to integer cents, percent to integer basis points, ISO dates, flags, enums, JSON objects, required and optional text. No `float` path. Errors carry a code only, never the rejected value.
- CSV reading: each file read once as bytes; SHA-256 and size of those bytes; UTF-8 with optional BOM; strict `csv` dialect; exact header set (unknown header names never echoed, amendment 02-04-R1); exact field count; NUL rejected; physical end line of each record.
- In-memory relational validation: primary keys, composite key of `order_items`, exact uniqueness of `categories.name`, five references. Fail fast in a deterministic order. No SQLite connection before validation ends.
- Build and replace: a temporary file in the target directory, one explicit transaction, real `PRAGMA foreign_key_check` and `PRAGMA integrity_check`, commit, close, `os.replace`. Cleanup of the temporary file and journal on handled failures. Provenance (`source_files`), metadata (`schema_version`, `prepared_at`, `importer_version`) and two deterministic quality-note rules (`status_stock_conflict`, `catalog_text_conflict`). Source values are stored unchanged; historical totals are not recalculated.
- Read-only access: `?mode=ro` URI, `sqlite3.Row`, `PRAGMA query_only = ON`, schema version check, close on every exit path, and one parameterized metadata read.
- Privacy: error and result surfaces defined in R1 carry no cell value and no directory path.

Public interfaces, as they exist in the code. `src/emporio_agent/data/__init__.py` holds only a docstring and re-exports nothing, so every name is imported from its module:

| Module | Names |
| --- | --- |
| `emporio_agent.data.errors` | `IssueCode`, `FieldError`, `DataPreparationError` |
| `emporio_agent.data.parsing` | `parse_id`, `parse_count`, `parse_money_cents`, `parse_percent_basis_points`, `parse_date`, `parse_optional_date`, `parse_flag`, `parse_enum`, `parse_json_object`, `parse_text`, `parse_optional_text`, `cents_to_decimal`, `basis_points_to_percent`, `SQLITE_MAX_INTEGER` |
| `emporio_agent.data.schema` | `SCHEMA_VERSION`, `SOURCE_FILE_PREFIX`, `TABLE_ORDER`, `KNOWN_CATALOG_TEXT_CONFLICTS`, DDL and column metadata (`FieldKind`, `ColumnSpec`, `Reference`, `TableSpec`, `TABLES`) |
| `emporio_agent.data.preparation` | `prepare_database`, `PreparationResult`, `SourceFileInfo`; lower-level `read_table`, `read_validated_tables`, `SourceRecord`, `TableReading` |
| `emporio_agent.data.readonly` | `connect_readonly`, `read_preparation_info`, `PreparationInfo`, `DatabaseUnavailableError` |

Outside this layer and not delivered: catalog search, store policies, customer-scoped (authorized) order access, agent tools, the agent, CLI commands and any OpenAI evaluation.

## Gap Analysis

Planning texts versus observed status:

- The plan header still says "proposed … Nothing in this plan has been executed", and its checklist T1–T9 is unchecked. The specification header still says "Not implemented. No test in this document has been executed" and its Compliance Report says "None executed" and "Status: PROPOSED". These texts are the planning baseline written before implementation. This task does not change them. Observed status, RED/GREEN evidence and revisions are recorded only in the task reports and in this document.

Kinds of evidence, kept separate below:

- Historical evidence: RED runs, mutation spot checks and intermediate counts reported by the implementer in the task reports. They were not re-run for T9.
- Current runs: the five gate commands run for T9 (section "Test Strategy").
- Reviewer approval: the T9 task brief states that T1–T8 were reviewed. The reports record two explicit reviewer decisions: 02-02-R1 (int-only conversion helpers accepted) and 02-04-R1 (header amendment approved). Revision 02-06-R1 answers a review finding. This document does not reproduce other review records.
- Limits and untested behavior: section "Limits".

Gaps found while tracing (recorded, not fixed):

1. Plan row for `src/emporio_agent/data/__init__.py` says "Package marker and public re-exports". The file has no re-exports. Callers import from the modules listed above.
2. Plan rows D12–D17, D35, D36 and D38 name `test_data_preparation.py` and the CSV-structure header says "each raises `DataPreparationError`; target absent afterwards". Their exact codes and locations are asserted through `read_table` in `tests/integration/test_csv_reading.py`. Through `prepare_database`, per-case target absence is asserted only for D10, D11, D37 (`test_field_error_through_prepare_keeps_location`), D18, D19 and D39(a). For the other structural cases, the "target absent" part relies on `prepare_database` running `read_validated_tables` before any file is created, which the cases above demonstrate. Status in the matrix: "reader layer".
3. Plan row D44 expects a skip where the platform allows replacement of an open file. On this machine the test ran and passed; no other platform was run.
4. `integrity_check_failed` has a deterministic test only on the build helper `_write_database` (revision 02-06-R1), not through `prepare_database`. The plan limit "no deterministic test" was written before that revision.
5. T2 report, first GREEN attempt: "5 failed … All 5 were defects in the new test expectations", followed by two listed causes. The report does not map the 5 failures to the 2 causes. Historical text, not re-verified.

No conflict with R1 was found. No production defect was found during T9.

## Implementation Plan (executed for T9)

1. Confirmed baseline: branch `feature/data-preparation`, HEAD `13e6e40`, clean tree.
2. Read the specification, the plan, the T2–T7 contracts and the T1–T8 reports with revisions.
3. Traced the 59 plan IDs to test functions in the current test files, reading test bodies for composite cases.
4. Checked the count evolution against the reports and the current per-file collection.
5. Ran the five gate commands.
6. Wrote this document.
7. Re-ran Ruff check and Ruff format check, and checked Git state.

## Test Checklist

### Task summary T1–T9

Counts are full-suite counts reported at the end of each task. Test counts show size only; they are not proof of conformance on their own. Conformance rests on the traced assertions in the matrix below.

| Task | Scope | Evidence (report) | Result reported | Limits |
| --- | --- | --- | --- | --- |
| Pricing (Slice 01) | regression base | `docs/validation/01-pricing.md` | 37 passed | unchanged in Slice 02 |
| T1 | errors, parsers (D20–D34) | `02-02-data-parsing.md`; RED 1 `ModuleNotFoundError`, RED 2 `AttributeError`; R1 formatting only | 251 passed (214 + 37) | listed inputs only; deep JSON nesting gives `RecursionError` (undefined in spec) |
| T2 | schema DDL, constraints | `02-03-data-schema.md`; RED 1 and RED 2 | 447 passed (+196) | SQL does not enforce source grammar; foreign keys only with the pragma on |
| T3 | CSV reading (D10–D17, D35–D38, D60 reader part) | `02-04-csv-reading.md`; RED 1–3; 5 mutations caught; R1 header amendment | 569 passed (+122) | quote inside unquoted field accepted (`csv` `strict=True`) |
| T4 | keys, references (D18, D19, D39) | `02-05-data-relations.md`; RED 1–2; 6 mutations caught | 623 passed (+54) | in-memory layer only at that point |
| T5 | build, replace, metadata (D01–D09, D40–D48) | `02-06-database-preparation.md`; RED 1–2; 9 mutations caught; 02-06-R1 with RED/GREEN | 677 passed (+54), then 678 after 02-06-R1 (+1) | handled failures only; see "Limits" |
| T6 | read-only access (D50–D55) | `02-07-readonly-connection.md`; RED 1–2; 7 mutations caught | 744 passed, 2 skipped (+66 passed, +2 skipped) | `?` in paths not run on Windows |
| T7 | privacy, static fixture (D60, D61, D70–D72) | `02-08-data-privacy.md`; no RED (verifies earlier behavior); first run passed | 757 passed, 2 skipped (+13) | R1 surfaces only |
| T8 | integrated gates | `02-09-integrated-gates.md` | 37 pricing; 759 collected; 757 passed, 2 skipped; Ruff clean | Windows only |
| T9 | this document | current runs below | see "Test Strategy" | submitted for review |

Current per-file collection (sum 759): `test_pricing.py` 37, `test_data_parsing.py` 214, `test_data_schema.py` 196, `test_csv_reading.py` 122, `test_data_relations.py` 54, `test_data_preparation.py` 42, `test_data_atomicity.py` 13, `test_readonly_connection.py` 68, `test_data_privacy.py` 13. The T5 files sum to 55, matching 54 + 1 after 02-06-R1.

### Traceability of the 59 plan cases

Status values:

- covered: every sub-scenario of the plan row is asserted by the named tests and passed in the current run.
- reader layer: codes and locations asserted through `read_table`; see Gap 2.
- Windows only: passed on the executed Windows machine; no other platform run.

Test files: `parsing` = `tests/unit/test_data_parsing.py`; `csv` = `tests/integration/test_csv_reading.py`; `rel` = `tests/integration/test_data_relations.py`; `prep` = `tests/integration/test_data_preparation.py`; `atom` = `tests/integration/test_data_atomicity.py`; `ro` = `tests/integration/test_readonly_connection.py`; `priv` = `tests/integration/test_data_privacy.py`.

#### Happy path and repetition (D01–D09)

| ID | Tests | Status | Notes |
| --- | --- | --- | --- |
| D01 | prep `test_d01_six_tables_imported_with_relations`, `test_domain_rows_equal_source_values` | covered | row counts, `foreign_key_check` empty, 3 join triples, `row_counts` |
| D02 | atom `test_d02_second_import_without_duplication` | covered | ordered `SELECT *` equal except `prepared_at` |
| D03 | prep `test_d03_historical_total_not_recalculated` | covered | 65000 stored vs 63970 current; no note |
| D04 | prep `test_d04_status_stock_conflicts_preserved_and_noted`, `test_status_without_stock_gives_no_note` | covered | notes only for 12 and 13 |
| D05 | prep `test_d05_curated_catalog_conflict_noted_and_text_preserved`, `test_product_matching_both_rules_gets_two_notes` | covered | exact note list proves absent curated IDs give no note |
| D06 | prep `test_d06_optional_fields_become_null` | covered | |
| D07 | prep `test_d07_quoted_comma_non_ascii_and_quotes_preserved` | covered | also quote and SQL text stored byte-equal |
| D08 | prep `test_d08_provenance_hashes_and_base_names` | covered | second run changes only the promotions hash |
| D09 | prep `test_d09_prices_and_percentages_convertible_for_pricing`; unit part parsing `test_d23_converted_values_are_accepted_by_pricing` | covered | `quote_price` gives 539.91 |

#### CSV structure (D10–D19, D39)

| ID | Tests | Status | Notes |
| --- | --- | --- | --- |
| D10 | csv `test_d10_missing_source_directory`, `test_d10_source_directory_that_is_a_file`; prep `test_d10_missing_source_dir_through_prepare` | covered | target absent, no connect, no `mkstemp` |
| D11 | csv `test_d11_missing_file`, `test_d11_directory_in_place_of_file_is_unreadable`, `test_d11_os_error_while_opening_is_unreadable`; prep `test_d11_missing_file_through_prepare` | covered | |
| D12 | csv `test_d12_invalid_utf8` | reader layer | |
| D13 | csv `test_d13_empty_file_has_no_header`, `test_d13_blank_first_line_has_no_header` | reader layer | |
| D14 | csv `test_d14_duplicate_schema_header`, `test_d14_duplicate_unknown_header_is_not_echoed` | reader layer | |
| D15 | csv `test_d15_missing_header_names_the_schema_column`, `test_d15_unexpected_header_is_not_echoed`, `test_d15_renamed_header_reports_the_missing_schema_column`, `test_d15_header_names_are_case_sensitive` | reader layer | amendment 02-04-R1: unknown name gives `column=None` |
| D16 | csv `test_d16_extra_field_on_line_3`, `test_d16_missing_field`, `test_d16_blank_line_inside_file`, `test_d16_two_final_line_breaks_leave_a_blank_record` | reader layer | |
| D17 | csv `test_d17_unbalanced_quote`, `test_d17_text_after_closing_quote`, `test_d17_nul_byte` | reader layer | |
| D18 | rel `test_d18_duplicate_single_primary_key`, `test_d18_repeated_order_item_pair`, `test_d18_same_product_in_different_orders_is_valid`; prep `test_d18_duplicate_key_through_prepare`, `test_d18_repeated_order_item_pair_through_prepare` | covered | through `prepare_database`: nothing built |
| D19 | rel `test_d19_each_reference_fails_alone`, `test_child_rows_referencing_an_empty_parent_are_rejected`; prep `test_d19_broken_reference_through_prepare` (3 references) | covered | |
| D39 (a) | rel `test_d39a_repeated_category_name_with_different_ids`; prep `test_d39a_repeated_category_name_through_prepare` | covered | not `sqlite3.Error`; line 3, column `name`; no connect, no `mkstemp`; target absent |
| D39 (b) | rel `test_d39b_names_differing_in_case_or_space_are_distinct`; prep `test_d39b_names_differing_in_case_or_space_stored_byte_equal` | covered | stored byte-equal |

#### Field rules (D20–D38)

| ID | Tests | Status | Notes |
| --- | --- | --- | --- |
| D20 | parsing `test_d20_money_to_exact_cents` | covered | |
| D21 | parsing `test_d21_money_rejected_by_grammar`, `test_d21_empty_money_is_required` | covered | |
| D22 | parsing `test_d22_largest_money_value_fits_sqlite_integer`, `test_d22_money_above_sqlite_limit`, `test_d22_grammar_is_checked_before_length` | covered | |
| D23 | parsing `test_d23_float_trap_values_are_exact`, `test_d23_cents_to_decimal_has_two_fraction_digits`, `test_d23_cents_to_decimal_accepts_only_int`, `test_d23_conversions_ignore_and_preserve_caller_decimal_context`, `test_d23_converted_values_are_accepted_by_pricing` | covered | |
| D24 | parsing `test_d24_percent_to_basis_points`, `test_d24_basis_points_to_percent`, `test_d24_basis_points_to_percent_accepts_only_int` | covered | |
| D25 | parsing `test_d25_percent_rejected`, `test_d25_empty_percent_is_required` | covered | |
| D26 | parsing `test_d26_valid_ids`, `test_d26_invalid_ids`, `test_d26_ids_above_sqlite_limit`, `test_d26_empty_id_is_required` | covered | |
| D27 | parsing `test_d27_valid_stock`, `test_d27_invalid_stock`, `test_d27_stock_above_sqlite_limit`, `test_d27_empty_stock_is_required`, `test_d27_quantity_uses_id_grammar` | covered | |
| D28 | parsing `test_d28_valid_dates_are_returned_unchanged`, `test_d28_invalid_dates`, `test_d28_empty_date_is_required` | covered | |
| D29 | parsing `test_d29_empty_optional_date_is_none`, `test_d29_optional_date_keeps_valid_text`, `test_d29_invalid_optional_dates` | covered | |
| D30 | parsing `test_d30_valid_flags`, `test_d30_invalid_flags`, `test_d30_empty_flag_is_required` | covered | |
| D31 | parsing `test_d31_allowlisted_values`, `test_d31_values_outside_allowlist`, `test_d31_empty_enum_is_required` | covered | allowlists copied from the spec in the test |
| D32 | parsing `test_d32_json_object_returned_unchanged`, `test_d32_invalid_json_object`, `test_d32_empty_json_is_required` | covered | |
| D33 | parsing `test_d33_required_text_returned_unchanged`, `test_d33_blank_required_text` | covered | |
| D34 | parsing `test_d34_empty_optional_text_is_none`, `test_d34_optional_text_returned_unchanged`, `test_d34_blank_optional_text` | covered | |
| D35 | csv `test_d35_empty_required_column_raises_value_required` (all 32 non-optional columns) | reader layer | |
| D36 | csv `test_d36_invalid_json_object` | reader layer | |
| D37 | csv `test_d37_invalid_money_in_orders`; prep `test_field_error_through_prepare_keeps_location` | covered | through `prepare_database`: nothing built |
| D38 | csv `test_d38_money_overflow_in_products` | reader layer | |

#### Atomicity (D40–D48)

| ID | Tests | Status | Notes |
| --- | --- | --- | --- |
| D40 | atom `test_d40_initial_failure_leaves_no_database` | covered | directory empty |
| D41 | atom `test_d41_d42_validation_failure_keeps_previous_database[d41_late_table]` | covered | SHA-256, `SELECT *` and listing unchanged |
| D42 | atom `test_d41_d42_validation_failure_keeps_previous_database[d42_early_table]` | covered | same assertions |
| D43 | atom `test_d43_missing_target_directory` | covered | directory still absent |
| D44 | atom `test_d44_replace_failure_with_open_handle` | Windows only | passed, not skipped: a real open `sqlite3` handle made `os.replace` fail; `target_write_failed`, SHA-256 and content unchanged, only the target in the directory. The skip branch did not run, so behavior on other platforms is not shown |
| D45 | atom `test_d45_rebuild_reflects_new_sources` | covered | |
| D46 | prep `test_d46_metadata_with_fixed_clock`, `test_d46_clock_with_other_timezone_converted_to_utc`, `test_d46_naive_or_invalid_clock_rejected_before_any_file`; atom `test_d46_naive_clock_keeps_previous_database` | covered | |
| D47 | atom `test_d47_construction_failure_keeps_previous_database` | covered | see below |
| D48 | atom `test_d48_construction_failure_without_previous_database` | covered | see below |

D47 and D48 details, from the test bodies. A wrapper calls the real `sqlite3.connect` and installs a real SQLite authorizer that denies `SQLITE_INSERT` on `order_items`. SQLite itself rejects the insert (`spy.denied == 1`). Assertions: code `database_build_failed` with `file_name`, `line`, `column` None and `__cause__` None; exactly one connect; the temporary file existed at connect time and does not exist afterwards; no `<temporary>-journal`; SQLite reported transaction actions `BEGIN` then `ROLLBACK`. D47: the directory holds only the target; target SHA-256 unchanged; ordered `SELECT *` from an independent connection unchanged; `prepared_at` still the first run. D48: target absent; directory empty. The patch is undone before verification. `test_construction_failure_leaves_other_files_untouched` shows that a foreign `.tmp` file in the directory is not removed.

`integrity_check_failed`: `test_integrity_check_failure_from_real_foreign_key_finding` (atom, revision 02-06-R1). Valid readings get one `order_items` row with `product_id` 99. A real authorizer returns `SQLITE_IGNORE` only for `PRAGMA foreign_keys`, so SQLite accepts the inserts, and the real `PRAGMA foreign_key_check` returns a finding in `_write_database`. A control in the same test, without the wrapper, gets `sqlite3.IntegrityError` at insert. Not covered: this path through `prepare_database` (validation rejects such readings first), an `integrity_check` result other than `ok`, physical corruption.

#### Read-only access (D50–D55)

| ID | Tests | Status | Notes |
| --- | --- | --- | --- |
| D50 | ro `test_d50_missing_database_not_created`, `test_d50_missing_file_in_existing_directory`, `test_d50_directory_is_rejected` | covered | nothing created; no connect |
| D51 | ro `test_d51_write_rejected_and_state_unchanged` (INSERT, UPDATE, DELETE, CREATE TABLE), `test_d51_query_only_off_still_rejected_by_mode_ro` | covered | independent snapshot, SHA-256 and listing unchanged |
| D52 | ro `test_d52_closed_after_normal_exit`, `test_d52_consumer_exception_preserved_and_connection_closed` | covered | `sqlite3.ProgrammingError` after exit |
| D53 | ro `test_d53_preparation_info_equals_result`, `test_d53_prepared_at_from_other_offset_is_utc`, `test_d53_caller_connection_stays_open_and_unchanged` | covered | |
| D54 | ro `test_d54_schema_mismatch`, `test_d54_prepared_database_with_other_version`, `test_d54_missing_table_uses_from_none`, `test_d54_empty_file_is_schema_mismatch`; related `test_d54_non_sqlite_file_is_open_failed` | covered | file unchanged |
| D55 | ro `test_d55_path_with_space_and_non_ascii` | covered | `dados ação/banco ç.sqlite3` |
| D55 (extra, T6 contract) | ro `test_d55_reserved_uri_characters`, `test_d55_missing_path_with_reserved_characters` | 10 passed, 2 skipped | passed for `a#b`, `100%41`, `x&y=z`, `semi;colon`, `%23`; both `q?mode=rwc` items skipped: "Windows does not allow '?' in file names". The `?` variant is not validated |

#### Privacy (D60, D61, D70–D72)

| ID | Tests | Status | Notes |
| --- | --- | --- | --- |
| D60 | priv `test_d60_sentinel_value_not_in_error[customers_customer_id]`, `[products_price_brl]` (through `prepare_database`); reader part csv `test_d60_field_error_is_wrapped_without_value` | covered | sentinel and path markers absent from `str`, `repr`, `args`, the four attributes and `format_exception_only`; `__cause__` None, `__suppress_context__` True; target absent |
| D61 | priv `test_d61_persisted_provenance_has_no_directory`, `test_d61_source_dir_missing_has_no_directory`, `test_d61_file_missing_has_no_directory`, `test_d61_build_failure_has_no_directory` | covered | |
| D70 | priv `test_d70_fixture_directory_holds_six_files`, `test_d70_customer_contacts_are_fictional`, `test_d70_fixture_is_importable_with_valid_relations` | covered | |
| D71 | priv `test_d71_missing_database_hides_directory`, `test_d71_database_without_metadata_hides_directory_and_sqlite_message` | covered | |
| D72 | priv `test_d72_result_has_no_customer_values_or_path`, `test_d72_other_fields_hold_only_contract_data` | covered | `result.database_path is target` |

Summary: 59 plan IDs traced. Covered 49 (D01–D11, D18–D34, D37, D39, D40–D43, D45–D48, D50–D55, D60, D61, D70–D72). Reader layer 9 (D12–D17, D35, D36, D38). Windows only 1 (D44). D39 counts as one ID with sub-scenarios (a) and (b), both covered. D55 counts as covered for its plan requirement (space and non-ASCII); the extra `?` variant is not validated.

### Privacy scope

Only the R1 surfaces are asserted: `str`, `repr`, `args`, documented attributes and `traceback.format_exception_only` of `DataPreparationError` and `DatabaseUnavailableError`; `repr(PreparationResult)` and its fields other than `database_path`; persisted `source_files` and `preparation_metadata`. Not asserted: full traceback, `__context__`, explicit printing of `database_path`, exceptions propagated unchanged (`sqlite3.OperationalError` on writes, `KeyboardInterrupt`, `SystemExit`).

### Fixtures

- `tests/fixtures/data/valid/` holds six hand-written synthetic CSV files (T7). Contacts use fictional names, `@example.invalid` and `(00)`. Only `tests/integration/test_data_privacy.py` reads this directory.
- Other integration tests write synthetic CSV per test into `tmp_path` (`prep_kit` in `tests/integration/conftest.py` and local helpers).
- No private CSV file was read, copied or transformed by the suite or for this document. No public demo dataset was generated; the R1 demo proposal stays a proposal.

## Test Strategy

Current runs for T9, existing environment, `--frozen`, no new dependency, no network, no OpenAI, no private data. Run before writing this document:

```
$ uv run --frozen pytest tests/unit/test_pricing.py -q
37 passed in 0.03s
exit 0

$ uv run --frozen pytest --collect-only -q
759 tests collected in 0.10s
exit 0

$ uv run --frozen pytest -q -rs
SKIPPED [1] tests\integration\test_readonly_connection.py:599: Windows does not allow '?' in file names
SKIPPED [1] tests\integration\test_readonly_connection.py:612: Windows does not allow '?' in file names
757 passed, 2 skipped in 24.42s
exit 0

$ uv run --frozen ruff check .
All checks passed!
exit 0

$ uv run --frozen ruff format --check .
40 files already formatted
exit 0
```

Accounting: 759 collected = 757 passed + 2 skipped + 0 failed + 0 errors. The 2 skips are the D55 `?` variants: line 599 is `test_d55_reserved_uri_characters[q?mode=rwc]`, line 612 is `test_d55_missing_path_with_reserved_characters[q?mode=rwc]`. They are not counted as approval of those scenarios.

Also run for T9: `uv run --frozen pytest tests/integration/test_data_atomicity.py -q -rs -k d44` gave `1 passed, 12 deselected` (exit 0), so D44 ran and did not skip.

After writing this document (Ruff also checks Markdown, so this file is in scope):

```
$ uv run --frozen ruff check .
All checks passed!
exit 0

$ uv run --frozen ruff format --check .
41 files already formatted
exit 0
```

No RED was produced for T9: it is documentation work and changes no behavior.

## Compliance Report

- Only T9 executed. Only file created: `docs/validation/02-data-preparation.md`. No contract, plan, earlier report, README, code, test, fixture, configuration, dependency or lockfile changed. The formatter ran in check mode only.
- All five gates pass on the executed platform. Numbers match the expected values: 37 pricing, 759 collected, 757 passed, 2 skipped, Ruff clean.
- Count evolution matches the reports: 37, 251, 447, 569, 623, 677 then 678 (02-06-R1), 744 + 2 skipped, 757 + 2 skipped.
- Gaps recorded in "Gap Analysis", not fixed.
- Git state after this document: one untracked file; nothing staged or committed.
- Status: T1–T8 reviewed; T9 submitted for review. Next slice not started.

## Limits

- No guarantee for process kill, interpreter crash, OS crash or power loss. Durability of `os.replace` is not guaranteed (no directory sync) and not tested.
- No recovery of orphan temporary files and no support for concurrent preparation runs. The last successful replace wins.
- Not executed, so not covered: failure of cleanup deletion, `Connection.close()` failure, `mkstemp` failure, `os.close` failure, `PackageNotFoundError`, `OSError` from `Path.resolve`, a file removed between the regular-file check and the open, transient SQLite I/O errors, `KeyboardInterrupt` and `SystemExit` paths.
- `integrity_check_failed` is shown only on the build helper with a real `foreign_key_check` finding. Not through `prepare_database`; no `integrity_check` result other than `ok`.
- Full traceback and `__context__` are outside the privacy guarantee.
- `read_preparation_info` validates `source_files` only as the T6 contract defines: row set and types, not contents against the source files. A transient read error there is reported as `schema_mismatch`.
- The quality register covers two deterministic rules. It is not a semantic validator of catalog text.
- Results come from small synthetic fixtures on one Windows 11 machine with the versions above. Other operating systems, file systems and SQLite versions were not executed and are not proven. D44 and the case-insensitive target-is-source variant depend on this platform. The `?` path variant of D55 is not validated.
- The real private dataset is not used by the automated suite. No manual run against it is recorded in this slice.
