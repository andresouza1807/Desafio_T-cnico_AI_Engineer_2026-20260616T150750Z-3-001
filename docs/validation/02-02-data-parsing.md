# Slice 02, task T1 validation — field errors and pure parsers

Date: 2026-11-02. Baseline: branch `feature/agent-mvp`, commit `bcdba0799b806f0876fbe97b4968eba418993f67`.
Source of truth: `docs/specs/02-data-preparation.md` (revision R1) and `docs/plans/02-data-preparation.md` (T1, D20–D34).

## Spec Summary

Implemented only T1: `IssueCode`, `FieldError` and the pure parsers and converters of `emporio_agent.data.parsing`. No schema, CSV reading, SQLite, connection, CLI or agent code was written. No private CSV file was read.

## Gap Analysis

No gap blocked test derivation. Decisions taken inside the specification's limits:

- `FieldError` subclasses `ValueError`, because it reports an invalid value. The specification fixes only `FieldError(code)` without the value.
- `IssueCode` contains all 26 codes of the R1 contract, so the enum is defined once. `DataPreparationError`, `DatabaseUnavailableError` and result objects are not created; they belong to later tasks.
- `cents_to_decimal` and `basis_points_to_percent` accept only `int` (not `bool`, `float`, `str` or `Decimal`) and raise `TypeError` otherwise. This keeps a float out of the money path.
- Every Decimal operation runs inside `localcontext()` on a copy of a private exact context (precision 60, traps for `Inexact`, `Rounded`, `InvalidOperation`, `Overflow`, `DivisionByZero`). The caller's context is not read or changed.
- Integer parsers check the digit count before `int()`, so very long digit strings give `out_of_range` and never reach the `int()` digit limit.
- Grammars use `re.fullmatch` with explicit `[0-9]`, so a trailing newline and non-ASCII digits are rejected.
- "Whitespace-only" means `not text.strip()` (Python Unicode whitespace, including U+00A0).
- JSON duplicate keys are rejected at every nesting level.
- Enum allowlists belong to `schema.py` (T2). The tests copy the allowlists from the specification.
- `SQLITE_MAX_INTEGER` and `MAX_MONEY_TEXT_LENGTH` are defined in `parsing.py`, where they are used.

## FieldError contract (recorded before implementation)

- Constructor: `FieldError(code: IssueCode)`. Base class: `ValueError`.
- Attributes: only `code: IssueCode`. `vars(error) == {"code": code}`.
- `args == (code.value,)`; `str(error) == code.value`; `repr(error) == "FieldError('<code>')"`.
- The rejected text is never stored, and no message is formatted from it.
- An error raised while another exception is handled (`date.fromisoformat`, `json.loads`) uses `raise ... from None`: `__cause__` is None and `__suppress_context__` is True.
- Guarantee surfaces: `str`, `repr`, `args`, attributes and `traceback.format_exception_only`. Not guaranteed: the full traceback and `__context__` (spec "Privacy").

## Implementation Plan

1. Write `tests/unit/test_data_parsing.py` from the specification, with hand-written expected values.
2. RED 1: run without the `emporio_agent.data` package.
3. RED 2: create `__init__.py`, `errors.py` and `parsing.py` with docstrings only, then run again.
4. Implement the minimum `errors.py` and `parsing.py`.
5. GREEN: run the parser tests, the full suite, Ruff check and Ruff format check.

## Test Checklist

