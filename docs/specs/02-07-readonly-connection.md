# Slice 02, task T6 contract: read-only connection and preparation metadata

Status: contract for task T6, written before tests, 2026-11-02.
Source of truth: `docs/specs/02-data-preparation.md` (revision R1, sections "Read-only access", "Privacy", "Service Contract"), `docs/plans/02-data-preparation.md` (T6, D50–D55), T2–T5 contracts (`docs/specs/02-03-data-schema.md` to `docs/specs/02-06-database-preparation.md`) and the approved `prepare_database`.
This document refines R1 for the module `emporio_agent.data.readonly`. It adds no business rule, no migration, no repair, no full integrity validation and no new persisted format. Where it differs from R1, R1 wins. No conflict with R1 was found.

## Scope

In scope:

- `src/emporio_agent/data/readonly.py`: `DatabaseUnavailableError`, `PreparationInfo`, `connect_readonly`, `read_preparation_info`, private helpers.
- `tests/integration/test_readonly_connection.py`. It reuses the existing `prep_*` fixtures of `tests/integration/conftest.py` without changing them.

Out of scope: privacy group D60–D72 (T7), catalog search, order authorization, CLI, agent. No change to `preparation.py`, `errors.py`, `schema.py`, parsers, pricing, `__init__.py`, earlier tests, configuration or dependencies.

## Public contract

```python
# emporio_agent.data.readonly
type UnavailableReason = Literal["missing", "open_failed", "schema_mismatch"]


class DatabaseUnavailableError(Exception):
    reason: UnavailableReason
    database_name: str | None  # base name of the database file, or None

    def __init__(self, reason: UnavailableReason, *, database_name: str | None = None) -> None: ...


@dataclass(frozen=True)
class PreparationInfo:
    schema_version: int
    prepared_at: datetime  # tzinfo is datetime.UTC
    sources: tuple[SourceFileInfo, ...]  # six values, in TABLE_ORDER


@contextmanager
def connect_readonly(database_path: Path) -> Iterator[sqlite3.Connection]: ...


def read_preparation_info(connection: sqlite3.Connection) -> PreparationInfo: ...
```

`SourceFileInfo` is the existing type of `emporio_agent.data.preparation`.

## DatabaseUnavailableError

- `reason` must be one of the three literals; any other value raises `ValueError("unknown reason")`.
- `database_name` must be None or a base name: a non-empty string with no `/` and no `\` component (same rule as the T3 base-name check, POSIX and Windows). Otherwise `ValueError("database_name must be a base name")`. The constructor never stores a directory path.
- Message: `f"{database_name}: {reason}"`, or `reason` when `database_name` is None. Same layout as `DataPreparationError`.
- `args == (str(error),)`. `repr` is the default `Exception` repr, so it shows the type and the message only.
- `str`, `repr`, `args`, `reason`, `database_name` and `traceback.format_exception_only(error)` hold only the reason and the base name. They never hold a directory path, an original exception message or a value read from the database.
- Origin of `database_name`:
  - in `connect_readonly`: `database_path.name`, or None when it is empty (for example `Path(".")`);
  - in `read_preparation_info`: the base name (`Path(file).name`) of the `file` column of the `main` row of `PRAGMA database_list` on the caller's connection, or None when that file is empty (memory or temporary database) or when the PRAGMA itself fails.

## connect_readonly

Steps, in order:

1. `database_path.is_file()`. If False (absent, directory, broken link, or a stat that fails), raise `DatabaseUnavailableError("missing")`. Nothing is created: no file, no directory, no database, no journal.
2. `uri = database_path.resolve().as_uri() + "?mode=ro"`; `connection = sqlite3.connect(uri, uri=True)`, the standard function through the `sqlite3` module attribute, with default `sqlite3` options otherwise. `as_uri` percent-encodes spaces, non-ASCII characters and reserved characters such as `#`, `?`, `%`, `&` and `=`, so they are part of the path and never a URI fragment or query. An `OSError` or `sqlite3.Error` here raises `open_failed` with `from None`. A file deleted between step 1 and step 2 therefore gives `open_failed`; `mode=ro` never creates it.
3. Configuration: `connection.row_factory = sqlite3.Row`; `PRAGMA query_only = ON`; then `SELECT count(*) FROM sqlite_schema`, which forces SQLite to read the file header. A `sqlite3.Error` in this step raises `open_failed` with `from None`. A file that is not SQLite is reported here (`file is not a database`).
4. Schema check: `SELECT value FROM preparation_metadata WHERE key = ?` with `("schema_version",)`. A `sqlite3.Error` here (missing table, missing column) raises `schema_mismatch` with `from None`. No row, or a value that is not exactly the text `"1"` (`str(SCHEMA_VERSION)`; the integer `1` does not match), raises `schema_mismatch` outside any `except` block, so `__context__` is None.
5. `yield connection`.

Distinction: `open_failed` means SQLite cannot open or read the file as a database (step 2 or 3). `schema_mismatch` means a readable SQLite database without the expected `preparation_metadata` version (step 4). An empty file (0 bytes) is a valid empty SQLite database for SQLite, so it gives `schema_mismatch`.

Closing policy:

