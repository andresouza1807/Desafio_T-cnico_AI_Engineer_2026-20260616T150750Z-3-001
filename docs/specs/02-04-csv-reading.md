# Slice 02, task T3 contract: CSV reading, hashes, headers and fields

Status: contract for task T3, written before tests, 2026-11-02.
Source of truth: `docs/specs/02-data-preparation.md` (revision R1, approved), `docs/plans/02-data-preparation.md` (T3), `docs/specs/02-03-data-schema.md` (T2).
This document refines the reader part of `emporio_agent.data.preparation`. It adds no business rule. Where it differs from R1, R1 wins. The one reviewer decision recorded in section "Header errors and unknown names" was incorporated into R1 by the amendment approved in review 02-04-R1, so no difference remains there.

## Scope

In scope:

- `src/emporio_agent/data/preparation.py`: one reader for one table (`read_table`) and its result types.
- `src/emporio_agent/data/errors.py`: addition of `DataPreparationError` (R1 "Service Contract"). `IssueCode` and `FieldError` stay unchanged.
- `tests/integration/test_csv_reading.py`.

Out of scope (T4/T5 and later): uniqueness of keys and of `categories.name`, foreign keys, data-quality notes, cross-field business checks, coordinated reading of the six files, `PreparationResult`, `prepare_database`, any SQLite connection, temporary files, replace, read-only access, CLI, agent and pricing.

## Public contract

```python
# emporio_agent.data.errors (addition)
class DataPreparationError(Exception):
    code: IssueCode
    file_name: str | None  # source base name only; None for directory errors
    line: int | None
    column: str | None

    def __init__(
        self,
        code: IssueCode,
        *,
        file_name: str | None = None,
        line: int | None = None,
        column: str | None = None,
    ) -> None: ...


# emporio_agent.data.preparation
@dataclass(frozen=True)
class SourceFileInfo:
    table_name: str
    file_name: str  # base name, f"{file_prefix}{table_name}.csv"
    sha256: str  # 64 lowercase hex of the raw file bytes
    size_bytes: int  # len(raw file bytes)
    row_count: int  # number of data records


@dataclass(frozen=True)
class SourceRecord:
    line: int  # physical line where the record ends
    values: tuple[int | str | None, ...] = field(repr=False)


@dataclass(frozen=True)
class TableReading:
    source: SourceFileInfo
    records: tuple[SourceRecord, ...] = field(repr=False)


def read_table(
    source_dir: Path,
    table: TableSpec,
    *,
    file_prefix: str = SOURCE_FILE_PREFIX,
) -> TableReading: ...
```

- `SourceFileInfo` is exactly the R1 type. T5 will reuse it for `PreparationResult.sources` and `source_files`.
- `SourceRecord.values` follows `table.columns` order (the order of `TABLES`), not the CSV header order. Each value is the result of the T1 parser for the column kind.
- `SourceRecord.line` is the physical line where the record ends (`csv.reader.line_num`); the header is line 1. T4 uses it for `duplicate_key` and `broken_reference`.
- `repr()` of `SourceRecord` and `TableReading` shows no cell value: `values` and `records` are declared with `repr=False`. `SourceFileInfo` holds only names, hash and counts.
- All three types are frozen dataclasses holding tuples. Equality is the dataclass default.
- `read_table` has no side effect besides reading one file: it creates no file, opens no SQLite connection, writes no log, reads no environment variable and changes no global state (it does not change `csv.field_size_limit`).
- `read_table` validates structure and single fields only. Records with repeated keys, repeated category names or unknown parent IDs are returned unchanged, with their line, for T4.

`DataPreparationError`:

- `code` is converted with `IssueCode(code)`.
- `str()`: `"<file_name> line <line>, column <column>: <code>"`, omitting absent parts. Examples: `"x.csv line 4, column price_brl: invalid_money"`, `"x.csv line 1: header_mismatch"`, `"x.csv: encoding_invalid"`, `"source_dir_missing"`.
- `args == (str(self),)`. `repr()` is the default `Exception` repr of that single argument.
- No cell value and no directory path appear in `str()`, `repr()`, `args`, the four attributes or `traceback.format_exception_only()`. The full traceback and `__context__` are outside this guarantee (R1 "Privacy").

## Reading algorithm

For one `TableSpec`:

