"""Reading, validation and SQLite preparation of the store CSV files.

This module holds the single-table reader of task T3 (structure and single fields,
docs/specs/02-04-csv-reading.md), the in-memory validation of keys and references
across the six tables of task T4 (docs/specs/02-05-data-relations.md) and the
build-and-replace of the database of task T5 (docs/specs/02-06-database-preparation.md).
"""

import csv
import hashlib
import importlib.metadata
import io
import os
import sqlite3
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType

from emporio_agent.data.errors import DataPreparationError, FieldError, IssueCode
from emporio_agent.data.parsing import (
    parse_count,
    parse_date,
    parse_enum,
    parse_flag,
    parse_id,
    parse_json_object,
    parse_money_cents,
    parse_optional_date,
    parse_optional_text,
    parse_percent_basis_points,
    parse_text,
)
from emporio_agent.data.schema import (
    CATALOG_TEXT_CONFLICT,
    DDL_STATEMENTS,
    KNOWN_CATALOG_TEXT_CONFLICTS,
    QUALITY_ENTITY_PRODUCT,
    SCHEMA_VERSION,
    SOURCE_FILE_PREFIX,
    STATUS_STOCK_CONFLICT,
    TABLES,
    ColumnSpec,
    FieldKind,
    TableSpec,
)

type FieldValue = int | str | None


@dataclass(frozen=True)
class SourceFileInfo:
    table_name: str
    file_name: str
    sha256: str
    size_bytes: int
    row_count: int


@dataclass(frozen=True)
class SourceRecord:
    line: int
    values: tuple[FieldValue, ...] = field(repr=False)


@dataclass(frozen=True)
class TableReading:
    source: SourceFileInfo
    records: tuple[SourceRecord, ...] = field(repr=False)


_PARSERS: dict[FieldKind, Callable[[str], FieldValue]] = {
    FieldKind.ID: parse_id,
    FieldKind.COUNT: parse_count,
    FieldKind.QUANTITY: parse_id,
    FieldKind.MONEY: parse_money_cents,
    FieldKind.PERCENT: parse_percent_basis_points,
    FieldKind.DATE: parse_date,
    FieldKind.OPTIONAL_DATE: parse_optional_date,
    FieldKind.FLAG: parse_flag,
    FieldKind.JSON_OBJECT: parse_json_object,
    FieldKind.TEXT: parse_text,
    FieldKind.OPTIONAL_TEXT: parse_optional_text,
}


def _parse_field(column: ColumnSpec, text: str) -> FieldValue:
    if column.kind is FieldKind.ENUM:
        return parse_enum(text, column.allowed or frozenset())
    return _PARSERS[column.kind](text)


def _is_base_name(name: str) -> bool:
    return PurePosixPath(name).name == name and PureWindowsPath(name).name == name


def _read_bytes(source_dir: Path, file_name: str) -> bytes:
    if not source_dir.is_dir():
        raise DataPreparationError(IssueCode.SOURCE_DIR_MISSING)
    try:
        return (source_dir / file_name).read_bytes()
    except FileNotFoundError:
        raise DataPreparationError(IssueCode.FILE_MISSING, file_name=file_name) from None
    except OSError:
        raise DataPreparationError(IssueCode.FILE_UNREADABLE, file_name=file_name) from None


def _decode(data: bytes, file_name: str) -> str:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise DataPreparationError(IssueCode.ENCODING_INVALID, file_name=file_name) from None
    if "\x00" in text:
        raise DataPreparationError(IssueCode.CSV_MALFORMED, file_name=file_name)
    return text


def _header_positions(header: list[str], table: TableSpec, file_name: str, line: int) -> list[int]:
    """Return, for each schema column, its position in the header."""
    known = {column.source_name for column in table.columns}

    def fail(code: IssueCode, column: str | None) -> DataPreparationError:
        # Header text that is not a schema column name is never echoed.
        shown = column if column in known else None
        return DataPreparationError(code, file_name=file_name, line=line, column=shown)

    seen: set[str] = set()
    for name in header:
        if name in seen:
            raise fail(IssueCode.HEADER_DUPLICATE, name)
        seen.add(name)
    for column in table.columns:
        if column.source_name not in seen:
            raise fail(IssueCode.HEADER_MISMATCH, column.source_name)
    if seen - known:
        raise fail(IssueCode.HEADER_MISMATCH, None)
    return [header.index(column.source_name) for column in table.columns]


