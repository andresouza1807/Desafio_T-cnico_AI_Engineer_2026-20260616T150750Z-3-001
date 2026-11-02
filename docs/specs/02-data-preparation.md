# Slice 02 specification: CSV preparation into SQLite

Status: proposed contract for review, 2026-11-02, revision R1 (answers review 02-01-R1). Not implemented. No test in this document has been executed.
Sources: internal scope v0.4 (RF02–RF06, RF09, T01, T02, T12, T22), ADR 0001, Slice 01 pricing contract, and a structural profile of the six supplied CSV files.

## Spec Summary

Prepare one SQLite database from the six supplied CSV files so later catalog, pricing and order services can read validated data. Preparation is an explicit offline step. The conversation runtime only opens the prepared database read-only.

In scope:

- Strict parsing and validation of six CSV files into six domain tables.
- Exact money conversion to integer cents and exact percentage conversion to integer basis points.
- Build-then-replace preparation: a run that fails with an error handled by the importer never leaves a partial database at the target path and never alters the last valid database. Process kills, OS crashes and power loss have weaker, stated limits (section "Atomicity and repetition").
- Provenance: SHA-256 of every source file, preparation time and schema version stored in the database.
- A small data-quality register for known, deterministic conflicts. Source values stay unchanged.
- A read-only connection helper with explicit closing and one basic read of preparation metadata.
- A proposal for synthetic public customer contacts. Generation and publication are not part of this slice.

Out of scope: catalog search, availability rules, promotion selection at query time, customer-scoped order authorization, policy PDF processing, CLI commands, OpenAI integration and generation of any public dataset.

### Observed source profile

Profile run on 2026-11-02 against the local private copy. Counts describe this sample only. The importer must not require these counts.

| File (relative name) | Data rows | Columns | Notes |
| --- | --- | --- | --- |
| `desafio_tecnico_ai_eng - categories.csv` | 9 | category_id, name, description | IDs 1–9; only 6 categories referenced by products. |
| `desafio_tecnico_ai_eng - customers.csv` | 50 | customer_id, name, phone, email, city | IDs 1–50; no empty field; one email value appears on two rows. |
| `desafio_tecnico_ai_eng - order_items.csv` | 22 | order_id, quantity, product_id | No source line ID; 20 orders; two orders have two distinct products; no repeated (order_id, product_id) pair; quantity 1–2. |
| `desafio_tecnico_ai_eng - orders.csv` | 20 | order_id, customer_id, order_date, status, total_brl, payment_method, tracking_code, estimated_delivery, notes | tracking_code and estimated_delivery empty on 12 rows; notes empty on 16 rows. |
| `desafio_tecnico_ai_eng - products.csv` | 65 | product_id, price_brl, name, category_id, description, stock_quantity, status, specs, created_at | IDs 81–145; specs always a JSON object with string values; some quoted fields contain commas. |
| `desafio_tecnico_ai_eng - promotions.csv` | 25 | promotion_id, product_id, discount_percent, description, is_active | is_active uses `0`/`1` (4 active); three products have two promotions; no product has two active promotions. |

Common format: UTF-8 without BOM, CRLF line endings, comma delimiter, double-quote quoting, no trailing newline, no leading/trailing whitespace in any field. All foreign keys resolve. Money has at most one fractional digit in the sample. Dates are `YYYY-MM-DD`.

Known semantic observations, preserved and not rejected:

- Products 130 (`coming_soon`) and 136 (`discontinued`) have positive stock.
- Orders 3 and 20 have `total_brl` different from the sum of current product price × quantity. Totals are historical and must not be recalculated.
- An internal manual review flagged products 135, 137, 139, 140, 142 and 144 for divergence between name, description and specs.
- Several inactive promotions carry campaign names such as seasonal events. Campaign names do not change `is_active`.

## Gap Analysis

### Decisions

