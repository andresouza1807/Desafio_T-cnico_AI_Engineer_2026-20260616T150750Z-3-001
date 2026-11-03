# Slice 04, part A validation: policy extraction, sections, provenance and index

Date: 2026-11-03. Baseline: branch `feature/data-preparation`, commit `1b358ef`. Before any change the working tree had only the two approved, untracked documents `docs/specs/04-policies.md` (SHA-256 `4bf210fe…e209e215`) and `docs/plans/04-policies.md` (SHA-256 `2f546979…8a666`); `uv run --frozen pytest -q -rs` gave `938 passed, 2 skipped` (exit 0; the two skips are the known Windows `?` file-name cases in `tests/integration/test_readonly_connection.py:599` and `:612`). No divergence from the expected baseline. Both documents are byte-identical after this task (same SHA-256). No commit, push, PR or merge.
Contract: `docs/specs/04-policies.md` (decisions P-01–P-06, P-12–P-15) and `docs/plans/04-policies.md`, section "Slice A" and matrix rows P01–P35. Neither document was changed.
Platform: Windows 11 Pro 10.0.26200, Python 3.14.3, `pypdf` 6.21.4 (locked, already a runtime dependency).

> Revision history. R0: first delivery (sections below, kept as recorded). R1: review 04-02-R1, section "Review 04-02-R1" at the end. R0 results marked *superseded by R1* are kept for history only; the current results are in the R1 section.

## Spec Summary

New module `src/emporio_agent/data/policy_index.py`:

- Constants `MAX_SOURCE_BYTES` (20 MiB), `MAX_PAGES` (100), `MAX_INDEX_BYTES` (5 MiB), `INDEX_FORMAT`, `INDEX_FORMAT_VERSION` (1), `EXTRACTION_RULES` (`layout-v1`).
- Frozen dataclasses `PolicySource`, `IndexedSection` (with derived `pages`), `PolicyIndex`, `PolicyPreparationResult`.
- Errors `PolicyPreparationError` (`code`, `file_name`, `page`, `outline_entry`; `str()` is `<file_name>: page <n>: <code>` with absent parts omitted; `args == (str(self),)`) and `PolicyIndexUnavailableError` (`code`, `file_name`; `str()` is `<file_name>: <code>`). Codes are fixed `Literal` sets; an unknown code raises `ValueError`.
- `clean_page_lines`: N1 (split, drop blank), N2 (compact whitespace), N3 (NFC), N4 (first line shared by two or more pages removed where it is first), N5 (last line `Página N` removed only when N is the physical page).
- `extract_policy_index`: read the source once (bounded read of `MAX_SOURCE_BYTES + 1`), parse from memory, reject encryption (also with an empty user password), check page count, extract every page with `extraction_mode="layout"`, clean, reject pages without text, flatten the outline depth-first, validate every entry (title grammar, unique ID, depth, parent prefix, destination), locate each heading by exact equality on its destination page after a moving cursor, build `front_matter` only when lines precede the first heading, keep empty-body sections, reject an index with no retrievable section.
- `prepare_policy_index`: target directory must exist, target must not be the source (`os.path.samefile` when both exist, normalized resolved path otherwise), extract, encode (`json.dumps(..., ensure_ascii=False, indent=2) + "\n"`, UTF-8, binary), run the load validation on the produced bytes (R1: including the `MAX_INDEX_BYTES` limit), then `tempfile.mkstemp` in the target directory, write, flush, `os.fsync`, close, `os.replace`. On a handled failure the temporary file is removed.
- `load_policy_index`: bounded read, strict UTF-8 and JSON (duplicate keys and `NaN`/`Infinity` rejected), exact keys and types as in P-05, `format_version` check right after `format`, booleans rejected wherever an integer is expected. Never opens the PDF.

Not implemented: retrieval, ranking, stemming, synonyms, notices, `CuratedNotes`/`MINI_NOTES` (slice B), CLI, OpenAI, preparation of the real manual (slice C). No file under `data/local/` was created.

## Gap Analysis

Decisions P-01–P-06 and P-12–P-15 were applied unchanged. No blocking ambiguity was found. Points resolved without changing the contract or an expected value, for review:

