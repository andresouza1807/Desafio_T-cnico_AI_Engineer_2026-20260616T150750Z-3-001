# Slice 04, part C validation: real manual and limits

Date: 2026-11-03. Baseline: branch `feature/data-preparation`, HEAD `b1dce15` (`feat(policies): add section retrieval and notices`), clean working tree. `uv run --frozen pytest -q -rs` before any change: exit 0, `1172 passed, 2 skipped` (the two known Windows skips at `tests/integration/test_readonly_connection.py:599` and `:612`). No divergence from the expected baseline. No commit, push, PR or merge.
Contract: `docs/specs/04-policies.md` and `docs/plans/04-policies.md`, sections "Slice C", "Slice C — real manual and limits" (P63–P68) and "Real-manual validation (V01–V24)". Neither document was changed. Reports read: `04-01-policy-preparation.md` (with R1) and `04-02-policy-retrieval.md`.
Platform: Windows 11 Pro 10.0.26200, Python 3.14.3, `pypdf` 6.21.4 (locked), uv 0.12.5.

Hashes of protected files after this task (SHA-256). They equal the values recorded by the slice A R1 and slice B reports:

| File | SHA-256 |
| --- | --- |
| `src/emporio_agent/data/policy_index.py` | `558442bc…e9b1b75a` |
| `src/emporio_agent/services/policies.py` | `a24d0d4f…bd24891d` |
| `docs/specs/04-policies.md` | `4bf210fe…f209e215` |
| `docs/plans/04-policies.md` | `2f546979…18f7a666` |
| `tests/conftest.py` | `a5192c25…76c53cec` |

## Spec Summary

Slice C adds evaluation code and one test file. No production module changed.

- `evals/policies_real_manual.py`: opt-in script. Arguments `--pdf` and `--index`, both required. Nothing runs on import (`if __name__ == "__main__"`). It checks the source SHA-256 before preparation, prepares the manual twice and compares the bytes, checks the P63 section map, runs V01–V24 and prints metadata only.
- `tests/integration/test_policy_limits.py`: P68, 4 test functions.
- This report.

## Gap Analysis

Decisions approved in review B were applied as written. The script checks them on every V row:

- `no_handoff_integration` refers to the four fixed IDs `("4.2", "7", "7.3", "10")`, not only to the returned sections.
- Service notices use fixed messages.
- `more_sections_matched` refers to the omitted IDs.
- `no_matching_section`, `no_searchable_terms` and `curated_notes_unavailable` have empty `section_ids`.
- Notice references are not the sections returned.

Interpretations used in the script, for review:

1. "Notices expected" is a required subset. Additional notice codes do not fail a row. They are printed and listed below. An empty expected field forbids nothing, except `curated_notes_unavailable`, which P65 forbids on every row.
2. "Must include" requires every listed ID. V17 and V20 require at least one of the listed alternatives.
3. Extra IDs are returned IDs outside "must include" and the alternatives. They never fail a row. They are always reported for relevance review.
4. The page constraints of V08 (`5.2`) and V19 (`9`) apply to the named section.
5. Checks added beyond the matrix, all derived from the contract: notice references (above), at most three sections, no duplicate notice code, `more_sections_matched` present exactly when `omitted_section_ids` is not empty, one fixed message per code over the whole run, and no notice message that contains the query.
6. Destination rule (task brief): the index must be new. It must be under `data/local/` of the repository (also confirmed by `git check-ignore`) or under the system temporary directory. Any other path is rejected with exit 2, and so is an existing file. The second preparation goes to a temporary directory that the script creates and removes.
7. `pypdf` log records go to a counting handler and are not printed. They could describe the PDF. The run reported 0 records.

No V row diverged, so no correction is proposed. The expected columns were not edited.

## Implementation Plan (executed)