| Topic | Decision | Grounding |
| --- | --- | --- |
| order_items key | Composite primary key `(order_id, product_id)`. A repeated pair is rejected with `duplicate_key`. | The source has no line identifier. The sample has no repeated pair. Summing quantities or dropping a row would change source meaning silently; a repeated pair is ambiguous (export duplicate or separate line). No surrogate source ID is invented. |
| Money storage | INTEGER cents, parsed from source text through `Decimal`, never `float`. | Scope persistence rule; Slice 01 requires prices exactly representable in cents. |
| Percent storage | INTEGER basis points (hundredths of a percent), 0–10000. More than two fractional digits is rejected, not rounded. | Sample percentages are integers 5–40. Integer storage is exact; `Decimal(bp) / Decimal(100)` yields a Decimal accepted by `quote_price`. |
| is_active | Accept exactly `0` or `1`; store INTEGER 0/1. | Sample uses only `0`/`1`. Other spellings are not guessed. |
| Required text | Empty or whitespace-only value is rejected. Non-blank text is stored byte-for-byte, without trimming. | Scope forbids silent correction. |
| Optional fields | Only `orders.tracking_code`, `orders.estimated_delivery` and `orders.notes` are optional. Exactly empty text becomes NULL. | Sample has empty values only in these three columns, matching order status semantics. |
| Customer email | No uniqueness constraint. | One email value repeats in the sample; uniqueness would reject legitimate source data. |
| Category name | UNIQUE by exact text equality (no trimming, no case folding, no Unicode normalization). A second row with the same name raises `duplicate_key`, column `name`, at the second occurrence's line, during in-memory validation. | Catalog search resolves categories by name (RF02); duplicate names would make that resolution ambiguous. Sample names are distinct. Exact equality matches the SQLite `UNIQUE` constraint on `TEXT` with the default `BINARY` collation, so Python and SQLite agree. Case-insensitive matching for search belongs to the catalog slice. |
| Header handling | Exact column set required; column order free; duplicate, missing or extra columns rejected. | Prevents silent drift between source and schema. |
| Validation mode | Fail fast on the first issue in deterministic order (file order, then line order, then column order). | Simple, deterministic error contract. Multi-error reporting is a future option. |
| Atomicity | Build a new database in a temporary file in the target directory, validate, commit, close, then `os.replace` over the target. | Gives one observable contract for initial build and rebuild when a failure is handled by the importer. Process kills, OS crashes and power loss are limits, not guarantees. See section "Atomicity and repetition". |
| Payment methods | Allowlist `pix`, `debit`, `boleto`, `credit_1x` … `credit_12x`. | Reviewer decision 02-01-R1. The upper bound of 12 follows section 3.1 of the store policies. Installment calculation is not part of this slice. |
| Orders without items, cross-field order checks | Allowed and not enforced. No extra check between status, tracking code and dates. | Reviewer decision 02-01-R1. Values are preserved as source data. |

### Alternatives rejected

- In-place `DELETE` + `INSERT` inside one transaction on the live database: rollback protects a rebuild, but an initial failure can still leave a file with an empty schema that readers could open. Schema changes would also need migrations.
- Surrogate `order_item_id` or a `line_number` column as key: invents an identifier that the source does not provide.
- Summing quantities for repeated `(order_id, product_id)`: alters source data.
- `TEXT` decimal for percentages: exact too, but needs text validation in SQL and numeric comparison through casts. Integer basis points are simpler to constrain.
- Storing money as REAL or parsing with `float`: inexact.
- pandas, an ORM or new dependencies: excluded by the adopted stack.
- Collecting every error in one run: more useful for large files, not needed for this dataset size.

### Open gaps

Blocking: none for writing tests and code of this slice.

Resolved by reviewer decision 02-01-R1 (moved to "Decisions"):

1. `payment_method` allowlist: `credit_1x` … `credit_12x` accepted, grounded on section 3.1 of the store policies. No installment calculation.
2. Cross-field order checks (tracking code only when shipped/delivered, estimated delivery not before order date): not enforced in this slice.
3. Orders without items and categories without products: allowed. The sample has the second case.
4. A CLI command for preparation belongs to the CLI slice. Until then, the service function is the entry point.

Deferred, non-blocking for this specification:

5. Whether the five non-customer CSVs may be redistributed in the public repository.
6. Generation of synthetic public customers, including whether they keep the original city. The proposal in section "Privacy" keeps city, because it is a coarse location shared by many rows and no planned feature identifies a person by it.

### Compatibility with Slice 01

