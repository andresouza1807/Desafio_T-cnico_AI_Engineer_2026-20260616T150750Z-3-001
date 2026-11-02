# Slice 02 plan: CSV preparation into SQLite

> Status: proposed, revision R1 (answers review 02-01-R1), awaiting reviewer approval. Nothing in this plan has been executed. RED/GREEN columns describe the expected future TDD evidence; they are not evidence.

Source of truth: `docs/specs/02-data-preparation.md`.
Architecture: modular monolith, synchronous; this slice adds the `emporio_agent.data` package only.
Stack: Python 3.14 standard library (`csv`, `decimal`, `hashlib`, `json`, `sqlite3`, `tempfile`, `os`), pytest. No new dependency, no ORM, no pandas, no lockfile change.

## Files

To create in the implementation phase (not in this documentation phase):

| Path | Responsibility |
| --- | --- |
| `src/emporio_agent/data/__init__.py` | Package marker and public re-exports. |
| `src/emporio_agent/data/errors.py` | `IssueCode`, `FieldError`, `DataPreparationError`. |
| `src/emporio_agent/data/parsing.py` | Pure field parsers and Decimal conversions. |
| `src/emporio_agent/data/schema.py` | `SCHEMA_VERSION`, DDL, table column definitions, allowlists, `KNOWN_CATALOG_TEXT_CONFLICTS`. |
| `src/emporio_agent/data/preparation.py` | CSV reading, hashing, validation, build-and-replace, `prepare_database`. |
| `src/emporio_agent/data/readonly.py` | `DatabaseUnavailableError`, `connect_readonly`, `read_preparation_info`. |
| `tests/fixtures/data/valid/*.csv` | Six tiny synthetic CSV files with the source file prefix. |
| `tests/data_helpers.py` or `tests/conftest.py` | Fixture that copies `valid/` into `tmp_path` and applies one textual edit per test. |
| `tests/unit/test_data_parsing.py` | Grammar, money, percent, dates, flags, JSON, text rules. |
| `tests/integration/test_data_preparation.py` | End-to-end preparation into real SQLite files. |
| `tests/integration/test_data_atomicity.py` | Initial failure, rebuild failure, construction failure, repetition, replace failure. |
| `tests/integration/test_readonly_connection.py` | Read-only behavior and basic read. |
| `tests/integration/test_data_privacy.py` | Error content and public fixture contacts. |
| `docs/validation/02-data-preparation.md` | Observed RED/GREEN and limits, written after execution. |

Existing files that must not change in this slice: `src/emporio_agent/services/pricing.py`, `tests/unit/test_pricing.py`, `pyproject.toml`, `uv.lock`, `.gitignore`. If `tests/integration/` needs package discovery, follow the existing `tests/unit/` layout instead of editing configuration.

## Minimal synthetic fixture (`valid/`)

All values invented. Contacts use `example.invalid` and the `(00)` area code.

- categories: 2 rows (IDs 1, 2).
- customers: 2 rows (IDs 1, 2), one `city` with a non-ASCII character.
- products: 4 rows. P10 active stock 3 price `599.9`; P11 active stock 0 price `19.90`; P12 `discontinued` stock 2; P13 `coming_soon` stock 1. One description contains a comma inside quotes. specs are JSON objects.
- promotions: 3 rows. Two on P10 (one active `10`, one inactive `40`); one active `12.5` on P11.
- orders: 2 rows. O1 `delivered`, tracking, estimated date, empty notes, `total_brl` deliberately different from current price × quantity. O2 `cancelled`, empty tracking and estimate, non-empty notes.
- order_items: 3 rows. O1 has P10 and P11; O2 has P12.

A curated-conflict test passes its own product ID set through a small fixture product whose ID belongs to `KNOWN_CATALOG_TEXT_CONFLICTS` (for example 135), so the real constant is exercised without real catalog text.

## Execution checklist

Dependency order. Each task: write its tests, run RED, implement minimum code, run GREEN.

- [ ] T1. Errors and parsers (`errors.py`, `parsing.py`; tests D20–D34). No I/O.
- [ ] T2. Schema DDL and allowlists (`schema.py`; covered through D01, D09, D50).
- [ ] T3. CSV reading, hashing and header/record validation (`preparation.py` reader part; D10–D17, D35–D38, D60).
- [ ] T4. Keys, category name uniqueness, references and full in-memory validation (D18, D19, D39, D40–D42).
- [ ] T5. Build-and-replace, construction-failure cleanup and metadata (D01–D09, D43–D48, D61).
- [ ] T6. Read-only connection (`readonly.py`; D50–D55).
- [ ] T7. Privacy checks (D70–D72).
- [ ] T8. Full suite, Ruff check and format check; regression of 37 pricing tests.
- [ ] T9. Write `docs/validation/02-data-preparation.md` with observed commands and output. Stop for review.

