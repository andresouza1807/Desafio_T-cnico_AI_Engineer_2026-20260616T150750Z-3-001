# Validation 07-03: agent loop, request budget, outcomes and diagnostics (slice C)

Date: 2026-11-03. Branch `feature/data-preparation`. Scope: slice C of `docs/plans/07-agent.md` (revision R1, cases AG37–AG66, AG84–AG88, AG109–AG116) against `docs/specs/07-agent.md` (revision R1 with the review 07-02-R1 clarification), sections G5, G8–G11, G14 and G15. The task brief calls this step "07-04 — recorte C"; the plan and the allowed file list name the report `07-03-agent-loop.md`, so that name is used. No commit, push, PR or merge. No OpenAI request, no network access, no secret, no `.env`, no private data. Slice D (offline integration), the CLI, bootstrap and the real evaluation were not started.

## Initial state

- HEAD `bee89fdefc863086a046fcd8ebbee1a3aa20daeb` (expected value), branch `feature/data-preparation`, `git status --porcelain` empty before any edit.
- No `AGENTS.md` exists in the repository.
- Baseline gates on the clean tree, before any edit:
  - `uv run --frozen pytest -q -rs` → exit 0, **2260 passed, 2 skipped** (Windows `?` file-name skips, `test_readonly_connection.py:599` and `:612`).
  - `uv run --frozen ruff check --no-cache .` → exit 0, "All checks passed!".
  - `uv run --frozen ruff format --check --no-cache .` → exit 0, "106 files already formatted".

## Spec Summary

One new production file, `src/emporio_agent/agent.py`. It has no `openai` import, no SDK object, no logging, no environment read and no file access.

- Constants: `MAX_MODEL_ATTEMPTS = 8`, `MAX_TOOL_EXECUTIONS = 6`, `MAX_RETRIES_PER_MESSAGE = 1`, `RETRY_DELAY_SECONDS = 1.0`, `MAX_RETRY_AFTER_SECONDS = 5.0`, `MAX_USER_MESSAGE_CHARS = 4_000`, `MAX_FINAL_TEXT_CHARS = 16_000`, `MAX_STATIC_BYTES = 16_384`, `MAX_TURN_CHAIN_BYTES = 262_144`, `MAX_REQUEST_BYTES = 671_744`.
- Public types, field for field as in spec G11, G14 and G15: `TurnOutcome`, `TurnResult`, `ToolRunRecord`, `AttemptRecord`, `TurnDiagnostics`, `AgentResources`, `Agent(provider, resources, *, sleep=time.sleep, monotonic=time.monotonic)` with `respond(session, text) -> TurnResult`. All data classes are frozen and slotted.
- `respond` flow:
  1. Type checks (`ConversationSession`, `str`). Then the input checks in G10 order: UTF-8 encodable, not blank, ≤ 4,000 code points. These run before any `UserMessage`, any provider call and any commit.
  2. Once per turn: the retained history items, `session_instructions(customer linked)`, `tuple(provider_tool_definitions())`, the static size, and one `ToolContext` built from `AgentResources` and `session.customer`.
  3. At most 8 iterations. Each iteration does the following:
     - It checks the budget first, in this order: static, chain, total.
     - It builds the `ModelRequest` (history + current chain; `allow_tool_calls` is `False` when 6 executions are done or when the attempt is the 8th). A retry reuses the same request object.
     - It calls `provider.create` once and times the call with the injected `monotonic`.
  4. Errors: a `ProviderError` keeps its kind. Any other `Exception` from `create`, or a return value that is not a `ModelResponse`, becomes `unexpected`. A retry happens only under G8 rule 1. The wait is `max(1.0, retry_after)` through the injected `sleep`.
  5. Responses are checked V1 → V2 → V3 → V4 → V4b → V4c → V5/V5b → V6 → V7. With calls, the response items are appended in provider order. Then each call runs through the module-level `execute_tool` in order, and its `FunctionCallOutput` (same `call_id`, unchanged payload) is appended. A final answer appends the response items, commits `CompletedTurn(chain)` once, and returns the model text unchanged.
  6. Every other outcome returns a fixed PT-BR text and leaves the session unchanged.
- Diagnostics: one `AttemptRecord` per provider call (none for a request that the budget stopped). There is one `ToolRunRecord` per execution, with references from `ok` envelopes only, by the fixed G14 paths. Usage counters count response attempts only, and the sums use valid usage only. `history_turns_after` and `history_pruned_turns` are read from the session after the turn.

## Gap Analysis

Checked before writing tests. No approved module, contract or file outside the allowed list needed a change.