- `price_cents` maps to `Decimal(price_cents).scaleb(-2)`, an exact Decimal with two fractional digits. It satisfies `quote_price` validation.
- `discount_basis_points` maps to `Decimal(bp) / Decimal(100)`, which is in [0, 100] and finite. `is_active` maps to `bool(value)`, a real `bool`.
- No pricing rule changes. No conflict found.

## Data Contract

### Source files and configuration

- `source_dir: Path` — directory containing the six files. No default path is hard-coded in the library. Future configuration name: `EMPORIO_SOURCE_DIR`.
- `file_prefix: str` — default `"desafio_tecnico_ai_eng - "`. File name is `f"{file_prefix}{table}.csv"`.
- `database_path: Path` — target database file. Suggested local default for the future CLI: `data/local/emporio.sqlite3` (ignored by Git). Future configuration name: `EMPORIO_DB_PATH`.

### CSV format

- Each file is read once as bytes. The same bytes are hashed and parsed, so the recorded hash describes exactly the parsed content.
- Encoding: UTF-8. An optional UTF-8 BOM is removed. Invalid UTF-8 raises `encoding_invalid`.
- Dialect: comma delimiter, `"` quote character, doubled quote as escape, `strict=True`. LF and CRLF line endings are both accepted. A final line ending is optional. Quoted fields may contain commas and line breaks.
- First record is the header. An empty file raises `header_missing`.
- A header with a duplicate name raises `header_duplicate`. A missing or unexpected column raises `header_mismatch`; the error names the column (a column name is not a cell value).
- Every data record must have exactly the header's field count, else `field_count`. A blank line inside the file is a record with zero fields and also raises `field_count`.
- Malformed quoting or a NUL byte raises `csv_malformed`.
- A file with a valid header and zero data records is structurally valid. Referential rules may still fail.
- `line` in errors is the physical line number where the failing record ends (Python `csv.reader.line_num`). The header is line 1.

### Field grammars

All grammars apply to the raw cell text. No trimming happens before matching.

| Kind | Grammar | Result | Error codes |
| --- | --- | --- | --- |
| `id` | `^[1-9][0-9]*$`, value ≤ 9223372036854775807 | int | `invalid_integer`, `out_of_range` |
| `count` (stock) | `^(0\|[1-9][0-9]*)$`, value ≤ 9223372036854775807 | int ≥ 0 | `invalid_integer`, `out_of_range` |
| `quantity` | same as `id` (≥ 1) | int ≥ 1 | `invalid_integer`, `out_of_range` |
| `money` | `^(0\|[1-9][0-9]*)(\.[0-9]{1,2})?$`; range rules in "Money rules" | int cents | `invalid_money` (grammar), `money_out_of_range` (range) |
| `percent` | `^(0\|[1-9][0-9]{0,2})(\.[0-9]{1,2})?$`, value ≤ 100 | int basis points 0–10000 | `invalid_percent` |
| `date` | `^[0-9]{4}-[0-9]{2}-[0-9]{2}$` and valid calendar date (`datetime.date.fromisoformat`) | ISO text | `invalid_date` |
| `flag` | `^[01]$` | int 0 or 1 | `invalid_flag` |
| `enum` | exact member of the column allowlist | text | `invalid_enum` |
| `json_object` | JSON object; duplicate keys, `NaN`, `Infinity` and `-Infinity` rejected | original text | `invalid_json` |
| `text` | at least one non-whitespace character | original text | `value_required` |
| `optional_text` | empty → NULL; whitespace-only → error; else original text | text or NULL | `value_blank` |
| `optional_date` | empty → NULL; else `date` grammar | ISO text or NULL | `invalid_date` |

A required field that is empty raises `value_required` before its grammar is checked.

### Money rules

1. Match the `money` grammar on the original text, at any length. A mismatch raises `invalid_money`. This rejects signs, exponents, `NaN`, `Infinity`, thousands separators, commas, whitespace and more than two fractional digits (sub-cent values).
2. Only text that matches the grammar reaches this step. Text longer than 40 characters raises `money_out_of_range` before any arithmetic.

