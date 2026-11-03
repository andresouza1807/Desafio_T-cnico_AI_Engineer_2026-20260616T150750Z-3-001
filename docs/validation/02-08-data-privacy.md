# Slice 02, task T7 validation: privacy checks and synthetic fixtures

Date: 2026-11-02. Baseline: `feature/data-preparation`, commit `2a1f14c`, clean working tree, `uv run --frozen pytest -q`: `744 passed, 2 skipped` (both skips in `tests/integration/test_readonly_connection.py`, "Windows does not allow '?' in file names"). No commit, push or PR.
Contract: `docs/specs/02-08-data-privacy.md`, written before the tests. Parent sources: `docs/specs/02-data-preparation.md` (R1, "Privacy"), `docs/plans/02-data-preparation.md` (T7; D60, D61, D70–D72), T1–T6 contracts and reports.
Platform: Windows 11 Pro 10.0.26200, Python 3.14.3, SQLite 3.50.4.

## Spec Summary

Executed only T7: tests D60, D61, D70, D71, D72 and the static fixture `tests/fixtures/data/valid/`.

Guarantee verified: `DataPreparationError` and `DatabaseUnavailableError` expose no cell value and no directory path in `str`, `repr`, `args`, documented attributes and `traceback.format_exception_only`. `repr(PreparationResult)` and its fields other than `database_path` hold no customer value and no path. `PreparationResult.database_path` is the caller's object (`is`).

Outside the guarantee, not asserted: full traceback, `__context__`, explicit printing of `database_path`, exceptions propagated unchanged under R1.

## Gap Analysis

- `tests/fixtures/data/valid/` did not exist. Six CSV files were written by hand from the synthetic values of `prep_kit` (`tests/integration/conftest.py`, `ROWS` and `HEADERS`). A one-off check outside the suite confirmed that each file, after line-ending normalization, is byte-equal to `prep_kit.csv_bytes(HEADERS[t], ROWS[t])`; the files are UTF-8 without BOM and use LF on disk. No private CSV file was read, copied, transformed or anonymized.
- The fixture is a test fixture, not a public demo dataset. No customer generator was created; the demo proposal in R1 is unchanged.
- No conflict with R1. No production defect was found, so no stop for a violation was needed.

## Implementation Plan (executed)

1. Confirmed baseline (branch, HEAD `2a1f14c`, clean tree, 744 passed, 2 skipped).
2. Wrote the contract `docs/specs/02-08-data-privacy.md`.
3. Created the six static CSV files and `tests/integration/test_data_privacy.py` (13 tests).
4. Ran the T7 tests, then the full suite, Ruff check and Ruff format check.

## Test Checklist

| Case | Tests | Result |
| --- | --- | --- |
| D60 | `test_d60_sentinel_value_not_in_error[customers_customer_id]`, `[products_price_brl]` | passed |
| D61 | `test_d61_persisted_provenance_has_no_directory`, `test_d61_source_dir_missing_has_no_directory`, `test_d61_file_missing_has_no_directory`, `test_d61_build_failure_has_no_directory` | passed |
| D70 | `test_d70_fixture_directory_holds_six_files`, `test_d70_customer_contacts_are_fictional`, `test_d70_fixture_is_importable_with_valid_relations` | passed |
| D71 | `test_d71_missing_database_hides_directory`, `test_d71_database_without_metadata_hides_directory_and_sqlite_message` | passed |
| D72 | `test_d72_result_has_no_customer_values_or_path`, `test_d72_other_fields_hold_only_contract_data` | passed |

Details:

