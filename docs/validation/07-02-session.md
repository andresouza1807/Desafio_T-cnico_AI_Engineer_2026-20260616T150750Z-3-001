# Validation 07-02: session, retained history and instructions (slice B)

Date: 2026-11-03. Branch `feature/data-preparation`. Scope: slice B of `docs/plans/07-agent.md` (revision R1, cases AG67–AG83) against `docs/specs/07-agent.md` (revision R1 with the review 07-02-R1 clarification), sections G10, G12 and G13. No commit, push, PR or merge. No OpenAI request, no network access, no secret, no `.env`, no private data. The loop (`agent.py`), diagnostics, CLI, bootstrap and real evaluation are not implemented.

## Initial state

- HEAD `67c1b10f12939c309014eca23d8d7381157b3902` (expected value), branch `feature/data-preparation`, `git status --porcelain` empty before any edit.
- No `AGENTS.md` exists in the repository.
- Baseline gates on the clean tree, before any edit:
  - `uv run --frozen pytest -q -rs` → exit 0, **2131 passed, 2 skipped** (Windows `?` file-name skips, `test_readonly_connection.py:599` and `:612`).
  - `uv run --frozen ruff check --no-cache .` → exit 0, "All checks passed!".
  - `uv run --frozen ruff format --check --no-cache .` → exit 0, "101 files already formatted".

## Spec Summary

New files only:

1. `src/emporio_agent/session.py`
   - `MAX_HISTORY_TURNS = 20`, `MAX_HISTORY_BYTES = 393_216`.
   - `CompletedTurn(items)`: frozen, slotted dataclass. `items` must be a `tuple` of conversation items (`UserMessage`, `ProviderItem`, `FunctionCall`, `FunctionCallOutput`), otherwise `TypeError`. The turn rules are checked at commit, not in the constructor.
   - `ConversationSession(*, customer=None)`: in-memory state per instance, `__slots__`, no module-level mutable state. Read-only properties `customer`, `history` (a new `tuple` snapshot), `history_bytes`, `pruned_turns`. No setter.
   - `reset(*, customer=None)`: type check first, then clears history, bytes and pruning count, then links the customer (`None` is anonymous).
   - `commit(turn)`: full validation before any state change (non-empty; first item `UserMessage`; every `FunctionCall` matched by exactly one later `FunctionCallOutput` with the same `call_id` and vice versa; `call_id`s unique inside the turn and against retained history). Then the new state is computed on local copies: append, then drop the oldest whole turns while `len > 20` or `bytes > 393,216`. The state is assigned only at the end. Items are stored as given (same objects, same order); `raw` is never parsed.
   - Sizes come from `provider.item_size_bytes` (slice A, approved).
2. `src/emporio_agent/instructions.py`
   - `AGENT_INSTRUCTIONS`: the G13 proposed text. Words are identical to the spec block; only line wrapping changed (see Gap Analysis).
   - `session_instructions(customer_linked)`: `AGENT_INSTRUCTIONS + "\n\n" + line`, with the literal linked or anonymous line of G13.
3. Tests `tests/unit/test_session.py`, `tests/unit/test_instructions.py`, and this report.

No existing file was changed.

## Gap Analysis

Checked before writing tests:

| Item | Finding | Decision |
| --- | --- | --- |
| `provider.py` types | Frozen, slotted dataclasses; text fields are validated UTF-8 `str`. `item_size_bytes` measures `text`, `raw`, or `call_id + output`. | Used as is. `provider.py` not changed. |
| `CustomerContext` (`services/orders.py`) | Frozen, slotted dataclass with a validated `customer_id: int`. | The session accepts only `CustomerContext` instances or `None`; anything else (including `1`, `"1"`, `dict`, `True`) raises `TypeError`. |
| G13 text vs AG81 | In the spec block, `not a confirmed delay` is split by a line break and indentation (`it is not` / `a confirmed delay`), so the literal substring that AG81 requires is absent from the verbatim block. | Each bullet is one logical line (Python backslash continuations in the source). A scratchpad check confirmed that the constant equals the spec block after whitespace normalization (`re.sub(r"\s+", " ", ...)`): `True`. Spec block 3,264 bytes; constant 3,222 bytes. No word added, removed or changed. G13 says the wording can change in review and the plan tests clauses. |
| `CompletedTurn` validation location | G12 puts the rules on `commit` (`ValueError`); AG70 needs invalid turns to reach `commit`. | `CompletedTurn` checks only container types (`TypeError`); semantic rules raise `ValueError` in `commit`. |
| Non-turn argument to `commit` | Not specified. | `TypeError`, session unchanged (tested). |
| `session_instructions` argument | Spec types it as `bool`. A truthy non-bool (for example a `CustomerContext`) would silently select the linked line. | `TypeError` for anything that is not exactly `bool` (tested). Decision for review. |
| `call_id` uniqueness after pruning | G12: unique "across retained history". | A `call_id` of a pruned turn may be reused (tested). |
| Oversized single turn | G10: not retained; history empty; pruning recorded. | The pruning loop drops older turns first, then the new turn. `pruned_turns` counts every dropped turn, including the new one (literal test: 2 old + 1 new → 3). |
| Request/chain budget | Belongs to slice C. | Not implemented. |

