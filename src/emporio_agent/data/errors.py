"""Error codes and field-level errors for data preparation."""

from enum import StrEnum


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


class FieldError(ValueError):
    """A cell failed its grammar or range rule.

    Only the code is kept. The rejected text is never stored, so str(), repr() and
    args show the code alone. The importer adds file, line and column.
    """

    code: IssueCode

    def __init__(self, code: IssueCode) -> None:
        self.code = IssueCode(code)
        super().__init__(self.code.value)


class DataPreparationError(Exception):
    """A source file failed preparation.

    Carries the code and a location: source base name, physical line and column name.
    It never carries a cell value or a directory path. args == (str(self),).
    """

    code: IssueCode
    file_name: str | None
    line: int | None
    column: str | None

    def __init__(
        self,
        code: IssueCode,
        *,
        file_name: str | None = None,
        line: int | None = None,
        column: str | None = None,
    ) -> None:
        self.code = IssueCode(code)
        self.file_name = file_name
        self.line = line
        self.column = column
        location = file_name or ""
        if line is not None:
            location = f"{location} line {line}".lstrip()
        if column is not None:
            location = f"{location}, column {column}" if location else f"column {column}"
        super().__init__(f"{location}: {self.code.value}" if location else self.code.value)