Precedence: grammar first, length second. Example: `"1" + "0" * 40` (41 characters, a syntactically valid integer) raises `money_out_of_range`, as D22 requires. `"-" + "1" * 40` (41 characters, invalid grammar) raises `invalid_money`. Any grammatical text longer than 40 characters is above the step 4 limit too, so the length check only bounds the arithmetic; it never changes the result code.
3. Build `Decimal(text)` (exact constructor). Inside `decimal.localcontext()` with precision 60 and traps for `Inexact` and `Rounded`, compute `value * 100` and convert to `int`.
4. Reject cents greater than `SQLITE_MAX_INTEGER = 2**63 - 1` with `money_out_of_range`. Largest accepted text: `92233720368547758.07`. Smallest rejected: `92233720368547758.08`.
5. `float` is never used. `0` and `0.00` are valid and yield 0 cents.

Percent rules: match the grammar, build `Decimal(text)`, reject values above 100 with `invalid_percent`, compute `int(value * 100)` under the same exact context. `100` yields 10000; `12.5` yields 1250; `12.345` is rejected.

### Enum allowlists

- `products.status`: `active`, `discontinued`, `coming_soon`.
- `orders.status`: `pending`, `confirmed`, `shipped`, `delivered`, `cancelled`.
- `orders.payment_method`: `pix`, `debit`, `boleto`, `credit_1x` … `credit_12x` (reviewer decision 02-01-R1, store policies section 3.1).

### Table schema

Schema version: `1`. All tables are created with `STRICT`. `PRAGMA foreign_keys = ON` during the build. Constraints duplicate the Python validation as a second line of defense; the Python validator must raise the documented error code before SQLite sees an invalid row.

Import order (parents first): categories, customers, products, promotions, orders, order_items.

| Table | Column | Type | Null | Source | Constraint |
| --- | --- | --- | --- | --- | --- |
| categories | category_id | INTEGER | no | category_id (`id`) | PRIMARY KEY, > 0 |
| | name | TEXT | no | name (`text`) | UNIQUE (exact text, `BINARY` collation), non-blank |
| | description | TEXT | no | description (`text`) | non-blank |
| customers | customer_id | INTEGER | no | customer_id (`id`) | PRIMARY KEY, > 0 |
| | name | TEXT | no | name (`text`) | non-blank |
| | phone | TEXT | no | phone (`text`) | non-blank, no format rule |
| | email | TEXT | no | email (`text`) | non-blank, not unique |
| | city | TEXT | no | city (`text`) | non-blank |
| products | product_id | INTEGER | no | product_id (`id`) | PRIMARY KEY, > 0 |
| | category_id | INTEGER | no | category_id (`id`) | REFERENCES categories |
| | name | TEXT | no | name (`text`) | non-blank, not unique |
| | description | TEXT | no | description (`text`) | non-blank |
| | price_cents | INTEGER | no | price_brl (`money`) | ≥ 0 |
| | stock_quantity | INTEGER | no | stock_quantity (`count`) | ≥ 0 |
| | status | TEXT | no | status (`enum`) | IN allowlist |
| | specs_json | TEXT | no | specs (`json_object`) | `json_valid` and `json_type = 'object'` |
| | created_at | TEXT | no | created_at (`date`) | `date(created_at) IS created_at` |
| promotions | promotion_id | INTEGER | no | promotion_id (`id`) | PRIMARY KEY, > 0 |
| | product_id | INTEGER | no | product_id (`id`) | REFERENCES products; not unique |
| | discount_basis_points | INTEGER | no | discount_percent (`percent`) | 0–10000 |
| | description | TEXT | no | description (`text`) | non-blank |
| | is_active | INTEGER | no | is_active (`flag`) | IN (0, 1) |
| orders | order_id | INTEGER | no | order_id (`id`) | PRIMARY KEY, > 0 |
| | customer_id | INTEGER | no | customer_id (`id`) | REFERENCES customers |
| | order_date | TEXT | no | order_date (`date`) | `date(order_date) IS order_date` |
| | status | TEXT | no | status (`enum`) | IN allowlist |
| | total_cents | INTEGER | no | total_brl (`money`) | ≥ 0; historical, never recalculated |
| | payment_method | TEXT | no | payment_method (`enum`) | IN allowlist |
| | tracking_code | TEXT | yes | tracking_code (`optional_text`) | NULL or non-blank |
| | estimated_delivery | TEXT | yes | estimated_delivery (`optional_date`) | NULL or valid date |
| | notes | TEXT | yes | notes (`optional_text`) | NULL or non-blank |
| order_items | order_id | INTEGER | no | order_id (`id`) | REFERENCES orders |
| | product_id | INTEGER | no | product_id (`id`) | REFERENCES products |
| | quantity | INTEGER | no | quantity (`quantity`) | ≥ 1 |
| | | | | | PRIMARY KEY (order_id, product_id) |

