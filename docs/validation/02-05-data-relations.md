# Slice 02, task T4 validation: key uniqueness and references between tables

Date: 2026-11-02. Baseline: branch `feature/agent-mvp`, commit `5dca5ea5eaf25d77ad34f73c328f206d74660ddb`, clean working tree, 569 tests passing before this task.
Contract: `docs/specs/02-05-data-relations.md`, written before the tests. Parent sources: `docs/specs/02-data-preparation.md` (R1 with amendment 02-04-R1), `docs/plans/02-data-preparation.md` (T4), `docs/specs/02-03-data-schema.md` (T2), `docs/specs/02-04-csv-reading.md` (T3).

## Spec Summary

Implemented only T4: `read_validated_tables(source_dir, *, file_prefix=SOURCE_FILE_PREFIX) -> tuple[TableReading, ...]` in `emporio_agent.data.preparation`. For each table in `TABLE_ORDER` it calls the existing `read_table`, runs a full uniqueness pass (primary key, then `categories.name`), runs a reference pass against in-memory key indexes of parent tables already validated, and only then reads the next file. It returns the six `TableReading` objects unchanged. Errors use the existing `DataPreparationError` with `duplicate_key` or `broken_reference` and carry only code, base name, physical line and schema column name.

Not implemented: `prepare_database`, `PreparationResult`, quality notes, SQLite, temporary files, replace, metadata, read-only access, CLI, agent.

## Gap Analysis

No blocking conflict or gap. Decisions written in the contract before the tests:

- Within one file, `read_table` validates all records before the uniqueness pass. A field error anywhere in a file therefore wins over a duplicate or broken reference in the same file, even on an earlier line. This follows the R1 text "After all records of a file: 1. Uniqueness pass … 2. Foreign keys". Covered by `test_reader_error_field_beats_duplicate_in_same_file`.
- Parent key index: the set of single-column primary-key values of the parent reading. Every referenced parent in R1 has a single-column key. The composite key of `order_items` is never referenced, so its index is empty.
- `TableSpec.primary_key`, `unique` and `Reference.column_name` hold schema column names. The error column is the matching `source_name`. For every key and reference column the two names are equal in T2.
- Plan rows D18, D19 and D39 name `test_data_preparation.py` and `prepare_database`. In T4 they are proved through `read_validated_tables` only; their run through `prepare_database` (target absent, D39 connect recorder through the full service) stays for T5.

## Implementation Plan (executed)

1. Contract `docs/specs/02-05-data-relations.md`.
2. Tests `tests/integration/test_data_relations.py`.
3. RED 1: import of `read_validated_tables` fails.
4. Temporary stub: `read_validated_tables` returns the six `read_table` results with no relational check. RED 2.
5. Implementation: `_check_unique_keys`, `_check_references`, `_primary_key_index`, `read_validated_tables`; module docstring updated.
6. GREEN, mutation spot checks, full suite, Ruff check, Ruff format check.

## Test Checklist

37 test functions, 54 collected items.

Plan cases:

- [x] D18 `test_d18_duplicate_single_primary_key` (categories, customers, products, promotions, orders; second occurrence on line 4), `test_d18_repeated_order_item_pair` (equal and different quantity; column `product_id`), `test_d18_same_product_in_different_orders_is_valid`
- [x] D19 `test_d19_each_reference_fails_alone` (five references), `test_child_rows_referencing_an_empty_parent_are_rejected` (five parent/child pairs)
- [x] D39 (a) `test_d39a_repeated_category_name_with_different_ids` (`duplicate_key`, categories, line 3, column `name`; not a `sqlite3.Error`; connect recorder empty)
- [x] D39 (b) `test_d39b_names_differing_in_case_or_space_are_distinct` (`bebidas`, `Bebidas `, `BEBIDAS`, ` Bebidas`; values kept byte-equal)

Coordination cases:

- [x] Six valid files in `TABLE_ORDER` with hand-written values, lines, SHA-256, size, row count: `test_six_valid_files_return_readings_in_table_order`; `file_prefix` passed through: `test_file_prefix_is_passed_to_the_reader`
- [x] Same order with different products: `test_same_order_with_different_products_is_valid`
- [x] Shared email: `test_shared_customer_email_is_valid`
- [x] Parent without children, order without items: `test_parents_without_children_and_order_without_items_are_valid`
- [x] Empty tables with headers: `test_header_only_tables_are_valid`, `test_header_only_children_with_filled_parents_are_valid`
- [x] Non-empty child referencing empty parent: `test_child_rows_referencing_an_empty_parent_are_rejected`
- [x] Multi-line record keeps physical line: `test_reference_error_in_multiline_record_uses_its_end_line`, `test_duplicate_after_multiline_record_uses_physical_line`
- [x] Primary key before name in one record: `test_primary_key_is_checked_before_unique_name_in_one_record`
- [x] Earlier line wins: `test_earlier_duplicate_line_wins_over_later_one`, `test_earlier_duplicate_id_wins_over_later_duplicate_name`
- [x] Uniqueness before references in one table: `test_uniqueness_pass_runs_before_reference_pass`
- [x] Reference record and column order: `test_references_follow_record_order`, `test_references_follow_schema_column_order_inside_a_record`
- [x] Earlier relational error before later field error or missing file, later files never opened: `test_relational_error_in_earlier_table_precedes_later_field_error`, `test_relational_error_in_earlier_table_precedes_later_missing_file`, `test_duplicate_category_precedes_later_customer_field_error`
- [x] Reader errors preserved: `test_reader_error_missing_source_directory`, `test_reader_error_missing_file`, `test_reader_error_field_in_valid_relations`, `test_reader_error_header`, `test_reader_error_field_beats_duplicate_in_same_file`, `test_reader_value_error_for_prefix_with_path`
- [x] Each file read once: `test_each_file_is_read_once`
- [x] Data, hashes and totals unchanged: `test_six_valid_files_return_readings_in_table_order`, `test_returned_readings_equal_plain_reader_output` (historical totals, discontinued product with stock)
- [x] Sentinel not in public surfaces: `test_sentinel_in_duplicate_name_is_not_in_public_surfaces`, `test_key_and_reference_values_are_not_in_public_surfaces`
- [x] No write, no SQLite connection: `test_validation_writes_nothing_and_opens_no_sqlite`, `test_failed_validation_writes_nothing_and_opens_no_sqlite`