## Test matrix

Notation: "edit" means a single textual change to a copy of `valid/` in `tmp_path`. Every integration test uses a real SQLite file inside pytest `tmp_path`. No mocks for integrity, rollback or read-only behavior. Error assertions check `code`, `file_name`, `line` and `column`.

Count: 59 distinct case IDs. D01–D09 (9), D10–D19 and D39 (11), D20–D38 (19), D40–D48 (9), D50–D55 (6), D60, D61, D70–D72 (5). Before revision R1 the matrix had 56; R1 adds D39, D47 and D48. Variants inside one row (for example D15, D16) share one ID and are not counted separately.

### Happy path and repetition

| ID | Requirement | Fixture | Action | Expected result | File |
| --- | --- | --- | --- | --- | --- |
| D01 | Six tables imported, relations preserved | `valid/` | `prepare_database` into new path | Row counts match fixture; `PRAGMA foreign_key_check` empty; join order_items→orders→customers and →products returns the 3 expected pairs; `PreparationResult.row_counts` equals table counts | test_data_preparation.py |
| D02 | Second import without duplication | `valid/` | Prepare twice into same path | Same row counts; all domain rows equal between runs (compare ordered `SELECT *`); only `prepared_at` differs | test_data_atomicity.py |
| D03 | Historical totals not recalculated | O1 total differs from current prices | Prepare | `orders.total_cents` equals source cents exactly; no error; no quality note | test_data_preparation.py |
| D04 | Status/stock conflicts preserved | P12, P13 with stock > 0 | Prepare | Products keep source `status` and `stock_quantity`; notes `status_stock_conflict` for P12 and P13 only | test_data_preparation.py |
| D05 | Curated text conflicts recorded, text preserved | Product ID 135 with arbitrary synthetic text | Prepare | Note `catalog_text_conflict` for 135; name, description and `specs_json` byte-equal to source; curated IDs absent from fixture produce no note | test_data_preparation.py |
| D06 | Optional fields become NULL | O2 empty tracking and estimate; O1 empty notes | Prepare | Those columns are NULL; no placeholder date or text | test_data_preparation.py |
| D07 | Quoted comma and non-ASCII preserved | `valid/` | Prepare | Description with comma and city with accent equal source text | test_data_preparation.py |
| D08 | Provenance hashes | `valid/` | Prepare; then edit one byte of promotions and prepare again | `source_files.sha256` equals `hashlib.sha256(file bytes)` for all six; `file_name` has no directory separator; second run changes only the promotions hash | test_data_preparation.py |
| D09 | Prices and percentages convertible for pricing | `valid/` | Prepare; read P10 and its promotions; call `quote_price` with converted values | `cents_to_decimal(59990) == Decimal("599.90")`; percent `Decimal("12.5")` from 1250; `quote_price` accepts them and gives 539.91 for P10 | test_data_preparation.py |

### CSV structure (each raises `DataPreparationError`; target absent afterwards)

| ID | Requirement | Edit | Expected code / location | File |
| --- | --- | --- | --- | --- |
| D10 | Missing source directory | non-existent dir | `source_dir_missing` | test_data_preparation.py |
| D11 | Missing file | delete order_items | `file_missing`, file name | test_data_preparation.py |
| D12 | Invalid UTF-8 | byte `0xFF` in customers | `encoding_invalid`, file name | test_data_preparation.py |
| D13 | Empty file | categories empty | `header_missing`, line None | test_data_preparation.py |
| D14 | Duplicate header | repeat `name` in categories header | `header_duplicate`, column `name` | test_data_preparation.py |
| D15 | Missing / extra header | drop `notes`; add `extra` | `header_mismatch`, column `notes` / `extra` | test_data_preparation.py |
| D16 | Wrong field count | extra field on products line 3; blank line inside orders | `field_count`, exact line | test_data_preparation.py |
| D17 | Malformed quoting | unbalanced quote in products | `csv_malformed`, file name | test_data_preparation.py |
| D18 | Duplicate key | repeat product ID; repeat (order_id, product_id) in order_items | `duplicate_key`, line of second occurrence, column of key | test_data_preparation.py |
| D19 | Broken reference | product category 99; order customer 99; item product 99 | `broken_reference`, referencing line and column | test_data_preparation.py |
| D39 | Category name unique, exact comparison | (a) categories `1,Bebidas,…` on line 2 and `2,Bebidas,…` on line 3: different IDs, same name. (b) names `Bebidas` and `bebidas`; names `Bebidas` and `Bebidas ` (trailing space). The connect recorder (section "Construction-failure mechanism") records calls only | (a) `DataPreparationError`, not `sqlite3.IntegrityError`; `duplicate_key`, file categories, line 3, column `name`; recorder saw no `sqlite3.connect` call, so SQLite never received the row; target absent. (b) preparation succeeds; both names stored byte-equal to source | test_data_preparation.py |