| Item | Finding | Decision (for review) |
| --- | --- | --- |
| Provider kind → failure code | G11 lists `provider_timeout`, `provider_unavailable`, `provider_rate_limited` as temporary codes. It does not say which kinds map to `provider_unavailable`. | `timeout` → `provider_timeout`; `connection` and `server_error` → `provider_unavailable`; `rate_limited` → `provider_rate_limited`. Non-transient kinds map one to one (`quota` → `provider_quota`, …). `provider_failed`, `response_invalid` and `unexpected` keep their names. |
| Fixed PT-BR texts | G11 gives the literal refusal text. For the other groups it gives only the meaning. | Wording written in `agent.py` (seven texts, one per group and one per input code). The texts mention no lookup result, human ticket, provider text, code or ID. The configuration text says "Avise o responsável pela aplicação" (the operator), as G11 asks. |
| Non-`str` input | G10: the checks run "after a `str` type check". The result of that check is not specified. | `TypeError` (caller defect), session unchanged, provider not called. Same for a non-session argument. |
| Non-`ModelResponse` return from `create` | G15 says only that exceptions other than `ProviderError` become `unexpected`. | A wrong return type is also `unexpected` (fail closed), with no retry. |
| Exceptions outside `create` | G15: loop defects must propagate. | Only `provider.create` is inside `try/except Exception`. An exception from `execute_tool` (for example a spy that raises), from `sleep`, from the session or from the agent code propagates. `KeyboardInterrupt`/`SystemExit` propagate. The chain is local, so the session is unchanged. |
| `AgentResources` validation | Not specified. `ToolContext` validates the same fields. | `TypeError` in `__post_init__` (Path, Path, callable), so a wrong value fails at construction, not at the first turn. `Agent.__init__` checks `isinstance(provider, ModelProvider)`, `AgentResources`, and that `sleep`/`monotonic` are callable. |
| Policy reference format | G14: `policy:<section_id>@p<pages>`. `pages` is a list. | Pages joined by `,` (`policy:5.1@p4,5`). Slice D (AG93) checks this against real sections. |
| References for `ok` envelopes in slice C | The plan says to use absent files, so every real envelope is non-`ok`. AG85 asks for references "checked with literal payloads from a delegating spy". A delegating spy cannot produce an `ok` envelope without real data. | Agent runs: the spy delegates and records the real non-`ok` results; their references are `()`. The `ok` paths are tested on the private pure helper `_tool_references(ToolExecution)` with literal envelopes shaped like spec 06 results. This test also checks that text which looks like a reference inside data is ignored, that `bool`/`str` IDs are rejected, and that the order ID never appears. No tool, rule or service is stubbed in an agent run. Real `ok` references come in slice D. |
| Usage counters and error attempts | G14 does not say whether a failed attempt counts as "usage missing". | Only response attempts count (`reported + missing + invalid == response attempts`). Error attempts have `usage None`, `usage_invalid False`. |
| `history_pruned_turns` | Not defined as per turn or cumulative. | The session's `pruned_turns` after the turn (cumulative since the last reset), the same as `ConversationSession.pruned_turns`. |
| `duration_ms` | Rounding not specified. | `max(0, round((end - start) * 1000))` from the injected `monotonic`. Two clock reads per attempt and two per tool execution. |
| Budget before a retry | G10b: checked "before every attempt"; a retry has the same size. | The check runs before every attempt, the retry included. It sees the same numbers, so its result is the same. |
| V4 vs V4b reachability | With 8 attempts, 6 executions and 1 retry, the 8th attempt is reached only after one retry and six executions. | The 8th attempt always has `executed == 6` and `retries == 1`. So the clauses "next attempt is the 8th" (allow flag) and "attempt remains" (G8 rule 1), and the loop bound, cannot be told apart from their neighbours through behaviour. See the mutation survivors. AG64 can only be built with the retry already spent. |
| Report name | The brief says "07-04"; the plan and the allowed list say `07-03-agent-loop.md`. | Used the allowed file name. |

## Implementation Plan

1. Confirm HEAD, the clean tree and the baseline gates (done).
2. Write `tests/unit/test_agent_loop.py` (shared helpers plus loop/protocol cases), `tests/unit/test_agent_limits.py` and `tests/unit/test_agent_diagnostics.py`.
3. Record the real RED before `agent.py` existed.
4. Implement `src/emporio_agent/agent.py` only.
5. GREEN, mutations with byte restore and a SHA-256 check, regression gates, Ruff.
6. Write this report, run Ruff again, stop for review.

## Test Checklist

193 tests: `test_agent_loop.py` 44, `test_agent_limits.py` 83, `test_agent_diagnostics.py` 66 (parametrized cases counted).