1. Baseline: branch, HEAD `b1dce15`, clean tree, `1172 passed, 2 skipped`.
2. Read the spec, the plan (slice C, P63–P68, V01–V24), the A/R1 and B reports, the error classes of both modules, `tests/conftest.py`, and the P27, P34 and P62 tests.
3. P68 derived from P-12 and written before any run.
4. Script written. Its rejection paths were tried on a synthetic PDF: tracked directory `docs/`, existing tracked file, a directory outside the temporary directory, an existing file, missing `data/local/` directory, wrong hash (exit 3, no file written), missing PDF, missing `--index`. Import without execution was confirmed (no output, 24 questions loaded).
5. The manual was located in the data directory named by the spec ("Inspected source"). Identity confirmed before the criteria were applied: base name `políticas_da_loja.pdf`, 180,519 bytes, SHA-256 `b7527f96896929f4ba929de117651c6717e25b7a3b92b34bc54d5ba32f6156f7`, 8 pages (index `page_count`). The PDF hash was the same before and after the run.
6. The script was run on the manual, with the index in a session temporary directory outside the repository.
7. Pages 1–8 were rendered to PNG in a temporary directory outside the repository and checked visually against the index (details below).
8. Gates run, this report written, Ruff run again.

## Test Checklist

### P68 (I, `tests/integration/test_policy_limits.py`, 4 items)

All material is under `tmp_path / "Desafio Técnico AI Engineer"`. The synthetic PDF is built by `policy_kit`, most cases with the `injection_title` variant (sentinel `SENTINELA-7Q`).

| Test | Cases | Assertions |
| --- | --- | --- |
| `test_p68_success_under_spaced_accented_directory_keeps_base_names` | prepare and load inside the directory | `file_name` is the base name in the result and the index; the index file holds no directory text; only the two expected files exist |
| `test_p68_missing_and_invalid_index` | missing index; missing parent directory; directory as index; JSON object with the sentinel; non-JSON with the sentinel and the directory | codes `index_missing`, `index_missing`, `index_unreadable`, `index_invalid`, `index_invalid`; `str()` is `index.json: <code>` |
| `test_p68_invalid_sources_and_targets` | missing source; directory as source; non-PDF bytes with the sentinel and the directory; truncated PDF; `outline_mismatch` with the sentinel; missing target directory; `os.replace` raising `OSError` whose message holds the sentinel and the directory | codes `source_missing`, `source_unreadable`, `not_pdf`, `not_pdf`, `outline_mismatch` (entry 5), `target_directory_missing`, `write_failed`; no index or temporary file left |
| `test_p68_query_errors_hold_no_query_text` | query of more than 4,000 characters that holds the sentinel and the directory; bytes; blank | codes `too_long`, `invalid_type`, `blank`; `field == "query"` |

Each error is checked on `str`, `repr`, `args`, the documented attributes and `traceback.format_exception_only`. It must contain neither the directory (full path, parent, or the words `Desafio`, `Técnico`/`Tecnico`, `Engineer`), nor the sentinel, nor the original OS message. In addition, `__cause__ is None`, `__suppress_context__ is True` and `args == (str(error),)` must hold.

P68 passed on its first run. No RED was produced, and none was fabricated. Reason: the P-12 sanitization (`from None`, base names, fixed codes) already exists from slices A and B (P27, P34, P42). P68 adds the directory with spaces and accents, the OS message, and the query errors. To show that the detector is not vacuous, it was run in a scratch script (not committed) on errors built by hand. It reported 18 hits for an `OSError` holding the directory path, 4 hits for the sentinel, 4 hits for the OS message, and 0 hits for a clean message. No production code was changed.

### P63–P67 (M, script on the supplied manual)

| Case | Result |
| --- | --- |
| P63 | Two preparations byte-identical (`byte_identical=True`, equal index SHA-256 `76ec1010…6327db8e`). A second, separate run also gave a byte-identical index and identical output. `page_count` 8, SHA-256 equals the reviewed hash, base name equal. 29 units, 26 retrievable, empty bodies `5`, `6`, `8`. All 29 IDs, their order and their pages equal the literal map of the plan. `pypdf_version` 6.21.4, `extraction_rules` `layout-v1`, `format_version` 1. |
| P64 | 24 of 24 rows passed the automatic comparison (table below). |
| P65 | `curated_notes_unavailable` absent on all 24 rows. |
| P66 | `git status --porcelain`: only `?? evals/` and `?? tests/integration/test_policy_limits.py` (this report is added afterwards). `git ls-files` lists no PDF, no index and no `data/local/` file (count 0). The real index was written outside the repository. `git check-ignore -v --no-index data/local/policy-index.json` gives `.gitignore:19:data/local/`. Nothing staged. |
| P67 | V24 query of exactly 4,000 characters accepted, status `ok`. |

