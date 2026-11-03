# Slice 02, task T7 contract: privacy checks and synthetic fixtures

Status: contract for task T7, written before tests, 2026-11-02.
Source of truth: `docs/specs/02-data-preparation.md` (revision R1, section "Privacy"), `docs/plans/02-data-preparation.md` (T7; cases D60, D61, D70–D72) and the approved T1–T6 contracts and reports (`docs/specs/02-03-data-schema.md` to `docs/specs/02-07-readonly-connection.md`, `docs/validation/02-02-data-parsing.md` to `docs/validation/02-07-readonly-connection.md`).
This document refines R1 for the privacy tests. It adds no business rule, no production behavior and no new public interface. Where it differs from R1, R1 wins. No conflict with R1 was found.

## Scope

In scope:

- `tests/integration/test_data_privacy.py`: cases D60, D61, D70, D71, D72. New helpers stay inside this module. Existing `prep_*` fixtures of `tests/integration/conftest.py` are reused unchanged where they fit (`prep_kit` for file names, `prep_build_spy` for the construction failure).
- `tests/fixtures/data/valid/`: six static synthetic CSV files with the source file prefix `desafio_tecnico_ai_eng - `.
- `docs/validation/02-08-data-privacy.md`: observed results.

Out of scope: production code (`src/`), existing tests and `conftest.py`, configuration, dependencies, a public demo dataset, a customer generator, CLI, agent, T8 and T9. If a test reveals a violation, the failure is recorded and work stops for review. No test or contract is weakened to pass.

## Guarantee under test

From R1 "Privacy":

- `DataPreparationError` and `DatabaseUnavailableError`: these surfaces contain no cell value and no directory path:
  - `str(error)`, `repr(error)`, `error.args`;
  - the documented attributes: `code`, `file_name`, `line`, `column` for `DataPreparationError`; `reason`, `database_name` for `DatabaseUnavailableError`;
  - `traceback.format_exception_only(error)` (exception type and message).
  They contain only the error code or reason, the file or database base name, the physical line and the column name.
- `PreparationResult`: `repr()` and the fields other than `database_path` contain no cell value and no directory path.
- `PreparationResult.database_path` is the same object the caller passed (`is`), not resolved.

Outside the guarantee, and not asserted: the full traceback (`traceback.format_exception`, `print_exc`, `sys.excepthook`), `__context__`, explicit printing of `database_path`, and exceptions propagated unchanged under R1 (`sqlite3.OperationalError` on a write through the read-only connection, `KeyboardInterrupt`, `SystemExit`). Tests do not change `traceback` or `sys.excepthook`.

## Static fixture `tests/fixtures/data/valid/`

- Six files: `desafio_tecnico_ai_eng - <table>.csv` for `categories`, `customers`, `products`, `promotions`, `orders`, `order_items`.
- Content: written by hand from the synthetic values already used by `prep_kit` (`tests/integration/conftest.py`, `ROWS` and `HEADERS`), which follow the plan section "Minimal synthetic fixture". Same headers in the same column order, same rows. No private CSV file is read, copied, transformed or anonymized.
- Contacts: fictional names `Cliente Teste Um` and `Cliente Teste Dois`; emails in `@example.invalid`; phones with the impossible area code `(00)`.
- Encoding UTF-8 without BOM. Line endings are not part of the contract (the repository uses `core.autocrlf`); tests read the bytes actually on disk.
- Purpose: test fixture only. It is not a public demo dataset and does not change the scope of the future demo proposal in R1.
- Tests never write to the static files. Every test that edits or imports them copies the directory into `tmp_path` first. D70 records the SHA-256 of each static file before and after its run and requires equality.

Expected content, written explicitly in the test module (not computed by production code):

| Table | Rows | Keys |
| --- | --- | --- |
| categories | 2 | 1, 2 |
| customers | 2 | 1, 2 |
| products | 4 | 10, 11, 12, 13 |
| promotions | 3 | 100, 101, 102 |
| orders | 2 | 1000, 1001 |
| order_items | 3 | (1000, 10), (1000, 11), (1001, 12) |