### Field rules (unit, pure parsers)

| ID | Requirement | Input → expected | File |
| --- | --- | --- | --- |
| D20 | Exact cents | `599.9`→59990; `19.90`→1990; `0`→0; `0.00`→0; `1`→100 | test_data_parsing.py |
| D21 | Money rejected | `-1`, `+1`, `1e3`, `NaN`, `Infinity`, `1,50`, `1.000,00`, ` 1`, `1.`, `.5`, `01`, `1.234`, empty → `invalid_money` (empty → `value_required`) | test_data_parsing.py |
| D22 | SQLite INTEGER limit and precedence | `92233720368547758.07`→9223372036854775807; `92233720368547758.08` → `money_out_of_range`; `"1" + "0" * 40` (41 characters, valid grammar) → `money_out_of_range`; `"-" + "1" * 40` (41 characters, invalid grammar) → `invalid_money` | test_data_parsing.py |
| D23 | No float path | Value `0.1`→10 and `1.15`→115 (classic float traps); helper `cents_to_decimal(115) == Decimal("1.15")` with exponent -2 | test_data_parsing.py |
| D24 | Percent exact | `10`→1000; `12.5`→1250; `0`→0; `100`→10000; `basis_points_to_percent(1250) == Decimal("12.5")` | test_data_parsing.py |
| D25 | Percent rejected | `100.01`, `101`, `-5`, `12.345`, `5%`, `NaN` → `invalid_percent` | test_data_parsing.py |
| D26 | IDs | `1`→1; `0`, `-1`, `01`, `1.0`, ` 1`, `abc` → `invalid_integer`; `9223372036854775808` → `out_of_range` | test_data_parsing.py |
| D27 | Stock / quantity | stock `0` ok, `-1` invalid; quantity `0` invalid, `1` ok | test_data_parsing.py |
| D28 | Dates | `2026-02-28` ok; `2026-02-30`, `2026-2-1`, `20260228`, `28/02/2026` → `invalid_date` | test_data_parsing.py |
| D29 | Optional date | empty → None; ` ` → `invalid_date` | test_data_parsing.py |
| D30 | Flag | `0`, `1` ok; `true`, `False`, `2`, ` 1`, empty → error | test_data_parsing.py |
| D31 | Enums | each allowlisted status and payment method ok; `Active`, `credit_13x`, `credit_0x`, `cash` → `invalid_enum` | test_data_parsing.py |
| D32 | JSON object | `{"color":"red"}` ok and returned unchanged; `[]`, `"x"`, `{` , `{"a":1,"a":2}`, `{"a":NaN}` → `invalid_json` | test_data_parsing.py |
| D33 | Required text | `" "` and empty → `value_required`; `" a "` returned unchanged | test_data_parsing.py |
| D34 | Optional text | empty → None; `"  "` → `value_blank`; `"x"` → `"x"` | test_data_parsing.py |
| D35 | Only three optional columns | empty `customers.city`, `products.description`, `promotions.description` in integration fixture | `value_required` with column name | test_data_preparation.py |
| D36 | Invalid JSON in file | products specs `[1]` | `invalid_json`, line, column `specs` | test_data_preparation.py |
| D37 | Invalid money in file | orders total `12.345` | `invalid_money`, line, column `total_brl` | test_data_preparation.py |
| D38 | Overflow in file | product price `92233720368547758.08` | `money_out_of_range`, column `price_brl` | test_data_preparation.py |

### Atomicity