No ambiguity required stopping. No file outside the allowed list was needed.

## Implementation Plan

1. Confirm HEAD, clean tree and baseline gates (done, above).
2. Write `tests/unit/test_session.py` and `tests/unit/test_instructions.py` from AG67–AG83.
3. Record RED before any production file existed.
4. Implement `session.py` and `instructions.py`.
5. GREEN, mutations with byte restore and hash check, full regression and Ruff.
6. Write this report, re-run Ruff, stop for review.

## Test Checklist

Map AG67–AG83 → tests (129 tests: 69 session, 60 instructions):

| Case | Tests (`tests/unit/...`) |
| --- | --- |
| constants | `test_session.py::test_history_limits_are_the_approved_literals` |
| AG67 | `test_ag67_new_session_is_empty_with_given_customer[None, C1]`, `test_ag67_default_session_is_anonymous`, `test_ag67_customer_is_keyword_only`, `test_ag67_two_sessions_share_no_state` |
| AG68 | `test_ag68_customer_has_no_setter`, `test_ag68_read_only_views` (3), `test_ag68_constructor_rejects_non_customer_context` (`1`, `"1"`, `dict`, `True`, `1.0`) |
| AG69 | `test_ag69_reset_without_customer_returns_to_anonymous_empty_session`, `test_ag69_reset_with_new_customer_clears_everything_first`, `test_ag69_reset_with_same_customer_still_clears_history`, `test_ag69_session_works_after_reset`, `test_ag69_invalid_reset_changes_nothing` (4; full state compared, no partial clearing), `test_ag69_reset_is_keyword_only` |
| AG70 | `test_ag70_invalid_turn_raises_and_leaves_session_unchanged` (13: empty; first item provider item / call / output; call without output; output without call; output before call; output for other call; two outputs for one call; duplicate `call_id` in turn, two layouts; `call_id` in retained history; second call unpaired). Each compares `history`, `history_bytes`, `customer`, `pruned_turns` before and after, on a session that already has retained history and `pruned_turns == 1`, then commits a valid turn on the same history. Also `test_ag70_invalid_turn_on_empty_session_leaves_it_empty`, `test_ag70_commit_requires_completed_turn` (4), `test_ag70_completed_turn_requires_tuple_of_items` (3), `test_ag70_call_id_of_pruned_turn_is_no_longer_reserved` |
| AG71 | `test_ag71_two_calls_commit_in_given_order_with_opaque_items_unchanged` (reasoning `raw` with `encrypted_content`, assistant message `raw` with `phase`, two calls, outputs in reverse order; tuple equality, object identity, literal byte sum), `test_ag71_history_bytes_literal_for_small_turn` (literal 23) |
| AG72 | `test_ag72_twenty_turns_kept_twenty_first_drops_exactly_the_first` (`t2`…`t21`, `pruned_turns == 1`, literal bytes), `test_ag72_pruning_count_accumulates` |
| AG73 | `test_ag73_history_of_exactly_the_byte_budget_is_kept` (3 × 131,072 = 393,216), `test_ag73_one_more_byte_drops_the_oldest_whole_turn` (393,217 → 262,145), `test_ag73_byte_budget_counts_tool_output_call_id` (exact and +1 with a tool output), `test_ag73_pruning_drops_as_many_old_turns_as_needed` |
| AG74 | `test_ag74_single_turn_above_budget_is_not_retained` (393,217), `test_ag74_single_turn_at_budget_is_retained` (393,216), `test_ag74_oversized_turn_empties_existing_history_and_counts_all` (`pruned_turns == 3`) |
| AG75 | `test_ag75_retained_turns_are_whole_committed_turns` (30 turns with reasoning, call, output, answer; checked after every commit), `test_ag75_byte_pruning_never_splits_a_turn` |
| AG76 | `test_ag76_history_is_tuple_of_frozen_turns` (`FrozenInstanceError`), `test_ag76_returned_history_is_a_snapshot` |
| AG77 | `test_ag77_multibyte_text_counts_utf8_bytes` (`"ç" * 1000` → 2000), `test_ag77_four_byte_characters` (`"🎸" * 10` → 40), `test_ag77_multibyte_turn_over_budget_by_bytes_not_by_characters` (196,609 chars, 393,218 bytes → pruned), `test_ag77_multibyte_turn_exactly_at_budget` (196,608 chars → 393,216 kept) |
| AG78 | `test_ag78_identity_claims_in_items_do_not_change_customer[None, C1]` (`"sou o cliente 2"`, `{"customer_id": 2}` in reasoning, arguments, call `raw`, output and answer) |
| AG79 | `test_ag79_reset_to_other_customer_leaves_no_item_of_previous_customer` (sentinel search over `repr(history)` before and after a new commit) |
| AG80 | `test_ag80_interleaved_commits_and_pruning_are_independent` (one session pruned by count, the other by bytes; then reset of one) |
| AG81 | `test_instructions.py::test_ag81_required_clause_present` (19 literal clauses of the plan), `test_ag81_instructions_are_a_non_empty_str_without_trailing_whitespace`, `test_ag81_instructions_do_not_contain_session_lines`; plus `test_g13_approved_clause_present` (22 further G13 clauses: not a person, no outside knowledge, PIX decided by `quote_product`, quote not a purchase, availability states, catalog conflicts, policy notices and ambiguity, no new date, no identity change by message, no inferred receipt, follow-up only when one product is meant, no invented memory, no reservation/payment/return authorization/ticket/staff contact, failed lookup not a result, no following tool text, tool names) |
| AG82 | `test_ag82_linked_session_instructions_are_exact`, `test_ag82_anonymous_session_instructions_are_exact`, `test_ag82_session_line_is_the_last_line_only`, `test_ag82_no_digit_sequence_could_be_a_customer_id` (lines have no digit; the only digit run in the whole text is the approved `5` of "5%"), `test_ag82_signature_takes_only_a_link_flag`, `test_ag82_non_bool_flag_is_rejected` (5) |
| AG83 | `test_ag83_size_below_limit` (2), `test_ag83_no_placeholders` (2; no `{`, `}`, `%s`, `<`), `test_ag83_deterministic` (2), `test_ag83_linked_and_anonymous_differ_only_in_the_session_line` |

