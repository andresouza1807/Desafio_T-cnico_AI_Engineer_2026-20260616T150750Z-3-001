# Slice 02, task T3 validation: CSV reading, hashes, headers and fields

Date: 2026-11-02. Baseline: branch `feature/agent-mvp`, commit `9f97b46315cce2e856e4da53ff43cf0e0523ec2a`, clean working tree.
Contract: `docs/specs/02-04-csv-reading.md`, written before the tests. Parent sources: `docs/specs/02-data-preparation.md` (R1), `docs/plans/02-data-preparation.md` (T3), `docs/specs/02-03-data-schema.md` (T2).

## Spec Summary

Implemented only T3: `read_table(source_dir, table, *, file_prefix)` in `emporio_agent.data.preparation`, its frozen result types (`SourceFileInfo`, `SourceRecord`, `TableReading`) and `DataPreparationError` in `errors.py`. The reader reads one file once as bytes, hashes those bytes, decodes them as UTF-8 with an optional BOM, parses them with a strict `csv.reader`, checks the exact header set, the field count and every field through the T1 parsers and T2 metadata, and returns typed values in schema column order with the physical end line of each record.

Not implemented: key uniqueness, category name uniqueness, references, quality notes, cross-field checks, coordinated reading of six files, `PreparationResult`, `prepare_database`, SQLite, temporary files, replace, read-only access.

## Gap Analysis

No gap blocked tests. Decisions recorded in the contract before the tests:

- Unknown header names are never echoed: `header_duplicate` and `header_mismatch` use `column=None` unless the name is a schema column. This differs from R1 ("the error names the column") and from plan row D15 (column `extra`). The exact R1 and D15 amendment is written in the contract, section "Header errors and unknown names". R1 and the plan were not modified; reviewer approval is pending.
- Header check order: no record, blank first record, duplicate, first missing schema column, unexpected name.
- NUL is checked explicitly on the decoded text (`csv_malformed`, line `None`), because Python 3.14 `csv` accepts NUL. Probe before the contract: `csv.reader` returned `['x\x00y', 'z']` without error.
- A quote inside an unquoted field (`a"b`) is accepted as text. This is `csv.reader` `strict=True` behavior, which R1 names; recorded as a limit.
- `file_prefix` producing a non-base file name raises `ValueError` (caller error), message without the prefix.
- `encoding_invalid`, NUL and file errors have line `None`; `csv.Error` uses `reader.line_num`.

## Implementation Plan (executed)

1. Contract `docs/specs/02-04-csv-reading.md`.
2. Tests `tests/integration/test_csv_reading.py`.
3. RED 1 without `DataPreparationError`.
4. `DataPreparationError` added to `errors.py`; `preparation.py` with a docstring only: RED 2.
5. Result types and a `read_table` stub raising `NotImplementedError`: RED 3.
6. Implementation of `read_table`.
7. GREEN: reader tests, full suite, Ruff check, Ruff format check.

## Test Checklist

43 test functions, 122 collected items.

Plan cases:

- [x] D10 `test_d10_missing_source_directory`, `test_d10_source_directory_that_is_a_file`
- [x] D11 `test_d11_missing_file`, `test_d11_directory_in_place_of_file_is_unreadable`, `test_d11_os_error_while_opening_is_unreadable`
- [x] D12 `test_d12_invalid_utf8` (`0xFF`, truncated multibyte, encoded surrogate)
- [x] D13 `test_d13_empty_file_has_no_header` (empty, BOM only), `test_d13_blank_first_line_has_no_header`
- [x] D14 `test_d14_duplicate_schema_header`, `test_d14_duplicate_unknown_header_is_not_echoed`
- [x] D15 `test_d15_missing_header_names_the_schema_column`, `test_d15_unexpected_header_is_not_echoed` (contract decision, see Gap Analysis), `test_d15_renamed_header_reports_the_missing_schema_column`, `test_d15_header_names_are_case_sensitive`
- [x] D16 `test_d16_extra_field_on_line_3`, `test_d16_missing_field`, `test_d16_blank_line_inside_file`, `test_d16_two_final_line_breaks_leave_a_blank_record`
- [x] D17 `test_d17_unbalanced_quote`, `test_d17_text_after_closing_quote`, `test_d17_nul_byte`
- [x] D35 `test_d35_empty_required_column_raises_value_required` (all 32 non-optional columns of the six tables)
- [x] D36 `test_d36_invalid_json_object`; D37 `test_d37_invalid_money_in_orders`; D38 `test_d38_money_overflow_in_products`
- [x] D60, reader part only: `test_d60_field_error_is_wrapped_without_value` (sentinel in `customer_id` and `price_brl`)

Reader-specific cases:

- [x] Reordered header: `test_reordered_header_maps_values_by_name`
- [x] Schema-order validation: `test_fields_are_validated_in_schema_order_not_header_order`
- [x] UTF-8 with and without BOM, LF/CRLF, with and without final newline: `test_accepts_bom_line_endings_and_optional_final_newline` (8 combinations)
- [x] Quoted comma, doubled quotes, multiline: `test_quoted_commas_escaped_quotes_and_multiline_fields_are_preserved`, `test_multiline_field_keeps_lf_when_file_uses_lf`
- [x] Physical line after multiline record: `test_record_lines_after_multiline_record`, `test_error_line_after_multiline_record_is_physical`
- [x] Header only: `test_header_only_file_returns_zero_records`
- [x] Hash and size of original bytes, BOM included: `test_hash_and_size_include_the_original_bom`, plus every happy-path test
- [x] Single read: `test_file_is_opened_once_for_hash_and_parsing` (audit hook counts `open` events for the source path)
- [x] Unknown header sentinel not echoed: D14 and D15 tests above
- [x] Optional and typed values: `test_reads_every_table_into_typed_values_in_schema_order`, `test_only_three_optional_columns_become_none`, `test_empty_optional_column_is_none`, `test_money_and_percent_are_exact_integers`, `test_quantity_uses_id_grammar_and_accepts_large_values`, `test_texts_are_preserved_without_trimming`, `test_field_rule_reported_with_location` (16 cases)
- [x] Duplicates and unresolved references kept for T4: `test_duplicates_and_unresolved_references_are_kept_for_t4`
- [x] Wrapped field error without value: D60 test
- [x] No write and no SQLite: `test_reading_writes_nothing_and_opens_no_sqlite`
- [x] Immutability and repr: `test_result_types_are_immutable`, `test_repr_of_results_shows_no_cell_values_or_directory`
- [x] `file_prefix`: `test_file_prefix_is_a_parameter`, `test_file_prefix_with_path_component_is_rejected`
- [x] Error text: `test_data_preparation_error_text`, `test_data_preparation_error_converts_code_and_keeps_attributes`

