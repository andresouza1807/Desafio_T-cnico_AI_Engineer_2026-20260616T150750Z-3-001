# Slice 02, task T6 validation: read-only connection and preparation metadata

Date: 2026-11-02. Baseline: `feature/data-preparation`, commit `0554cf0`, clean working tree, 678 tests passing before this task (`uv run --frozen pytest -q`: `678 passed`). No commit, push or PR.
Contract: `docs/specs/02-07-readonly-connection.md`, written before the tests. Parent sources: `docs/specs/02-data-preparation.md` (R1: "Read-only access", "Privacy", "Service Contract"), `docs/plans/02-data-preparation.md` (T6, D50–D55), T2–T5 contracts and `prepare_database`.
Platform: Windows 11 Pro 10.0.26200, Python 3.14.3, SQLite 3.50.4.

## Spec Summary

Implemented only T6 in the new module `emporio_agent.data.readonly`:

- `DatabaseUnavailableError(reason, *, database_name=None)`: reason is one of `missing`, `open_failed`, `schema_mismatch`; `database_name` must be a base name; message `"<base name>: <reason>"` or `"<reason>"`; `args == (str(error),)`.
- `PreparationInfo`: frozen dataclass (`schema_version`, `prepared_at` in UTC, `sources` in `TABLE_ORDER`).
- `connect_readonly(database_path)`: regular-file check, `Path.resolve().as_uri() + "?mode=ro"` with `uri=True`, `sqlite3.Row`, `PRAGMA query_only = ON`, header read, `schema_version == "1"` check, yield, close on every exit path.
- `read_preparation_info(connection)`: fixed SQL with `?` parameters on a cursor with `row_factory = None`; returns version, UTC datetime and six `SourceFileInfo`; never closes the caller's connection.

Not implemented: privacy group D60–D72 (T7), CLI, agent. T7 not started.

## Gap Analysis

No conflict with R1. Decisions written in the contract before the tests:

- Error signature: `reason` validated against the literal set; `database_name` validated as base name (`ValueError` otherwise), so a directory path can never be stored.
- `open_failed` versus `schema_mismatch`: `open_failed` when `sqlite3.connect`, the configuration or `SELECT count(*) FROM sqlite_schema` fails (file not readable as SQLite); `schema_mismatch` when the database is readable but `preparation_metadata.schema_version` is missing, not readable or not exactly `"1"`. An empty file is a valid empty SQLite database, so it gives `schema_mismatch`.
- Malformed metadata in `read_preparation_info`: always `schema_mismatch`. Conversion errors (`ValueError`, `OverflowError`) are caught in a helper that returns None, and the error is raised outside the `except` block, so no stored value and no chained exception reach the error. SQLite errors use `from None`.
- `sqlite3.ProgrammingError` (closed connection) in `read_preparation_info` propagates unchanged: caller error, not database state.
- Base name for errors in `read_preparation_info`: `Path(file).name` of the `main` row of `PRAGMA database_list`; None for a memory database or if the PRAGMA fails.
- Closing policy: quiet close (close failures suppressed) on validation failure and on any consumer `BaseException`, followed by a bare `raise` of the same object; plain `close()` after normal exit.
- Connection options: only `uri=True`; other `sqlite3` defaults kept.

## Implementation Plan (executed)

1. Baseline checked: branch `feature/data-preparation`, `0554cf0`, clean tree, 678 passed.
2. Contract `docs/specs/02-07-readonly-connection.md`.
3. Tests `tests/integration/test_readonly_connection.py` (existing `prep_kit` fixture reused unchanged; one local `connect_recorder` fixture).
4. RED 1: collection fails, module absent.
5. Temporary stub (types plus functions raising `NotImplementedError`). RED 2.
6. Stub replaced by the implementation.
7. GREEN; one test setup fix; mutation spot checks; full suite; Ruff check and format check.

## Test Checklist

30 test functions, 68 collected items (66 passed, 2 skipped on Windows).