| ID | Requirement | Fixture / action | Expected state | File |
| --- | --- | --- | --- | --- |
| D40 | Initial failure leaves no usable database | Invalid last table (order_items broken reference); target absent before | Error raised; target path does not exist; directory contains no `.tmp` or `-journal` file | test_data_atomicity.py |
| D41 | Late invalid data keeps previous database | Prepare `valid/`; record target bytes hash and `SELECT *`; edit order_items to duplicate pair; prepare again | Error raised; target bytes hash unchanged; content unchanged; no temporary leftovers | test_data_atomicity.py |
| D42 | Failure in earlier table also keeps database | Same as D41 with invalid categories | Same as D41 | test_data_atomicity.py |
| D43 | Missing target directory | parent directory absent | `target_directory_missing`; directory still absent | test_data_atomicity.py |
| D44 | Replace failure | Windows: hold target open with a separate `sqlite3` connection during rebuild; skip on platforms where replace succeeds with an open handle | `target_write_failed`; old content unchanged; temporary file removed | test_data_atomicity.py |
| D45 | Successful rebuild reflects new sources | Prepare; change one product price; prepare | New price in target; no duplicate rows | test_data_atomicity.py |
| D46 | Metadata | Fixed clock `2026-11-02T12:00:00+00:00` | `preparation_metadata` has `schema_version=1`, matching `prepared_at`; naive clock → `ValueError`, target unchanged | test_data_preparation.py |
| D47 | Failure during construction keeps previous database | Prepare `valid/`; record target SHA-256 and ordered `SELECT *` of every table through an independent connection. Prepare `valid/` again with the construction-failure mechanism active (INSERT into `order_items` denied) | `DataPreparationError` with `code=database_build_failed`; `file_name`, `line`, `column` None; `__cause__` None; recorded temporary path existed at connect time and no longer exists; no `<temporary>-journal`; directory contains only the target; target SHA-256 unchanged; independent `SELECT *` unchanged | test_data_atomicity.py |
| D48 | Failure during construction without previous database | Target absent; prepare `valid/` with the same mechanism | Same error as D47; target does not exist; recorded temporary path and its journal do not exist; directory empty | test_data_atomicity.py |

### Read-only access

| ID | Requirement | Fixture / action | Expected | File |
| --- | --- | --- | --- | --- |
| D50 | Missing database not created | Path in `tmp_path` that does not exist | `DatabaseUnavailableError(reason="missing")`; path still absent | test_readonly_connection.py |
| D51 | Writes rejected | Prepared database; `INSERT`, `UPDATE`, `DELETE`, `CREATE TABLE` through connection | `sqlite3.OperationalError`; row counts unchanged (verified with a second connection) | test_readonly_connection.py |
| D52 | Explicit close | Use context manager, then execute after exit | `sqlite3.ProgrammingError` (closed connection) | test_readonly_connection.py |
| D53 | Parameterized basic read | `read_preparation_info` | Returns schema version, `prepared_at`, six sources equal to `PreparationResult.sources` | test_readonly_connection.py |
| D54 | Schema mismatch | Plain SQLite file without metadata, created by test | `reason="schema_mismatch"`; file unchanged | test_readonly_connection.py |
| D55 | Path with space and non-ASCII | Directory `dados ação` under `tmp_path` | Prepare and open read-only succeed | test_readonly_connection.py |

### Privacy and provenance in errors

| ID | Requirement | Fixture / action | Expected | File |
| --- | --- | --- | --- | --- |
| D60 | Errors carry location, not values | Sentinel value `SENTINEL-PII-7781` placed in an invalid customers `customer_id`, then in products `price_brl` | `str`, `repr`, `args`, the four attributes and `traceback.format_exception_only` of the error do not contain the sentinel; they contain base file name, line and column; `__cause__` is None and `__suppress_context__` is True. The full traceback is not asserted (outside the guarantee) | test_data_privacy.py |
| D61 | No directory path in errors or metadata | Prepare from `tmp_path`; then trigger D10 (missing directory), D11 (missing file) and D47 (construction failure) under `tmp_path` | `source_files.file_name` and every `preparation_metadata` value contain no `tmp_path` text; `str`, `repr`, `args` and `format_exception_only` of each error contain no `tmp_path` text | test_data_privacy.py |
| D70 | Public fixtures are synthetic | Read `tests/fixtures/data/valid/*customers.csv` | Every email ends with `@example.invalid`; every phone starts with `(00)` | test_data_privacy.py |
| D71 | Read-only errors hide paths | D50 and D54 scenarios | `str`, `repr`, `args` and `format_exception_only` contain the reason and the database base name, and no `tmp_path` text | test_data_privacy.py |
| D72 | Result object has no cell values or paths | `PreparationResult` from `valid/` under `tmp_path` | `repr` contains no customer name, phone or email from the fixture and no `tmp_path` text; `result.database_path` equals the argument passed, unchanged | test_data_privacy.py |