Not covered in T3: D18, D19, D39 (T4); D01–D09, D40–D48 (T5); D50–D55 (T6); D60 through `prepare_database`, D61, D70–D72 (T7).

## Test Strategy

- Real small CSV files written by each test into pytest `tmp_path`. No CSV in the repository, no private data. Contacts use `example.invalid` and `(00)`.
- Expected values written by hand. `hashlib.sha256` of the bytes the test wrote is the hash oracle.
- One process-wide audit hook (`sys.addaudithook`), active only while a test enables it. It counts `open` events under `tmp_path` and, in one test, raises `PermissionError` for one path to simulate an I/O failure. Parsing and validation always run for real.
- `sqlite3.connect` is replaced by a function that fails if called, in the no-write test only.
- Mutation spot check after GREEN (temporary edits, then restored; final file confirmed by a passing run): second read for the hash (2 failures), echo of unknown header names (1), validation in header order (2), `QUANTITY` with the `count` grammar (1), missing `from None` on `file_missing` (1). Each mutation was caught.

## Observed evidence

Environment: Python 3.14.3, Windows 11.

RED 1, before any production change:

```text
$ uv run --frozen pytest tests/integration/test_csv_reading.py -q
E   ImportError: cannot import name 'DataPreparationError' from 'emporio_agent.data.errors'
1 error in 0.17s                        (exit 2)
```

RED 2, `DataPreparationError` added, `preparation.py` docstring only:

```text
E   ImportError: cannot import name 'SourceFileInfo' from 'emporio_agent.data.preparation'
1 error in 0.12s                        (exit 2)
```

RED 3, result types and `read_table` raising `NotImplementedError`:

```text
117 failed, 5 passed in 1.90s           (exit 1)
```

The 5 passing items were the `DataPreparationError` text tests. The class was added in step 4, before those tests could run individually; they were part of the collection failure in RED 1.

GREEN, first implementation attempt passed. One Ruff finding in `errors.py` (E501, line too long); the message construction was rewritten in plain steps. No test changed.

```text
$ uv run --frozen pytest tests/integration/test_csv_reading.py -q
122 passed in 0.36s                     (exit 0)
$ uv run --frozen pytest -q
569 passed in 13.00s                    (exit 0)   = 447 previous + 122 new
$ uv run --frozen ruff check .
All checks passed!                      (exit 0)
$ uv run --frozen ruff format --check .
23 files already formatted              (exit 0)
```

## Compliance Report

Files:

- Created: `src/emporio_agent/data/preparation.py`, `tests/integration/test_csv_reading.py`, `docs/specs/02-04-csv-reading.md`, `docs/validation/02-04-csv-reading.md`.
- Modified by addition only: `src/emporio_agent/data/errors.py` (`DataPreparationError`). `IssueCode` and `FieldError` unchanged.
- Unchanged: parsers, schema, pricing, configuration, dependencies, lockfile, README, R1 specification, plan, earlier tests.

No commit, push or PR. No network, OpenAI, credentials, private CSV or database.

Guarantees and limits:

- Proved on small synthetic files on Windows: single read, hash and size of original bytes, BOM/LF/CRLF/final newline handling, strict CSV parsing, exact header set, field count, field grammars through T1 parsers, schema-order values and physical lines.
- Error surfaces (`str`, `repr`, `args`, attributes, `format_exception_only`) carry code, base name, line and schema column name only. Full tracebacks and `__context__` are not sanitized.
- Not validated here: complete import of six files, uniqueness, references, quality notes, database construction, atomicity, durability, read-only access.
- Pending reviewer decision: the R1 and D15 amendment for unknown header names (contract, section "Header errors and unknown names"). Resolved in revision 02-04-R1 below.

## Revision 02-04-R1: header documentation alignment

Date: 2026-11-02. Documentation only. The text above records the state before this revision and is kept as history.

- Approval: the reviewer approved the header amendment in review 02-04-R1. No approval existed before this revision; the T3 implementation applied the decision from the T3 task instructions while R1 still differed.
- Approved rule: column names recognized by the schema may appear in errors; a known missing column and a known duplicate name stay identified; header identifiers unknown to the schema are never echoed and give `column=None`.
- Incorporated into `docs/specs/02-data-preparation.md` (section "CSV format") and `docs/plans/02-data-preparation.md` (row D15). The contract `docs/specs/02-04-csv-reading.md` now records the amendment as incorporated, not pending.
- No code, test, configuration or dependency changed. No test was added. Other rules, signatures and expectations unchanged.

Checks after the documentation edit:

```text
$ uv run --frozen pytest -q
569 passed                              (exit 0)
$ uv run --frozen ruff check .
All checks passed!                      (exit 0)
$ uv run --frozen ruff format --check .
24 files already formatted              (exit 0)
```
