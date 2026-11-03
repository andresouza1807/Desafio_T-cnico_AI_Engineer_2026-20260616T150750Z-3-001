# Slice 02, task T8 validation: integrated gates and pricing regression

Date: 2026-11-02. Baseline: `feature/data-preparation`, commit `9e093da`, clean working tree. No commit, push or PR.
Sources: `docs/specs/02-data-preparation.md` (R1 and amendment), `docs/plans/02-data-preparation.md` (T8), approved T1–T7 contracts and reports.
Platform: Windows 11 Pro 10.0.26200, Python 3.14.3 (MSC v.1944 64 bit), SQLite 3.50.4, uv 0.12.5, pytest 9.2.0, Ruff 0.17.3.

## Spec Summary

Executed only T8: full test suite, Ruff check, Ruff format check and regression of the 37 pricing tests. T8 validates existing behavior. It adds no rule, interface or test.

## Gap Analysis

- No gap found. Baseline matched the expected state (branch, HEAD `9e093da`, clean tree).
- No RED was fabricated and no production code was changed to provoke a failure.
- The 2 skips are the known Windows cases in `tests/integration/test_readonly_connection.py` (`?` not allowed in Windows file names). They are recorded separately and are not counted as approval of those scenarios.

## Implementation Plan (executed)

1. Confirmed baseline and environment (Git state, Python, uv, SQLite, pytest, Ruff).
2. Ran the isolated pricing regression.
3. Collected and ran the full suite.
4. Ran Ruff check and Ruff format check (check mode only, no write).
5. Wrote this report.
6. Re-ran Ruff check and Ruff format check, because Ruff also includes Markdown files.
7. Checked Git state.

## Test Checklist

| Gate | Command | Expected | Observed | Exit |
| --- | --- | --- | --- | --- |
| Pricing regression | `uv run --frozen pytest tests/unit/test_pricing.py -q` | 37 passed | 37 passed | 0 |
| Collection | `uv run --frozen pytest --collect-only -q` | 759 items | 759 tests collected | 0 |
| Full suite | `uv run --frozen pytest -q -rs` | 757 passed, 2 skipped | 757 passed, 2 skipped | 0 |
| Lint | `uv run --frozen ruff check .` | no errors | All checks passed! | 0 |
| Format | `uv run --frozen ruff format --check .` | no differences | 39 files already formatted | 0 |

Collection count of pricing tests in the full collection: 37 (`test_pricing.py::` items), equal to the isolated run.

Accounting: 759 collected = 757 passed + 2 skipped + 0 failed + 0 errors.

## Test Strategy

- Existing environment and lockfile (`--frozen`); real tests; no added mocks.
- No private CSV files, OpenAI, network, dependency change or lockfile change.
- Collection, pass, failure and skip are reported separately.
- Validation is not extended to other operating systems or to failures that were not executed.

## Evidence

Before writing this report:

```
$ uv run --frozen pytest tests/unit/test_pricing.py -q
37 passed in 0.03s
exit 0

$ uv run --frozen pytest --collect-only -q
759 tests collected in 0.10s
exit 0

$ uv run --frozen pytest -q -rs
SKIPPED [1] tests\integration\test_readonly_connection.py:599: Windows does not allow '?' in file names
SKIPPED [1] tests\integration\test_readonly_connection.py:612: Windows does not allow '?' in file names
757 passed, 2 skipped in 18.00s
exit 0

$ uv run --frozen ruff check .
All checks passed!
exit 0

$ uv run --frozen ruff format --check .
39 files already formatted
exit 0
```

The T7 report recorded `38 files already formatted`. The current count is 39. Ruff includes tracked Markdown files (`README.md`, `docs/specs/*.md`, `docs/validation/*.md` and others not ignored by `.gitignore`). The difference is consistent with the T7 report file, which was created after the T7 Ruff run. This cause was not verified separately.

After writing this report (this file adds one Markdown file to the Ruff scope):

```
$ uv run --frozen ruff check .
All checks passed!
exit 0

$ uv run --frozen ruff format --check .
40 files already formatted
exit 0
```

## Compliance Report

- Only T8 executed. T9 (`docs/validation/02-data-preparation.md`) not started.
- Only file created: `docs/validation/02-09-integrated-gates.md`. No code, test, fixture, contract, plan, README, configuration, dependency or lockfile changed. The formatter ran only in check mode.
- All gates passed. No divergence from expected numbers.
- Git state after this report: one untracked file, `docs/validation/02-09-integrated-gates.md`; nothing staged or committed.

## Limits

- Results apply to Windows 11 with the versions listed above. Other operating systems not executed.
- The 2 skipped scenarios (file names with `?`) are not validated on this platform.
- Timings are single observations and are not performance claims.