| Case | Tests (`tests/unit/...`) |
| --- | --- |
| AG37 | `test_agent_loop.py::test_ag37_text_only_answer_commits_one_turn`, `test_ag37_answer_follows_retained_history` |
| AG38 | `test_ag38_one_tool_round_sends_real_output_with_call_id` (literal envelope; equal to a direct `execute_tool` call; context fields and `customer None`) |
| AG39 | `test_ag39_three_rounds_literal_sequences` (four literal request sequences, committed turn, one `ToolContext` object for the turn, linked customer passed) |
| AG40 | `test_ag40_reasoning_and_commentary_resent_in_order` |
| AG41 | `test_ag41_batch_runs_in_order_and_outputs_follow_all_items`, `test_ag41_outputs_matched_by_call_id_not_position` |
| AG42 | `test_ag42_duplicate_call_id_in_one_response`, `test_ag42_duplicate_call_id_with_valid_call_runs_nothing` |
| AG43 | `test_ag43_call_id_equal_to_retained_history`, `test_ag43_call_id_equal_to_earlier_call_in_same_turn` |
| AG44 | `test_ag44_unknown_tool_and_invalid_arguments_are_real_envelopes` (`unknown_tool`, `invalid_json`, `unexpected_field`, 3 executions; customer unchanged), `test_ag44_empty_name_counts_as_an_execution` |
| AG45 | `test_ag45_incomplete_response_fails_without_execution` [text, call, text_and_call, empty] |
| AG46 | `test_ag46_refusal_wins_and_runs_nothing` [refusal, refusal_with_calls], `test_ag46_refusal_with_text_shows_only_fixed_message` |
| AG47 | `test_ag47_empty_response` [empty_text, whitespace, reasoning_only, no_items] |
| AG48 | `test_ag48_final_text_of_exactly_16000_code_points_is_answered` [ascii, four_byte], `test_ag48_final_text_of_16001_code_points_fails_untruncated` |
| AG49 | `test_ag49_response_invalid_is_not_retried` |
| AG50 | `test_ag50_unexpected_exception_from_provider_fails_closed`, `test_ag50_non_response_return_value_fails_closed` |
| AG51 | `test_agent_limits.py::test_ag51_transient_then_success_retries_same_request` (same object, equal, `retry [False, True]`, sleep `[1.0]`) |
| AG52 | `test_ag52_two_transient_failures_end_the_turn` [timeout, connection, server_error, rate_limited, provider_failed] |
| AG53 | `test_ag53_permanent_error_is_not_retried` (8 kinds), `test_ag53_quota_with_retry_after_is_not_retried` |
| AG54 | `test_ag54_retry_after_within_limit` [5.0, 0.2, 0.0, None, 3.5], `test_ag54_retry_after_above_limit_is_not_retried` (5.01) |
| AG55 | `test_ag55_retry_after_tool_round_reuses_outputs` |
| AG56 | `test_ag56_second_transient_failure_later_is_not_retried` |
| AG57 | `test_ag57_six_rounds_then_text_at_attempt_7` (allow `[T×6, F]`), `test_ag59_batch_exactly_filling_budget_runs` |
| AG58 | `test_ag58_call_after_six_executions_fails` |
| AG59 | `test_ag59_batch_exceeding_remaining_budget_runs_nothing` |
| AG60 | `test_ag60_seven_calls_in_first_response_run_nothing` |
| AG61 | `test_ag61_retry_six_rounds_and_text_at_attempt_8` (allow `[T×7, F]`), `test_ag62_allow_false_on_attempt_8_after_transient_at_7` |
| AG62 | `test_ag62_call_at_attempt_8_is_not_executed` (no 9th call, 6 executions) |
| AG63 | `test_ag63_bounds_hold_for_mixed_scripts` (16 literal rows: attempts, executions, failure) |
| AG64 | `test_ag64_transient_failure_at_attempt_8_is_not_retried` |
| AG65 | `test_agent_loop.py::test_ag65_tools_identical_in_every_attempt` (one `provider_tool_definitions` call per turn, same tuple object in all 4 attempts), `test_ag65_tools_are_requested_again_on_the_next_turn` |
| AG66 | `test_agent_limits.py::test_ag66_user_text_of_4000_code_points_is_accepted` [ASCII, 4-byte, `ç`], `test_ag66_invalid_user_text_is_rejected_before_any_attempt` [4,001 ASCII, 4,001 four-byte, empty, blank, blank 4,001], `test_ag66_non_str_input_is_a_caller_defect` |
| AG84 | `test_agent_loop.py::test_ag84_instructions_resent_on_every_attempt` [anonymous, linked], `test_ag84_reset_to_linked_changes_the_session_line_only` |
| AG85 | `test_agent_diagnostics.py::test_ag85_tool_run_records_literal_with_scripted_clock` (durations 250/125 ms, attempts 500/125 ms, 8 clock reads), `test_ag85_unknown_tool_record_has_no_name`, `test_ag85_references_only_by_fixed_paths` (10 literal envelopes) |
| AG86 | `test_ag86_usage_sums_only_reported_values`, `test_ag86_all_usage_missing_gives_none_sums`, `test_ag86_explicit_zero_usage_is_zero`, `test_ag86_error_attempts_are_not_usage_attempts`, `test_ag86_usage_of_failed_turn_is_still_reported` |
| AG87 | `test_ag87_answered_turn_diagnostics_hold_no_content` (sentinels in user text, arguments, tool name, reasoning, commentary, model text, customer ID 424242, order ID 987654, `sk-`), `test_ag87_failed_turn_text_and_diagnostics_hold_no_content`, `test_ag87_provider_error_metadata_only` |
| AG88 | `test_ag88_failure_leaves_session_intact` (all 19 failure codes; next turn sends the same retained history), `test_ag88_refused_leaves_session_intact`, `test_ag88_rejected_input_leaves_session_intact` (3), `test_ag88_fixed_texts_are_distinct_and_safe`, `test_ag88_interrupt_from_provider_propagates` [KeyboardInterrupt, SystemExit], `test_ag88_interrupt_from_tool_propagates` (2), `test_ag88_unexpected_exception_from_tool_layer_is_not_masked`, `test_ag88_interrupt_in_retry_sleep_propagates` |
| AG109 | `test_agent_limits.py::test_ag109_lone_surrogate_rejected_first` [mixed, alone, blank_around, too_long, over_limit_tail]; a delegating spy on `agent.UserMessage` confirms that no `UserMessage` is built; history, bytes and pruning are unchanged; the next valid request has the same retained items |
| AG110 | `test_ag110_chain_of_exactly_262144_bytes_is_sent`, `test_ag110_chain_of_262145_bytes_is_not_sent`, `test_ag110_total_budget_exact_is_sent`, `test_ag110_total_budget_minus_one_is_not_sent`, `test_ag110_total_budget_with_retained_history` [9,691 sent, 9,690 not sent], `test_budget_literals_match_their_strings` |
| AG111 | `test_ag111_growth_after_tool_output_blocks_next_attempt` (262,094 → 262,210 bytes) |
| AG112 | `test_ag112_multibyte_budget_counts_utf8_bytes` [262,144 bytes sent, 262,145 bytes not sent; both under 262,144 code points] |
| AG113 | `test_ag113_retry_keeps_request_and_size` (`request_bytes [6,720, 268,855, 268,855]`) |
| AG114 | `test_agent_loop.py::test_ag114_*` (7 tests: commentary only, commentary + blank final, no phase, commentary + final, commentary + call, text + calls, text + calls over the limit) |
| AG115 | `test_agent_limits.py::test_ag115_large_reasoning_item_blocks_its_calls` (300,000-byte raw), `test_ag115_chain_exactly_at_limit_after_response_runs_calls`, `test_ag115_chain_one_over_limit_after_response_runs_nothing`, `test_ag115_static_budget_exact_is_sent`, `test_ag115_static_budget_minus_one_is_not_sent`, `test_ag115_static_budget_linked_session_literal` |
| AG116 | `test_agent_diagnostics.py::test_ag116_request_bytes_per_attempt_are_literal` (`[9,695, 9,928, 9,928]`), `test_ag116_invalid_usage_counted_and_excluded`, `test_ag116_only_invalid_usage_gives_none_sums`, `test_ag116_budget_failure_records_no_attempt_for_unsent_request`, `test_ag116_answered_diagnostics_full_literal`, `test_ag116_pruning_reported_after_commit` |
| Interface, G15 | `test_limit_constants_are_the_approved_literals`, `test_interface_*` (8: fields, frozen/slots, `TurnOutcome`, keyword-only `sleep`/`monotonic`, type guards, subprocess import without `openai`, source without `openai`/`logging`/`os.environ`/`open(`), `test_g15_one_agent_serves_two_sessions_without_crossing_state`, `test_g15_respond_rejects_non_session`, `test_budget_never_prunes_history_at_request_time` |

