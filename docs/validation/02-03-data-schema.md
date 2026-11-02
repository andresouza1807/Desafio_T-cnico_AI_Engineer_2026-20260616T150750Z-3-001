# Slice 02, task T2 validation — SQLite schema and constraints

Date: 2026-11-02. Baseline: branch `feature/agent-mvp`, commit `149c4fd2395ca4dbe12a5de25582f2ec9de613c9`.
Contract: `docs/specs/02-03-data-schema.md`, written before the tests. Parent sources: `docs/specs/02-data-preparation.md` (R1) and `docs/plans/02-data-preparation.md` (T2).

## Spec Summary

Implemented only T2: `emporio_agent.data.schema` with public constants, column metadata and nine `CREATE TABLE ... STRICT` statements, plus integration tests on real SQLite files. No CSV reading, preparation, PRAGMA handling, transaction control, read-only connection, CLI or agent code was written. No private CSV file was read. T1 files, pricing, configuration, dependencies and lockfile are unchanged.

## Gap Analysis

No gap blocked the task. Decisions inside R1 limits (recorded in the contract before tests):

- `SQLITE_MAX_INTEGER` stays only in `parsing.py`. `schema.py` does not need it, because the `INTEGER` storage class already bounds values. No reorganization of T1.
- DDL statements have no trailing semicolon, no `IF NOT EXISTS`, no `PRAGMA` and no transaction statement.
- Column metadata (`FieldKind`, `ColumnSpec`, `Reference`, `TableSpec`, `TABLES`) carries source header names, SQLite column names, grammar kind, enum allowlists, keys and references for the future importer.
- `specs_json` uses `CASE WHEN json_valid(...) THEN json_type(...) = 'object' ELSE 0 END`. A bare `AND` made SQLite raise `OperationalError: malformed JSON` on invalid text during a pre-test probe.
- Auxiliary-table checks restate R1 content descriptions (six table names, base file name, 64 lowercase hex, entity `product`, two note codes). `preparation_metadata.key` has no allowlist. `data_quality_notes.entity_id` has no foreign key.

Environment probe before the contract (scratch script, not part of the repository): SQLite 3.50.4; `STRICT` and `pragma_table_list.strict` available; `json_valid`/`json_type` built in; `NULL` into `INTEGER PRIMARY KEY NOT NULL` becomes a rowid; `json_valid` accepts duplicate keys; `date('0000-01-01')` is accepted; SQL `trim` removes spaces only.

## Implementation Plan (executed)

1. Contract `docs/specs/02-03-data-schema.md`.
2. Tests `tests/integration/test_data_schema.py`, expected values written by hand.
3. RED 1 without `schema.py`; RED 2 with a docstring-only `schema.py`.
4. Minimum `schema.py`.
5. GREEN: schema tests, full suite, Ruff check, Ruff format check.

## Test Checklist

42 test functions, 196 collected items (parametrization). Requested cases:

- [x] Nine tables created, all `STRICT`: `test_schema_creates_nine_strict_tables`, `test_schema_ddl_follows_import_order_then_auxiliary_tables`.
- [x] Columns, types, nullability, keys, references: `test_schema_columns_types_nullability_and_keys`, `test_schema_foreign_keys`, `test_schema_unique_index_only_on_category_name`.
- [x] Constants and metadata: `test_schema_public_constants`, `test_schema_collections_are_immutable`, `test_schema_column_metadata`, `test_schema_metadata_matches_created_columns`, `test_schema_field_kinds`, `test_schema_reuses_sqlite_max_integer_from_parsing`.
- [x] Module without side effects, individual DDL: `test_schema_import_has_no_side_effects` (subprocess in an empty `tmp_path`; no file created; `sqlite3` not imported), `test_schema_ddl_is_a_sequence_of_single_create_statements`, `test_schema_ddl_cannot_run_twice`.
- [x] Valid insert and read back: `test_schema_valid_rows_are_preserved`, `test_schema_json_object_text_is_stored_byte_for_byte`.
- [x] Duplicate primary keys and repeated `order_items` pair: `test_schema_duplicate_primary_key_rejected`, `test_schema_repeated_order_item_pair_rejected`, `test_schema_auxiliary_duplicate_key_rejected`.
- [x] Category name exact uniqueness: `test_schema_duplicate_category_name_rejected`, `test_schema_category_name_compared_exactly`.
- [x] Shared customer email accepted: `test_schema_customers_may_share_an_email`.
- [x] Broken references with `foreign_keys=ON`: `test_schema_broken_reference_rejected_with_foreign_keys_on`; limit with the pragma off: `test_schema_limit_foreign_keys_need_the_pragma`.
- [x] Negative values, zero quantity, invalid flag, unknown enums, blank text, invalid JSON and dates: `test_schema_check_constraint_rejects` (53 cases).
- [x] Boundaries and every allowlisted value accepted: `test_schema_boundary_and_allowlisted_values_accepted`.
- [x] Maximum money preserved: `test_schema_maximum_money_is_preserved_exactly`; above the limit: `test_schema_money_above_sqlite_integer_cannot_be_bound`.
- [x] NULL in optional fields accepted, required protected: `test_schema_optional_order_fields_accept_null`, `test_schema_required_column_rejects_null`; documented exception: `test_schema_limit_null_integer_primary_key_becomes_rowid`.
- [x] STRICT type rejection and lossless conversion: `test_schema_strict_rejects_unconvertible_values`, `test_schema_limit_strict_converts_lossless_values`.
- [x] Rules left to parsers: `test_schema_limit_rules_left_to_parsers`.
- [x] Discontinued / coming-soon product with positive stock: `test_schema_status_and_stock_are_stored_independently`.
- [x] Historical total different from catalog: `test_schema_historical_total_is_not_recalculated`.
- [x] Auxiliary tables: `test_schema_auxiliary_constraints_reject` (19 cases), `test_schema_source_files_accept_every_domain_table`, `test_schema_quality_note_needs_no_product_row`.
- [x] Caller-controlled transaction: `test_schema_ddl_and_dml_roll_back_together`, `test_schema_failed_insert_keeps_transaction_until_caller_rolls_back`, `test_schema_committed_rows_visible_to_independent_connection`.