## Real-manual validation (V01–V24)

Columns:

- "Observed": returned sections as `ID[pages](selected_by)`, in result order.
- "Extra": returned IDs outside "must include" and the alternatives.
- "Auto": result of the script.
- "Visual": check by the AgentDev (method below).
- Codes: `cnd` `contact_numbers_differ`, `esd` `email_spelling_differs`, `ird` `installment_rules_differ`, `fstg` `free_shipping_threshold_gap`, `wncr` `withdrawal_needs_channel_and_receipt_date`, `nhc` `no_holiday_calendar`, `nhi` `no_handoff_integration`, `msm` `more_sections_matched`, `nms` `no_matching_section`, `nst` `no_searchable_terms`.

| ID | Expected | Observed | Omitted | Extra | Notices observed | Auto | Visual | Findings |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| V01 | `1.2`; `cnd`, `esd` | `1.2[2](match)`, `9[7-8](match)`, `front_matter[1](match)` | — | `9`, `front_matter` | `cnd`, `esd` | pass | done | F1 |
| V02 | `2`; `nhc` | `2[2](match)` | — | — | `nhc` | pass | done | — |
| V03 | `2`; `nhc` | `2[2](match)` | — | — | `nhc` | pass | done | — |
| V04 | `3`, `3.1`; `ird` | `3[3](match)`, `3.1[3](companion)` | — | — | `ird` | pass | done | F9 |
| V05 | `3`, `6.2`; `ird` | `6.2[6](match)`, `3[3](match)`, `3.1[3](companion)` | — | `3.1` | `ird` | pass | done | — |
| V06 | `3.1`, `3`; `ird` | `3.1[3](match)`, `3[3](companion)` | — | — | `ird` | pass | done | F9 |
| V07 | `5.1`; `fstg` | `5.1[4](match)` | — | — | `fstg` | pass | done | — |
| V08 | `5.2` pages (4, 5) | `5.2[4-5](match)` | — | — | — | pass | done | — |
| V09 | `5.3` | `5.3[5](match)`, `7.2[6-7](match)`, `9[7-8](match)` | — | `7.2`, `9` | — | pass | done | F3 |
| V10 | `4.3`, `4.4` | `4.3[4](match)`, `4.4[4](companion)` | — | — | — | pass | done | — |
| V11 | `4.1`; `wncr` | `4.1[4](match)` | — | — | `wncr` | pass | done | — |
| V12 | `4.2`, `4.4`; `nhi` | `4.2[4](match)`, `4.4[4](companion)` | — | — | `nhi` (refs `4.2`, `7`, `7.3`, `10`) | pass | done | F8 |
| V13 | `8.1`, `8.2`, `8.3` | `8.1[7](match)`, `8.3[7](match)`, `8.2[7](match)` | — | — | — | pass | done | F7 |
| V14 | `1.2`, `7`; `cnd` | `1.2[2](match)`, `7[6](companion)`, `5.1[4](match)` | `9` | `5.1` | `cnd`, `esd`, `fstg`, `nhi`, `msm` (refs `9`) | pass | done | F2 |
| V15 | `7`, `1.2`; `cnd` | `7[6](match)`, `1.2[2](companion)` | — | — | `cnd`, `esd`, `nhi` | pass | done | F6 |
| V16 | `1.2`, `front_matter`; `esd` | `1.2[2](match)`, `front_matter[1](companion)`, `5.3[5](match)` | `9` | `5.3` | `cnd`, `esd`, `msm` (refs `9`) | pass | done | F4 |
| V17 | one of `7`, `7.1`, `10`; `nhi` | `7.1[6](match)`, `7[6](match)`, `10[8](match)` | — | — | `cnd`, `nhc`, `nhi` | pass | done | F6 |
| V18 | `7.3`; `nhi` | `7.2[6-7](match)`, `7.3[7](match)` | — | `7.2` | `nhi` | pass | done | F5 |
| V19 | `9` pages (7, 8); not `1.2` | `9[7-8](match)` | — | — | — | pass | done | — |
| V20 | one of `1`, `7.1` | `7.1[6](match)`, `8.3[7](match)`, `1[2](match)` | — | `8.3` | `nhc` | pass | done | F5 |
| V21 | `not_found`; `nms` | `not_found`, no section | — | — | `nms` | pass | n/a (no section) | — |
| V22 | `not_found`; `nst` | `not_found`, no section | — | — | `nst` | pass | n/a (no section) | — |
| V23 | `not_found`; `nms`; no file written | `not_found`, no section; directory listings of the index directory and the working directory unchanged | — | — | `nms` | pass | n/a (no section) | — |
| V24 | `8.1` | `8.1[7](match)`, `8.3[7](match)`, `8.2[7](match)` | — | `8.3`, `8.2` | — | pass | done | F7 |