Expected relations: join order_items→orders→customers and →products gives exactly `(1000, 1, 10)`, `(1000, 1, 11)`, `(1001, 2, 12)` as `(order_id, customer_id, product_id)`; `PRAGMA foreign_key_check` is empty. Expected quality notes: 2 (`status_stock_conflict` for products 12 and 13; product 135 is not in the fixture).

## Cases

Common terms: "public surfaces" of an error are `str`, `repr`, every element of `args` (as `str`) and the joined text of `traceback.format_exception_only`. "Path markers" are `str(tmp_path)`, `str(tmp_path.resolve())` and the unique private directory name used by the test (`pasta-privada-t7`). Every test uses a real SQLite file in `tmp_path`.

### D60 — errors carry location, not values

- Copy `valid/` into `tmp_path/pasta-privada-t7/fontes`.
- Variant (a): replace the `customer_id` of customer 1 (physical line 2 of customers) with `SENTINEL-PII-7781`. Expected: `DataPreparationError`, `code == "invalid_integer"`, `file_name == "desafio_tecnico_ai_eng - customers.csv"`, `line == 2`, `column == "customer_id"`, `str == "desafio_tecnico_ai_eng - customers.csv line 2, column customer_id: invalid_integer"`.
- Variant (b), separate run: replace the `price_brl` of product 12 (physical line 4 of products) with `SENTINEL-PII-7781`. Expected: `code == "invalid_money"`, `file_name == "desafio_tecnico_ai_eng - products.csv"`, `line == 4`, `column == "price_brl"`, `str == "desafio_tecnico_ai_eng - products.csv line 4, column price_brl: invalid_money"`.
- Each edit is a single textual replacement; the test asserts the original text occurs exactly once before replacing.
- `prepare_database` runs for real. The sentinel does not appear in any public surface or in the four attributes. Path markers do not appear either. `args == (str(error),)`. `__cause__ is None` and `__suppress_context__ is True`. The target database does not exist afterwards.
- Not asserted: removal of `__context__`, content of the full traceback.

### D61 — no directory path in errors or metadata