## Test Strategy

- Six real CSV files written by each test into pytest `tmp_path`. All values invented; contacts use `example.invalid` and `(00)`. No CSV in the repository, no private data. No helper imported from other test files.
- Expected values, lines and columns written by hand. `hashlib.sha256` of the bytes the test wrote is the hash oracle. One test compares the result with plain `read_table` output to prove the readings are returned unchanged; it is not used to derive expected relational errors.
- A process-wide audit hook, active only inside tests that request it, records `open` events under `tmp_path`. It never changes behavior.
- `sqlite3.connect` is replaced by a recorder that fails if called. No relation is mocked.
- Mutation spot checks after GREEN (temporary edits to `preparation.py`, then restored from a byte copy and confirmed with `cmp`):

| Mutation | Result |
| --- | --- |
| Reference pass before uniqueness pass | 1 failed |
| Read all six files before validating | 3 failed |
| Case folding and trimming of key values | 4 failed |
| Composite key reported at `order_id` | 2 failed |
| Unique name checked before primary key | 1 failed |
| References checked in reverse column order | 1 failed |

## Observed evidence

Environment: Python 3.14.3, Windows 11.

RED 1, before any production change:

```text
$ uv run --frozen pytest tests/integration/test_data_relations.py -q
E   ImportError: cannot import name 'read_validated_tables' from 'emporio_agent.data.preparation'
1 error in 0.14s                        (exit 2)
```

RED 2, stub without relational checks:

```text
$ uv run --frozen pytest tests/integration/test_data_relations.py -q
33 failed, 21 passed in 0.83s           (exit 1)
```

The 21 passing items were valid-data cases and reader-error cases, which the stub already satisfied through `read_table`. All duplicate, reference and precedence cases failed.

First run after implementation: `3 failed, 51 passed` (exit 1). Cause: a test setup defect, not production code. Three precedence tests requested the audit fixture but did not clear the events recorded while the test itself wrote the six files, so the write opens were counted. Fix: `audit["events"].clear()` after `write_tables`, as `test_each_file_is_read_once` already did. No expected value changed. Ruff format then reformatted two long tuples in the test file (layout only).

GREEN:

```text
$ uv run --frozen pytest tests/integration/test_data_relations.py -q
54 passed in 0.33s                      (exit 0)
$ uv run --frozen pytest -q
623 passed in 13.84s                    (exit 0)   = 569 previous + 54 new
$ uv run --frozen ruff check .
All checks passed!                      (exit 0)
$ uv run --frozen ruff format --check .
26 files already formatted              (exit 0)
```

## Compliance Report

Files:

- Modified: `src/emporio_agent/data/preparation.py` (additions: `read_validated_tables` and three private helpers; `TABLES` import; module docstring). `read_table`, `SourceFileInfo`, `SourceRecord`, `TableReading` unchanged.
- Created: `tests/integration/test_data_relations.py`, `docs/specs/02-05-data-relations.md`, `docs/validation/02-05-data-relations.md`.
- Unchanged: errors, parsers, schema, pricing, earlier tests, R1 specification, plan, configuration, dependencies, lockfile, README.

Git: branch `feature/agent-mvp`, HEAD `5dca5ea`. No commit, push or PR. No network, OpenAI, credentials, private CSV or database.

Guarantees and limits:

- Proved on small synthetic files on Windows: uniqueness of every primary key and of `categories.name` by exact comparison, the five references, the stated precedence (file, then within a file reader checks, uniqueness pass, reference pass; records in line order; primary key before unique; references in schema column order), single read per file, unchanged readings, no write and no SQLite connection.
- Error surfaces (`str`, `repr`, `args`, attributes, `format_exception_only`) carry code, base name, line and schema column name only. Full tracebacks and `__context__` are not sanitized by this guarantee.
- D18, D19 and D39 are validated at the in-memory layer only. Their run through `prepare_database`, persisted import, atomicity (D40–D42) and durability are not proved here; they belong to T5.
