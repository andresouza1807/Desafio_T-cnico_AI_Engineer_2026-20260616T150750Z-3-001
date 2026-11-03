"""Read-only access to a prepared SQLite database (task T6).

connect_readonly opens an existing database with mode=ro and PRAGMA query_only = ON
after checking the preparation schema version; read_preparation_info returns the
persisted preparation metadata. Errors carry only a reason and the database base
name. See docs/specs/02-07-readonly-connection.md.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal, get_args

from emporio_agent.data.preparation import SourceFileInfo
from emporio_agent.data.schema import SCHEMA_VERSION, TABLE_ORDER

type UnavailableReason = Literal["missing", "open_failed", "schema_mismatch"]

_REASONS = frozenset(get_args(UnavailableReason.__value__))
_SCHEMA_VERSION_TEXT = str(SCHEMA_VERSION)


class DatabaseUnavailableError(Exception):
    """The database cannot be used read-only.

    Carries the reason and the database base name only; never a directory path, an
    original error message or a value read from the database. args == (str(self),).
    """

    reason: UnavailableReason
    database_name: str | None

    def __init__(self, reason: UnavailableReason, *, database_name: str | None = None) -> None:
        if reason not in _REASONS:
            raise ValueError("unknown reason")
        if database_name is not None and not _is_base_name(database_name):
            raise ValueError("database_name must be a base name")
        self.reason = reason
        self.database_name = database_name
        super().__init__(f"{database_name}: {reason}" if database_name else reason)


@dataclass(frozen=True)
class PreparationInfo:
    schema_version: int
    prepared_at: datetime
    sources: tuple[SourceFileInfo, ...]


def _is_base_name(name: str) -> bool:
    return bool(name) and PurePosixPath(name).name == name and PureWindowsPath(name).name == name


def _close_quietly(connection: sqlite3.Connection) -> None:
    # Cleanup never replaces the primary error.
    try:
        connection.close()
    except sqlite3.Error:
        pass


def _check_database(connection: sqlite3.Connection, name: str | None) -> None:
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("SELECT count(*) FROM sqlite_schema").fetchone()
    except sqlite3.Error:
        raise DatabaseUnavailableError("open_failed", database_name=name) from None
    try:
        row = connection.execute(
            "SELECT value FROM preparation_metadata WHERE key = ?", ("schema_version",)
        ).fetchone()
    except sqlite3.Error:
        raise DatabaseUnavailableError("schema_mismatch", database_name=name) from None
    if row is None or row[0] != _SCHEMA_VERSION_TEXT:
        raise DatabaseUnavailableError("schema_mismatch", database_name=name)


@contextmanager
def connect_readonly(database_path: Path) -> Iterator[sqlite3.Connection]:
    """Open an existing prepared database read-only and close it on exit.

    Consumer exceptions, including sqlite3.OperationalError on a write, propagate
    unchanged. The connection is closed on every exit path.
    """
    name = database_path.name or None
    if not database_path.is_file():
        raise DatabaseUnavailableError("missing", database_name=name)
    try:
        uri = database_path.resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
    except OSError, sqlite3.Error:
        raise DatabaseUnavailableError("open_failed", database_name=name) from None
    try:
        _check_database(connection, name)
    except BaseException:
        _close_quietly(connection)
        raise
    try:
        yield connection
    except BaseException:
        _close_quietly(connection)
        raise
    connection.close()


def _database_name(connection: sqlite3.Connection) -> str | None:
    try:
        cursor = connection.cursor()
        cursor.row_factory = None
        rows = cursor.execute("PRAGMA database_list").fetchall()
    except sqlite3.Error:
        return None
    for _sequence, schema, file in rows:
        if schema == "main" and isinstance(file, str) and file:
            return Path(file).name or None
    return None


def _mismatch(connection: sqlite3.Connection) -> DatabaseUnavailableError:
    return DatabaseUnavailableError("schema_mismatch", database_name=_database_name(connection))


def _parse_prepared_at(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() is None:
            return None
        return parsed.astimezone(UTC)
    except ValueError, OverflowError:
        return None


def _parse_sources(rows: list[tuple[object, ...]]) -> tuple[SourceFileInfo, ...] | None:
    by_table: dict[str, SourceFileInfo] = {}
    for table_name, file_name, sha256, size_bytes, row_count in rows:
        if not isinstance(table_name, str) or table_name not in TABLE_ORDER:
            return None
        if table_name in by_table:
            return None
        if not (isinstance(file_name, str) and isinstance(sha256, str)):
            return None
        if type(size_bytes) is not int or type(row_count) is not int:
            return None
        by_table[table_name] = SourceFileInfo(
            table_name=table_name,
            file_name=file_name,
            sha256=sha256,
            size_bytes=size_bytes,
            row_count=row_count,
        )
    if len(by_table) != len(TABLE_ORDER):
        return None
    return tuple(by_table[table_name] for table_name in TABLE_ORDER)


def read_preparation_info(connection: sqlite3.Connection) -> PreparationInfo:
    """Return schema version, preparation time (UTC) and the six source records.

    Does not close the caller's connection and does not change its row_factory.
    Missing or malformed metadata raises DatabaseUnavailableError("schema_mismatch").
    """
    try:
        cursor = connection.cursor()
        cursor.row_factory = None
        metadata = dict(
            cursor.execute(
                "SELECT key, value FROM preparation_metadata WHERE key IN (?, ?)",
                ("schema_version", "prepared_at"),
            ).fetchall()
        )
        rows = cursor.execute(
            "SELECT table_name, file_name, sha256, size_bytes, row_count FROM source_files"
        ).fetchall()
    except sqlite3.ProgrammingError:
        raise
    except sqlite3.Error:
        raise _mismatch(connection) from None
    if metadata.get("schema_version") != _SCHEMA_VERSION_TEXT:
        raise _mismatch(connection)
    prepared_at = _parse_prepared_at(metadata.get("prepared_at"))
    sources = _parse_sources(rows)
    if prepared_at is None or sources is None:
        raise _mismatch(connection)
    return PreparationInfo(schema_version=SCHEMA_VERSION, prepared_at=prepared_at, sources=sources)