Metadata tables:

| Table | Columns | Content |
| --- | --- | --- |
| preparation_metadata | key TEXT PRIMARY KEY, value TEXT NOT NULL | `schema_version` = `1`; `prepared_at` = UTC ISO-8601 from the injected clock; `importer_version` = package version. |
| source_files | table_name TEXT PRIMARY KEY, file_name TEXT NOT NULL, sha256 TEXT NOT NULL (64 lowercase hex), size_bytes INTEGER NOT NULL, row_count INTEGER NOT NULL | One row per source file. `file_name` is the base name only, never a directory path. |
| data_quality_notes | entity TEXT NOT NULL (`product`), entity_id INTEGER NOT NULL, code TEXT NOT NULL, PRIMARY KEY (entity, entity_id, code) | Known conflicts for later services. No free text copied from the source. |

### Validation order and categories

For each file in import order: read bytes, decode, parse header, then each record. Within a record, check columns in header-definition order (the table order above). After all records of a file:

1. Uniqueness pass. Scan records in line order. For each record, check its unique keys in column order: the primary key first, then `categories.name` (categories only). The first value already seen raises `duplicate_key` at the second occurrence's line. The column is the key column (`category_id`, `name`, and so on); for the order_items composite key it is `product_id`, the last key column. Comparison is exact: Python equality on the parsed value (int for IDs, decoded text for `name`). So the reported issue is the one with the lowest line, then the lowest column, as the fail-fast rule requires.
2. Foreign keys against already validated parent tables (reported at the referencing line).

After all files: build the database. SQLite never receives a row that fails any of these checks; the `UNIQUE` and `REFERENCES` constraints are only a second line of defense.

Structurally invalid data (always an error, preparation aborts):

- source directory or file missing or unreadable;
- invalid encoding, malformed CSV, header problems, wrong field count;
- value not matching its grammar, out of range, unknown enum member, invalid JSON object;
- duplicate primary key, duplicate category name, broken foreign key.

Known semantic inconsistency (never an error, values preserved, optionally registered):

- non-active product with positive stock;
- name, description and specs that disagree;
- order total different from current prices × quantities;
- inactive promotion with a seasonal campaign name;
- orders whose status and optional fields look unusual.

### Data-quality register

Rules are deterministic. They do not claim to detect every semantic conflict.

- `status_stock_conflict`: product with status `discontinued` or `coming_soon` and `stock_quantity > 0`. Computed from data. Sample result: 130 and 136.
- `catalog_text_conflict`: product ID present in a curated constant `KNOWN_CATALOG_TEXT_CONFLICTS = {135, 137, 139, 140, 142, 144}` from the manual review of 2026-11-02. IDs not present in the imported products produce no note and no error. The importer never edits name, description or specs.

Price, stock and status columns keep source values exactly. Availability decisions belong to the catalog service.

### Atomicity and repetition

Strategy: build and replace.

1. Validate `source_dir` and the parent directory of `database_path`. The parent must exist; the importer does not create directories.
2. Read and validate all six files completely in memory, before creating any file.
3. Create a unique temporary file in the target's directory with `tempfile.mkstemp(prefix=".<target name>.", suffix=".tmp")`.
4. Open it with `sqlite3`, set `journal_mode = DELETE`, `synchronous = FULL`, `foreign_keys = ON`. Create the schema and insert all rows and metadata inside one transaction. Run `PRAGMA foreign_key_check` and `PRAGMA integrity_check`; any finding raises `integrity_check_failed`. Commit and close. Any `sqlite3.Error` or `OSError` in steps 3–4 (create, write, check, commit, close) raises `database_build_failed`.
5. `os.replace(temporary, database_path)`. An `OSError` raises `target_write_failed`.
6. On any failure in steps 3–5, close the connection (an uncommitted transaction is rolled back by SQLite), delete the temporary file and `<temporary>-journal` if present, and raise the categorized error with `raise ... from None`. The target is not touched. If a cleanup deletion itself fails, the categorized error is still raised and the leftover file is a known limit.

