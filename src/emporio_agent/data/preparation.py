"""Reading and validation of the store CSV files.

This module holds the single-table reader of task T3 (structure and single fields,
docs/specs/02-04-csv-reading.md) and the in-memory validation of keys and
references across the six tables of task T4 (docs/specs/02-05-data-relations.md).
Database construction belongs to a later task.
"""

import csv
import hashlib
import io
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath

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
from emporio_agent.data.schema import SOURCE_FILE_PREFIX, TABLES, ColumnSpec, FieldKind, TableSpec

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