def _parse_records(text: str, table: TableSpec, file_name: str) -> tuple[SourceRecord, ...]:
    reader = csv.reader(
        io.StringIO(text, newline=""),
        delimiter=",",
        quotechar='"',
        doublequote=True,
        skipinitialspace=False,
        strict=True,
    )
    try:
        header = next(reader, None)
        if header is None:
            raise DataPreparationError(IssueCode.HEADER_MISSING, file_name=file_name)
        if not header:
            raise DataPreparationError(
                IssueCode.HEADER_MISSING, file_name=file_name, line=reader.line_num
            )
        positions = _header_positions(header, table, file_name, reader.line_num)
        records = []
        for row in reader:
            line = reader.line_num
            if len(row) != len(header):
                raise DataPreparationError(IssueCode.FIELD_COUNT, file_name=file_name, line=line)
            values = []
            for column, position in zip(table.columns, positions, strict=True):
                try:
                    values.append(_parse_field(column, row[position]))
                except FieldError as error:
                    raise DataPreparationError(
                        error.code, file_name=file_name, line=line, column=column.source_name
                    ) from None
            records.append(SourceRecord(line=line, values=tuple(values)))
    except csv.Error:
        raise DataPreparationError(
            IssueCode.CSV_MALFORMED, file_name=file_name, line=reader.line_num
        ) from None
    return tuple(records)


def read_table(
    source_dir: Path, table: TableSpec, *, file_prefix: str = SOURCE_FILE_PREFIX
) -> TableReading:
    """Read one source CSV once, hash its bytes and parse the same bytes.

    Validates encoding, CSV structure, the exact header set, field counts and each
    field grammar. Returns typed values in table.columns order with the physical end
    line of each record. Repeated keys and unknown references are returned unchanged.
    """
    file_name = f"{file_prefix}{table.name}.csv"
    if not _is_base_name(file_name):
        raise ValueError("file_prefix must not contain a path component")
    data = _read_bytes(source_dir, file_name)
    records = _parse_records(_decode(data, file_name), table, file_name)
    source = SourceFileInfo(
        table_name=table.name,
        file_name=file_name,
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        row_count=len(records),
    )
    return TableReading(source=source, records=records)


def _position(table: TableSpec, column_name: str) -> int:
    for index, column in enumerate(table.columns):
        if column.column_name == column_name:
            return index
    raise LookupError(f"unknown column in schema metadata for {table.name}")


def _check_unique_keys(table: TableSpec, reading: TableReading) -> None:
    """Raise duplicate_key at the first record that repeats a key value.

    Keys are checked per record: primary key first, then each unique column. The
    reported column is the last key column. Values are compared exactly.
    """
    keys = [table.primary_key, *((name,) for name in table.unique)]
    checks = [
        (
            [_position(table, name) for name in key],
            table.columns[_position(table, key[-1])].source_name,
            set(),
        )
        for key in keys
    ]
    for record in reading.records:
        for positions, column, seen in checks:
            value = tuple(record.values[position] for position in positions)
            if value in seen:
                raise DataPreparationError(
                    IssueCode.DUPLICATE_KEY,
                    file_name=reading.source.file_name,
                    line=record.line,
                    column=column,
                )
            seen.add(value)


def _check_references(
    table: TableSpec, reading: TableReading, parent_keys: dict[str, frozenset[FieldValue]]
) -> None:
    """Raise broken_reference at the first value absent from its parent key index.

    Records are checked in source order and, inside a record, in schema column order.
    """
    parents = {reference.column_name: reference.parent_table for reference in table.references}
    checks = [
        (position, column.source_name, parent_keys[parents[column.column_name]])
        for position, column in enumerate(table.columns)
        if column.column_name in parents
    ]
    for record in reading.records:
        for position, column, keys in checks:
            if record.values[position] not in keys:
                raise DataPreparationError(
                    IssueCode.BROKEN_REFERENCE,
                    file_name=reading.source.file_name,
                    line=record.line,
                    column=column,
                )