Automatic summary: `v_rows_passed=24 v_rows_failed=0 p63_ok=True`. The notice references matched the approved values on every row. Every code had one fixed message. Script exit code 0.

### Visual check performed by the AgentDev

- Method: the 8 pages were rendered to PNG at 1300 px width with the Windows built-in PDF renderer (`Windows.Data.Pdf`, called from Windows PowerShell). No dependency was installed. The renderer is independent of `pypdf`. Output went to a session temporary directory outside the repository. The AgentDev viewed every page. It compared each cited section with the index lines of a local, unpublished index: heading, page, first and last line, unit boundaries, tables and exceptions. No rendered page or text was copied into the repository.
- All 29 units start at the printed heading and end before the next heading. Body pages match the page footers.
- Running header and `Página N` footers are absent from the index (O6).
- `4` has its own two-line body on page 4, so it is retrievable. `5`, `6` and `8` have only a heading (O8).
- `5.2`: heading at the bottom of page 4, body on page 5, pages `(4, 5)`. `7.2`: steps 1–2 on page 6, steps 3–5 on page 7, inside `7.2`, with no false headings (O3). `9`: pages 7–8, with the deletion line on page 8.
- Tables (`1.2`, `2`, `3`, `5.2`): one line per row, columns joined by single spaces. The wrapped PIX cell of section 3 is a separate line (O5).
- Exceptions kept whole: `3.1` (the "exceto abaixo de" clause wraps over two lines, both kept), `4.1` (four conditions), `4.2` (exclusions), `4.4` (three items), `5.1` (above/below R$ 500,00), `8.3` (four exclusions).
- `10` keeps the two trailer lines (O9).
- Both e-mail spellings are byte-preserved. The accented `ó` (U+00F3) appears in `1.2` only. Non-ASCII characters in the index: Portuguese letters, `º`, en dash, em dash and bullet (O10). There is no replacement character.
- Facts behind open questions Q2/Q3 seen as described in the spec: `4.2` counts from the purchase, `8.1` counts from receipt, and `4.3` gives 7 days with no reference event.

Reviewer approval: **pending**. No human check by the reviewer is claimed. The plan asks to record the reviewer and the date. Both fields stay empty until the review.

### Findings (relevance and notices; none is an automatic divergence)

Under the plan, any returned section that matched lexically is allowed. These points are for relevance review only. No ranking, synonym, notice or expected value was changed.

- **F1 (V01).** `front_matter` matched on the cover address (relevant: the cover repeats the address). `9` matched because its body lists the address among the personal data collected. It is of low relevance to the question.
- **F2 (V14).** `5.1` matched through the body word for telephone (the courier calls the customer before delivery). Its presence adds `free_shipping_threshold_gap`, which is unrelated to a phone question. `9` was omitted with `more_sections_matched`. `email_spelling_differs` and `no_handoff_integration` come from `1.2` and `7`, as the registry requires.
- **F3 (V09).** `7.2` and `9` matched through the tracking word in their bodies. They are of low relevance next to `5.3`.
- **F4 (V16).** `5.3` matched through the e-mail word in its body. `9` was omitted with `more_sections_matched`.
- **F5 (V18, V20).** `7.2` (V18) matched through the complaint word in step 2. `8.3` (V20) matched through the strings item in the wear list. Both are of low relevance. In V20, `1` and `7.1`, which state the store sells no accessories, are both returned.
- **F6 (V15, V17).** The notices triggered by `7` and `7.1` (`contact_numbers_differ`, `no_holiday_calendar`) appear on handoff questions. This follows the trigger table.
- **F7 (V13, V24).** Order `8.1`, `8.3`, `8.2`. `8.3` is inserted after `8.1` by the companion rule, but it matched, so it keeps `selected_by = "match"` (P-10 step 5).
- **F8 (V12, V14, V15, V17, V18).** `no_handoff_integration` refers to `4.2`, `7`, `7.3` and `10`, even when only one of them is returned. This is the approved review B decision: references are not returned sections.
- **F9 (V04, V06).** The companion `3` ↔ `3.1` always returns both installment sources with `installment_rules_differ`, as A7 requires.