Literal sizes used (by hand, confirmed by `len(s.encode("utf-8"))` over literal strings in the tests only): static part 6,711 bytes (anonymous) and 6,690 (linked); `get_order` `order_not_accessible` envelope 108; padded reasoning prefix 68 + suffix 2; padded call `raw` 130; seeded history 2,978.

## Test Strategy

- `ScriptedProvider` implements the real `ModelProvider` protocol (the `Agent` constructor checks it with `isinstance`). It records every `ModelRequest`, raises scripted exceptions and flags an over-call. Over-calls are also excluded by exact call counts.
- Tools: the real `execute_tool`, behind a delegating `ToolSpy` patched at `emporio_agent.agent.execute_tool`. Resources are absent synthetic files under `tmp_path`, so the real tools return `data_unavailable`/`order_not_accessible`/`invalid_input` without opening anything. There is no mock of tool rules, authorization, SQL or ranking.
- `sleep` (a list's `append`) and `monotonic` (`Ticker`: scripted values or `calls × 0.125 s`) are injected. Nothing waits.
- Oracles are literal item sequences, codes, PT-BR texts, envelopes and byte counts. No production size helper computes an expected value.
- `monkeypatch.setattr` on limits: only `MAX_REQUEST_BYTES` (AG110) and `MAX_STATIC_BYTES` (AG115). The other patches are delegating spies (`execute_tool`, `provider_tool_definitions`, `UserMessage`) that only observe.
- Not proven here: language quality, real tool choice, real resistance to prompt injection, real `ok` envelopes with real data and index (slice D), and acceptance by the real provider (slice E).

### RED

`uv run --frozen pytest tests/unit/test_agent_loop.py tests/unit/test_agent_limits.py tests/unit/test_agent_diagnostics.py -q -rs`, run after the three test files were written and before `agent.py` existed → exit 2, "3 errors in 0.32s":

```
E   ModuleNotFoundError: No module named 'emporio_agent.agent'
```

This is the expected RED of the plan (import failure). No born-green case was claimed.

### GREEN

The same command after the implementation gave exit 1: 1 failed, 192 passed. The failure was a **test defect**: `test_interface_agent_source_has_no_openai_reference` asserted that the word `logging` is absent from the source, but the module docstring says "no logging". The assertion was narrowed to `import logging` / `from logging` (and `open(` was added). No production change was made for it. The next run → exit 0, **193 passed**. After that, `ruff format` reformatted two test files (line wrapping only; no assertion changed).

### Mutations

Script: `C:\Users\ANDRE~1.SOU\AppData\Local\Temp\claude\C--Users-andre-souza-Desktop-artefact-challege\0c13bae7-836d-405b-a19e-dafd33e7b44d\scratchpad\mutate.py` (outside the repository). For each mutation:

- The original pattern must occur exactly once. A first run stopped at M05c because its pattern occurred twice. The `finally` restored the file and the hash matched. The pattern was made unique and the whole run was repeated; the table shows the full run.
- `agent*.pyc` is deleted, `PYTHONDONTWRITEBYTECODE=1` is set, and `OPENAI_*` variables are removed.
- The three new test files run.
- The original bytes are written back in an inner and an outer `finally`.

No network, DNS, OpenAI client or real call.

| Mutation | Result | Example failing tests |
| --- | --- | --- |
| M01 retry counted per request (counter reset after each response) | killed, 4 | `test_ag51_*`, `test_ag56_*` |
| M02 retry not counted as an attempt | killed, 3 | `test_ag62_call_at_attempt_8_is_not_executed`, `test_ag63[...model_call_limit_reached]` |
| M03 tool re-executed on retry | killed, 5 | `test_ag55_*`, `test_ag63[...provider_timeout]` |
| M04 batch partially executed | killed, 9 | `test_ag58_*`, `test_ag114_text_with_calls_at_limit_is_not_shown` |
| M05a batch bound `>=` instead of `>` | killed, 17 | `test_ag57_*`, `test_ag58_*` |
| M05b attempt-8 call bound `>` instead of `>=` | killed, 3 | `test_ag62_*` |
| M05c allow flag: attempt `<=` 8 | **survived** | — |
| M05d allow flag: executed `<=` 6 | killed, 2 | `test_ag57_*`, `test_ag59_batch_exactly_filling_budget_runs` |
| M05e loop range allows a 9th attempt | **survived** | — |
| M05f retry allowed on the 8th attempt | **survived** | — |
| M06a text with calls shown as final | killed, 2 | `test_ag114_text_with_calls_*` |
| M06b commentary-only shown as final | killed, 3 | `test_ag114_commentary_only_is_no_final_answer` |
| M07 commit on failure | killed, 47 | `test_ag42_*`, `test_ag43_*` |
| M08 commit before the final answer | killed, 67 | `test_ag37_*`, `test_ag38_*` |
| M09 outputs placed before response items | killed, 34 | `test_ag38_*`, `test_ag39_*` |
| M10 `allow_tool_calls` always `True` | killed, 4 | `test_ag57_*`, `test_ag59_*` |
| M11 missing usage summed as zero | killed, 2 | `test_ag86_all_usage_missing_gives_none_sums`, `test_ag116_only_invalid_usage_gives_none_sums` |
| M12 budget checked only at commit (pre-attempt and V4c removed) | killed, 11 | `test_ag110_chain_of_262145_bytes_is_not_sent`, `test_ag110_total_budget_minus_one_is_not_sent` |
| M13 budget measured in code points | killed, 167 | `test_ag37_*` (history bytes no longer match the session) |
| M14 budget ignores tool outputs | killed, 9 | `test_ag110_*` |
| M15 tools of an over-budget response executed (V4c removed) | killed, 2 | `test_ag115_large_reasoning_item_blocks_its_calls` |
| M16 oldest history turn dropped at request time | killed, 26 | `test_ag37_answer_follows_retained_history`, `test_ag109_*` |
| M17 surrogate input passed on (`surrogatepass`) | killed, 6 | `test_ag109_lone_surrogate_rejected_first[*]` |
| M18 duplicate `call_id` accepted | killed, 5 | `test_ag42_*` |
| M19 retained-history `call_id` not checked | killed, 1 | `test_ag43_call_id_equal_to_retained_history` |
| M20 refusal loses to calls | killed, 1 | `test_ag46_refusal_wins_and_runs_nothing[refusal_with_calls]` |
| M21 incomplete response accepted | killed, 5 | `test_ag45_*` |
| M22 `Retry-After` ignored | killed, 2 | `test_ag54_retry_after_within_limit[5.0]`, `[3.5]` |
| M23 `Retry-After` limit ignored | killed, 2 | `test_ag54_retry_after_above_limit_is_not_retried` |
| M24 request rebuilt on retry | killed, 3 | `test_ag51_*`, `test_ag55_*` |
| M25 final text limit off by one | killed, 2 | `test_ag48_final_text_of_exactly_16000_*` |
| M26 input length limit off by one | killed, 3 | `test_ag66_user_text_of_4000_code_points_is_accepted[*]` |
| M27 chain budget `>=` instead of `>` | killed, 3 | `test_ag110_chain_of_exactly_262144_bytes_is_sent`, `test_ag112[262144_bytes]` |
| M28 `ToolContext` without the session customer | killed, 1 | `test_ag39_three_rounds_literal_sequences` |
| M29 order reference carries the order ID | killed, 1 | `test_ag85_references_only_by_fixed_paths[execution5-expected5]` |
| M30 permanent error retried | killed, 21 | `test_ag49_*`, `test_ag50_*` |

Result: 33 of 36 killed. The planned list is covered by M01–M06b, M07–M17 and M24. The surrogate mutation M17 shows that the input check comes first: without it, `UserMessage` raises `ValueError` and nothing is sent.

Survivors (recorded, not turned into a fabricated RED): M05c, M05e and M05f are equivalent under the approved constants. Each attempt before the 8th either fails the turn, is the single allowed retry, or carries at least one call. So the 8th attempt is reached only with the retry spent and exactly 6 executions:

- M05c: `allow_tool_calls` is already `False` by the execution count.
- M05e: the 8th attempt always returns, through V4, V5–V7 or a provider failure with the retry spent.
- M05f: the retry counter already blocks a retry.

These clauses stay as defence in depth. Testing them would need a patch of `MAX_TOOL_EXECUTIONS` or `MAX_RETRIES_PER_MESSAGE`, which the plan does not allow.

Restoration (SHA-256), before and after the mutation run and at the end of the gates:

- `src/emporio_agent/agent.py` `baec11ed5a33ab639c0096c9a675ac634c4e8a3803764e4ac66b30e9f41c9ac5` (equal)
- `tests/unit/test_agent_loop.py` `486fe57fb7858425da72ee46098c2adebbcee7095138ced8939327f8fba8b78e`
- `tests/unit/test_agent_limits.py` `67200337ba3b2b2d64a216037e982b32647018e8b42d0a4ac498fc271d6dba76`
- `tests/unit/test_agent_diagnostics.py` `f52a78368ff83b50175785eae4a6709b5697f9f459381c436cc3102531bab762`

## Compliance Report

Gates after the implementation (HEAD `bee89fdefc863086a046fcd8ebbee1a3aa20daeb`, unchanged):

| Command | Exit | Result |
| --- | --- | --- |
| `uv run --frozen pytest tests/unit/test_agent_loop.py tests/unit/test_agent_limits.py tests/unit/test_agent_diagnostics.py -q -rs` | 0 | 193 passed |
| `uv run --frozen pytest tests/unit/test_session.py tests/unit/test_instructions.py tests/unit/test_provider_contract.py tests/unit/test_openai_provider.py -q -rs` | 0 | 559 passed |
| `uv run --frozen pytest tests/unit/test_pricing.py -q` | 0 | 37 passed |
| `uv run --frozen pytest -q -rs` | 0 | 2453 passed, 2 skipped (2260 + 193; same two Windows `?` skips, `test_readonly_connection.py:599`, `:612`) |
| `uv run --frozen ruff check --no-cache .` | 0 | All checks passed! |
| `uv run --frozen ruff format --check --no-cache .` | 0 | 110 files already formatted |

Ruff was run again after this report was written (result in the review hand-off message).

Files: `git status --porcelain` shows only the five allowed new files (`src/emporio_agent/agent.py`, the three test files, this report). `git diff --stat` on tracked files is empty. No provider, adapter, session, instructions, tools, services, data layer, earlier test, conftest, README, spec, plan, earlier report, configuration, dependency or lockfile was changed. No dependency was added.

Spec conformance:

- G5: V1–V7 including V4b, V4c and V5b, in table order; refusal before calls; text with calls never shown; commentary never shown; unknown or invalid calls go through `execute_tool` and count; outputs follow all response items, in call order, each with its own `call_id`.
- G8/G9: an attempt is one `provider.create` call; one retry per message, only when the error is transient, an attempt remains and `retry_after` ≤ 5.0 s; the wait is `max(1.0, retry_after)`; the retry sends the same `ModelRequest` object; no tool runs again. There is never a 9th attempt or a 7th execution. A batch over the budget runs nothing. Calls on the 8th attempt never run.
- G10/G10b: input checks in order before anything else; the static/chain/total budget is checked before every attempt, after each response with calls (V4c) and, through the next pre-attempt check, after tool outputs; no partial pruning; sizes come from `provider.item_size_bytes`/`static_size_bytes`. They are logical bytes, not HTTP body sizes and not tokens.
- G11: fixed codes and PT-BR texts; one atomic commit only on `answered`; every other outcome leaves history, bytes, pruning count and customer unchanged.
- G14: diagnostics hold no user text, model text, arguments, payloads, customer ID or order ID. `request_id` and the reported model are the allowed exceptions. References use the fixed paths only.
- G15: `Agent` holds no conversation state; one `ToolContext` and one schema tuple per turn.

Limits and open points for review:

- The decisions in Gap Analysis: failure-code mapping, wording of the fixed texts, `TypeError` for non-`str`/non-session input, `unexpected` for a wrong return type, the usage-counter scope, the cumulative `history_pruned_turns`, the policy-reference page format, and the private-helper test for `ok` references.
- Three equivalent mutants survive (M05c, M05e, M05f), explained above.
- The byte budget is not a token guarantee. Real reasoning and message sizes are unknown until EV05.
- A scripted provider does not prove language quality, real tool choice or real injection resistance.
- Slice D was not started. No commit, push, PR or merge.

## Review 07-04-R1: chain budget on the final answer

Problem (reproduced): on the final-answer path without calls (V7), the loop appended `response.output` and called `session.commit` without checking `MAX_TURN_CHAIN_BYTES`. A response with a large reasoning item and the final text `"ok"`, giving a 262,145-byte chain, returned `answered` and changed the session.

Fix (`src/emporio_agent/agent.py`, 4 lines): just before V7, the loop measures `response.output` with `item_size_bytes`. If `chain_bytes + output_bytes > MAX_TURN_CHAIN_BYTES`, the turn fails with `request_too_large` and `budget_failure "chain"`. This happens before any commit, so the session is unchanged. The check runs after V1–V6, so `response_incomplete`, `protocol_error`, `refused`, `empty_response`, `no_final_answer` and `response_too_long` keep their precedence. No limit, contract, provider, service, session or instructions file changed.

### Regressions (`tests/unit/test_agent_limits.py`)

Expected sizes are computed by hand from literal strings. The chain is 9 (user `"pedido 1?"`) + 68 + padding + 2 (reasoning) + 139 (final message `FINAL_OK_RAW`, confirmed literal) = 218 + padding bytes.

| Test | Check |
| --- | --- |
| `test_r1_final_answer_literals_match_their_strings` | `FINAL_OK_RAW` is 139 bytes and is the `raw` of the scripted final message |
| `test_r1_final_answer_with_reasoning_respects_chain_budget[ascii_exact]` | padding 261,926 `x` → 262,144 bytes → `answered`, turn committed with items `(user, reasoning, message)` |
| `[ascii_plus_one]` | 261,927 → 262,145 → `failed/request_too_large`, `budget_failure "chain"`, session state equal (history, bytes, pruning 1, customer), `history_turns_after 20` |
| `[multibyte_exact]` | 130,963 `ç` (261,926 bytes, under the limit in code points) → `answered` |
| `[multibyte_plus_one]` | 130,963 `ç` + `x` (261,927 bytes) → `request_too_large`, session unchanged |
| all four rows | exactly 1 provider call (a second scripted answer is never requested), 0 tool executions, 1 `AttemptRecord` |
| `test_r1_final_answer_after_tool_round_respects_chain_budget` [261,680 sent, 261,681 not sent] | 9 + 130 + 116 + 70 + n + 139 = 464 + n; exact → `answered`; +1 → `request_too_large`/`chain`, 1 tool execution, 2 provider calls, session unchanged |
| `test_r1_other_final_checks_keep_precedence_over_chain_budget` | over budget and empty text → `empty_response`, `budget_failure None` |
| `test_r1_refusal_keeps_precedence_over_chain_budget` | over budget with refusal → `refused`, `budget_failure None` |

### RED (code before the fix)

`uv run --frozen pytest tests/unit/test_agent_limits.py -q -k r1` → exit 1, **3 failed, 7 passed**: `[ascii_plus_one]`, `[multibyte_plus_one]` and `after_tool_round[261811-False]` (the padding value of that first draft; see the test defects below). Each returned `('answered', None, 'ok')` instead of `failed`.

Test defects fixed before and during the RED/GREEN runs. No production code was changed for any of them:

1. The first draft asserted "under the limit in code points" for the ASCII rows too. That is false: for ASCII text, bytes equal code points. It made `[ascii_exact]` fail for a reason unrelated to the bug. The assertion was limited to the multibyte rows, and then the RED above was recorded.
2. The first draft of the after-tool-round test used paddings 261,810/261,811. These do not match its own formula (464 + n), and both rows are over the limit. Corrected to 261,680/261,681.

### GREEN and the effect on earlier tests

After the fix, 4 tests failed. Three were earlier tests whose expectation was the behaviour this review removes:

- `test_ag110_chain_of_exactly_262144_bytes_is_sent`
- `test_ag112_multibyte_budget_counts_utf8_bytes[262144_bytes]`
- `test_ag113_retry_keeps_request_and_size`

In each one, the request before the final attempt already measures exactly 262,144 chain bytes. The 139-byte final message then makes the chain 262,283 bytes, and R1 requires `request_too_large` for that. Their original assertions are kept unchanged:

- attempt 2 is sent;
- the request items are the literal chain;
- `request_bytes = 6,711 + 262,144`;
- for AG113, the retry reuses the same object and size `[6,720, 268,855, 268,855]`, with no tool re-run and one sleep.

Only the final-outcome assertion changed, from `answered` to `failed/request_too_large/chain`. Each change carries a "Review 07-04-R1" comment. The fourth failure was test defect 2 above.

Open point for review: plan AG113 says "the turn is answered" for a request of exactly 262,144 chain bytes. Under R1 this is no longer possible, because any final message adds bytes. The plan text was not changed (out of scope).

Then `uv run --frozen pytest tests/unit/test_agent_loop.py tests/unit/test_agent_limits.py tests/unit/test_agent_diagnostics.py -q -rs` → exit 0, **202 passed**. `test_agent_diagnostics.py` was not changed.

### Mutations of the fix

Script: `C:\Users\ANDRE~1.SOU\AppData\Local\Temp\claude\C--Users-andre-souza-Desktop-artefact-challege\0c13bae7-836d-405b-a19e-dafd33e7b44d\scratchpad\mutate_r1.py`. It uses the same restore and hash procedure as the main run. No network.

| Mutation | Result | Example failing tests |
| --- | --- | --- |
| R1-M1 final-answer chain check removed | killed, 6 | `test_ag110_chain_of_exactly_262144_bytes_is_sent`, `test_ag113_*`, `test_r1_*[*_plus_one]` |
| R1-M2 `>=` instead of `>` | killed, 3 | `test_r1_*[ascii_exact]`, `[multibyte_exact]`, `after_tool_round[261680-True]` |
| R1-M3 measured in code points | killed, 1 | `test_r1_*[multibyte_plus_one]` |

`agent.py` SHA-256 before and after the run: `ba74ced1d63c77f114da2e86b897d6f43bbde40f1e95427f66f94c70f83a9e4e` (equal). The original 36-mutation script was not re-run. Its M12/M15 pattern now occurs twice, so the script would stop on its uniqueness assertion.

### Gates after R1

| Command | Exit | Result |
| --- | --- | --- |
| `uv run --frozen pytest tests/unit/test_agent_loop.py tests/unit/test_agent_limits.py tests/unit/test_agent_diagnostics.py -q -rs` | 0 | 202 passed |
| `uv run --frozen pytest -q -rs` | 0 | 2462 passed, 2 skipped (same two Windows `?` skips) |
| `uv run --frozen ruff check --no-cache .` | 0 | All checks passed! |
| `uv run --frozen ruff format --check --no-cache .` | 0 | 111 files already formatted |

Hashes after R1: `src/emporio_agent/agent.py` `ba74ced1d63c77f114da2e86b897d6f43bbde40f1e95427f66f94c70f83a9e4e`; `tests/unit/test_agent_limits.py` `12369cd594dfb42ec91103f0a0dd7865fa9cd3318de868b4f356f4b167952d66`; `test_agent_loop.py` and `test_agent_diagnostics.py` unchanged. HEAD `bee89fd` unchanged; only the five slice files are untracked; no tracked file changed. No commit, push or PR. Slice D not started.