- [x] D20 exact cents: `test_d20_money_to_exact_cents`.
- [x] D21 money grammar: `test_d21_money_rejected_by_grammar`, `test_d21_empty_money_is_required`.
- [x] D22 SQLite limit and grammar/length precedence: `test_d22_largest_money_value_fits_sqlite_integer`, `test_d22_money_above_sqlite_limit`, `test_d22_grammar_is_checked_before_length`.
- [x] D23 no float path and pricing compatibility: `test_d23_float_trap_values_are_exact`, `test_d23_cents_to_decimal_has_two_fraction_digits`, `test_d23_cents_to_decimal_accepts_only_int`, `test_d23_conversions_ignore_and_preserve_caller_decimal_context`, `test_d23_converted_values_are_accepted_by_pricing`.
- [x] D24 percent in basis points: `test_d24_percent_to_basis_points`, `test_d24_basis_points_to_percent`, `test_d24_basis_points_to_percent_accepts_only_int`.
- [x] D25 percent rejections, no rounding: `test_d25_percent_rejected`, `test_d25_empty_percent_is_required`.
- [x] D26 IDs: `test_d26_valid_ids`, `test_d26_invalid_ids`, `test_d26_ids_above_sqlite_limit`, `test_d26_empty_id_is_required`.
- [x] D27 stock and quantity: `test_d27_valid_stock`, `test_d27_invalid_stock`, `test_d27_stock_above_sqlite_limit`, `test_d27_empty_stock_is_required`, `test_d27_quantity_uses_id_grammar`.
- [x] D28 calendar dates: `test_d28_valid_dates_are_returned_unchanged`, `test_d28_invalid_dates`, `test_d28_empty_date_is_required`.
- [x] D29 optional date: `test_d29_empty_optional_date_is_none`, `test_d29_optional_date_keeps_valid_text`, `test_d29_invalid_optional_dates`.
- [x] D30 flags: `test_d30_valid_flags`, `test_d30_invalid_flags`, `test_d30_empty_flag_is_required`.
- [x] D31 enums: `test_d31_allowlisted_values`, `test_d31_values_outside_allowlist`, `test_d31_empty_enum_is_required`.
- [x] D32 JSON object: `test_d32_json_object_returned_unchanged`, `test_d32_invalid_json_object`, `test_d32_empty_json_is_required`.
- [x] D33 required text: `test_d33_required_text_returned_unchanged`, `test_d33_blank_required_text`.
- [x] D34 optional text: `test_d34_empty_optional_text_is_none`, `test_d34_optional_text_returned_unchanged`, `test_d34_blank_optional_text`.
- [x] Error contract (no D ID; prerequisite for D60): `test_issue_codes_match_specification`, `test_field_error_public_representation`, `test_field_error_public_surfaces_hide_rejected_value`.

Counts: 15 matrix cases (D20–D34) plus the error contract, implemented as 48 test functions. pytest collects 214 items, because most functions are parametrized. The 214 items are not 214 scenarios of the matrix.

## Test Strategy and observed evidence

Pure unit tests. No files, no database, no network, no mocks. Expected values were written by hand from the specification. Modules are imported inside test calls, so a missing module gives failed tests, not a collection error.

Environment: Python 3.14.3, ruff 0.17.3, commands run from the project root.

Module origin, checked with `uv run --frozen python -c "import ...; print(module.__file__)"`:

```text
C:\Users\andre.souza\Desktop\artefact_challege\src\emporio_agent\__init__.py
C:\Users\andre.souza\Desktop\artefact_challege\src\emporio_agent\services\pricing.py
```

After implementation:

```text
C:\Users\andre.souza\Desktop\artefact_challege\src\emporio_agent\data\__init__.py
C:\Users\andre.souza\Desktop\artefact_challege\src\emporio_agent\data\errors.py
C:\Users\andre.souza\Desktop\artefact_challege\src\emporio_agent\data\parsing.py
```

Collection before RED:

```text
uv run --frozen pytest tests/unit/test_data_parsing.py -q --collect-only
214 tests collected in 0.04s
exit code 0
```

RED 1, without `src/emporio_agent/data/`:

```text
uv run --frozen pytest tests/unit/test_data_parsing.py -q --tb=line
214 failed in 0.27s
exit code 1
Only failure reason: ModuleNotFoundError: No module named 'emporio_agent.data'
```

RED 2, with docstring-only `__init__.py`, `errors.py` and `parsing.py`:

```text
uv run --frozen pytest tests/unit/test_data_parsing.py -q --tb=line
214 failed in 0.23s
exit code 1
Failure reasons: AttributeError: module 'emporio_agent.data.errors' has no attribute 'FieldError'
                 AttributeError: module 'emporio_agent.data.errors' has no attribute 'IssueCode'
                 AttributeError: module 'emporio_agent.data.parsing' has no attribute '<name>'
                 for each of the 13 contracted parser and converter names
```

Both RED runs collected every test. No failure came from fixture paths, import configuration or syntax. Package resolution was correct: `emporio_agent` and `pricing` loaded from this project's `src`.

GREEN:

```text
uv run --frozen pytest tests/unit/test_data_parsing.py -q
214 passed in 0.13s
exit code 0

uv run --frozen pytest -q
251 passed in 0.16s
exit code 0
(214 new items + 37 existing pricing tests; tests/unit/test_pricing.py alone: 37 passed)

uv run --frozen ruff check .
All checks passed!
exit code 0

uv run --frozen ruff format --check .
1 file would be reformatted, 14 files already formatted
exit code 1
```

The only file reported by the format check is `docs/specs/02-data-preparation.md`. Ruff formats the Python block in its "Service Contract" section (comment spacing and blank lines between definitions). This task does not authorize changes to that document, so it was not changed. The code and test files pass:

```text
uv run --frozen ruff format --check src tests
8 files already formatted
exit code 0
```

The format-check failure above is superseded by revision 02-02-R1 below.

## Revision 02-02-R1 — documentation formatting