Not closed: D01, D09 and D50. They need `prepare_database` and `connect_readonly`, which do not exist yet.

## Test Strategy

Integration tests on real SQLite files in pytest `tmp_path`. Synthetic rows defined in the test file (`example.invalid` emails, `(00)` phones). No CSV, no mocks. The test opens connections with `autocommit=True`, sets `PRAGMA foreign_keys = ON` and issues `BEGIN`/`COMMIT`/`ROLLBACK` itself. State checks after a transaction use an independent connection. Constraint failures are asserted by exception type and by the SQLite message family (`CHECK`, `NOT NULL`, `UNIQUE`, `FOREIGN KEY`).

## Observed evidence

Environment: Python 3.14.3, SQLite 3.50.4 (`sqlite3.sqlite_version`), ruff 0.17.3. Required features present: `STRICT` tables, `pragma_table_list.strict`, `json_valid`, `json_type`, `date()`.

RED 1 — no `schema.py`:

```text
$ uv run --frozen pytest tests/integration/test_data_schema.py -q
13 failed, 183 errors in 3.29s        (exit 1)
195 x ModuleNotFoundError: No module named 'emporio_agent.data.schema'
1 x subprocess.CalledProcessError (test_schema_import_has_no_side_effects; same missing module in the child process)
```

RED 2 — `schema.py` with a docstring only:

```text
11 failed, 2 passed, 183 errors in 2.59s   (exit 1)
194 x AttributeError: module 'emporio_agent.data.schema' has no attribute ...
```

The 2 passing items were `test_schema_reuses_sqlite_max_integer_from_parsing` and `test_schema_import_has_no_side_effects`: both are absence checks that an empty module satisfies.

First GREEN attempt — 5 failed, 191 passed. All 5 were defects in the new test expectations, not in the schema:

- `sqlite3.complete_statement` returns `False` without a terminating semicolon. The contract does not require one. The test now checks `statement + ";"`.
- For a non-key `INTEGER` column, STRICT reports `cannot store TEXT value in INTEGER column products.price_cents` (and `REAL`), not `datatype mismatch`; the probe had used a rowid key. The test now accepts `datatype mismatch|cannot store`, still requiring `sqlite3.IntegrityError`. The contract sentence was corrected and marks the correction.

No T1 or pricing test was changed.

GREEN:

```text
$ uv run --frozen pytest tests/integration/test_data_schema.py -q
196 passed in 14.44s                    (exit 0)
$ uv run --frozen pytest -q
447 passed in 15.13s                    (exit 0)   = 251 previous + 196 new
$ uv run --frozen ruff check .
All checks passed!                      (exit 0)
$ uv run --frozen ruff format --check .
20 files already formatted              (exit 0)
```

Ruff format also checks Python blocks inside Markdown; the contract's code block was aligned to Ruff style once (comment spacing only).

## Compliance Report

Files created (all untracked):

- `src/emporio_agent/data/schema.py`
- `tests/integration/test_data_schema.py`
- `docs/specs/02-03-data-schema.md`
- `docs/validation/02-03-data-schema.md`

No tracked file changed (`git diff --quiet HEAD` exit 0). No commit, push or PR.

Guarantees, exact limits:

- Enforced by SQLite 3.50.4 with this DDL: storage class per column with lossless conversion, `NOT NULL`, primary keys, composite key of `order_items`, `UNIQUE` on `categories.name` (BINARY), every listed `CHECK`, foreign keys only with `PRAGMA foreign_keys = ON`, rollback of DDL and DML in a caller transaction.
- Not enforced by SQL, owned by parsers and the future importer: source text grammar (leading zeros, signs, whitespace; `'05'` is stored as 5), `NULL` into a single-column `INTEGER PRIMARY KEY` (becomes a rowid), whitespace other than spaces in blank text, duplicate JSON keys, year `0000`, values above 2^63 − 1 (`OverflowError` before SQLite), all `DataPreparationError` codes and locations, foreign keys when the pragma is off.
- The schema computes nothing: no availability, discount or order total. Semantic observations are stored unchanged.
- Tests ran on Windows 11 with SQLite 3.50.4 only.