- [x] Error type: message, `args`, `repr`, `format_exception_only`; without name; unknown reason (3); name with directory or empty (5)
- [x] D50 `test_d50_missing_database_not_created` (parent absent, nothing created, no connect call), `test_d50_missing_file_in_existing_directory`, `test_d50_directory_is_rejected`, `test_empty_path_name_gives_no_database_name`
- [x] Configuration: `test_connect_uses_readonly_uri` (exact URI and `uri=True`), `test_row_factory_and_query_only_observable`, `test_open_and_close_leave_directory_and_bytes_unchanged`
- [x] D51 `test_d51_write_rejected_and_state_unchanged` (INSERT, UPDATE, DELETE, CREATE TABLE: same `OperationalError` object, independent snapshot, SHA-256 and listing unchanged, connection closed), `test_d51_query_only_off_still_rejected_by_mode_ro`
- [x] D52 `test_d52_closed_after_normal_exit`, `test_d52_consumer_exception_preserved_and_connection_closed` (Exception subclass, BaseException subclass, ValueError: same object, same type, no cause or context added)
- [x] D53 `test_d53_preparation_info_equals_result`, `test_d53_prepared_at_from_other_offset_is_utc`, `test_d53_caller_connection_stays_open_and_unchanged` (row factories None, Row, dict), closed connection gives `ProgrammingError`, memory database gives `schema_mismatch` without name, other version on caller connection
- [x] Malformed metadata (11 variants): prepared_at missing, not ISO (sentinel), naive, overflow on UTC conversion, not text; source row missing, table missing, column missing, wrong type (sentinel), unknown row (sentinel), repeated row. All `schema_mismatch`, sanitized, no chained cause, file unchanged, connection still open
- [x] D54 `test_d54_schema_mismatch` (no metadata table, version missing, `"2"`, sentinel, integer `1`, value column missing: file and listing unchanged, connection closed), prepared database with other version, `from None` on missing table, empty file; `test_d54_non_sqlite_file_is_open_failed` (sentinel and SQLite message absent, `from None`, connection closed)
- [x] D55 `test_d55_path_with_space_and_non_ascii` (`dados ação/banco ç.sqlite3`), `test_d55_reserved_uri_characters` and `test_d55_missing_path_with_reserved_characters` for `#`, `%41`, `&` and `=`, `;`, `%23`; `?` skipped on Windows

## Test Strategy

- Real SQLite files in `tmp_path`, built with `prepare_database` from synthetic CSV (`prep_kit.write_sources`). No private data, no OpenAI API.
- Invalid variants created with a real writable connection before the read-only open.
- State checked with independent read-only connections, SHA-256 and directory listings.
- `connect_recorder` wraps the real `sqlite3.connect` and keeps the real connection; it simulates nothing.

## Evidence

RED 1 (module absent):

```
uv run --frozen pytest tests/integration/test_readonly_connection.py -q
E   ModuleNotFoundError: No module named 'emporio_agent.data.readonly'
1 error in 0.16s
```

RED 2 (stub):

```
uv run --frozen pytest tests/integration/test_readonly_connection.py -q
66 failed, 2 skipped in 2.08s
```

First GREEN run: `1 failed, 65 passed, 2 skipped`. Cause: test setup. `test_connect_uses_readonly_uri` counted the build connection of `prepare_database`. Fixed in the test (`connect_recorder.clear()` after preparation); no production change.

Mutation spot checks (each applied alone, then reverted; file restored byte-equal):

| Mutation | Result |
| --- | --- |
| No `?mode=ro` | 2 failed |
| No `PRAGMA query_only = ON` | 1 failed |
| No close on consumer exception | 7 failed |
| No close on validation failure | 9 failed |
| No header read (`sqlite_schema`) | 1 failed |
| No UTC conversion of `prepared_at` | 1 failed |
| `ProgrammingError` recategorized | 1 failed |

Final commands:

| Command | Exit | Output |
| --- | --- | --- |
| `uv run --frozen pytest tests/integration/test_readonly_connection.py -q` | 0 | `66 passed, 2 skipped` |
| `uv run --frozen pytest -q` | 0 | `744 passed, 2 skipped` (678 earlier + 66 new) |
| `uv run --frozen ruff check .` | 0 | `All checks passed!` |
| `uv run --frozen ruff format --check .` | 0 | `35 files already formatted` |

Skips: 2, both `Windows does not allow '?' in file names` (`q?mode=rwc` in the two D55 reserved-character tests).

## Files

Created: `src/emporio_agent/data/readonly.py`, `tests/integration/test_readonly_connection.py`, `docs/specs/02-07-readonly-connection.md`, `docs/validation/02-07-readonly-connection.md`. No other file changed.

## Limits

- `?` in a path not exercised on this machine (Windows forbids it).
- Not tested: failure of `Connection.close()`, `OSError` from `Path.resolve`, a file deleted between the regular-file check and the open, transient SQLite I/O errors.
- A transient SQLite read error inside `read_preparation_info` is reported as `schema_mismatch` (R1 defines no other reason).
- `read_preparation_info` checks types, not contents, of `source_files`; no full integrity validation.
- Privacy assertions cover `str`, `repr`, `args`, attributes and `format_exception_only`; not full tracebacks or `__context__` (R1). T7 covers the privacy group.
- Results come from small synthetic fixtures on one Windows machine.