- If step 3 or 4 fails, or any `BaseException` (for example `KeyboardInterrupt`) occurs there, the connection is closed before the error propagates. Close failures (`sqlite3.Error`) during this cleanup are suppressed, so they never replace the primary error.
- After a normal exit of the `with` block the connection is closed with `Connection.close()`. A close failure there propagates.
- If the consumer block raises any `BaseException`, the connection is closed quietly (close failures suppressed) and the same exception object is re-raised with a bare `raise`. It is never wrapped, translated or chained. This covers `sqlite3.OperationalError` from a write and any other consumer exception.
- The connection is not used as `sqlite3.Connection` context manager (that only commits or rolls back).
- After exit, any `execute` on the yielded connection raises `sqlite3.ProgrammingError`.

Write protection: two layers. `mode=ro` makes SQLite open the file read-only; `PRAGMA query_only = ON` rejects writes before they reach the file. If the consumer sets `PRAGMA query_only = OFF`, writes still fail with `sqlite3.OperationalError` because of `mode=ro`. The library never commits and never changes the database bytes.

## read_preparation_info

- Uses a new cursor of the caller's connection with `cursor.row_factory = None`, so rows are tuples whatever the connection's `row_factory`. The connection's `row_factory` is not changed. The connection is never closed and no transaction statement is executed.
- Queries (fixed SQL, values as `?` parameters, no identifier from input):
  - `SELECT key, value FROM preparation_metadata WHERE key IN (?, ?)` with `("schema_version", "prepared_at")`;
  - `SELECT table_name, file_name, sha256, size_bytes, row_count FROM source_files`.
- Result:
  - `schema_version`: the stored value must be exactly `"1"`; the result holds the int `SCHEMA_VERSION`.
  - `prepared_at`: the stored value must be a `str` accepted by `datetime.fromisoformat`, timezone-aware; it is converted with `astimezone(UTC)`. Equal to `PreparationResult.prepared_at` of the run that wrote the database.
  - `sources`: exactly one row per name of `TABLE_ORDER`, no other row; `table_name`, `file_name` and `sha256` of type `str`, `size_bytes` and `row_count` of type `int`; returned as `SourceFileInfo` in `TABLE_ORDER`. Equal to `PreparationResult.sources`. Contents are checked for type only; the persisted `STRICT` and `CHECK` constraints of T2 cover their form. This is not a full integrity validation.
- Errors:
  - `sqlite3.ProgrammingError` (for example a closed connection) propagates unchanged: it is a caller error, not a database state.
  - Any other `sqlite3.Error` during the queries raises `schema_mismatch` with `from None`.
  - Missing or malformed metadata (missing key, version other than `"1"`, `prepared_at` not a string, not ISO, naive, or out of range on UTC conversion, missing, repeated or unknown `source_files` rows, wrong types) raises `schema_mismatch`. Conversion errors (`ValueError`, `OverflowError`) are caught inside a helper that returns no value; the error is then raised outside any `except` block, so `__context__` is None and no stored value appears in any message.
- Limit: a transient read error of SQLite (for example disk I/O) inside `read_preparation_info` is reported as `schema_mismatch`, because R1 defines no other reason.

## Privacy

- No logging, no network, no environment variable reads, no global state, no write to the database.
- Exceptions propagated unchanged (outside the R1 guarantee): `sqlite3.OperationalError` from a consumer write, other consumer exceptions, `sqlite3.ProgrammingError` from `read_preparation_info`, `KeyboardInterrupt`, `SystemExit`.
- The full traceback and `__context__` are outside the guarantee (R1 "Privacy").

## Test mechanisms

- Real SQLite files in pytest `tmp_path`, built with `prepare_database` from synthetic CSV written by `prep_kit.write_sources`.
- Invalid variants (no metadata, missing version, other version, malformed metadata, malformed `source_files`) are produced with a real independent writable `sqlite3` connection on a copy before opening read-only.
- State checks use independent connections, SHA-256 of file bytes and directory listings.
- A wrapper around `sqlite3.connect` (`monkeypatch`) calls the real function with the same arguments and records the real connection and its arguments, to assert the URI and that the connection is closed after a failure. It never simulates a lock, a close, a query or persistence.
- Not tested: a failure of `Connection.close()` (no real way to provoke it), `OSError` from `Path.resolve`, a file deleted between steps 1 and 2, transient SQLite I/O errors.

## Traceability

| Plan case | T6 coverage |
| --- | --- |
| D50 | Absent path and directory: `missing`; nothing created; parent directory not created |
| D51 | `INSERT`, `UPDATE`, `DELETE`, `CREATE TABLE` raise `sqlite3.OperationalError` (same object); state equal by independent query and SHA-256; `query_only = OFF` still rejected |
| D52 | `ProgrammingError` after normal exit, after consumer exception and after validation failure |
| D53 | `read_preparation_info` equals `PreparationResult`; connection stays open |
| D54 | No metadata table, missing version, other version, integer version, empty file: `schema_mismatch`; file unchanged; connection closed. Non-SQLite file: `open_failed` |
| D55 | Directory `dados ação`, and names with `#`, `%`, `&`, `=` (and `?` where the OS allows it) |
