# Slice 02, task T5 validation: database construction and replacement

Date: 2026-11-02. Baseline: `main`, commit `a93c2b2846aa4611688fa51570a11977f90465ef`, clean working tree, 623 tests passing before this task. Work branch: `feature/data-preparation`, created from that commit. No commit, push or PR.
Contract: `docs/specs/02-06-database-preparation.md`, written before the tests. Parent sources: `docs/specs/02-data-preparation.md` (R1 with amendment 02-04-R1), `docs/plans/02-data-preparation.md` (T5, D01–D09, D40–D48), T2–T4 contracts.
Platform: Windows 11 Pro 10.0.26200, Python 3.14.3, SQLite 3.50.4.

## Spec Summary

Implemented only T5 in `emporio_agent.data.preparation`: `PreparationResult` (frozen dataclass, `database_path` with `repr=False`, read-only `row_counts`) and `prepare_database`. It checks the target directory, calls the clock once, reuses `read_validated_tables`, rejects a target that is one of the source files, resolves the importer version, builds all rows in memory, then builds a SQLite file in a temporary file of the target directory inside one explicit transaction, runs real `foreign_key_check` and `integrity_check`, commits, closes and replaces the target with `os.replace`. Nothing runs after the replace except the return.

Not implemented: read-only connection (T6), privacy group D60–D72 (T7), CLI, agent.

## Gap Analysis

No blocking conflict. Decisions written in the contract before the tests:

- Precedence: target directory, clock, sources, target-is-source, importer version, in-memory rows, build, replace. All fallible checks run before any file is created.
- Target equal to a source file: not in R1, requested for T5. `ValueError("database_path must not be a source file")`, checked after source validation (only then the six names are validated). Comparison: normalized real path, plus `os.path.samestat` when the target exists and the inode is non-zero (hard links).
- Clock: a non-`datetime` value or a naive `datetime` raises `ValueError`; exceptions raised by the clock propagate unchanged.
- Transaction mode: `sqlite3.connect(temporary, autocommit=True)`. Explicit `BEGIN`, `COMMIT`, `ROLLBACK` SQL; `Connection.commit()`/`rollback()` not used. PRAGMAs run before `BEGIN`.
- `importer_version`: `importlib.metadata.version("emporio-agent")` (observed `0.1.0`). If the distribution is not installed, `PackageNotFoundError` propagates before any file is created. Not tested.
- `PreparationResult` is not hashable (mapping proxy). Equality is the dataclass default.

## Implementation Plan (executed)

1. Contract `docs/specs/02-06-database-preparation.md`.
2. Fixtures `tests/integration/conftest.py` (new names only: `prep_kit`, `prep_forbid_build`, `prep_build_spy`); tests `test_data_preparation.py`, `test_data_atomicity.py`.
3. RED 1: import of `prepare_database` fails at collection.
4. Temporary stub (`PreparationResult` plus `prepare_database` that validates then raises `NotImplementedError`). RED 2.
5. Stub removed; implementation added; module docstring updated.
6. GREEN, mutation spot checks, full suite, Ruff check and format check.

## Test Checklist

47 test functions, 54 collected items (36 functions in `test_data_preparation.py`, 11 in `test_data_atomicity.py`).