Failure classes. Only the first class has guarantees.

1. Handled failure. An exception that reaches the importer's own error handling during normal execution: invalid source data, `sqlite3.Error` or `OSError` in steps 3–5, a naive clock. The observable contract below applies, and tests D40–D48 cover it.
2. Interruption inside Python (`KeyboardInterrupt`, `SystemExit`). Cleanup of step 6 runs on a best-effort basis and the exception propagates unchanged, not categorized. Not tested.
3. Process termination without Python cleanup (process killed, interpreter crash), OS crash, power loss. No cleanup runs. The importer gives no guarantee here: the temporary file and its journal may remain, and the slice does not demonstrate which version of the target survives or that it is intact. The recovery action is to run preparation again; no recovery code is part of this slice.

Observable contract (handled failures only):

- Initial success: target exists and contains all six tables, metadata and quality notes.
- Initial failure: target does not exist after the call. No temporary file or journal remains in the directory.
- Rebuild success: target is replaced by a complete new database. Row content depends only on the source bytes; repeating with identical sources produces identical domain rows and no duplicates. Only `prepared_at` may differ.
- Rebuild failure during validation (including invalid data in the last imported table): target bytes and content are unchanged. No temporary file was created.
- Failure during construction (step 3 or 4, after the temporary file exists and before replace): `database_build_failed`; previous target bytes and content unchanged; temporary file and journal removed. Without a previous target: target does not exist; temporary file and journal removed.
- `os.replace` failure (for example, a reader holds the file open on Windows): raise `target_write_failed`; target unchanged; temporary file removed.

Limits:

- `os.replace` is atomic on POSIX filesystems when source and target are on the same filesystem. On Windows it uses `MoveFileEx` with replace semantics; it is not documented as atomic under all conditions, and it fails while another process holds the target open.
- `synchronous = FULL` makes SQLite sync the temporary database file at commit. The importer does not sync the directory after `os.replace`. Durability of the rename after an OS crash or power loss is therefore not guaranteed and not tested.
- An orphan temporary file from failure class 2 or 3 is never opened by readers, because `connect_readonly` opens only `database_path`. Automatic cleanup of orphans is not part of this slice.
- Concurrent preparation runs are not supported. The last successful replace wins.
- `integrity_check_failed` is a defensive path. No deterministic way to make a real SQLite file fail these checks after valid inserts is known, so no test covers it.
- Hardware corruption is out of scope.

### Read-only access

- `connect_readonly(database_path)` opens `Path.resolve().as_uri() + "?mode=ro"` with `uri=True`. It checks first that the path is an existing regular file; otherwise it raises `DatabaseUnavailableError(reason="missing")` and creates nothing.
- The connection sets `row_factory = sqlite3.Row` and `PRAGMA query_only = ON`.
- It verifies `preparation_metadata.schema_version == "1"`; a mismatch or a missing metadata table raises `DatabaseUnavailableError(reason="schema_mismatch")` and closes the connection.
- It is a context manager that always closes the connection on exit. `sqlite3.Connection` used directly as a context manager only commits or rolls back; it does not close, so it is not the public helper.
- Any write through the connection raises `sqlite3.OperationalError`, propagated unchanged as a programming error.
- All queries use `?` parameters. Table and column names are never built from input.
- Paths containing spaces or non-ASCII characters must work.

Catalog search and order authorization are not defined here.

### Privacy

