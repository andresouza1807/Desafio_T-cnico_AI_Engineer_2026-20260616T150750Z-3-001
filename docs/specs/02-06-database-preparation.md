# Slice 02, task T5 contract: database construction and replacement

Status: contract for task T5, written before tests, 2026-11-02.
Source of truth: `docs/specs/02-data-preparation.md` (revision R1 with amendment 02-04-R1), `docs/plans/02-data-preparation.md` (T5, D01–D09, D40–D48), `docs/specs/02-03-data-schema.md` (T2), `docs/specs/02-04-csv-reading.md` (T3), `docs/specs/02-05-data-relations.md` (T4).
This document refines the build-and-replace step of `emporio_agent.data.preparation`. It adds no business rule. Where it differs from R1, R1 wins. One addition requested for T5 is not in R1: the target must not be one of the source files (section "Precedence"). It does not contradict R1.

## Scope

In scope:

- `src/emporio_agent/data/preparation.py`: `PreparationResult` and `prepare_database`, plus private helpers for quality notes, the build connection, cleanup and replace.
- `tests/integration/test_data_preparation.py`, `tests/integration/test_data_atomicity.py`, and `tests/integration/conftest.py` with new, specifically named fixtures only.

Out of scope: `connect_readonly` and `read_preparation_info` (T6), privacy cases D60–D72 as a group (T7), CLI, agent, pricing changes. No change to `read_table`, `read_validated_tables`, their types, errors, parsers or schema.

## Public contract

```python
# emporio_agent.data.preparation (additions)
@dataclass(frozen=True)
class PreparationResult:
    database_path: Path = field(repr=False)  # the caller's argument, same object, not resolved
    schema_version: int  # SCHEMA_VERSION (1)
    prepared_at: datetime  # clock value converted to UTC (tzinfo is datetime.UTC)
    sources: tuple[SourceFileInfo, ...]  # in TABLE_ORDER, the objects returned by the reader
    row_counts: Mapping[str, int]  # read-only (types.MappingProxyType), keys in TABLE_ORDER
    quality_note_count: int  # number of rows in data_quality_notes


def prepare_database(
    source_dir: Path,
    database_path: Path,
    *,
    file_prefix: str = SOURCE_FILE_PREFIX,
    clock: Callable[[], datetime] = _utc_now,  # returns datetime.now(UTC)
) -> PreparationResult: ...
```

- `row_counts[table]` equals `sources[i].row_count` and the number of rows in that table of the written database.
- `repr(result)` shows schema version, `prepared_at`, the six `SourceFileInfo` values (table name, base file name, hash, size, row count), `row_counts` and `quality_note_count`. It shows no cell value and no directory path. `database_path` is omitted from `repr` (R1 "Privacy").
- `PreparationResult` is not hashable, because a mapping proxy is not hashable. Equality is the dataclass default.

## Precedence

`prepare_database` runs these steps in this order. Each step that can fail runs before any file is created.

1. Target directory. If `database_path.parent` is not an existing directory, raise `DataPreparationError(TARGET_DIRECTORY_MISSING)` with `file_name`, `line` and `column` None. No directory is created and no source file is read.
2. Clock. `clock()` is called exactly once. A value that is not a `datetime`, or a `datetime` whose `utcoffset()` is None (naive), raises `ValueError` with a fixed message. An exception raised by the clock itself, or by the UTC conversion, propagates unchanged. `prepared_at = value.astimezone(UTC)`. No source file is read yet.
3. Sources. `read_validated_tables(source_dir, file_prefix=file_prefix)`. Every error propagates unchanged, in the T3/T4 order: `ValueError` for a prefix with a path component, then `source_dir_missing`, `file_missing`, structure, field, `duplicate_key` and `broken_reference` errors. So D10–D19, D35–D39 keep their codes and locations through `prepare_database`.
4. Target is a source file. If `database_path` designates one of the six source files just read, raise `ValueError("database_path must not be a source file")`. The message contains no path. The check compares `os.path.normcase(os.path.realpath(...))` of the target and of each source path, and, when the target exists, `os.path.samestat` of both stats (ignored when either stat fails or reports inode 0). This runs after step 3 because only then the six file names are validated and the files known to exist. The source files are never opened for writing.
5. Importer version. `importlib.metadata.version("emporio-agent")`. If the distribution is not installed, `importlib.metadata.PackageNotFoundError` propagates unchanged. No file has been created.
6. In memory: metadata rows, `source_files` rows, quality notes and the `PreparationResult` object are built from the readings. No file is read again.
7. Build (section "Construction"). Failures raise `database_build_failed` or `integrity_check_failed`.
8. Replace (section "Replace"). Failure raises `target_write_failed`.
9. Return the result built in step 6. Nothing runs between a successful `os.replace` and the return, so no step after publication can turn a success into a reported error.