- D60: `SENTINEL-PII-7781` replaces customer 1 `customer_id` (line 2) and, in a separate run, product 12 `price_brl` (line 4) in a `tmp_path` copy. Exact code, base name, line, column and message asserted; sentinel and path markers absent from public surfaces and the four attributes; `__cause__ is None`, `__suppress_context__ is True`; target absent.
- D61: independent connection reads `source_files` (six exact base names) and `preparation_metadata` (exactly `schema_version`, `prepared_at`, `importer_version`); no `/`, `\` or path marker. `source_dir_missing`, `file_missing` and `database_build_failed` asserted with exact location and message and no path marker. The build failure uses `prep_build_spy` (real `sqlite3.connect`, real authorizer denying INSERT on `order_items`), undone before verification; target absent, directory empty, temporary file and journal absent; temporary path and its base name absent from the error.
- D70: static `customers.csv` read with `csv`; non-empty; names in `{Cliente Teste Um, Cliente Teste Dois}`; emails `@example.invalid`; phones `(00)`. Exactly six files. Copied set imports; independent row counts, empty `foreign_key_check`, exact join triples. SHA-256 of static files equal before and after.
- D71: missing database and real SQLite without metadata (holding the sentinel). Exact `str`, `repr`, `args`, `reason`, `database_name` and `format_exception_only`; no path marker, sentinel, `no such table` or `preparation_metadata`; missing file and parent not created; invalid file SHA-256 and directory listing unchanged.
- D72: contacts extracted from the static fixture absent from `repr(result)`, as are `str(target)`, the database name and path markers; `result.database_path is target`; field set and values of the other fields asserted explicitly; `sources` hashes and sizes match fixture bytes.

## Test Strategy

- Real files, real SQLite, real `prepare_database` and `connect_readonly`. Expected values are literals in the test module or are read from fixture bytes with `csv` and `hashlib`, never produced by the code under test.
- The only patch is `prep_build_spy` (existing fixture) in one D61 test.
- No mock of errors, transactions, rollback or persistence. No access to private CSV files, network, OpenAI, logs or secrets. No change to `traceback` or `sys.excepthook`.
- No fabricated RED: T7 verifies behavior implemented in T3–T6. The 13 tests were written in full before their first run, and the first run passed. No stub, removed protection or production change was used.

## Evidence

First run, before any change to the test module:

```
$ uv run --frozen pytest tests/integration/test_data_privacy.py -q
13 passed in 0.29s
exit 0
```

First Ruff run reported one issue in the new test module (`E501 Line too long (124 > 100)` at line 68, and the same line flagged by `ruff format --check`). The line was wrapped as Ruff proposes; no assertion changed. Final runs:

```
$ uv run --frozen pytest tests/integration/test_data_privacy.py -q
13 passed in 0.65s
exit 0

$ uv run --frozen pytest -q
757 passed, 2 skipped in 15.39s
SKIPPED [1] tests\integration\test_readonly_connection.py:599: Windows does not allow '?' in file names
SKIPPED [1] tests\integration\test_readonly_connection.py:612: Windows does not allow '?' in file names
exit 0

$ uv run --frozen ruff check .
All checks passed!
exit 0

$ uv run --frozen ruff format --check .
38 files already formatted
exit 0
```

757 = 744 previous tests + 13 new. The 2 existing Windows skips are unchanged.

## Files

Created:

- `docs/specs/02-08-data-privacy.md`
- `docs/validation/02-08-data-privacy.md`
- `tests/integration/test_data_privacy.py`
- `tests/fixtures/data/valid/desafio_tecnico_ai_eng - categories.csv`
- `tests/fixtures/data/valid/desafio_tecnico_ai_eng - customers.csv`
- `tests/fixtures/data/valid/desafio_tecnico_ai_eng - products.csv`
- `tests/fixtures/data/valid/desafio_tecnico_ai_eng - promotions.csv`
- `tests/fixtures/data/valid/desafio_tecnico_ai_eng - orders.csv`
- `tests/fixtures/data/valid/desafio_tecnico_ai_eng - order_items.csv`

No existing file changed. Git state: the files above are untracked; nothing staged or committed.

## Limits

- Covered surfaces: `str`, `repr`, `args`, documented attributes, `format_exception_only`, `repr(PreparationResult)` and its non-path fields, persisted `source_files` and `preparation_metadata`.
- Not covered and not claimed: full traceback, `__context__`, physical corruption, process kill, OS crash or power loss, other operating systems.
- The sentinel cases cover two parser paths (integer and money); other parsers rely on the T1 unit tests.
- The repository uses `core.autocrlf=true`; checked-out fixture line endings may differ by platform. Tests read actual bytes and do not depend on line endings.
- T8 and T9 not started.