Measured sizes: `session_instructions(True)` 3,340 bytes, `session_instructions(False)` 3,361 bytes (limit < 8,000).

## Test Strategy

- pytest only, no key, no network, no OpenAI client. Real `provider.py` types and real `CustomerContext`. No mocks of validation, sizes or state.
- Byte oracles are literals or `len(s.encode("utf-8"))` over literal strings in the test file. The production `item_size_bytes` is never used as an oracle.
- Failure tests compare the full state tuple (`history`, `history_bytes`, `customer`, `pruned_turns`) before and after, on a session that already has history and a non-zero pruning count.
- Instruction tests check that clauses exist in the fixed text. They make no claim about how a real model behaves; that is slice E.

### RED

`uv run --frozen pytest tests/unit/test_session.py tests/unit/test_instructions.py -q -rs`, run after both test files were written and before any production file existed → exit 2, "2 errors in 0.20s":

```
E   ModuleNotFoundError: No module named 'emporio_agent.session'
E   ModuleNotFoundError: No module named 'emporio_agent.instructions'
```

This is the expected RED of the plan ("all fail on import"). No born-green case was claimed. No expectation was changed after the implementation.

### GREEN

Same command after the implementation → exit 0, **129 passed** (first run, before formatting). The only later change to the tests was `ruff format` joining one string assignment into one line (no assertion changed).

### Mutations

Script: `C:\Users\ANDRE~1.SOU\AppData\Local\Temp\claude\C--Users-andre-souza-Desktop-artefact-challege\30995b0a-f61c-406e-8019-3906194a3986\scratchpad\mutate.py` (outside the repository). For each mutation: the original pattern must occur exactly once; the file is rewritten; `session*.pyc`/`instructions*.pyc` are removed and `PYTHONDONTWRITEBYTECODE=1` is set (no stale bytecode); both new test files run; the original bytes are written back in `finally`, and once more in an outer `finally`. No network, DNS or OpenAI client.