1. `file_name = f"{file_prefix}{table.name}.csv"`. If `file_name` is not a plain base name (it contains `/`, `\`, a drive or any other path component), raise `ValueError("file_prefix must not contain a path component")`. This is a caller programming error, not a data error; the message does not echo the prefix.
2. If `source_dir` is not an existing directory: `source_dir_missing`, all locations `None`.
3. Read `source_dir / file_name` once, as bytes. `FileNotFoundError`: `file_missing`. Any other `OSError` (directory in place of the file, permission): `file_unreadable`. Both carry `file_name`.
4. `sha256 = hashlib.sha256(data).hexdigest()`, `size_bytes = len(data)`. These bytes include the original BOM and line endings.
5. Decode the same bytes with `utf-8-sig`, strict. One leading BOM is removed; a second BOM stays as text. `UnicodeDecodeError`: `encoding_invalid`, `file_name`, line `None`.
6. If the decoded text contains NUL (`\x00`): `csv_malformed`, `file_name`, line `None`. Python 3.14 `csv` accepts NUL, so this check is explicit.
7. Parse with `csv.reader(io.StringIO(text, newline=""), delimiter=",", quotechar='"', doublequote=True, skipinitialspace=False, strict=True)`. `newline=""` keeps line breaks inside quoted fields and accepts LF, CRLF and a final line without terminator. Any `csv.Error` (unbalanced quote, text after a closing quote, field above the default `csv.field_size_limit`): `csv_malformed`, `file_name`, line = `reader.line_num`.
8. Header checks, in this order (section below).
9. Each data record, in line order: field count, then fields in `table.columns` order.
10. Return `TableReading` with `row_count = len(records)`.

No second read of the file happens between hashing and parsing. The test checks this with an audit hook that counts `open` events for the source path.

## Header errors and unknown names

Header names are compared exactly: no trimming, no case folding, no Unicode normalization. Column order is free.

Checks, first match wins:

| Order | Condition | Code | line | column |
| --- | --- | --- | --- | --- |
| 1 | No record at all (empty file, or BOM only) | `header_missing` | `None` | `None` |
| 2 | First record has zero fields (blank first line) | `header_missing` | header end line (1) | `None` |
| 3 | A name appears twice; the first repeated name in header order is reported | `header_duplicate` | header end line | the name if it is a schema `source_name`, else `None` |
| 4 | A schema column is absent; the first absent one in `table.columns` order is reported | `header_mismatch` | header end line | the schema `source_name` |
| 5 | A header name is not a schema `source_name` | `header_mismatch` | header end line | `None` |

Decision (reviewer instruction for T3): diagnostics may name columns recognized by the schema. Identifiers that come from the header and are not schema names are never echoed; such errors use `column=None`. A header name is file content and may carry arbitrary text.

R1 amendment (proposed with this contract, approved and incorporated in review 02-04-R1):

- `docs/specs/02-data-preparation.md`, section "CSV format", previous text: "A header with a duplicate name raises `header_duplicate`. A missing or unexpected column raises `header_mismatch`; the error names the column (a column name is not a cell value)."
- Incorporated text: "A header with a duplicate name raises `header_duplicate`. A missing or unexpected column raises `header_mismatch`. The error names a column only when it is a schema column name: the missing column, or the repeated name when it belongs to the schema. A header name that is not a schema column name is never echoed; the error then has `column=None`."
- `docs/plans/02-data-preparation.md`, row D15, previous expected value: "`header_mismatch`, column `notes` / `extra`". Incorporated: "`header_mismatch`, column `notes` / column `None`; the unexpected name does not appear in the error".
- D14 (duplicate `name`, column `name`) is compatible and unchanged.

## Record rules

- Field count: a record whose field count differs from the header's raises `field_count` at the record's end line, column `None`. A blank line inside the file is a record with zero fields and raises `field_count`. A file ending with two line terminators has a blank last record and raises `field_count`. One final terminator, or none, is accepted.
- A header-only file is valid: zero records, `row_count = 0`.
- Fields are parsed in `table.columns` order. The cell is taken from the header position of `column.source_name`. The first failing column wins, so the reported column follows schema order, not CSV order.
- Parser per `FieldKind`:

| Kind | Parser (T1) |
| --- | --- |
| `ID` | `parse_id` |
| `COUNT` | `parse_count` |
| `QUANTITY` | `parse_id` (same grammar, value ≥ 1) |
| `MONEY` | `parse_money_cents` |
| `PERCENT` | `parse_percent_basis_points` |
| `DATE` | `parse_date` |
| `OPTIONAL_DATE` | `parse_optional_date` |
| `FLAG` | `parse_flag` |
| `ENUM` | `parse_enum(text, column.allowed)` |
| `JSON_OBJECT` | `parse_json_object` |
| `TEXT` | `parse_text` |
| `OPTIONAL_TEXT` | `parse_optional_text` |

- A `FieldError` becomes `DataPreparationError(error.code, file_name=..., line=record end line, column=column.source_name)`, raised with `from None`.
- Only the three optional columns of T2 metadata (`orders.tracking_code`, `orders.estimated_delivery`, `orders.notes`) can yield `None`. Every other empty cell raises `value_required`.
- Text values are returned exactly as decoded, including inner commas, doubled quotes reduced to one quote, and line breaks inside quoted fields. Line breaks inside a quoted field are kept as written in the file (`\r\n` stays `\r\n`).
- Money and percentages use the T1 conversions to integer cents and basis points. No `float`.

## Error locations

| Code | file_name | line | column |
| --- | --- | --- | --- |
| `source_dir_missing` | `None` | `None` | `None` |
| `file_missing`, `file_unreadable` | base name | `None` | `None` |
| `encoding_invalid` | base name | `None` | `None` |
| `csv_malformed` (NUL) | base name | `None` | `None` |
| `csv_malformed` (`csv.Error`) | base name | `reader.line_num` | `None` |
| header codes | base name | see header table | see header table |
| `field_count` | base name | record end line | `None` |
| field codes | base name | record end line | schema `source_name` |

Every conversion of a `FieldError`, `OSError`, `UnicodeDecodeError` or `csv.Error` uses `raise ... from None`. The original message is never formatted into the new error.

## Limits

- `csv.reader` with `strict=True` accepts a quote character inside an unquoted field (`a"b` is read as text `a"b`). R1 names `strict=True` as the dialect, so the reader does not add a stricter rule.
- With `newline=""`, a lone `\r` also ends a physical line. `line` follows `csv.reader.line_num` in every case.
- Fields longer than the process-wide `csv.field_size_limit` (default 131072 characters) raise `csv_malformed`. The reader does not change that limit.
- `read_table` reads the whole file into memory. The dataset size makes this acceptable (R1).
- Privacy covers `str()`, `repr()`, `args`, attributes and `format_exception_only()` only. Full tracebacks and `__context__` are not sanitized.
- T3 does not prove a complete import, atomicity or durability. Those belong to T4/T5.

## Traceability

| Plan case | T3 coverage | Notes |
| --- | --- | --- |
| D10 | missing directory; path that is a file | through `read_table` |
| D11 | missing file; directory in place of file; mocked `PermissionError` | `file_unreadable` added under the same ID |
| D12 | invalid UTF-8 byte; truncated multibyte sequence | |
| D13 | empty file; BOM only; blank first line | |
| D14 | duplicate schema name; duplicate unknown name | unknown name uses `column=None` |
| D15 | missing `notes`; unexpected sentinel column; renamed column | unexpected uses `column=None` (R1 amendment above, approved in 02-04-R1) |
| D16 | extra field; missing field; blank line inside; double final terminator | |
| D17 | unbalanced quote; text after closing quote; NUL | |
| D35 | every non-optional column left empty | parametrized over all tables |
| D36 | `specs` = `[1]` | |
| D37 | `total_brl` = `12.345` | |
| D38 | `price_brl` = `92233720368547758.08` | |
| D60 (part) | sentinel in invalid `customer_id` and `price_brl`, through `read_table` | through `prepare_database` stays for T7 |
| D18, D19, D39 | not in T3 | T3 only proves that such rows are returned with their line |

Reader-specific cases (no plan ID): reordered header mapping, encodings and line endings, quoted commas, doubled quotes and multiline records, line after a multiline record, header only, hash and size, single read, unknown header sentinel, optional and typed values, schema-order validation, `QUANTITY` grammar, `file_prefix` parameter and guard, `repr` without cell data, error text format, no write and no SQLite.