- Private sources: the original CSV files stay outside the repository, or under `data/local/` (ignored by Git). The private database lives under `data/local/`.
- Public test fixtures: small hand-written synthetic CSV files under `tests/fixtures/data/`. Contacts use fictional names, `example.invalid` email domains and phones in the impossible area code `(00)`.
- The library writes no logs.
- Guarantee scope. For `DataPreparationError` and `DatabaseUnavailableError`, these surfaces contain no cell value and no directory path: `str()`, `repr()`, `args`, the documented attributes, and the sanitized output `traceback.format_exception_only(error)` (exception type and message). They contain only error code, file or database base name, line and column name. For `PreparationResult`, `repr()` and the fields other than `database_path` contain no cell value and no directory path.
- Outside the guarantee:
  - The full traceback (`traceback.format_exception`, `traceback.print_exc`, the default `sys.excepthook`). It lists absolute file paths and source lines of library frames and of the caller's frames. The library does not control the caller's code or paths.
  - `__context__`. `raise ... from None` sets `__cause__ = None` and `__suppress_context__ = True`, so the default traceback does not print a chained exception. It does not clear `__context__`, does not sanitize the traceback and does not change the caller's frames. Parsers match the grammar before any conversion, so conversion exceptions that echo input are not expected on the normal path; this is a design measure, not a guarantee.
  - Exceptions propagated unchanged: `sqlite3.OperationalError` on a write through the read-only connection, `KeyboardInterrupt`, `SystemExit`.
- This slice does not change `traceback` or `sys.excepthook` globally. What a future CLI prints is decided in the CLI slice.
- `PreparationResult.database_path` is the `Path` object the caller passed, unchanged (not resolved). It is the caller's own input, not source data. It is declared with `field(repr=False)`, so `repr()` omits it. The path is never written into the database: `source_files.file_name` and error messages use base names only. A caller that prints `database_path` explicitly is outside the guarantee.
- Proposal for a future public demo dataset (not generated in this slice): keep `customer_id` and `city`; replace `name` with `Cliente Demo <id, two digits>`, `phone` with `(00) 90000-<id, four digits>`, `email` with `cliente<id, two digits>@example.invalid`. Order relations stay valid because orders reference only `customer_id`. The generator reads only `customer_id` and `city` from the original. A test must assert that no original name, phone or email appears in the generated file. This step needs separate approval.

## Service Contract

Package: `emporio_agent.data`. Synchronous. No network, no logging, no environment variable reads, no global state.

```python
SCHEMA_VERSION: Final = 1
SOURCE_FILE_PREFIX: Final = "desafio_tecnico_ai_eng - "
TABLE_ORDER: Final = ("categories", "customers", "products", "promotions", "orders", "order_items")
SQLITE_MAX_INTEGER: Final = 2**63 - 1
KNOWN_CATALOG_TEXT_CONFLICTS: Final[frozenset[int]]

class IssueCode(StrEnum):
    SOURCE_DIR_MISSING = "source_dir_missing"
    FILE_MISSING = "file_missing"
    FILE_UNREADABLE = "file_unreadable"
    ENCODING_INVALID = "encoding_invalid"
    CSV_MALFORMED = "csv_malformed"
    HEADER_MISSING = "header_missing"
    HEADER_DUPLICATE = "header_duplicate"
    HEADER_MISMATCH = "header_mismatch"
    FIELD_COUNT = "field_count"
    VALUE_REQUIRED = "value_required"
    VALUE_BLANK = "value_blank"
    INVALID_INTEGER = "invalid_integer"
    OUT_OF_RANGE = "out_of_range"
    INVALID_MONEY = "invalid_money"
    MONEY_OUT_OF_RANGE = "money_out_of_range"
    INVALID_PERCENT = "invalid_percent"
    INVALID_DATE = "invalid_date"
    INVALID_FLAG = "invalid_flag"
    INVALID_ENUM = "invalid_enum"
    INVALID_JSON = "invalid_json"
    DUPLICATE_KEY = "duplicate_key"
    BROKEN_REFERENCE = "broken_reference"
    TARGET_DIRECTORY_MISSING = "target_directory_missing"
    TARGET_WRITE_FAILED = "target_write_failed"
    DATABASE_BUILD_FAILED = "database_build_failed"
    INTEGRITY_CHECK_FAILED = "integrity_check_failed"

class DataPreparationError(Exception):
    code: IssueCode
    file_name: str | None   # source base name only; None for directory, build and replace errors
    line: int | None
    column: str | None
    # str(): "<file_name> line <line>, column <column>: <code>", omitting absent parts.
    # args == (str(self),)

@dataclass(frozen=True)
class SourceFileInfo:
    table_name: str
    file_name: str
    sha256: str
    size_bytes: int
    row_count: int

@dataclass(frozen=True)
class PreparationResult:
    database_path: Path = field(repr=False)   # caller's argument, unchanged; see "Privacy"
    schema_version: int
    prepared_at: datetime
    sources: tuple[SourceFileInfo, ...]       # in TABLE_ORDER
    row_counts: Mapping[str, int]             # read-only mapping
    quality_note_count: int

def prepare_database(
    source_dir: Path,
    database_path: Path,
    *,
    file_prefix: str = SOURCE_FILE_PREFIX,
    clock: Callable[[], datetime] = <UTC now>,
) -> PreparationResult: ...
```