Configuration errors (`ValueError`, `target_directory_missing`) and source errors therefore never create a temporary file, never open a SQLite connection and never touch the target.

## Construction

1. `tempfile.mkstemp(prefix=f".{database_path.name}.", suffix=".tmp", dir=database_path.parent)`. The returned descriptor is closed at once with `os.close`.
2. Exactly one connection: `sqlite3.connect(temporary, autocommit=True)`, the standard function, called through the `sqlite3` module attribute. No test hook exists.
3. Autocommit mode: `autocommit=True` (PEP 249 manual-commit disabled; the `sqlite3` module never opens or closes a transaction implicitly). Therefore `Connection.commit()` and `Connection.rollback()` are not used; transaction control is explicit SQL.
4. Before the transaction: `PRAGMA journal_mode = DELETE`, `PRAGMA synchronous = FULL`, `PRAGMA foreign_keys = ON`. (`journal_mode` cannot change and `foreign_keys` is a no-op inside a transaction.)
5. `BEGIN`. Then each statement of `DDL_STATEMENTS` with `Connection.execute`, one by one; `executescript` is never used.
6. Domain rows for each table in `TABLE_ORDER`: `INSERT INTO <table> (<columns>) VALUES (?, …)` with `executemany`. Table and column names come only from `schema.TABLES`; values are bound parameters, taken unchanged from the readings. Money, percentages and order totals are never recalculated.
7. Metadata rows in the same transaction: `preparation_metadata` (`schema_version` = `"1"`, `prepared_at` = `prepared_at.isoformat()` in UTC, `importer_version`), six `source_files` rows from the `SourceFileInfo` values, and the quality notes.
8. Checks in the same transaction, real SQLite pragmas: `PRAGMA foreign_key_check` must return no row and `PRAGMA integrity_check` must return exactly one row `ok`. Otherwise `DataPreparationError(INTEGRITY_CHECK_FAILED)`.
9. `COMMIT`, then `Connection.close()` explicitly. A context manager is not used for the connection.

## Data-quality notes

For each product record, in source line order:

- `status_stock_conflict` when `status` is `discontinued` or `coming_soon` and `stock_quantity > 0`;
- `catalog_text_conflict` when `product_id` is in `KNOWN_CATALOG_TEXT_CONFLICTS`.

A product matching both rules gets two rows. IDs of the constant absent from the products file give no row and no error. Rows hold only `entity = 'product'`, the product ID and the code: no source text is copied and no source value is changed.

## Replace

`os.replace(temporary, database_path)` after the connection is closed. On `OSError`: the temporary file and its journal are removed, the target is not touched, `DataPreparationError(TARGET_WRITE_FAILED)` is raised.

## Errors and cleanup