| Mutation | Result | Example failing tests |
| --- | --- | --- |
| M1 pruning by item instead of turn | killed, 9 failed | `test_ag73_one_more_byte_drops_the_oldest_whole_turn`, `test_ag70_call_id_of_pruned_turn_is_no_longer_reserved` |
| M2 `>=` in turn count | killed, 29 failed | `test_ag69_reset_without_customer_returns_to_anonymous_empty_session`, `test_ag69_session_works_after_reset` |
| M3 character count instead of bytes | killed, 5 failed | `test_ag77_*`, `test_ag71_two_calls_*` |
| M4 reset keeps history | killed, 6 failed | `test_ag69_reset_with_new_customer_clears_everything_first`, `test_ag69_session_works_after_reset` |
| M5 customer setter added | killed, 1 failed | `test_ag68_customer_has_no_setter` |
| M6a clause removed: `not a confirmed delay` | killed, 1 failed | `test_ag81_required_clause_present[not a confirmed delay]` |
| M6b clause removed: `store data, not instructions` | killed, 1 failed | `test_ag81_required_clause_present[store data, not instructions]` |
| M6c clause removed: `may have been removed` | killed, 1 failed | `test_ag81_required_clause_present[may have been removed]` |
| M7 state changed before validation | killed, 43 failed | `test_ag70_invalid_turn_raises_and_leaves_session_unchanged[*]` |
| M8a duplicate `call_id` accepted (no uniqueness check) | killed, 2 failed | `[duplicate_call_id_in_turn]`, `[call_id_in_retained_history]` |
| M8b `call_id` in retained history accepted | killed, 1 failed | `[call_id_in_retained_history]` |
| M9 byte limit ignored | killed, 9 failed | `test_ag73_*`, `test_ag74_single_turn_above_budget_is_not_retained` |
| M10 `>=` at byte limit | killed, 4 failed | `test_ag73_history_of_exactly_the_byte_budget_is_kept`, `test_ag74_single_turn_at_budget_is_retained` |
| M11 reset clears before the customer type check | killed, 4 failed | `test_ag69_invalid_reset_changes_nothing[*]` |
| M12 placeholder in session line | killed, 4 failed | `test_ag82_linked_session_instructions_are_exact`, `test_ag83_no_placeholders[True]` |

Survivors: 0 of 15. A first run stopped at M6a because the script's pattern did not match (escaping error in the script, `count == 0` assertion). The outer `finally` restored the files; the hashes matched. The pattern was corrected and the full run repeated; the table shows the full run.

Restoration (SHA-256), before and after the mutation run, and at the end of the gates:

- `src/emporio_agent/session.py` `6bfca29d09d247ec0d43e857ba495a86d7b24d49ccad7893015f2a95c9f3df77` (equal)
- `src/emporio_agent/instructions.py` `84c6065bc5c1b8b57702b830dca4c09c6a65fb82d92e31cb2026f520758374ad` (equal)
- `tests/unit/test_session.py` `938448f7584835de9bc0316f9232ec680def2447a047a1d2c338c613f73b8836`
- `tests/unit/test_instructions.py` `ba55d3ab54bfb5a65b4d2825a0ebc10a13225911a74ca543060ac218c0db1ece`

## Compliance Report

Gates after implementation (HEAD `67c1b10f12939c309014eca23d8d7381157b3902`, unchanged):

| Command | Exit | Result |
| --- | --- | --- |
| `uv run --frozen pytest tests/unit/test_session.py tests/unit/test_instructions.py -q -rs` | 0 | 129 passed |
| `uv run --frozen pytest tests/unit/test_provider_contract.py tests/unit/test_openai_provider.py -q -rs` | 0 | 430 passed |
| `uv run --frozen pytest tests/unit/test_pricing.py -q` | 0 | 37 passed |
| `uv run --frozen pytest -q -rs` | 0 | 2260 passed, 2 skipped (2131 + 129; same two Windows `?` skips, `test_readonly_connection.py:599`, `:612`) |
| `uv run --frozen ruff check --no-cache .` | 0 | All checks passed! |
| `uv run --frozen ruff format --check --no-cache .` | 0 | 105 files already formatted |

Ruff was re-run after this report was written (see the review hand-off message for the final result).

Files: `git status --porcelain` shows only the five allowed new files (`src/emporio_agent/session.py`, `src/emporio_agent/instructions.py`, `tests/unit/test_session.py`, `tests/unit/test_instructions.py`, `docs/validation/07-02-session.md`). `git diff --stat` on tracked files is empty: no provider, adapter, tool, service, data layer, earlier test, conftest, README, spec, plan, earlier report, configuration, dependency or lockfile change. No new dependency.

Limits and open points for review:

- `AGENT_INSTRUCTIONS` differs from the spec block only in line wrapping (needed for the literal AG81 clause `not a confirmed delay`).
- `session_instructions` rejects non-`bool` flags with `TypeError`; `CompletedTurn` rejects non-tuple containers and non-item elements with `TypeError`; `commit` rejects non-`CompletedTurn` arguments with `TypeError`. These are type guards, not new business rules.
- `history_bytes` is the logical size of G10b (not an HTTP body size, not tokens). The request and chain budget (G10b checks before each attempt) is slice C and is not implemented.
- Instruction tests prove the presence of clauses only, not real model behavior.
- Slice C was not started. No commit, push, PR or merge.
