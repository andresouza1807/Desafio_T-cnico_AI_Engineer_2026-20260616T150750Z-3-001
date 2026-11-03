# Slice 04, part B validation: policy retrieval, synonyms, references and notices

Date: 2026-11-03. Baseline: branch `feature/data-preparation`, HEAD `781d748` (`feat(policies): prepare and load local PDF index`), clean working tree. `uv run --frozen pytest -q -rs` before any change: exit 0, `1075 passed, 2 skipped` (the two known Windows skips at `tests/integration/test_readonly_connection.py:599` and `:612`). No divergence from the expected baseline. No commit, push, PR or merge.
Contract: `docs/specs/04-policies.md` (decisions P-07–P-11, P-13, P-15, "Service Contract") and `docs/plans/04-policies.md`, section "Slice B" and matrix rows P36–P62. Neither document was changed.
Platform: Windows 11 Pro 10.0.26200, Python 3.14, `pypdf` 6.21.4 (locked).

Hashes before and after this task (SHA-256):

| File | Before | After |
| --- | --- | --- |
| `src/emporio_agent/data/policy_index.py` | `558442bc…e9b1b75a` (equals the slice A R1 hash) | unchanged |
| `docs/specs/04-policies.md` | `4bf210fe…f209e215` | unchanged |
| `docs/plans/04-policies.md` | `2f546979…18f7a666` | unchanged |
| `tests/integration/conftest.py` | `cf22b3cf…56efdfb5` | unchanged |
| `tests/conftest.py` | `3ac3a8c4…41ee1526` | `a5192c25…76c53cec` (append only, 126 lines added, 0 removed) |

## Spec Summary

New module `src/emporio_agent/services/policies.py` (slice B only):

- Constants `MAX_QUERY_LENGTH` (4000), `MAX_RESULTS` (3); type `NoticeCode` (the eleven codes of the spec, in the order of the two notice tables of P-11).
- Frozen dataclasses `CompanionRule`, `NoticeRule`, `CuratedNotes`, `PolicySection`, `PolicyNotice`, `PolicySearchResult`, with the fields of the spec.
- Errors `PolicyInputError(ValueError)` (`field`, `code`; `str()` is `query: <code>`; `args == (str(self),)`) and `PolicyDataError` (`reason`; `str()` is the reason). Both raised `from None`. Neither holds query or index text.
- `policy_terms` (P-08): `normalize_search_text` from `emporio_agent.services.catalog`, reused unchanged; tokens `[0-9a-z]+`; stemming S1–S6 (digit-only tokens unchanged); stopwords of the spec, stemmed with the same rules; duplicates removed, first kept.
- `concept_of` (P-09): the 20 synonym groups of the spec, written as in the spec and folded and stemmed at import; a token matching `^\d{1,2}x$` maps to `parcelamento`; any other token is its own key. Group overlap raises at import.
- `search_policies(index, query, *, notes=REVIEWED_MANUAL_NOTES)` (P-07, P-10, P-11): validates the query before any access to `index`; computes coverage and score per retrievable unit (weight 3 own title, 1 body or ancestor title); keeps the highest coverage, then `2 × score ≥ top`; orders by score, level descending, document order; inserts companions after each unit when the notes hash equals `index.source.sha256` and the concept condition holds; returns the first three whole sections; lists the rest in `omitted_section_ids`; emits curated and service notices in spec order, each code at most once.
- `REVIEWED_MANUAL_NOTES`: bound to the literal `b7527f96896929f4ba929de117651c6717e25b7a3b92b34bc54d5ba32f6156f7`, with the 10 companion rules and 7 notice rules of P-11 and their fixed messages. The real PDF was not opened.

`search_policies` reads only the in-memory `PolicyIndex`. It opens no file or PDF, reads no environment variable, does not log, and makes no network call.

Not implemented: tool schema, Pydantic, JSON serialization, CLI, OpenAI, agent, evaluation of the real manual (slice C).

## Gap Analysis

Decisions P-07–P-15 were applied unchanged. No defect was found in `policy_index.py` and it was not changed. Nothing blocked the work. These points were resolved without changing the contract or an expected value of the plan. They are listed for review:

1. **Stemming "first matching rule".** A rule matches when its suffix and its condition both hold. If the condition fails, the next rule is tried. Example: `mais` (S4 suffix, length 4) falls to S6 and gives `mai`. The other reading (stop at the first suffix, even when its condition fails) would leave `mais` unchanged. No matrix case tells the two readings apart: `mais` is a stopword and is stemmed the same way on both sides. `pais` would give `pai` under this reading.
2. **`only_for_concepts`.** The companion applies when the set is empty, or when it shares at least one concept with the query.
3. **`no_handoff_integration` references.** The spec says "the triggering sections". `NoticeRule.section_ids` is a static tuple, so for `REVIEWED_MANUAL_NOTES` the tuple holds all four trigger IDs `("4.2", "7", "7.3", "10")`. The tuple is not filtered to the sections returned. In `MINI_NOTES` the trigger and the reference are both `("7",)`, so the synthetic tests cannot tell the two readings apart. **Needs reviewer confirmation.**
4. **Service notices.** The spec defines when they are emitted but gives no message text. This module uses fixed English messages that never contain query or index text. `section_ids` is `omitted_section_ids` for `more_sections_matched` and `()` for the other three. P45 and P54 assert these two choices. **Needs reviewer confirmation.**
5. **When `notes_mismatch` is checked.** The check runs on every call where the hashes match: after query validation and before ranking, so also on `not_found` paths (test `test_p55_mismatch_also_on_not_found`). It is skipped when the hashes differ, so a broken registry for another manual is ignored (`test_p54_other_sha_ignores_broken_registry`). References from companions (trigger and companion) and from notices (triggers and `section_ids`) are all checked.
6. **Notice order.** The order is fixed by `NoticeCode`, which follows the two tables of the spec. It does not depend on the order of the rules in the registry. For each code, the first triggered rule wins.
7. **`matched_terms`.** These are the query terms, in query order, whose concept has a weight above zero for the unit. A matched unit that is first placed as a companion keeps `selected_by = "match"` and its `matched_terms` (P46).
8. **Ancestor titles.** Ancestor titles count with weight 1, and this includes the titles of empty-body parents: `4` "Trocas e Devoluções" gives `troca` and `devolucao` to `4.1`, `4.2` and `4.4`. P43, P49 and the P59 pipeline depend on this (mutation B20).
9. **Companions with an empty body.** A companion rule that points to an empty-body section is skipped ("only retrievable units are inserted"). The added test `test_p49_empty_body_parent_never_returned_as_companion` covers this.
10. **Test material.** The plan names a `mini_index()` builder. The repository has no `tests/__init__.py`, so test modules cannot import `tests/conftest.py`. The module-level builders `mini_index()` and `mini_notes()` exist in `tests/conftest.py`, and the fixture `policy_retrieval` exposes them. The name `policy_retrieval` avoids shadowing the module-local fixture `mini_index` at `tests/integration/test_policy_preparation.py:138`. Imports of production modules are local to the builders, so the earlier tests still collected during RED (see Compliance Report).
11. **`FRONT_MATTER_ID`.** The constant is imported from `policy_index` (it exists there but is not listed in the spec's public names). `policy_index.py` was not changed.
12. **Spec header.** The spec and plan headers still say "proposed". The task brief states they are approved. The documents were left unchanged.

## Implementation Plan (executed)

1. Baseline check: branch, HEAD `781d748`, clean tree, `1075 passed, 2 skipped`. Hashes recorded.
2. Read the spec, the plan, `policy_index.py`, the slice A report with R1, `tests/conftest.py`, and `normalize_search_text` / `_check_text` in `services/catalog.py`.
3. Derived every expected value of P36–P62 by hand from `MINI` and P-08–P-11, before writing code. All plan rows matched the derivation.
4. Appended the slice B builders to `tests/conftest.py` and wrote `tests/unit/test_policy_retrieval.py` and `tests/integration/test_policy_search.py`.
5. RED recorded.
6. Implemented `src/emporio_agent/services/policies.py` (slice B only).
7. GREEN, then 26 mutations one at a time with a byte restoration check, full gates, this report, and Ruff again.

## Test Checklist

97 new items in 46 test functions: `tests/unit/test_policy_retrieval.py` has 84 items (40 functions); `tests/integration/test_policy_search.py` has 13 items (6 functions). No earlier test file changed.

| Case | Type | Tests (items) |
| --- | --- | --- |
| P36 | U | `test_p36_policy_terms` (6): the six rows of the plan |
| P37 | U | `test_p37_stemming` (9): the nine rows of the plan; `test_p37_short_words_and_rule_conditions` (1, added): `mais`, `gás`, `lápis`, `10x 12X` |
| P38 | U | `test_p38_no_searchable_terms` (3): `o que é?`, `?!`, `Привет` |
| P39 | U | `test_p39_every_term_maps_to_its_group_and_groups_are_disjoint` (1): literal table of the spec; `test_p39_hand_folded_spot_checks` (1): `veze`, `juro`, `correio`, `sabado`, … written by hand; `test_p39_installment_counts_and_own_keys` (1) |
| P40 | U | `test_p40_synonyms` (8); `test_p40_unknown_term_is_not_found` (1): `acessório` |
| P41 | U | `test_p41_folding_and_stemming` (5); `matched_terms == ("devolucao",)` |
| P42 | U | `test_p42_rejected_before_index_read` (10): five non-strings, two blanks, three 4,001-character queries, all against an index object whose attribute access raises; `test_p42_length_limit_is_raw_code_points` (1): exactly 4,000 accepted with result `6`, and a 4,000-character query that NFKD expands (`ﬁ`) is still accepted; `test_p42_valid_query_reads_the_index` (1, control); `test_p42_error_holds_no_query_text` (1) |
| P43 | U | `test_p43_highest_coverage_only` (1) |
| P44 | U | `test_p44_score_threshold` (1) |
| P45 | U | `test_p45_tie_break_level_then_document_order` (1) |
| P46 | U | `test_p46_matched_companion_keeps_match_and_limit` (1) |
| P47 | U | `test_p47_no_padding` (1) |
| P48 | U | `test_p48_no_match` (1) |
| P49 | U | `test_p49_empty_body_section_never_returned` (1); `test_p49_empty_body_parent_never_returned_as_companion` (1, added, Gap 9) |
| P50 | U | `test_p50_companions_follow_concepts` (4) |
| P51 | U | `test_p51_whole_section_text` (1); `test_p51_long_section_is_not_truncated` (1): 200 lines with internal runs of spaces, returned byte for byte, pages `(3, 4)` |
| P52 | U | `test_p52_pages_and_heading_path` (1): `4.1`, `front_matter`, and also `1.1` (heading path, page, text) |
| P53 | U | `test_p53_notices` (6): codes, `section_ids`, fixed messages; `test_p53_each_code_at_most_once` (1); `test_p53_notice_order_does_not_depend_on_registry_order` (1) |
| P54 | U | `test_p54_other_sha_disables_curated_notes` (1): `zap`, no match and no searchable terms; `test_p54_other_sha_ignores_broken_registry` (1) |
| P55 | U | `test_p55_notes_mismatch` (2): missing companion and missing trigger; `test_p55_notice_reference_mismatch` (1); `test_p55_mismatch_also_on_not_found` (1) |
| P56 | U | `test_p56_reviewed_registry_hash_and_rules` (1): literal hash, all 10 companion rules and 7 notice rules; `test_p56_reviewed_registry_messages_and_ids` (1): messages equal the hand-written spec texts; every ID is in the literal section map of P63; every concept is a synonym group; `test_p56_reviewed_registry_is_default_and_not_applied_to_mini` (1); `test_p56_registry_types_are_frozen` (1) |
| P57 | U | `test_p57_instruction_text_is_data` (1): `socket.socket`, `create_connection`, `getaddrinfo` and `subprocess.Popen` patched to fail; `8` returned with its line unchanged |
| P58 | U | `test_p58_deterministic_and_index_unchanged` (1) |
| P59 | I | `test_p59_pipeline_topics` (8): address, hours, payment, PIX, delivery, exchange, return, warranty; sections, `selected_by`, notice codes, text and pages equal `MINI`; `test_p59_curated_notes_need_the_exact_pdf_hash` (1); `test_p59_no_match_is_not_found` (1) |
| P60 | I | `test_p60_multipage_section_keeps_next_page_exception` (1) |
| P61 | I | `test_p61_injection_text_stays_data` (1): `injection_title` variant; title and line verbatim; directory listing unchanged by the search |
| P62 | I | `test_p62_missing_index_is_unavailable` (1): `index_missing`, base name only. The mapping to the tool status `unavailable` belongs to the future tool and is not implemented. |

All 27 planned IDs (P36–P62) are covered.

## Test Strategy

- Unit tests run the real `search_policies` on `mini_index()`, a `PolicyIndex` built by hand from `MINI`, with `mini_notes(sha256)`. The default hash is `MINI_SOURCE.sha256` (`"a" * 64`). `"b" * 64` is used for the other-manual cases.
- Integration tests use `policy_kit.write()`, then the real `prepare_policy_index` into `tmp_path`, then the real `load_policy_index`, with the PDF deleted before the load. They then run the real `search_policies`. `MINI_NOTES` is bound to the SHA-256 that `hashlib` computes from the PDF bytes.
- Oracles: the `MINI` rows, the literal synonym table and notice messages of the spec (written by hand in the tests and in `tests/conftest.py`), the literal reviewed hash, the literal P63 section map, and the hand-derived rankings of the plan. No production function computes an expected value. `policy_terms` folds the literal synonym words in P39: the concept keys it is compared against are literals, and the spot checks are hand-folded.
- No mock of ranking, extraction or results. Patching is limited to network and subprocess probes (P57). The broken index object (P42) proves the validation order.
- No CSV, real manual, OpenAI, network, or file under `data/local/`.

## Compliance Report

### RED

`uv run --frozen pytest tests/unit/test_policy_retrieval.py tests/integration/test_policy_search.py -q -p no:cacheprovider` → exit 2, `2 errors in 0.19s`. Cause, for both files: `ModuleNotFoundError: No module named 'emporio_agent.services.policies'` at module import. This is the RED the plan expected.

`uv run --frozen pytest -q -p no:cacheprovider --ignore=tests/unit/test_policy_retrieval.py --ignore=tests/integration/test_policy_search.py` at RED → exit 0, `1075 passed, 2 skipped`. The appended builders in `tests/conftest.py` do not affect the earlier tests.

During RED, `ruff check` reported I001 on the new unit test file. The cause: Ruff classifies a module that does not exist yet as third-party. The finding went away once the module existed. The import block was not changed.

### GREEN

The first run after implementation gave `97 passed` (exit 0). No test or expectation was changed after the implementation.

### Mutations (after GREEN, one at a time, original bytes restored after each)

Script in the session scratch directory, outside the repository. Each mutation replaced one exact string in `policies.py` (and the script checked that the string occurs exactly once). The script then ran the two new files and restored the original bytes. It checked the SHA-256 after each restore.

| ID | Mutation | Result | Killed by (functions) |
| --- | --- | --- | --- |
| B01 | no accent folding (`lower()` instead of `normalize_search_text`) | killed (collection error, exit 2) | import: the synonym table no longer folds to one term per word |
| B01b | query side not folded or stemmed (index side unchanged) | killed (8 failed) | P41, P49, P57, P59, P61 |
| B02 | no stemming | killed (15 failed) | P36, P37, P39, P41, P43, P49, P57, P59 |
| B03 | synonyms ignored | killed (33 failed) | P39, P40, P41, P44, P47, P49–P54, P56, P59, P60 |
| B04 | no coverage filter | killed (2 failed) | P43, P59 |
| B05 | no score threshold | killed (19 failed) | P40, P41, P44, P50, P54, P56, P59 |
| B06 | parent before child on equal score | killed (3 failed) | P45, P46, P59 |
| B07 | padding to three sections | killed (47 failed) | P40–P47, P49, P50, P53, P54, P56, P57, P59, P61 |
| B08 | empty-body section ranked | killed (1 failed) | P49 |
| B09 | companions ignore `only_for_concepts` | killed (6 failed) | P40, P50, P52, P53, P59 |
| B10 | text joined with a space instead of `\n` | killed (10 failed) | P51, P52, P59, P60 |
| B11 | curated notes applied for another SHA-256 | killed (6 failed) | P44, P45, P54, P56, P59 |
| B12 | query validated after reading the index | killed (10 failed) | P42 |
| B13 | `notes_mismatch` check removed | killed (4 failed) | P55 |
| B14 | matched unit placed as companion labelled `companion` | killed (39 failed) | P40–P47, P49, P50, P54, P56, P57, P59, P61 |
| B15 | notices in registry order | killed (1 failed) | P53 |
| B16 | `heading_path` without ancestors | killed (1 failed) | P52 |
| B17 | duplicate notice codes kept | killed (1 failed) | P53 |
| B18 | blank checked before length | killed (1 failed) | P42 (`" " * 4001`) |
| B19 | length measured after normalization | killed (3 failed) | P42 |
| B20 | ancestor titles not searched | killed (2 failed) | P43, P49 |
| B21 | companions not limited to retrievable units | killed (1 failed) | P49 (added test) |
| B22 | stopwords not removed | killed (10 failed) | P36, P37, P38, P48, P54, P59 |
| B23 | `^\d{1,2}x$` rule ignored | killed (3 failed) | P39, P40, P53 |
| B24 | `curated_notes_unavailable` dropped | killed (5 failed) | P44, P45, P54, P56, P59 |
| B25 | result limit 4 instead of 3 | killed (2 failed) | P45, P46 |

B01–B12 cover the eleven behaviors listed in the plan for slice B (accent folding and stemming are split into B01/B01b and B02). B13–B25 are extra. No mutation survived. B01 is killed only at import, so B01b was added to prove that P41 itself detects a missing fold on the query side.

Restoration: the SHA-256 of `policies.py` was `a24d0d4f2bca542557886f7b23dbcaa798a2943241b13ff27ab4ec71bd24891d` in four places: before the mutations, after every restore (checked by the script), after the final restore, and in a separate `sha256sum` run before the gates.

### Gates (run after the mutations and the restoration check)

| Command | Exit | Result |
| --- | --- | --- |
| `uv run --frozen pytest tests/unit/test_policy_retrieval.py tests/integration/test_policy_search.py -q` | 0 | 97 passed |
| `uv run --frozen pytest -q -rs` | 0 | 1172 passed, 2 skipped (1075 earlier + 97 new; skips: the two known Windows cases at `test_readonly_connection.py:599` and `:612`); 0 failed |
| `uv run --frozen ruff check --no-cache .` | 0 | All checks passed |
| `uv run --frozen ruff format --check --no-cache .` | 0 | 63 files already formatted |

Ruff (both commands, `--no-cache`) is run again after this report is written; see the final response of the task.

### Files

Created:

- `src/emporio_agent/services/policies.py`
- `tests/unit/test_policy_retrieval.py`
- `tests/integration/test_policy_search.py`
- `docs/validation/04-02-policy-retrieval.md` (this file)

Changed:

- `tests/conftest.py`: append only. Added the `POLICY_NOTICE_MESSAGES`, `MINI_COMPANIONS` and `MINI_NOTICES` data, the `mini_index()` and `mini_notes()` builders (with local imports), the `PolicyRetrievalKit` class and the `policy_retrieval` fixture. Earlier data, helpers and fixtures are unchanged (`git diff` shows 0 removed lines).

Unchanged: `policy_index.py`, catalog, pricing, all earlier tests, `tests/integration/conftest.py`, `tests/fixtures/`, spec, plan, README, `pyproject.toml`, `uv.lock`, `.gitignore`.

Git state: branch `feature/data-preparation`, HEAD `781d748`. Modified: `tests/conftest.py`. Untracked: the four created files. Nothing is staged or committed. No PDF, no index, and no file under `data/local/` was created.

### Limits

- The real manual was not opened, prepared or searched. `REVIEWED_MANUAL_NOTES` holds the approved literal hash and IDs. Whether those IDs match the real manual is checked only in slice C (P63–P65). **No claim that the real manual is validated.**
- No tool, Pydantic schema, CLI or model integration exists. The mapping from `PolicyInputError`, `PolicyIndexUnavailableError` and `PolicyDataError` to tool statuses is only specified. **No claim that integration with a model is done.**
- Lexical retrieval only. Negation is not interpreted (`nao` and `sem` are stopwords). There is no spelling correction, phrase matching or semantic similarity. Paraphrases outside the synonym table are missed.
- Retrieval does not decide eligibility. Notices are fixed metadata: they do not answer the customer, compute fees, installments or deadlines, open tickets, or forward requests.
- The prompt-injection cases (P57, P61) show that this code stores and returns such text as data, with no side effect. They do not show how a future model will behave with that text.
- The synthetic PDFs are produced by `pypdf`, not by the real producer.
- Open for review: Gap 3 (`no_handoff_integration` references) and Gap 4 (service notice texts and `section_ids`).

Status: slice B GREEN. Slice C not started. Stopped for review.