`prepare_database` side effects: reads the six source files; after successful validation, creates one temporary file in the target directory (SQLite creates and deletes its journal next to it); on success the temporary file replaces `database_path`; on a handled failure the temporary file and any journal are deleted. It opens exactly one `sqlite3` connection, to the temporary file, through the standard `sqlite3.connect`; it adds no test hook. It never modifies source files. `clock` must return a timezone-aware datetime; a naive value raises `ValueError`.

Parsing helpers in `emporio_agent.data.parsing` (pure, raise `FieldError(code)` without the value; the importer adds file, line and column):

```python
def parse_id(text: str) -> int: ...
def parse_count(text: str) -> int: ...
def parse_money_cents(text: str) -> int: ...
def parse_percent_basis_points(text: str) -> int: ...
def parse_date(text: str) -> str: ...
def parse_optional_date(text: str) -> str | None: ...
def parse_flag(text: str) -> int: ...
def parse_enum(text: str, allowed: frozenset[str]) -> str: ...
def parse_json_object(text: str) -> str: ...
def parse_text(text: str) -> str: ...
def parse_optional_text(text: str) -> str | None: ...
def cents_to_decimal(cents: int) -> Decimal: ...  # Decimal(cents).scaleb(-2)
def basis_points_to_percent(bp: int) -> Decimal: ...  # Decimal(bp) / Decimal(100)
```

Read-only access in `emporio_agent.data.readonly`:

```python
class DatabaseUnavailableError(Exception):
    reason: Literal["missing", "open_failed", "schema_mismatch"]
    # str() contains the reason and the database base name only.


@dataclass(frozen=True)
class PreparationInfo:
    schema_version: int
    prepared_at: datetime
    sources: tuple[SourceFileInfo, ...]


@contextmanager
def connect_readonly(database_path: Path) -> Iterator[sqlite3.Connection]: ...


def read_preparation_info(connection: sqlite3.Connection) -> PreparationInfo: ...
```

Error message rules:

- An error raised while handling another exception uses `raise ... from None`. This sets `__cause__ = None` and `__suppress_context__ = True`, so the default traceback does not print the original exception (for example, a Python `int()` message that echoes the input). It does not clear `__context__` and does not sanitize the traceback.
- `str()`, `repr()`, `args`, the documented attributes and `traceback.format_exception_only()` of `DataPreparationError` and `DatabaseUnavailableError` contain no cell value and no directory path. The full traceback is outside this guarantee (section "Privacy").

## Compliance Report

- Spec vs contract: every requirement maps to a function, table or error code above.
- Spec vs tests: 59 distinct cases are listed in `docs/plans/02-data-preparation.md` (56 before revision R1, plus D39, D47, D48). None executed.
- Revision R1: privacy guarantee limited to public message, `repr`, `args`, attributes and `format_exception_only`; atomicity guarantees limited to handled failures; construction-failure cases added; category name uniqueness defined; money precedence fixed; reviewer decisions recorded.
- Guarantee limits: no guarantee for process kill, OS crash or power loss; no durability guarantee after `os.replace`; full tracebacks and `__context__` are outside the privacy guarantee; `integrity_check_failed` has no deterministic test.
- Persistence impact: new SQLite schema version 1 under an ignored local path. No migration from a previous database exists.
- API impact: no HTTP endpoint. No FastAPI.
- Slice 01 impact: none. Conversions produce inputs accepted by `quote_price`.
- Status: PROPOSED. Awaiting reviewer approval before tests and code.