- Success: prepare the copied fixture into `tmp_path/pasta-privada-t7/db/emporio.sqlite3` with the fixed clock `2026-11-02T12:00:00+00:00`. Read `source_files` and `preparation_metadata` through an independent `sqlite3` connection (nothing patched).
  - `source_files.file_name` is exactly the expected base name for each of the six tables; it has no `/` or `\` and no path marker.
  - `preparation_metadata` has exactly the keys `schema_version`, `prepared_at`, `importer_version`; values `"1"`, `"2026-11-02T12:00:00+00:00"` and `importlib.metadata.version("emporio-agent")`; no value contains a path marker, `/` or `\`.
- Errors, each under `tmp_path/pasta-privada-t7`:
  - `source_dir_missing`: source directory does not exist. Expected `file_name`, `line`, `column` None; `str == "source_dir_missing"`.
  - `file_missing`: order_items removed from the copy. Expected `file_name == "desafio_tecnico_ai_eng - order_items.csv"`, `line` and `column` None.
  - `database_build_failed`: target absent; `prep_build_spy.install(target_dir, deny_insert_into="order_items")` (real `sqlite3.connect`, real authorizer returning `SQLITE_DENY` for INSERT on `order_items`). Expected `file_name`, `line`, `column` None; `str == "database_build_failed"`; the spy saw one temporary connection and at least one denial. `undo()` runs before the independent verification: target absent, target directory empty, recorded temporary path and its journal absent.
  - For each error: no path marker and, for the build failure, no recorded temporary path in the public surfaces or attributes; `args == (str(error),)`; `__cause__ is None`.

### D70 — public fixtures are synthetic

- Read `valid/desafio_tecnico_ai_eng - customers.csv` with the standard `csv` module (UTF-8). Header equals `customer_id,name,phone,email,city`. Row set is not empty.
- Every `name` belongs to `{"Cliente Teste Um", "Cliente Teste Dois"}`; every `email` ends with `@example.invalid`; every `phone` starts with `(00)`.
- The directory holds exactly the six expected file names.
- The copied six files form an importable set: `prepare_database` succeeds; an independent connection gives the row counts, `PRAGMA foreign_key_check` result and join triples of the table above.
- SHA-256 of each static file is equal before and after the test.

### D71 — read-only errors hide paths

- Missing database: `tmp_path/pasta-privada-t7/emporio.sqlite3`, parent absent. Expected `reason == "missing"`, `database_name == "emporio.sqlite3"`, `str == "emporio.sqlite3: missing"`, `args == (str,)`, `repr == "DatabaseUnavailableError('emporio.sqlite3: missing')"`, `format_exception_only == ["emporio_agent.data.readonly.DatabaseUnavailableError: emporio.sqlite3: missing\n"]`. The file and its parent are not created.
- Plain SQLite without metadata: created by the test with a real connection; one table `t(x)` holding the text `SENTINEL-PII-7781`. Expected `reason == "schema_mismatch"`, same layout of `str`, `repr`, `args`, `format_exception_only` with `schema_mismatch`. No path marker, no sentinel, no original SQLite message (`no such table`, `preparation_metadata`) in any surface. `__cause__ is None`. File SHA-256 and directory listing unchanged.

### D72 — result object has no cell values or paths

- Prepare from the copied static fixture into `tmp_path/pasta-privada-t7/db/emporio.sqlite3` with the fixed clock.
- Names, phones and emails are extracted from the static `customers.csv` with the `csv` module. None appears in `repr(result)`. Path markers, `str(target)` and the database base name do not appear in `repr(result)`.
- `result.database_path is target`.
- Field set of `PreparationResult` is exactly `database_path`, `schema_version`, `prepared_at`, `sources`, `row_counts`, `quality_note_count`. Values: `schema_version == 1`; `prepared_at == 2026-11-02T12:00:00+00:00`; `row_counts == {categories: 2, customers: 2, products: 4, promotions: 3, orders: 2, order_items: 3}`; `quality_note_count == 2`; `sources` holds six `SourceFileInfo` in table order with `file_name` equal to the prefixed base name, `sha256` equal to `hashlib.sha256` of the static file bytes, `size_bytes` equal to their length and `row_count` as above. No customer value and no path marker appears in `str()` of any field other than `database_path`.

## Test mechanisms

- Real files, real SQLite, real `prepare_database` and `connect_readonly`. Expected values are literals in the test module or are read from the fixture bytes with the standard library (`csv`, `hashlib`); they are never produced by the code under test.
- No mock of errors, transactions, rollback or persistence. The only patch is `prep_build_spy` in D61, which calls the real `sqlite3.connect` and installs a real SQLite authorizer; it is undone before verification.
- No access to private CSV files, network, OpenAI, logs or secrets.

## Expected evidence

T7 verifies behavior already implemented in T3–T6. The tests may pass on the first run. No RED is fabricated: no stub, no removed protection, no production change. The validation report records that the tests were written before their first run and the observed result.

## Limits

- Covered surfaces: `str`, `repr`, `args`, documented attributes, `format_exception_only`, `repr(PreparationResult)` and its non-path fields, persisted `source_files` and `preparation_metadata`.
- Not covered: full traceback, `__context__`, physical corruption, process kill, OS crash or power loss, behavior on other operating systems.
- The sentinel cases cover two parser paths (integer, money). Other parsers are covered by the T1 unit tests, which assert that `FieldError` stores only the code.

## Traceability

| Case | Test functions (prefix) |
| --- | --- |
| D60 | `test_d60_` |
| D61 | `test_d61_` |
| D70 | `test_d70_` |
| D71 | `test_d71_` |
| D72 | `test_d72_` |