1. `headings_only` layout. Removing every body line and the front matter leaves page 1 empty. With the empty page kept, `page_text_missing` (page 1) would fire before `no_retrievable_section`, contradicting P21. The builder drops the emptied page and shifts outline destinations by one, which is the only layout where P21 and P04 ("absent in the variant") both hold.
2. P04 absence of `front_matter`. The `headings_only` variant fails extraction (P21), so the absence cannot be observed on an index. One added test removes the cover page (test-local layout edit, not added to the plan's edit table) and checks that the index starts at `1`. Mutation M15 is killed only by this added test.
3. `str()` of `PolicyPreparationError` follows the spec literally (`file_name`, `page`, `code`); the outline entry is carried only by the `outline_entry` attribute.
4. Page reported for outline errors: the resolved destination page (P-12 "located by entry number and page"). P19 and P20 assert it (pages 2/3 and 3/4/3/3); these assertions are additional to the matrix rows.
5. Not covered by a test (no case in the matrix, no synthetic mechanism without mocking `pypdf`):
   - `extraction_failed` (an exception from `pypdf` while extracting a page);
   - an outline that raises while being read: mapped to `outline_invalid` with no entry number (the spec table has no row for it);
   - a failure of the in-memory load validation inside `prepare_policy_index`: it propagates as `PolicyIndexUnavailableError("index_invalid")`. The spec defines no preparation code for it. Unreachable for valid Windows file names; on POSIX a source base name containing `\` or `:` would reach it. Flagged for review, not decided here. *R1: the size-limit path of this validation was reachable (reviewer finding) and is now tested; the file-name path is still untested;*
   - lone surrogates in extracted text would raise `UnicodeEncodeError` at encoding (not handled, not observed).
6. Check order inside extraction: every page is extracted before cleaning (N4 needs all pages), so `extraction_failed` on any page is reported before `page_text_missing` on an earlier page.
7. `MINI` and helpers are exposed through attributes of the `policy_kit` fixture. The repository has no `tests/__init__.py`, so importing `tests.conftest` would depend on `sys.path`. Module-level `MINI` and `MINI_SOURCE` stay in `tests/conftest.py` for slice B. `tests/conftest.py` does not import `emporio_agent.data.policy_index`; RED showed the 938 earlier tests still collect.
8. Loader strictness beyond the P33 row, taken from P-05 text or from the project convention (`parse_json_object`): duplicate JSON keys, `NaN`, UTF-8 BOM, float or boolean versions, extra/missing keys in `source` and sections, and 31 more single changes (see Test Checklist). No expected value of the matrix was changed.
9. Test defect found during GREEN and fixed: the P33 guard `changed != document` was true-equal for `True` vs `1` and `5.0` vs `5` in Python; it now compares `json.dumps` output. The expectation (`index_invalid`) was not changed.
10. The plan predicted two mutation outcomes that did not hold (see Mutations): M01 survives with the approved synthetic mechanism, and M08 is killed by P26, not P25.
11. The spec and plan headers still read "proposed"; the task brief states they are approved. Documents left unchanged.

## Implementation Plan (executed)

1. Baseline checked: branch, `1b358ef`, two untracked approved documents, `938 passed, 2 skipped`; hashes of both documents recorded; `pypdf` 6.21.4 confirmed in the environment.
2. Read spec and plan, `data/preparation.py` (atomic write, sanitized errors), `data/errors.py`, `data/parsing.py` (strict JSON hook) and `tests/integration/conftest.py`.
3. Scratch prototype (outside the repository) of the plan's PDF mechanism: layout text round-trips accents, `—`, `•`, `º`; RC4-128 with empty password is decrypted automatically by `pypdf` and still reports `is_encrypted`; `b""`, `b"not a pdf"` and a 400-byte truncation fail to parse.
4. Tests written before the implementation: `tests/conftest.py`, `tests/unit/test_policy_cleaning.py`, `tests/integration/test_policy_preparation.py`, `tests/integration/test_policy_index_load.py`.
5. RED recorded.
6. Implemented `src/emporio_agent/data/policy_index.py` (slice A only).
7. GREEN, mutations one at a time, byte restoration check, full gates, this report, Ruff again.

## Test Checklist

133 new collected items in 56 test functions: `tests/unit/test_policy_cleaning.py` 15 items, `tests/integration/test_policy_preparation.py` 54 items, `tests/integration/test_policy_index_load.py` 64 items. No earlier test file changed.

| Case | Type | Test (items) |
| --- | --- | --- |
| P01 | I | `test_p01_section_ids_levels_parents_and_titles` (1) |
| P02 | I | `test_p02_heading_pages_lines_and_derived_pages` (1): all rows equal `MINI`; derived pages of all 15 units, `4.1` `(3, 4)`, `front_matter` `(1,)` |
| P03 | I | `test_p03_empty_body_sections_stay_as_ancestors` (1) |
| P04 | I | `test_p04_front_matter_first` (1); `test_p04_headings_only_variant_has_no_front_matter` (1, P21 applies); `test_p04_no_front_matter_when_no_line_precedes_first_heading` (1, added, Gap 2) |
| P05 | I | `test_p05_header_and_footers_removed` (1) |
| P06 | I | `test_p06_numbered_steps_in_body_are_not_headings` (1) |
| P07 | I | `test_p07_lines_byte_equal_including_accents_and_values` (1) |
| P08 | I | `test_p08_every_cleaned_line_belongs_to_one_unit` (1): literal 38-line stream |
| P09 | I | `test_p09_injection_text_stored_verbatim_without_side_effects` (1): `socket.socket`, `socket.create_connection`, `socket.getaddrinfo`, `subprocess.Popen`, `os.system` patched to fail; only `out/index.json` added |
| P10 | U | 15 functions in `test_policy_cleaning.py` (N1–N5, order N2/N3 before N4/N5, single page, exact footer form) |
| P11 | I | `test_p11_provenance_and_result` (1); `test_p11_file_layout_follows_the_approved_format` (1, key order and encoding) |
| P12 | I | `test_p12_same_source_gives_identical_bytes` (1) |
| P13 | I | `test_p13_rebuild_replaces_the_index` (1) |
| P14, P15 | I | `test_p14_p15_encrypted_rejected` (2); `test_p14_empty_password_pdf_is_readable_by_pypdf` (1, precondition) |
| P16 | I | `test_p16_not_pdf` (3) |
| P17 | I | `test_p17_page_without_text` (2): page 3, page 1 |
| P18 | I | `test_p18_outline_missing` (1) |
| P19 | I | `test_p19_outline_mismatch` (2): entry 5 |
| P20 | I | `test_p20_outline_invalid` (4): entries 5, 8, 5, 6 |
| P21 | I | `test_p21_no_retrievable_section` (1) |
| P22 | I | `test_p22_source_missing_and_unreadable` (1) |
| P23 | I | `test_p23_size_and_page_limits` (1): patched 100 bytes and 4 pages fail; exact size and 5 pages pass |
| P24 | I | `test_p24_target_checks` (1) |
| P25 | I | `test_p25_failed_rebuild_keeps_previous_index` (15): every failing variant of P14–P21 |
| P26 | I | `test_p26_replace_failure` (2): initial build and rebuild; temporary file existed in the target directory, was complete and closed when `os.replace` was called, and was removed |
| P27 | I | `test_p27_errors_hold_no_content_or_directory` (1): 19 errors, 14 codes, under `Pasta Técnica Sentinela`; `test_p27_message_format` (1) |
| P28 | I | `test_p28_source_unchanged` (1): SHA-256 and `st_mtime_ns` |
| P29 | I | `test_p29_load_equals_extract_and_mini` (1): PDF deleted before load |
| P30 | I | `test_p30_missing_and_directory` (1) |
| P31 | I | `test_p31_invalid_bytes_json_or_shape` (5): truncated JSON, `\xff\xfe`, array, empty file, BOM |
| P32 | I | `test_p32_format_and_version` (5): `2`, `"1"`, `true`, `1.0` give `index_version_unsupported`; other `format` gives `index_invalid` |
| P33 | I | `test_p33_structure_rejected` (47: the 16 matrix changes plus 31 added); `test_p33_duplicate_json_key_and_non_finite_rejected` (1); `test_p33_rewritten_valid_document_still_loads` (1, control) |
| P34 | I | `test_p34_errors_hold_no_content_or_directory` (1): 55 errors, 4 codes; `test_p34_message_format` (1) |
| P35 | I | `test_p35_index_size_limit` (1): size − 1 fails, exact size loads |

## Test Strategy

- Synthetic PDFs: `policy_kit` lays out `MINI` with `pypdf` only (`add_blank_page`, one `BT … Tj T* … ET` stream per page, Helvetica `/WinAnsiEncoding`, `cp1252` strings, outline via `add_outline_item`, RC4-128 encryption for the two encrypted variants). All PDFs and indexes are written to `tmp_path`.
- No mock of extraction, cleaning, structure or persistence. Patches: `os.replace` (P26, P27, P28), the three size/page constants (P23, P27, P34, P35), network and subprocess probes (P09).
- Oracles: the hand-written `MINI` table, the literal stream of P08, the literal page map, literal codes, entries and pages; `hashlib.sha256` and `len` on the PDF bytes. No production function computes an expected value.
- No CSV, real manual, network, OpenAI or file under `data/local/`.

## Compliance Report

### RED

`uv run --frozen pytest tests/unit/test_policy_cleaning.py tests/integration/test_policy_preparation.py tests/integration/test_policy_index_load.py -q` → exit 2, `3 errors in 0.22s`. Cause, for each of the three files: `ModuleNotFoundError: No module named 'emporio_agent.data.policy_index'` at the module import. This is the expected RED of the plan.

`uv run --frozen pytest -q -rs` at RED → exit 2, interrupted by the same 3 collection errors. With the three new files excluded (`--ignore` on each) → exit 0, `938 passed, 2 skipped`: the new root `tests/conftest.py` does not affect the earlier tests.

### GREEN

First run after implementation: `2 failed, 131 passed` (exit 1), both in the P33 guard (Gap 9, test defect). After the fix: `133 passed` (exit 0).

### Mutations (after GREEN, one at a time, with byte restoration) — R0 run, *superseded by R1 rerun*

Script in the session scratch directory (outside the repository). Each mutation replaced one exact string in `policy_index.py`, ran the three new files, and restored the original bytes.

| ID | Mutation | Result | Killed by |
| --- | --- | --- | --- |
| M01 | default `extract_text()` instead of layout | **survived** (133 passed) | — |
| M02 | section body ends at the next line matching the heading regex | killed (5 failed) | P06, P02, P07, P08, P29 |
| M03a | header kept (N4 off) | killed (16 failed) | P05, P10, P02, P08, … |
| M03b | footer kept (N5 off) | killed (14 failed) | P05, P10, P02, P08, … |
| M04 | footer removed whatever its number | killed (1 failed) | P10 |
| M05 | `pages` from the heading page only | killed (2 failed) | P02, P04 (added test) |
| M06 | empty-body sections dropped | killed (87 failed) | P03, P01, P11, … |
| M07 | timestamp key in the index | killed (82 failed) | P12, P11, P33, … |
| M08 | target written directly, no temporary file or replace | killed (4 failed) | P26, P27, P28 (not P25) |
| M09 | outline title in the error `file_name` | killed (1 failed) | P27 |
| M10 | encrypted file accepted after automatic decryption | killed (4 failed) | P14, P25, P27 |
| M11 | `isinstance(value, int)` (booleans accepted) | killed (6 failed) | P32, P33, P34 |
| M12 | temporary file not removed on replace failure | killed (2 failed) | P26 |
| M13 | `os.fsync` removed | **survived** (133 passed) | — |
| M14 | `source_missing` raised without `from None` | killed (1 failed) | P27 |
| M15 | `front_matter` always emitted | killed (1 failed) | P04 (added test only) |
| M16 | heading cursor never advances | **survived** (133 passed) | — |

M01–M12 are the ten behaviors listed in the plan (M03 split in two); M13–M16 are extra.

Survivors:

- M01: the approved synthetic mechanism writes each line as one `Tj`, so default and layout modes return the same lines. The plan expected P07 to fail; it does not. The difference (O4) exists only in the real Google Docs PDF and is to be checked in slice C. No test or expectation was changed.
- M13: fsync has no observable effect in a test; durability is not tested.
- M16: equivalent on `MINI`, where no heading text repeats; no matrix case has a repeated heading line.

Restoration: SHA-256 of `policy_index.py` before the mutations, after the script's final restore and after a separate `sha256sum` was `53bbbefe13ec6ce9bb006a91a1f2031ab8167ee3aea492389e638ad6a7d42d10` each time. After the mutations, `ruff check --fix .` reordered one import block in `tests/integration/test_policy_index_load.py` (an earlier `ruff` run from the `tests/` directory had sorted it differently); no production file changed. All gates below ran after that.

### Gates (R0) — *superseded by R1*

| Command | Exit | Result |
| --- | --- | --- |
| `uv run --frozen pytest tests/unit/test_policy_cleaning.py tests/integration/test_policy_preparation.py tests/integration/test_policy_index_load.py -q` | 0 | 133 passed |
| `uv run --frozen pytest -q -rs` | 0 | 1071 passed, 2 skipped (938 earlier + 133 new; skips: the two known Windows cases at `test_readonly_connection.py:599` and `:612`); 0 failed |
| `uv run --frozen ruff check .` | 0 | All checks passed |
| `uv run --frozen ruff format --check .` | 0 | all files already formatted |

Ruff was repeated after this report was written (see the final response of the task).

*Superseded by R1:* the R0 `ruff check .` result was wrong. It came from a stale entry in `.ruff_cache`; `ruff check --no-cache .` reported I001 in `tests/integration/test_policy_preparation.py` on the same R0 bytes. R0 also asserted `__suppress_context__ or __context__ is None`, which accepted the `encrypted` error without `from None`, and P35 did not cover preparation above the index limit.

### Files

Created:

- `src/emporio_agent/data/policy_index.py`
- `tests/conftest.py`
- `tests/unit/test_policy_cleaning.py`
- `tests/integration/test_policy_preparation.py`
- `tests/integration/test_policy_index_load.py`
- `docs/validation/04-01-policy-preparation.md` (this file)

Unchanged: every existing file, including `tests/integration/conftest.py`, catalog, pricing, CSV preparation, readonly, `pyproject.toml`, `uv.lock`, README, `.gitignore`, the approved spec and plan.

Git state: branch `feature/data-preparation`, HEAD `1b358ef`, `git diff --stat` empty (no tracked file modified); untracked: the two approved documents and the six files above. Nothing staged or committed.

### Limits

- No claim that policies are retrievable: retrieval is slice B. No claim about the real manual: it was not prepared or read in this task (slice C).
- Synthetic PDFs are produced by `pypdf`, not by the real producer; layout-mode differences of the real PDF (O4, O5) are not covered here (see M01).
- `extraction_failed`, an unreadable outline tree and the in-memory validation failure of `prepare_policy_index` have no test (Gap 5). *R1: the size-limit failure is now tested.*
- Atomic replacement protects against handled failures only. No guarantee after an OS crash or power loss, and no guarantee that the rename itself is durable (the directory is not fsynced), same limit as slice 02.
- `pypdf` emits warnings through its logger on malformed files; this slice does not configure logging (spec P-12).
- Text produced by another `pypdf` version may differ; only 6.21.4 was exercised.

R0 status (superseded by R1): slice A GREEN. Slices B and C not started. Stopped for review.

## Review 04-02-R1

Date: 2026-11-03. Starting state: same branch and HEAD `1b358ef`, nothing staged; R0 files untracked with SHA-256 `policy_index.py` `53bbbefe…7d42d10`, `test_policy_preparation.py` `d2dc2626…1a2b7`, `test_policy_index_load.py` `07a97732…62e51`. Approved spec and plan unchanged (`4bf210fe…e209e215`, `2f546979…8a666`). Sources: spec P-05, P-06, P-12. No contract changed.

### Spec Summary (R1)

1. `prepare_policy_index` no longer publishes an index larger than `MAX_INDEX_BYTES`. The byte limit moved into the shared validation `_decode`, which both `load_policy_index` and `prepare_policy_index` call. In preparation it runs on the encoded bytes before `tempfile.mkstemp`, so a failure leaves no temporary file and does not touch the target. Code reused: `PolicyIndexUnavailableError("index_invalid")`, the same path as the other in-memory validation failures (R0 Gap 5). No new code.
2. Every `raise` of `PolicyPreparationError` and `PolicyIndexUnavailableError` uses `from None` (23 of 23, checked with an AST walk), including the 11 raised with no active `except`. `__context__`, tracebacks and `sys.excepthook` are not touched.
3. I001 fixed in `tests/integration/test_policy_preparation.py`: `from emporio_agent.data import policy_index as pi` moved into the first-party block; `PolicyIndexUnavailableError` added to the existing import because the new tests use it.

### Gap Analysis (R1)

- Reviewer reproduction confirmed by the new tests: with `MAX_INDEX_BYTES = 100`, R0 `prepare_policy_index` returned success and published the 4,805-byte index (`DID NOT RAISE` in RED).
- Root cause of the missed I001: R0 gates ran `ruff check .` with the default cache. A stale entry in `.ruff_cache` (ignored by Git) reported "All checks passed". On the same bytes `ruff check --no-cache .` and `ruff check --isolated --select I` report I001. All R1 Ruff gates use `--no-cache`.
- Root cause of the missed `from None`: the R0 assertion `__suppress_context__ or __context__ is None` was true for raises without an active exception. R1 asserts `__suppress_context__ is True` in P27 (19 preparation errors, 14 codes) and P34 (55 index errors, 4 codes), plus the four R1 tests.
- Test defect found and fixed before GREEN: the first version of `_index_size` measured a reference index prepared from `reference.pdf`. That base name is 7 characters shorter than `manual-sintetico.pdf`, so the measured size was 4,798 instead of 4,805. The helper now prepares the reference from a PDF with the same base name, in its own directory. No expected value changed.
- Accepted limit: with the size check before publication, a failure caused by `MAX_INDEX_BYTES` raises `PolicyIndexUnavailableError` from `prepare_policy_index`, not `PolicyPreparationError`. This keeps the R0 behavior for in-memory validation failures, as the review requested. The future CLI must handle both types for preparation.

### Implementation Plan (R1, executed)

1. Checked the starting state and recorded hashes. Reproduced I001 with `--no-cache`.
2. Wrote regressions and stronger assertions; fixed the import block.
3. RED, then the fix in `policy_index.py`, GREEN, the full mutation rerun, gates without cache, this section, Ruff again.

### Test Checklist (R1)

New tests in `tests/integration/test_policy_preparation.py` (4 items). Each one uses the real `policy_kit` PDF, real JSON and real files, and lowers only `MAX_INDEX_BYTES`:

| Test | Checks |
| --- | --- |
| `test_r1_first_build_above_index_limit_publishes_nothing` | limit 100: `index_invalid`, `file_name == "index.json"`, `__cause__ is None`, `__suppress_context__ is True`; target absent; output directory empty; no `*.tmp` |
| `test_r1_rebuild_above_index_limit_keeps_previous_bytes` | limit 100 with `b"previous index bytes"` in the target: same error; previous bytes kept; one file in the directory; no `*.tmp` |
| `test_r1_index_exactly_at_limit_is_published_and_loadable` | limit = size of the real index (measured with `stat` on a reference preparation): published, `st_size` equals the limit, `index_sha256` matches `hashlib`, `load_policy_index` succeeds under the same limit; no `*.tmp` |
| `test_r1_index_one_byte_above_limit_is_rejected` | limit = size − 1: same error; target absent; only the source in the directory |

Strengthened: `test_p27_errors_hold_no_content_or_directory` and `test_p34_errors_hold_no_content_or_directory` now assert `__suppress_context__ is True` (replacing `or __context__ is None`). `test_policy_index_load.py` changed only on that line. No other expected value changed. Fixtures, `tests/conftest.py` and `tests/unit/test_policy_cleaning.py` unchanged.

Slice A total: 137 items (unit 15, preparation 58, load 64).

### Test Strategy (R1)

Same as R0. Patching is limited to `MAX_INDEX_BYTES` with `monkeypatch`. No simulated persistence, no stubbed loader result. The size oracle is the filesystem (`stat`) on a real prepared file, never a production function.

### Compliance Report (R1)

RED (`pytest tests/integration/test_policy_preparation.py tests/integration/test_policy_index_load.py -q -p no:cacheprovider -k "r1_ or p27_errors or p34_errors"`, exit 1): `5 failed, 1 passed` after the helper fix:

- `test_r1_first_build_…`, `test_r1_rebuild_…`, `test_r1_index_one_byte_above_…`: `Failed: DID NOT RAISE PolicyIndexUnavailableError`.
- `test_p27_errors_…`: `AssertionError: assert False is True`, where `False = PolicyPreparationError('manual-sintetico.pdf: encrypted').__suppress_context__`.
- `test_p34_errors_…`: same assertion, `PolicyIndexUnavailableError('index.json: index_invalid').__suppress_context__` (the size-limit raise in `load_policy_index`).
- `test_r1_index_exactly_at_limit_…` passed in RED, as expected: R0 already published an index at the limit.

GREEN: the same selection gave `6 passed`; with P35 added, `7 passed` (exit 0). Slice A gave `137 passed` (exit 0).

Mutation rerun on the final module (SHA-256 `558442bc…c1b75a`), one at a time, original bytes restored after each:

| ID | Mutation | Result | Killed by |
| --- | --- | --- | --- |
| M01–M16 | as in R0 | same outcomes: M01, M13, M16 survive; all others killed. Counts are on 137 items (for example M06 89 failed, M07 84 failed) | as in R0 |
| M17 | size check removed from `_decode` | killed (4 failed) | P35, R1 first build, R1 rebuild, R1 one byte above |
| M18 | `encrypted` raised without `from None` | killed (1 failed) | P27 |
| M19 | `prepare_policy_index` skips the in-memory validation | killed (3 failed) | the three R1 tests that expect rejection |
| M20 | size error raised without `from None` | killed (4 failed) | P34, the three R1 tests that expect rejection |

Restoration: the script's final check and a separate `sha256sum` both gave `558442bcbf1e4ba91f6880f95c567a3571a80c64ebe7be80e814c423e9b1b75a`. The R0 module was rebuilt in the scratch directory by reversing the R1 edits. Its SHA-256 equals the recorded R0 hash `53bbbefe…7d42d10`, so the R1 diff below is exact. The R0 test files were checked the same way (`d2dc2626…`, `07a97732…`).

Gates (R1, current):

| Command | Exit | Result |
| --- | --- | --- |
| `uv run --frozen pytest tests/integration/test_policy_preparation.py tests/integration/test_policy_index_load.py -q -p no:cacheprovider -k "r1_ or p27_errors or p34_errors or p35"` | 0 | 7 passed |
| `uv run --frozen pytest tests/unit/test_policy_cleaning.py tests/integration/test_policy_preparation.py tests/integration/test_policy_index_load.py -q -p no:cacheprovider` | 0 | 137 passed |
| `uv run --frozen pytest -q -rs -p no:cacheprovider` | 0 | 1075 passed, 2 skipped (938 earlier + 137 slice A; the two known Windows skips at `test_readonly_connection.py:599` and `:612`); 0 failed |
| `uv run --frozen ruff check --no-cache .` | 0 | All checks passed |
| `uv run --frozen ruff format --check --no-cache .` | 0 | 60 files already formatted |

R1 diff (exact, against the rebuilt R0 bytes):

- `src/emporio_agent/data/policy_index.py`: `from None` added to 11 raises (`source_too_large`, the `outline_invalid` helper, `encrypted`, `page_count_invalid`, `page_text_missing`, `outline_missing`, `outline_mismatch`, `no_retrievable_section`, `target_directory_missing`, `target_is_source`, and the size check). The size check moved from `load_policy_index` to the start of `_decode`. A comment was added at the `_decode` call in `prepare_policy_index`, and the docstring now states the limit and the error.
- `tests/integration/test_policy_preparation.py`: import block reordered (plus `PolicyIndexUnavailableError`); one assertion strengthened; 4 tests and 2 helpers appended.
- `tests/integration/test_policy_index_load.py`: one assertion strengthened.
- This report: R1 section added; R0 results marked superseded.

Files changed in R1: the four files allowed by the review. Git: HEAD `1b358ef`, nothing staged or committed, `git diff --stat` empty; untracked: the two approved documents and the six slice A files.

Limits (current): as in R0. Also: preparation reports the index size limit and other in-memory validation failures as `PolicyIndexUnavailableError("index_invalid")`, not as `PolicyPreparationError`; the file-name path of that validation is still untested. There is still no guarantee after an OS crash or power loss, and the durability of the rename is not guaranteed.

Status: slice A R1 GREEN. Slices B and C not started. Stopped for review.