def _primary_key_index(table: TableSpec, reading: TableReading) -> frozenset[FieldValue]:
    # Only single-column parent keys are referenced (see TABLES).
    if len(table.primary_key) != 1:
        return frozenset()
    position = _position(table, table.primary_key[0])
    return frozenset(record.values[position] for record in reading.records)


def read_validated_tables(
    source_dir: Path, *, file_prefix: str = SOURCE_FILE_PREFIX
) -> tuple[TableReading, ...]:
    """Read the six source files in TABLE_ORDER and validate keys and references.

    Each table is read with read_table, then checked for unique keys, then for
    references to parent tables already validated, before the next file is read.
    Readings are returned unchanged. No file is written and no database is opened.
    See docs/specs/02-05-data-relations.md.
    """
    readings: list[TableReading] = []
    parent_keys: dict[str, frozenset[FieldValue]] = {}
    for table in TABLES:
        reading = read_table(source_dir, table, file_prefix=file_prefix)
        _check_unique_keys(table, reading)
        _check_references(table, reading, parent_keys)
        parent_keys[table.name] = _primary_key_index(table, reading)
        readings.append(reading)
    return tuple(readings)


# --- T5: build and replace -------------------------------------------------------------

_DISTRIBUTION = "emporio-agent"
_STOCK_CONFLICT_STATUSES = frozenset({"discontinued", "coming_soon"})

type _Row = tuple[FieldValue, ...]


@dataclass(frozen=True)
class PreparationResult:
    database_path: Path = field(repr=False)
    schema_version: int
    prepared_at: datetime
    sources: tuple[SourceFileInfo, ...]
    row_counts: Mapping[str, int]
    quality_note_count: int


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _prepared_at(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value.astimezone(UTC)


def _same_file(target: Path, source: Path, target_stat: os.stat_result | None) -> bool:
    if os.path.normcase(os.path.realpath(target)) == os.path.normcase(os.path.realpath(source)):
        return True
    if target_stat is None or target_stat.st_ino == 0:
        return False
    try:
        return os.path.samestat(target_stat, os.stat(source))
    except OSError:
        return False


def _is_source_file(
    database_path: Path, source_dir: Path, readings: tuple[TableReading, ...]
) -> bool:
    try:
        target_stat: os.stat_result | None = os.stat(database_path)
    except OSError:
        target_stat = None
    return any(
        _same_file(database_path, source_dir / reading.source.file_name, target_stat)
        for reading in readings
    )


def _quality_notes(readings: tuple[TableReading, ...]) -> tuple[_Row, ...]:
    """Notes per product in source line order: status and stock rule, then curated IDs."""
    table, reading = next(
        (table, reading)
        for table, reading in zip(TABLES, readings, strict=True)
        if table.name == "products"
    )
    product_id = _position(table, "product_id")
    status = _position(table, "status")
    stock = _position(table, "stock_quantity")
    notes: list[_Row] = []
    for record in reading.records:
        values = record.values
        identifier = values[product_id]
        if values[status] in _STOCK_CONFLICT_STATUSES and int(values[stock] or 0) > 0:
            notes.append((QUALITY_ENTITY_PRODUCT, identifier, STATUS_STOCK_CONFLICT))
        if identifier in KNOWN_CATALOG_TEXT_CONFLICTS:
            notes.append((QUALITY_ENTITY_PRODUCT, identifier, CATALOG_TEXT_CONFLICT))
    return tuple(notes)


def _insert_sql(table: str, columns: tuple[str, ...]) -> str:
    # Identifiers come only from the internal schema, never from input.
    placeholders = ", ".join("?" for _ in columns)
    return f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"


def _rollback_and_close_quietly(connection: sqlite3.Connection) -> None:
    # Cleanup never replaces the primary error.
    try:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
    except sqlite3.Error:
        pass
    try:
        connection.close()
    except sqlite3.Error:
        pass


def _write_database(
    temporary: str,
    readings: tuple[TableReading, ...],
    metadata: tuple[_Row, ...],
    notes: tuple[_Row, ...],
) -> None:
    """Build the whole database in one explicit transaction, then commit and close.

    autocommit=True: the sqlite3 module opens and ends no transaction implicitly, so
    BEGIN, COMMIT and ROLLBACK are explicit SQL and Connection.commit() is not used.
    """
    connection = sqlite3.connect(temporary, autocommit=True)
    try:
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN")
        for statement in DDL_STATEMENTS:
            connection.execute(statement)
        for table, reading in zip(TABLES, readings, strict=True):
            columns = tuple(column.column_name for column in table.columns)
            connection.executemany(
                _insert_sql(table.name, columns), (record.values for record in reading.records)
            )
        connection.executemany(_insert_sql("preparation_metadata", ("key", "value")), metadata)
        connection.executemany(
            _insert_sql(
                "source_files", ("table_name", "file_name", "sha256", "size_bytes", "row_count")
            ),
            (
                (s.table_name, s.file_name, s.sha256, s.size_bytes, s.row_count)
                for s in (reading.source for reading in readings)
            ),
        )
        connection.executemany(
            _insert_sql("data_quality_notes", ("entity", "entity_id", "code")), notes
        )
        foreign_key_findings = connection.execute("PRAGMA foreign_key_check").fetchall()
        integrity = connection.execute("PRAGMA integrity_check").fetchall()
        if foreign_key_findings or integrity != [("ok",)]:
            raise DataPreparationError(IssueCode.INTEGRITY_CHECK_FAILED) from None
        connection.execute("COMMIT")
    except BaseException:
        _rollback_and_close_quietly(connection)
        raise
    connection.close()


def _remove_leftovers(temporary: str) -> None:
    # Only the files of this run. A failed removal is a known limit and never raised.
    for path in (temporary, temporary + "-journal"):
        try:
            os.remove(path)
        except OSError:
            pass


def _build_temporary(
    database_path: Path,
    readings: tuple[TableReading, ...],
    metadata: tuple[_Row, ...],
    notes: tuple[_Row, ...],
) -> str:
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{database_path.name}.", suffix=".tmp", dir=database_path.parent
        )
    except OSError:
        raise DataPreparationError(IssueCode.DATABASE_BUILD_FAILED) from None
    try:
        os.close(descriptor)
        _write_database(temporary, readings, metadata, notes)
    except sqlite3.Error, OSError:
        _remove_leftovers(temporary)
        raise DataPreparationError(IssueCode.DATABASE_BUILD_FAILED) from None
    except BaseException:
        _remove_leftovers(temporary)
        raise
    return temporary