Date: 2026-11-02. Reviewer result before this revision: 251 tests passed and Ruff check passed. The only open item was `ruff format --check .` on the Python block of `docs/specs/02-data-preparation.md`.

Authorized files: `docs/specs/02-data-preparation.md` and this report. No configuration, code, test, dependency or lockfile change. The documents are not excluded from the formatter.

### Formatting change (authorized in this revision)

The specification is untracked, so `git diff` cannot show its earlier content. A copy was saved outside the repository before formatting, and the copy was compared with the formatted file.

```text
sha256 before: 9f975f6aeae2f24e22daaae0d1d630636923965c78c42abca8a6b1cf729b0ea3
uv run --frozen ruff format docs/specs/02-data-preparation.md
1 file reformatted
exit code 0
sha256 after:  4b183e2aec09e70e6d82f06e1849a7c6e2ba7d430b3a7bad0874841cc0a796db
```

Complete `diff -u` (before, after):

````diff
@@ -387,8 +387,8 @@
 def parse_json_object(text: str) -> str: ...
 def parse_text(text: str) -> str: ...
 def parse_optional_text(text: str) -> str | None: ...
-def cents_to_decimal(cents: int) -> Decimal: ...          # Decimal(cents).scaleb(-2)
-def basis_points_to_percent(bp: int) -> Decimal: ...      # Decimal(bp) / Decimal(100)
+def cents_to_decimal(cents: int) -> Decimal: ...  # Decimal(cents).scaleb(-2)
+def basis_points_to_percent(bp: int) -> Decimal: ...  # Decimal(bp) / Decimal(100)
 ```
@@ -398,15 +398,18 @@
     reason: Literal["missing", "open_failed", "schema_mismatch"]
     # str() contains the reason and the database base name only.
 
+
 @dataclass(frozen=True)
 class PreparationInfo:
     schema_version: int
     prepared_at: datetime
     sources: tuple[SourceFileInfo, ...]
 
+
 @contextmanager
 def connect_readonly(database_path: Path) -> Iterator[sqlite3.Connection]: ...
 
+
 def read_preparation_info(connection: sqlite3.Connection) -> PreparationInfo: ...
````

Changes: spaces before two inline comments were reduced, and three blank lines were added between top-level definitions. A second comparison removed all spaces, tabs, carriage returns and newlines from both versions. The two results are identical. So no signature, rule, example, limit or decision changed.

### Reviewer acceptance recorded

The reviewer accepted the Gap Analysis decision that `cents_to_decimal` and `basis_points_to_percent` accept only `int` and raise `TypeError` for every other type, including `bool`. The specification signatures (`cents: int`, `bp: int`) are unchanged. The tests `test_d23_cents_to_decimal_accepts_only_int` and `test_d24_basis_points_to_percent_accepts_only_int` cover this decision.

### Current gate results

Environment: uv 0.12.5, Python 3.14.3, ruff 0.17.3, commands run from the project root after the formatting change.

```text
uv run --frozen pytest -q
251 passed in 0.15s
exit code 0

uv run --frozen ruff check .
All checks passed!
exit code 0

uv run --frozen ruff format --check .
16 files already formatted
exit code 0
```

## Limits

- Tests prove the parser contract on the listed inputs. They do not read CSV files and do not cover D35–D38 (integration, T3).
- Privacy checks cover `str`, `repr`, `args`, attributes and `format_exception_only`. They do not cover the full traceback or `__context__`.
- JSON nesting deep enough to hit Python's recursion limit raises `RecursionError`, not `FieldError`. The specification does not define this case.
- The Decimal context test checks one reduced caller context (precision 3, `ROUND_DOWN`). It does not enumerate every context setting.

## Compliance Report

- Spec vs code: the 13 contracted functions and `FieldError(code)` exist with the specified grammar, ranges and codes.
- Spec vs tests: D20–D34 are traced to test functions above; the error contract is tested separately.
- Tests vs code: RED (two stages) observed before implementation, GREEN after it. 37 pricing tests unchanged and passing. Ruff check passes. After revision 02-02-R1, the repository-wide Ruff format check passes.
- Files created in T1: `src/emporio_agent/data/__init__.py`, `src/emporio_agent/data/errors.py`, `src/emporio_agent/data/parsing.py`, `tests/unit/test_data_parsing.py`, this report. No dependency or lockfile change. No commit.
- Files changed in 02-02-R1: `docs/specs/02-data-preparation.md` (whitespace only, by `ruff format`) and this report. No configuration, code or test file changed. No commit.
- Limits: the limits listed in the "Limits" section are unchanged by 02-02-R1.
- Status: T1 COMPLIANT. All three gates pass (pytest exit 0, Ruff check exit 0, Ruff format check exit 0). T2 not started.