- [x] D01 `test_d01_six_tables_imported_with_relations`
- [x] D02 `test_d02_second_import_without_duplication`
- [x] D03 `test_d03_historical_total_not_recalculated`
- [x] D04 `test_d04_status_stock_conflicts_preserved_and_noted`, `test_status_without_stock_gives_no_note`
- [x] D05 `test_d05_curated_catalog_conflict_noted_and_text_preserved`, `test_product_matching_both_rules_gets_two_notes`
- [x] D06 `test_d06_optional_fields_become_null`
- [x] D07 `test_d07_quoted_comma_non_ascii_and_quotes_preserved` (includes a value with `'`, `"` and SQL text, stored byte-equal)
- [x] D08 `test_d08_provenance_hashes_and_base_names`
- [x] D09 `test_d09_prices_and_percentages_convertible_for_pricing`
- [x] D18/D19/D39 through `prepare_database`: `test_d18_duplicate_key_through_prepare`, `test_d18_repeated_order_item_pair_through_prepare`, `test_d19_broken_reference_through_prepare` (3), `test_d39a_repeated_category_name_through_prepare`, `test_d39b_names_differing_in_case_or_space_stored_byte_equal`; recorders show no `sqlite3.connect` and no `tempfile.mkstemp` call
- [x] D10, D11, field error and prefix through `prepare_database`
- [x] D40 `test_d40_initial_failure_leaves_no_database`
- [x] D41/D42 `test_d41_d42_validation_failure_keeps_previous_database` (2)
- [x] D43 `test_d43_missing_target_directory`
- [x] D44 `test_d44_replace_failure_with_open_handle` — passed, not skipped: a real open `sqlite3` connection made `os.replace` fail on this Windows machine
- [x] D45 `test_d45_rebuild_reflects_new_sources`
- [x] D46 fixed clock, other timezone converted, default clock, naive/invalid clock (3), clock exception, naive clock keeps previous database
- [x] D47 `test_d47_construction_failure_keeps_previous_database`
- [x] D48 `test_d48_construction_failure_without_previous_database`
- [x] Extra: metadata/result agreement, frozen result and read-only `row_counts`, `database_path` identity and `repr`, importer version, build protocol through a real authorizer (one connection; PRAGMAs before `BEGIN`; checks between `BEGIN` and `COMMIT`; inserts into all nine tables), precedence (3), target-is-source (direct, `..`, case, hard link), target in source directory with another name, foreign files left untouched, closed handles allow replace, reopen and delete

## Test Strategy

- Real SQLite files in `tmp_path`; synthetic CSV written per test. No private CSV, no published dataset.
- State verified with independent read-only connections (`mode=ro`), SHA-256 of target bytes and directory listings.
- D47/D48: wrapper calls the real `sqlite3.connect` and installs a real authorizer denying `SQLITE_INSERT` on `order_items`; SQLite itself fails the insert. The authorizer also records `SQLITE_PRAGMA` and `SQLITE_TRANSACTION` actions, so `BEGIN` then `ROLLBACK` is observed from SQLite. The patch is undone before verification.
- D44: real open handle, no simulation.
- No mock proves a transaction, relation, rollback or persistence. Integrity checks are real.

## RED/GREEN evidence

| Step | Command | Exit | Result |
| --- | --- | --- | --- |
| RED 1 | `uv run --frozen pytest tests/integration/test_data_preparation.py tests/integration/test_data_atomicity.py -q` | 2 | 2 collection errors: `ImportError: cannot import name 'prepare_database'` |
| RED 2 (stub) | same | 1 | 42 failed, 12 passed. The 12 passing items only exercise validation already implemented in T3/T4 (D10, D11, D18 ×2, D19 ×3, D39a, field error, prefix, source error precedence, D40) |
| GREEN | same | 0 | 54 passed, 0 skipped |

Mutation spot checks (each applied alone, file restored afterwards and verified identical): no `ROLLBACK`; no cleanup after build failure; no target-is-source guard; no naive-clock check; no cleanup after replace failure; no catalog note; `foreign_keys` set inside the transaction; no explicit close on success; no UTC conversion. All 9 detected (at least one failing test each).

## Compliance Report

Final commands:

| Command | Exit | Result |
| --- | --- | --- |
| `uv run --frozen pytest tests/integration/test_data_preparation.py tests/integration/test_data_atomicity.py -q` | 0 | 54 collected, 54 passed, 0 failed, 0 skipped |
| `uv run --frozen pytest -q` | 0 | 677 collected, 677 passed, 0 failed, 0 skipped (623 previous + 54 new) |
| `uv run --frozen ruff check .` | 0 | All checks passed |
| `uv run --frozen ruff format --check .` | 0 | 32 files already formatted (after formatting the code block of the new spec; rerun after this report was written) |

Files: modified `src/emporio_agent/data/preparation.py`; created `docs/specs/02-06-database-preparation.md`, `docs/validation/02-06-database-preparation.md`, `tests/integration/conftest.py`, `tests/integration/test_data_preparation.py`, `tests/integration/test_data_atomicity.py`. No change to reader, relational validation, errors, parsers, schema, earlier tests, README, configuration, dependencies or lockfile.