## Test Strategy

- P68 runs the real `prepare_policy_index`, `load_policy_index` and `search_policies` on PDFs built by `policy_kit`, with real files in `tmp_path`. The only patch is `os.replace`, for failure injection. There is no mock of extraction, loading or search.
- The real manual is evaluated by the opt-in script, outside the offline suite (`testpaths = ["tests"]`; the file name does not start with `test_`). The expected values are the literal plan rows, written by hand in the script. No production function computes an expected value.
- The automatic comparison checks IDs, pages, status, notices and references. It does not judge the quality of an answer. The visual check above covers section text and boundaries for this one manual.
- No OpenAI, network, new dependency, change to the PDF, or private CSV. The script makes one local `git check-ignore` call, and only for a destination inside the repository. The evaluated run used a temporary directory, so this call did not run there. It ran once during the rejection checks.

## Compliance Report

### Commands

| Command | Exit | Result |
| --- | --- | --- |
| `uv run --frozen pytest -q -rs` (baseline, before changes) | 0 | 1172 passed, 2 skipped |
| `uv run --frozen pytest tests/integration/test_policy_limits.py -q` | 0 | 4 passed |
| `uv run --frozen pytest -q -rs` | 0 | 1176 passed, 2 skipped (1172 earlier + 4 new; skips: `test_readonly_connection.py:599` and `:612`); 0 failed |
| `uv run --frozen ruff check --no-cache .` | 0 | All checks passed |
| `uv run --frozen ruff format --check --no-cache .` | 0 | 66 files already formatted |
| `uv run --frozen python evals/policies_real_manual.py --pdf <manual> --index <temporary directory>/policy-index.json` | 0 | P63 ok; 24 passed, 0 failed; 0 `pypdf` log records |
| same, second run to a new temporary path | 0 | output identical to the first run; index byte-identical |
| same, with the first index path again | 2 | `rejected: index_exists` (no overwrite) |
| rejection checks on a synthetic PDF (tracked directory, tracked file, non-temporary directory, existing file, missing `data/local/`, wrong hash, missing PDF, missing argument) | 2 / 3 | rejected with a code; wrong hash stopped before preparation, no file written |

The script output was searched for user directory names, the data folder name, contact numbers and the e-mail prefix: 0 matches. The output holds IDs, pages, `selected_by`, notice codes, counts and pass/fail only.

### Files

Created:

- `evals/policies_real_manual.py`
- `tests/integration/test_policy_limits.py`
- `docs/validation/04-03-policy-real-manual.md` (this file)

Unchanged: production modules (hashes above), earlier tests, fixtures, `tests/conftest.py`, spec, plan, README, `pyproject.toml`, `uv.lock` and `.gitignore`. `git diff --stat` is empty. Nothing staged or committed. No PDF, index or rendered page is in the repository.

### Limits

- The real manual was evaluated with 24 questions. This is not a statistical evaluation of retrieval quality.
- The visual check was done by the AgentDev on one rendering (`Windows.Data.Pdf`). It does not replace the reviewer's check, which is pending.
- The automatic comparison does not judge relevance beyond the matrix. Extra sections (F1–F5) are allowed by the plan and reported for review.
- Results hold for this PDF (SHA-256 above) and `pypdf` 6.21.4 only. Compatibility across other versions was not tested.
- The prompt-injection row (V23) and P68 show that this code returns `not_found` without writing files and that errors carry no content or directory. They show nothing about how a future model behaves. There is no claim of absolute security.
- There is still no tool, CLI or model integration.

Status: slice C submitted for review. Not approved. Next slice not started. Stopped for review.