- Steps 1–2 of "Construction", any `sqlite3.Error` or `OSError` while building, checking, committing or closing: `DataPreparationError(DATABASE_BUILD_FAILED)`.
- All three build and replace codes have `file_name`, `line` and `column` None, are raised with `from None` (no `__cause__`, `__suppress_context__` True), and carry no path and no original message in `str`, `repr`, `args`, attributes or `format_exception_only`.
- On any failure after the temporary file exists (handled errors, `integrity_check_failed`, and best effort for `KeyboardInterrupt` and `SystemExit`): if a transaction is open, `ROLLBACK` is executed; the connection is closed; then `temporary` and `temporary + "-journal"` are removed if present. Rollback, close and removal failures during this cleanup (`sqlite3.Error`, `OSError`) are suppressed, so they never replace the primary error. Only files of this run are removed; the target and other files in the directory are never removed or changed.
- `KeyboardInterrupt` and `SystemExit` propagate unchanged, not categorized.
- If `mkstemp` itself fails, there is nothing to remove.

## Limits (unchanged from R1)

- Guarantees cover handled failures only. A failed cleanup deletion leaves a file behind (known limit). Process kill, interpreter crash, OS crash and power loss run no cleanup; no guarantee is given about which target version survives.
- No directory sync after `os.replace`; durability of the rename is not guaranteed.
- No automatic recovery, no scan for orphan temporary files, no lock against concurrent runs (last successful replace wins).
- `integrity_check_failed` is tested only for a real `foreign_key_check` finding, on the build helper called directly (revision 02-06-R1): inconsistent synthetic readings and a real authorizer that ignores only `PRAGMA foreign_keys = ON`. No test reaches it through `prepare_database` (validation rejects such readings first), and no test covers an `integrity_check` failure, physical corruption or a crash.
- `os.replace` is not documented as atomic on Windows under all conditions.

## Test mechanisms

- Construction failure (D47, D48): `monkeypatch` replaces `sqlite3.connect` by a wrapper that calls the real function with the same arguments, records the database argument and whether it existed, and, for a `.tmp` file in the test's target directory, installs a real authorizer with `Connection.set_authorizer`. The authorizer denies `SQLITE_INSERT` on `order_items`, allows everything else, and records `SQLITE_PRAGMA` and `SQLITE_TRANSACTION` actions. SQLite itself rejects the first `order_items` insert. The patch is undone before independent verification connections are opened.
- No connection and no temporary before validation (D18, D19, D39, configuration errors): recorders around `sqlite3.connect` and `tempfile.mkstemp` that call nothing and fail the test if called.
- Replace failure (D44): a real independent `sqlite3` connection holds the target open during the rebuild. Where the platform allows the replace anyway, the test is skipped with that reason and the case is not claimed.
- State is verified with independent connections, SHA-256 of target bytes and directory listings. No mock proves a transaction, a relation or persistence.

## Traceability

| Plan case | T5 coverage |
| --- | --- |
| D01 | Six tables, counts, `foreign_key_check` empty, joins, `row_counts` |
| D02 | Two runs, equal domain rows and metadata except `prepared_at`, no duplicates |
| D03 | Historical total kept exactly, no note |
| D04 | Status and stock kept; `status_stock_conflict` for discontinued and coming_soon with positive stock only |
| D05 | `catalog_text_conflict` for curated ID, text byte-equal; absent curated IDs give no note; double note |
| D06 | Optional empty fields stored as NULL |
| D07 | Quoted comma and non-ASCII text preserved |
| D08 | `source_files` equals hashes of file bytes; base names; one-byte edit changes only that hash |
| D09 | Stored cents and basis points accepted by `quote_price` |
| D18, D19, D39 | Through `prepare_database`: same errors, no connect, no temporary, target absent |
| D40–D42 | Validation failure: no target created; previous target bytes and content unchanged; no leftovers |
| D43 | `target_directory_missing`, directory not created |
| D44 | `target_write_failed` with real open handle, or recorded skip |
| D45 | Rebuild reflects new sources |
| D46 | Fixed clock, offset converted to UTC, naive clock rejected before any file |
| D47, D48 | `database_build_failed` with real authorizer; rollback and cleanup observed; target unchanged or absent |