def prepare_database(
    source_dir: Path,
    database_path: Path,
    *,
    file_prefix: str = SOURCE_FILE_PREFIX,
    clock: Callable[[], datetime] = _utc_now,
) -> PreparationResult:
    """Validate the six source files and publish a new SQLite database.

    Order: target directory, clock, sources (read_validated_tables), target not a
    source file, importer version; then build in a temporary file of the target
    directory and os.replace it over database_path. Nothing runs after the replace
    except the return. See docs/specs/02-06-database-preparation.md.
    """
    if not database_path.parent.is_dir():
        raise DataPreparationError(IssueCode.TARGET_DIRECTORY_MISSING)
    prepared_at = _prepared_at(clock)
    readings = read_validated_tables(source_dir, file_prefix=file_prefix)
    if _is_source_file(database_path, source_dir, readings):
        raise ValueError("database_path must not be a source file")
    importer_version = importlib.metadata.version(_DISTRIBUTION)

    metadata: tuple[_Row, ...] = (
        ("schema_version", str(SCHEMA_VERSION)),
        ("prepared_at", prepared_at.isoformat()),
        ("importer_version", importer_version),
    )
    notes = _quality_notes(readings)
    result = PreparationResult(
        database_path=database_path,
        schema_version=SCHEMA_VERSION,
        prepared_at=prepared_at,
        sources=tuple(reading.source for reading in readings),
        row_counts=MappingProxyType(
            {reading.source.table_name: reading.source.row_count for reading in readings}
        ),
        quality_note_count=len(notes),
    )

    temporary = _build_temporary(database_path, readings, metadata, notes)
    try:
        os.replace(temporary, database_path)
    except OSError:
        _remove_leftovers(temporary)
        raise DataPreparationError(IssueCode.TARGET_WRITE_FAILED) from None
    except BaseException:
        _remove_leftovers(temporary)
        raise
    return result