Synthetic public customer generation (spec section "Privacy") has no test here, because generation is not part of this slice.

## Test strategy

- Unit: pure parsers and conversions (D20–D34). Fast, no file system.
- Integration: `prepare_database` and `connect_readonly` against real SQLite files in `tmp_path`. Fixtures are small text files edited per test, so each failure has one cause.
- No mocks for integrity, rollback, atomic replace or read-only mode. D44 uses a real open handle and is skipped with a stated reason where the platform allows replacement.
- D39, D47 and D48 use the construction-failure mechanism below. It adds no production code.
- Verification of state uses independent connections and direct SQL, not the code under test.
- Regression: the 37 pricing tests must still pass unchanged.

### Construction-failure mechanism (D39, D47, D48)

- The test uses `monkeypatch` to replace `sqlite3.connect` with a wrapper. The wrapper calls the real `sqlite3.connect` with the same arguments and records the database argument.
- Only when that argument is a `.tmp` file in the test's target directory, the wrapper installs a real SQLite authorizer with `Connection.set_authorizer`. The authorizer returns `sqlite3.SQLITE_DENY` for `SQLITE_INSERT` on table `order_items` and `sqlite3.SQLITE_OK` for every other action.
- SQLite itself then rejects the first `order_items` insert with `sqlite3.DatabaseError` ("not authorized"). At that moment the temporary file exists, the schema and the five parent tables are written inside the open transaction, and the journal exists. The failure point is deterministic: it depends on the documented import order, not on timing or disk state.
- Rollback, close, deletion and error categorization run through the real production path. Nothing simulates a successful integrity check or rollback.
- The test undoes the patch before it opens independent verification connections.
- For D39 the wrapper only records calls. An empty record proves that validation ended before any SQLite connection was opened.
- Dependency on production code: `prepare_database` opens its build connection through the standard `sqlite3.connect` (spec, "Service Contract"). No parameter, flag or hook exists for tests.

### Future RED/GREEN by behavior

| Group | Expected RED | GREEN criterion |
| --- | --- | --- |
| Parsers (T1) | Collection error: `ModuleNotFoundError: No module named 'emporio_agent.data'`. After creating empty modules: `ImportError` for each parser name. | All D20–D34 pass with exact values and codes. |
| Reading and validation (T3, T4) | `prepare_database` missing; then each structural test fails because no error or a different code is raised. D39(a) fails if `sqlite3.IntegrityError` escapes or the recorder saw a connect call. | Every structural edit raises the documented code at the documented location; D35–D39 pass. |
| Build and replace (T5) | D40/D41 fail because a partial or modified target exists, or no target is produced. D47/D48 fail if a raw `sqlite3.DatabaseError` escapes or the temporary file or journal remains. | D01–D09 and D40–D48 pass; leftover-file assertions pass. |
| Read-only (T6) | `connect_readonly` missing; then D50 fails if the file is created, D51 if writes succeed. | D50–D55 pass. |
| Privacy (T7) | D60 fails if `int()` or `Decimal` messages are chained or values are formatted. | D60–D72 pass. |

A RED caused by test setup (wrong fixture path, import configuration) must be fixed in the tests before implementation.

### Limits

- Tests prove behavior on small synthetic fixtures and on the developer's operating system. They cover handled failures only. They do not prove behavior after a process kill, an OS crash or power loss, and they do not prove durability of the rename.
- `integrity_check_failed` has no deterministic test.
- Privacy tests cover `str`, `repr`, `args`, attributes and `format_exception_only`. They do not cover full tracebacks or `__context__`.
- The quality register covers two deterministic rules. It is not a semantic validator of catalog text.
- The real private dataset is not used by the automated suite. A manual run against it is a separate, recorded validation step without printing personal values.

## Acceptance

- Spec, plan and tests agree before code is written.
- All tests above pass, plus the 37 pricing tests, Ruff check and Ruff format check.
- No change to pricing code, dependency files or `.gitignore`.
- No original customer data in fixtures, documentation or error output.
- Validation report records actual commands and output.
- No commit, push or next slice without separate approval.