Limits of this validation:

- Handled failures only. Nothing proves behavior after process kill, interpreter crash, OS crash or power loss, nor durability of the rename (no directory sync).
- `integrity_check_failed` is tested only for a real `foreign_key_check` finding on the build helper (revision 02-06-R1). Not through `prepare_database`; no `integrity_check` failure, physical corruption or crash is tested.
- Cleanup failure, `KeyboardInterrupt` and `SystemExit` paths are not tested.
- `mkstemp` failure, `os.close` failure and `PackageNotFoundError` paths are not tested.
- D44 is proven on this Windows machine only; on platforms that allow replacing an open file the test skips.
- The case-spelling variant of the target-is-source test depends on a case-insensitive file system; it ran (not skipped) here.
- No concurrency, lock or orphan-scan behavior exists or is tested.

## Revision 02-06-R1: chaining of `integrity_check_failed`

Finding: section "Errors and cleanup" of the contract requires `from None` (`__cause__` None, `__suppress_context__` True) for the three build and replace codes. `_write_database` raised `DataPreparationError(INTEGRITY_CHECK_FAILED)` without `from None`.

Fix: one line in `src/emporio_agent/data/preparation.py`, `raise DataPreparationError(IssueCode.INTEGRITY_CHECK_FAILED) from None`. No other code change.

Regression test: `test_integrity_check_failure_from_real_foreign_key_finding` in `tests/integration/test_data_atomicity.py`.

- Readings: valid synthetic sources read with `read_validated_tables`, then one `order_items` record changed to `product_id` 99 (no such product).
- `sqlite3.connect` is wrapped: the real function is called with the same arguments and a real authorizer is installed. The authorizer returns `SQLITE_IGNORE` only for `SQLITE_PRAGMA` on `foreign_keys`, so SQLite skips `PRAGMA foreign_keys = ON` and the inserts are accepted. Every other action is allowed. PRAGMA and transaction actions are recorded.
- `_write_database` is called directly on an empty file in `tmp_path`. The real `PRAGMA foreign_key_check` returns the finding; nothing about PRAGMA results, transaction, rollback or persistence is simulated.
- Assertions: code `integrity_check_failed`; `file_name`, `line`, `column` None; `__cause__` None; `__suppress_context__` True; no path and no SQLite message in `str` or `repr`; the authorizer ignored exactly one `foreign_keys` PRAGMA with argument `ON`; `foreign_key_check` and `integrity_check` ran; transactions observed `BEGIN`, `ROLLBACK`; an independent connection opened after the patch is undone finds no table in the file.
- Control in the same test: without the wrapper, the same readings make SQLite raise `sqlite3.IntegrityError` at insert, so the finding depends on the ignored PRAGMA only.

Not covered: the path through `prepare_database` (validation rejects such readings before any build), an `integrity_check` result other than `ok`, physical corruption, crashes.

| Step | Command | Exit | Result |
| --- | --- | --- | --- |
| RED | `uv run --frozen pytest tests/integration/test_data_atomicity.py -q -k integrity_check_failure` | 1 | 1 failed: `AssertionError: assert False is True` on `error.__suppress_context__` (code, location and `__cause__` assertions passed before it) |
| GREEN | same | 0 | 1 passed |
| T5 tests | `uv run --frozen pytest tests/integration/test_data_preparation.py tests/integration/test_data_atomicity.py -q` | 0 | 55 passed (54 previous + 1) |
| Full suite | `uv run --frozen pytest -q` | 0 | 678 passed, 0 failed, 0 skipped |
| Lint | `uv run --frozen ruff check .` | 0 | All checks passed |
| Format | `uv run --frozen ruff format --check .` | 0 | 32 files already formatted |

Files changed in this revision: `src/emporio_agent/data/preparation.py` (one line), `tests/integration/test_data_atomicity.py` (module docstring, imports, one helper and one test added; earlier tests unchanged), this report and the contract limit line. No configuration, dependency or earlier test change.
