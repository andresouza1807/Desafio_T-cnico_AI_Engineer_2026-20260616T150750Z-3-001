# Validation 07-01: provider types, size functions and OpenAI adapter (slice A)

Date: 2026-11-03. Branch `feature/data-preparation`. Scope: slice A of `docs/plans/07-agent.md` (revision R1, cases AG01–AG36 and AG97–AG108, plus regressions AG119–AG121 of review 07-02-R1) against `docs/specs/07-agent.md` (revision R1 with the review 07-02-R1 clarification). The section "Review 07-02-R1" at the end records the fixes after the first review. No commit, push, PR or merge. No OpenAI request, no network access, no secret, no `.env`, no private data. Session, instructions, loop, CLI, bootstrap and real evaluation are not implemented.

## Initial state

- HEAD `615b7bdd4036bf01b5957f88575c48b1427086fb` (expected value), branch `feature/data-preparation`, `git status --porcelain` empty before any edit.
- No `AGENTS.md` exists in the repository.
- Baseline gates on the clean tree, before any edit:
  - `uv run --frozen pytest -q -rs` → exit 0, **1701 passed, 2 skipped** (Windows `?` file-name skips, `test_readonly_connection.py:599` and `:612`).
  - `uv run --frozen ruff check --no-cache .` → exit 0, "All checks passed!".
  - `uv run --frozen ruff format --check --no-cache .` → exit 0, "96 files already formatted".
- Installed packages read for the gap analysis: `openai` 3.42.1, `httpx2` 2.16.0, `httpcore2` 2.16.0, `pydantic` 2.14.2, Python 3.14.

## Spec Summary

New files only:

1. `src/emporio_agent/provider.py` (no `openai` import).
   - Frozen, slotted dataclasses `UserMessage`, `ProviderItem`, `FunctionCall`, `FunctionCallOutput`, `ModelRequest`, `Usage`, `ModelResponse`, and the alias `ConversationItem` (spec G2).
   - Item constructors check that every text field (`text`, `raw`, `call_id`, `name`, `arguments`, `output`) is exactly `str` and encodes to UTF-8. Otherwise they raise `ValueError` with a fixed message per class. The message never contains the value. The error is raised outside the `except` block, so the `UnicodeEncodeError` (which holds the text) is not chained. `ProviderItem.kind` is limited to `reasoning` and `assistant_message`.
   - `ProviderErrorKind` (12 kinds) and `ProviderError` (spec G3). `transient` is derived from the kind. The exception is `provider_failed`: there it is given explicitly (default `False`). A contradictory flag or an unknown kind raises `ValueError`. `str(error)` is a fixed English sentence per kind, and `args == (message,)`.
   - `ModelProvider`, a `typing.Protocol` marked `runtime_checkable`.
   - `item_size_bytes` and `static_size_bytes`, exactly as in spec G10b. These are logical UTF-8 byte counts. They are not HTTP body sizes and not token counts. Limits and their enforcement belong to slice C.
2. `src/emporio_agent/openai_provider.py`, the only module that imports `openai`.
   - Constants: `MAX_OUTPUT_TOKENS = 8000`, `MAX_RESPONSE_BODY_BYTES = 1_048_576`, `MAX_OUTPUT_ITEMS = 64`, `DEFAULT_BASE_URL = "https://api.openai.com/v1"`.
   - `OpenAIResponsesProvider(*, api_key, model, timeout_seconds=30.0, max_output_tokens=8000, base_url=DEFAULT_BASE_URL, http_client=None)`:
     - The constructor validates its arguments with fixed messages. Then it builds `openai.OpenAI(api_key=..., base_url=..., timeout=float(timeout_seconds), max_retries=0, http_client=...)`. No client is built on import.
     - `repr()` shows the model only.
     - `close()` is idempotent, and the class is a context manager.
     - `create` after `close()` raises `ProviderError("unexpected")` without sending a request.
   - `create` makes exactly one `client.responses.with_raw_response.create(...)` call. The body contains `model` (as configured), `instructions`, `input` (spec G4 mapping), `tools` (`list(request.tools)`), `tool_choice` (`auto`/`none`), `parallel_tool_calls=False`, `store=False` and `max_output_tokens`. It reads `http_response.content` and `x-request-id` only. It never calls `.parse()`.
   - Validation of the success body follows every rule in spec G4b: B1–B7, R1, M1–M4, C1, X1, I1, U1–U4 and T1–T2.
   - SDK failures are mapped with the spec G3 table, including quota vs rate limit and `retry-after-ms`/`retry-after`. Every `ProviderError` is raised `from None`, outside any `except` block, so `__cause__` and `__context__` are `None` and `__suppress_context__` is `True`. `KeyboardInterrupt`/`SystemExit` are not caught.
3. Tests `tests/unit/test_provider_contract.py` and `tests/unit/test_openai_provider.py`, and this report.

No existing file was changed.

## Gap Analysis

### Installed SDK against spec F1–F16 (before tests)

| Fact | Check | Result |
| --- | --- | --- |
| F1, F21 | `openai/_constants.py`: `DEFAULT_MAX_RETRIES = 2`, `DEFAULT_TIMEOUT = Timeout(600, connect=5.0)`. `OpenAI.__init__` accepts `api_key`, `base_url`, `timeout`, `max_retries`, `http_client`. | Matches |
| F2 | `httpx2` 2.16.0 with `MockTransport`, `ReadTimeout`, `ConnectTimeout`, `ConnectError`. | Matches |
| F3 | `_base_client.py` `request`: `for retries_taken in range(max_retries + 1)`. Status errors are raised inside the loop through `_make_status_error_from_response(...) from None`. The header `x-stainless-retry-count` is set from `retries_taken`. | Matches |
| F4 | `_client.py` `_make_status_error`: 400/401/403/404/409/422/429/>=500 mapping. 408 and 418 become a plain `APIStatusError`. | Matches (the adapter classifies by `status_code`, so it does not depend on the subclass) |
| F5 | `APIError` stores `message`, `body`, `code` (`str(...)` of `error.code`) and `type` (`construct_type`, can be a non-`str`). | Matches. The adapter accepts `code`/`type` only when they are a non-empty `str`. |
| F6 | `lib/_client_auth.py` reads `OPENAI_API_KEY`, `OPENAI_ADMIN_KEY`, `OPENAI_ORG_ID`, `OPENAI_PROJECT_ID`, `OPENAI_WEBHOOK_SECRET`, `OPENAI_BASE_URL` (only when `base_url is None`) and `OPENAI_CUSTOM_HEADERS`. | Matches |
| F7 | `SyncAPIClient.close()` closes `self._client`, which is the supplied client. `__exit__` calls `close()`. | Matches |
| F8 | `Responses.create` accepts `input`, `instructions`, `max_output_tokens`, `parallel_tool_calls`, `store`, `tool_choice`, `tools`, `timeout`. | Matches |
| F15, F16 | `_legacy_response.LegacyAPIResponse` has `http_response`, `content`, `headers`, `request_id` (`x-request-id`). The body is parsed only by `.parse()`. | Matches |

Offline probe (scratchpad script, `python -I`, every `OPENAI_*` variable removed, socket guard, `MockTransport`, literal key `test-key-not-a-secret`):

- one `POST https://api.openai.com/v1/responses`;
- `extensions["timeout"] == {"connect": 30.0, "read": 30.0, "write": 30.0, "pool": 30.0}`;
- `x-stainless-retry-count: 0`;
- body keys exactly `model, input, instructions, max_output_tokens, parallel_tool_calls, store, tool_choice, tools`;
- `input` and `tools` deep-equal to what was passed, including unknown item keys (`future_key`, `async`, `caller: null`, `namespace: null`);
- `close()` twice without error.

**No material divergence from F1–F16 was found**, so the contract was not adapted. No dependency was added (`httpx2` is the locked transitive dependency approved in review 07-01-R1).

### Plan and spec points resolved without changing the contract

| # | Point | Decision |
| --- | --- | --- |
| 1 | Plan AG24 lists function call `status "incomplete"` as `response_invalid`. Spec C1 (normative, R1) and plan AG105 make `in_progress`/`incomplete` an incomplete item (I1). | The spec rule was applied. AG24 uses `status "done"` and `5` as invalid statuses. `incomplete`/`in_progress` are tested under AG105 as `status "incomplete"`, `incomplete_reason "item_incomplete"`. The plan text of AG24 was aligned in review 07-02-R1. |
| 2 | Spec I1 sets `incomplete_reason "item_incomplete"` "even when the outer status is completed". When the outer status is already `incomplete`, the text does not say which reason wins. | The outer B6 reason (for example `max_output_tokens`) is kept. `item_incomplete` is used only when the outer status is `completed`. In both cases the status is `incomplete`, so V1 (slice C) is the same. No test asserts the combined case; it is open for review. |
| 3 | B4/B5 do not require `model` and `id` to encode to UTF-8. Both are copied into `ModelResponse` and later into diagnostics. | Stricter rule: `model` and `id` must also encode to UTF-8 (`response_invalid` otherwise, AG104). This follows the brief: "falha de encoding nunca expõe detalhe interno". |
| 4 | U3 says "an object whose `cached_tokens`/`reasoning_tokens` follows the U2 integer rule". A details object without that key is not explicit. | A missing key fails the U2 rule, so it gives `usage None`, `usage_invalid True` (AG103 row `"input_tokens_details":{}`). |
| 5 | `retry-after-ms` present but not numeric, with `retry-after` also present. | Falls back to `retry-after` (AG31 row `abc`/`7` → 7.0). This matches the SDK's own order. Numeric means ASCII `[0-9]+(\.[0-9]+)?`, so `1_0`, `1e3`, `inf`, `nan`, `-1`, an HTTP date and an empty value give `None`, and an overflow to `inf` also gives `None`. |
| 6 | Ownership of a supplied `http_client` when the constructor rejects its arguments. | The supplied client is closed before the `ValueError` (ownership transfers, spec G4). Tested in AG02. |
| 7 | `retry_after_seconds` scope. | Set for every 4xx/5xx status error when the header is valid. It is `None` for body `failed`, redirects (review 07-02-R1), transport and validation failures. The loop (slice C) decides whether to use it. |
| 8 | `ruff format` (target `py314`) rewrote `except (A, B, C, D):` as `except A, B, C, D:` (PEP 758). | Accepted, because the formatter gate requires it. Mutations were re-run on the formatted bytes. |

## Implementation Plan (as executed)

1. Confirmed HEAD, clean tree and baseline gates (above). Read spec 07 R1, plan 07 slice A, the installed SDK sources and `provider_tool_definitions()`.
2. Ran the offline SDK probe (gap analysis).
3. Wrote both test files, then ran them with no production module present (RED).
4. Implemented `provider.py`, then `openai_provider.py`.
5. Ran GREEN, mutations (with restore and SHA-256 check), regression and gates. Ran `ruff format` on the four new files only. Re-ran mutations and gates on the final bytes.
6. Wrote this report and re-ran Ruff. Stopped for review. Slice B was not started.

## Test Checklist

### Map AGxx → tests

`C` = `tests/unit/test_provider_contract.py`, `O` = `tests/unit/test_openai_provider.py`.

| Case | Test(s) |
| --- | --- |
| AG01 | `C::test_ag01_imports_create_no_client_and_open_no_socket`: a subprocess with `python -I`, no `OPENAI_*`, a guard class for `socket.socket`, and `create_connection`/`getaddrinfo`/`socketpair` replaced. `provider` leaves `openai` unimported. `openai_provider` builds 0 clients (spy on `OpenAI.__init__`) and no module attribute is a client. 0 socket attempts. Contract types: `C::test_contract_types_are_frozen_slotted_dataclasses` (7 types), `C::test_model_provider_is_a_protocol_satisfied_by_a_scripted_provider`, `C::test_provider_item_kind_is_restricted`. Also checks the G3 surface: `C::test_provider_error_*` (5 functions). |
| AG02 | `O::test_ag02_constructor_rejects_invalid_arguments` (24 rows: blank, padded, `None`, `int`; timeout `0`, `-1`, `nan`, `inf`, `True`, `"30"`; tokens `0`, `-5`, `True`, `1.0`; base URL), `O::test_ag02_message_is_fixed_per_argument`, `O::test_ag02_invalid_arguments_close_the_supplied_client`, `O::test_max_output_tokens_default_is_8000` |
| AG03 | `O::test_ag03_repr_and_str_show_model_never_key` |
| AG04 | `O::test_ag04_one_request_with_timeout_retry_count_and_url` |
| AG05 | `O::test_ag05_explicit_base_url_wins_over_environment` (`OPENAI_BASE_URL=https://example.invalid/v1` through `monkeypatch`, plus an explicit non-default URL) |
| AG06 | `O::test_ag06_request_body_literal` (whole body equal to a literal dict, and the 15 omitted parameters checked absent), `O::test_ag06_max_output_tokens_is_configurable` |
| AG07 | `O::test_ag07_tool_calls_not_allowed_sends_none_and_keeps_tools` |
| AG08 | `O::test_ag08_input_mapping_for_each_item_kind_in_order` (user, reasoning, assistant message, call, output; payload byte-equal) |
| AG09 | `O::test_ag09_tools_are_provider_tool_definitions_unchanged` (deep-equal and equal `json.dumps`, so key order is kept) |
| AG10 | `O::test_ag10_key_only_in_authorization_header` |
| AG11 | `O::test_ag11_completed_text_response` (whole `ModelResponse` equal to a literal) |
| AG12 | `O::test_ag12_function_call_item` |
| AG13 | `O::test_ag13_reasoning_keeps_encrypted_content_and_summary` |
| AG14 | `O::test_ag14_commentary_phase_kept_in_raw` |
| AG15 | `O::test_ag15_mixed_output_keeps_provider_order` |
| AG16 | `O::test_ag16_text_is_concatenation_without_separator` |
| AG17 | `O::test_ag17_refusal_flag_and_text_excluded` |
| AG18 | `O::test_ag18_incomplete_reason` (4 F9 reasons, unknown reason, missing details), `O::test_ag18_completed_ignores_incomplete_details` |
| AG19 | `O::test_ag19_usage_variants` |
| AG20 | `O::test_ag20_configured_model_sent_reported_model_returned` |
| AG21 | `O::test_ag21_failed_status_is_provider_failed` (`server_error`, `rate_limit_exceeded` transient; `invalid_prompt` not transient; `request_id` kept) |
| AG22 | `O::test_ag22_unusable_status_is_invalid` |
| AG23 | `O::test_ag23_unexpected_item_or_part_type_is_invalid` |
| AG24 | `O::test_ag24_function_call_rules_c1` (16 rows), `O::test_ag24_empty_name_and_non_json_arguments_returned_unchanged`, `O::test_ag24_function_call_optional_fields_accepted` (`call_id` of 256 code points accepted; 257 rejected in the C1 rows). See gap point 1. |
| AG25 | `O::test_ag25_status_matrix_one_request_each` (400, 401, 403, 404, 408, 409, 418, 422, 429, 500, 502, 503) |
| AG26 | `O::test_ag26_quota_is_not_transient` (5 quota names × `code`/`type`), `O::test_ag26_slow_down_and_rate_limit_are_transient` |
| AG27 | `O::test_ag27_model_not_found_is_model_unavailable` |
| AG28 | `O::test_ag28_transport_errors_one_request` (Read/Connect/Write/Pool timeouts → `timeout`; ConnectError/ReadError/RemoteProtocolError → `connection`) |
| AG29 | `O::test_ag29_non_json_and_non_list_output_are_invalid` |
| AG30 | `O::test_ag30_no_leakage_except_request_id`. 11 steps: status 400/401/429/500 with sentinel JSON bodies and headers, 503 with a sentinel text body, body `failed` with a sentinel error, an invalid status, an invalid model, ConnectError/ReadTimeout/RuntimeError with sentinel messages. The sentinels are the body, a fake `sk-` string, request text placed in instructions and user message, header values and the key. They are checked against `str`, `repr`, `args`, `__dict__`, each attribute and `traceback.format_exception`. Also asserted: `__cause__ is None`, `__suppress_context__ is True`, `__context__ is None`. |
| AG31 | `O::test_ag31_retry_after` (15 rows), `O::test_ag31_retry_after_on_server_error` |
| AG32 | `O::test_ag32_request_id_is_the_only_header_copied` (error and success; another header with a sentinel is not copied; no header gives `None`) |
| AG33 | `O::test_ag33_other_exception_is_unexpected` |
| AG34 | `O::test_ag34_keyboard_interrupt_and_system_exit_propagate` |
| AG35 | `O::test_ag35_close_is_idempotent_and_blocks_create`, `O::test_ag35_context_manager_closes_on_exit_and_on_exception` |
| AG36 | `O::test_ag36_sdk_retries_disabled` (500 + `x-should-retry: true`; 429 + `retry-after: 0`; 400 + `x-should-retry: true`; a second scripted response stays unused), `O::test_ag36_timeout_is_not_retried` |
| AG97 | `O::test_ag97_body_size_limit_exact_and_plus_one` (1,048,576 accepted; 1,048,577 rejected; lengths asserted), `O::test_ag97_body_decoding_rules` (invalid UTF-8, duplicate top-level and nested keys, `NaN`, `Infinity`, `-Infinity`, top-level array, string, empty body, UTF-8 BOM) |
| AG98 | `O::test_ag98_model_rules_b4` (absent, `""`, `5`, `null`, 257 code points, `\n`, `\u0000`, `\u007f`), `O::test_ag98_model_limits_accepted` (256 × `"ç"`), `O::test_ag98_id_accepted_b5`, `O::test_ag98_id_rejected_b5`, `O::test_ag98_id_256_code_points_accepted` |
| AG99 | `O::test_ag99_status_rules_b3`, `O::test_ag99_failed_status_transience` (7 `error` shapes), `O::test_ag99_incomplete_details_b6`, `O::test_ag99_incomplete_details_not_object_is_invalid` |
| AG100 | `O::test_ag100_output_rules_b7_x1` (absent, `null`, object, 65 items, string item, number item, no `type`, `type` `5`, `type` `null`), `O::test_ag100_exact_item_limit_and_empty_list` |
| AG101 | `O::test_ag101_incomplete_message_makes_response_incomplete`, `O::test_ag101_message_rules_m1_m2_m4` (17 rows), `O::test_ag101_message_optional_id_and_empty_content_accepted` |
| AG102 | `O::test_ag102_final_eligible_phases`, `O::test_ag102_commentary_excluded_and_flagged`, `O::test_ag102_unknown_phase_is_invalid` |
| AG103 | `O::test_ag103_absent_usage_is_none_not_invalid`, `O::test_ag103_invalid_usage_is_none_and_flagged` (21 rows, the response is still accepted), `O::test_ag103_null_details_are_none` |
| AG104 | `O::test_ag104_lone_surrogate_is_invalid` (`output_text`, `refusal`, `arguments`, `name`, `encrypted_content`, unknown field, nested unknown field), `O::test_ag104_lone_surrogate_in_model_or_id_is_invalid` (gap point 3), `O::test_ag104_valid_surrogate_pair_is_accepted` |
| AG105 | `O::test_ag105_reasoning_rules_r1` (10 rows), `O::test_ag105_reasoning_optional_fields_accepted`, `O::test_ag105_incomplete_items_make_response_incomplete` |
| AG106 | `O::test_ag106_raw_preservation_and_resend`. Reasoning has `encrypted_content` with non-ASCII text and an unknown nested key. The message has `phase`, `annotations`, `logprobs` and an unknown key. The call has `id`, `status`, `caller` and `namespace`. `raw` equals the literal item text of the HTTP body, and request 2 resends the same compact JSON in the same order. |
| AG107 | `C::test_ag107_item_size_bytes_literal` (10 rows: ASCII, `ç`, `🎸`, mixed, empty, a 200,000-byte `raw`, a multibyte `raw`, `FunctionCall` counting `raw` only, `FunctionCallOutput` counting `call_id + output`), `C::test_ag107_item_size_bytes_rejects_other_objects`, `C::test_ag107_static_size_bytes_literal` (44, 12 with `ensure_ascii=False`, 5 with an empty tool tuple) |
| AG108 | `C::test_ag108_constructors_reject_bad_text` (8 fields × 7 bad values: lone high and low surrogates in several positions, `None`, `bytes`, `list`, `int`), `C::test_ag108_message_is_fixed_per_field`, `C::test_ag108_valid_pair_and_empty_text_accepted` |

Counts: `test_provider_contract.py` has 15 test functions and 109 collected cases. `test_openai_provider.py` has 75 test functions and 293 collected cases. Total: **402**.

### RED

Command: `uv run --frozen pytest tests/unit/test_provider_contract.py tests/unit/test_openai_provider.py -q -rs`, run before any production file existed.

- Exit 2, "2 errors in 0.26s", "Interrupted: 2 errors during collection".
- Cause, in both files: `ModuleNotFoundError: No module named 'emporio_agent.provider'` and `No module named 'emporio_agent.openai_provider'`.
- The plan expects this RED. No case was green before production existed.
- No born-green case: the collection error covers every case, and no test was run against a partial implementation.

### GREEN

- The first run after both modules were implemented gave exit 0, **402 passed**.
- No defect was found in the tests themselves. No test was weakened or rewritten to pass.
- After `ruff format`, the same command again gave exit 0, 402 passed.
- The Ruff fixes before formatting were made in the test files only: 4 lines over 100 characters and one UP012 (`'...'.encode()` → `b'...'`).

### Mutations

Script: scratchpad `mutate.py`.

- One textual mutation of `openai_provider.py` at a time. Each mutation target must occur exactly once.
- Each run is `python -m pytest <both slice files> -x -q -p no:cacheprovider`.
- The file bytes are restored in `finally`, and the SHA-256 is checked after each mutation.
- Isolation: the tests use `MockTransport`, an autouse socket guard and no `OPENAI_*` variable. No DNS lookup, real socket or provider call is made.
- The final run used the formatted bytes:
  - baseline `openai_provider.py` SHA-256 `bb3e5dd054cc9d0bf8f537fda7d9c58beea87af811ab6149c3d59bdf010126b7`;
  - after all mutations: the same value (`True`).
- An earlier run on the pre-format bytes (SHA-256 `89ba1e10…dee20c`) gave identical outcomes.

| ID | Mutation | Result | First failing test |
| --- | --- | --- | --- |
| M01 | `max_retries=0` removed (SDK default 2) | killed | `test_ag25_status_matrix_one_request_each[408-…]` |
| M02 | `store=True` | killed | `test_ag06_request_body_literal` |
| M03 | `parallel_tool_calls` omitted | killed | `test_ag06_request_body_literal` |
| M04 | assistant `raw` reduced to the `output_text` | killed | `test_ag11_completed_text_response` |
| M05 | reasoning items dropped from `output` | killed | `test_ag13_reasoning_keeps_encrypted_content_and_summary` |
| M06 | `encrypted_content` dropped | killed | `test_ag13_…` |
| M07 | `phase` dropped from `raw` | killed | `test_ag14_commentary_phase_kept_in_raw` |
| M08 | 429 quota classified as `rate_limited` (transient) | killed | `test_ag26_quota_is_not_transient[code-insufficient_quota]` |
| M09 | SDK error message copied into `args` | killed | `test_ag30_no_leakage_except_request_id[step0]` |
| M10 | `.parse().model_dump()` instead of the raw body | killed | `test_ag11_completed_text_response` |
| M11 | `bool` accepted as a usage integer (`isinstance`) | killed | `test_ag103_invalid_usage_is_none_and_flagged[…true…]` |
| M12 | invalid usage reported as zeros, `usage_invalid False` | killed | `test_ag103_…` |
| M13 | message `status "incomplete"` accepted as final | killed | `test_ag101_incomplete_message_makes_response_incomplete["incomplete"]` |
| M14 | all response headers copied into `request_id` (success) | killed | `test_ag11_completed_text_response` |
| M15 | all error headers copied into `request_id` | killed | `test_ag30_…[step0]` |
| M16 | body limit `>=` (off by one) | killed | `test_ag97_body_size_limit_exact_and_plus_one` |
| M17 | item limit `>=` (off by one) | killed | `test_ag100_exact_item_limit_and_empty_list` |
| M18 | commentary text included in `text` | killed | `test_ag102_commentary_excluded_and_flagged` |
| M19 | lenient UTF-8 decode (`errors="replace"`) | killed | `test_ag97_body_decoding_rules[invalid-utf8]` |
| M20 | duplicate-key hook removed | killed | `test_ag97_body_decoding_rules[duplicate-status]` |
| M21 | T2 check removed from `_raw` | **survived** (402 passed) | — |
| M22 | function call `status "incomplete"` accepted | killed | `test_ag105_incomplete_items_make_response_incomplete[item2]` |

M21 is an equivalent mutant:

- The item constructors in `provider.py` (spec G2) repeat the T2 encodability check.
- After M21, a lone surrogate still raises `ValueError` in `ProviderItem`/`FunctionCall`, and the adapter maps that to `response_invalid`.
- To confirm this, scratchpad `mutate_m21b.py` removed both checks together (`_raw` in `openai_provider.py` and `value.encode("utf-8")` in `provider.py`). Result: exit 1, "7 failed, 3 passed" on `-k ag104`. Both files were restored with SHA-256 equal.
- The adapter check stays in the code as the explicit T2 rule.

## Test Strategy

- pytest with the real `openai` 3.42.1 SDK and `http_client=httpx2.Client(transport=httpx2.MockTransport(handler))`. The test-only `Transport` class records every `httpx2.Request` and returns the next literal `httpx2.Response`, or calls a step that raises. A missing step fails the request.
- Autouse fixture `offline`:
  - removes every `OPENAI_*` variable with `monkeypatch.delenv`, without printing values;
  - replaces `socket.create_connection`, `socket.getaddrinfo`, `socket.socket.connect` and `socket.socket.connect_ex` with a raising guard.
- AG01 runs in a `python -I` subprocess with a guard class for `socket.socket` and an environment without `OPENAI_*`.
- Key: only the literal `"test-key-not-a-secret"`. No `.env` was read, and no environment value was printed.
- Oracles are literal:
  - JSON bodies are written by hand. The `envelope`/`message`/`function_call`/`reasoning` helpers only join literal JSON fragments and never call production code.
  - Expected `ModelResponse` values, request bodies, item `raw` strings, byte counts and error kinds are literals.
  - The only computed lengths are the AG97 padding (asserted against the literals 1,048,576 and 1,048,577) and the independent `len(... .encode())` in AG107.
- No mock of tool validation, business rules, SQL or ranking. AG09 uses the real `provider_tool_definitions()`.

## Compliance Report

### Commands and results

| Command | Exit | Result |
| --- | --- | --- |
| `uv run --frozen pytest -q -rs` (baseline) | 0 | 1701 passed, 2 skipped |
| `uv run --frozen ruff check --no-cache .` (baseline) | 0 | All checks passed |
| `uv run --frozen ruff format --check --no-cache .` (baseline) | 0 | 96 files already formatted |
| `uv run --frozen pytest tests/unit/test_provider_contract.py tests/unit/test_openai_provider.py -q -rs` (RED) | 2 | 2 collection errors (`ModuleNotFoundError`) |
| same (GREEN, final bytes) | 0 | 402 passed, 0 skipped |
| `uv run --frozen pytest tests/unit/test_pricing.py -q` | 0 | 37 passed |
| `uv run --frozen pytest -q -rs` (final) | 0 | **2103 passed, 2 skipped** (1701 + 402; same two Windows skips) |
| `uv run --frozen ruff check --no-cache .` (before formatting) | 1 | 4 × E501, 1 × UP012, test files only |
| `uv run --frozen ruff format --no-cache <four new files>` | 0 | 3 files reformatted, 1 unchanged |
| `uv run --frozen ruff check --no-cache .` (final, before report) | 0 | All checks passed |
| `uv run --frozen ruff format --check --no-cache .` (final, before report) | 0 | 100 files already formatted |
| `uv run --frozen python <scratchpad>/mutate.py` | 0 | 21 killed, 1 equivalent survivor (M21), restore verified |
| `uv run --frozen python <scratchpad>/mutate_m21b.py` | 0 | double mutation killed (7 failed), restore verified |

Ruff was run again after this report was written (results in the hand-off message).

### Files

Created (SHA-256 of the final bytes):

- `src/emporio_agent/provider.py`: `6c22ee8294d826578c17d7ec56b006487c275401a62f6f1865a19ce0200f4bce`
- `src/emporio_agent/openai_provider.py`: `bb3e5dd054cc9d0bf8f537fda7d9c58beea87af811ab6149c3d59bdf010126b7` (first delivery; superseded, see "Review 07-02-R1")
- `tests/unit/test_provider_contract.py`: `a6b43ea3344256d6aa5707903b7aae09013013517d7a7bd2907bfcbbcb655aa2`
- `tests/unit/test_openai_provider.py`: `934ee44bbd630f8d0299d7e53391606957d4094e72966926c772074924453799` (first delivery; superseded, see "Review 07-02-R1")
- `docs/validation/07-01-openai-adapter.md` (this report)

Changed: none. Tools, services, data layer, earlier tests, `conftest.py`, README, specs, plans, earlier reports, `pyproject.toml` and `uv.lock` are untouched (`git status --porcelain` lists only the five new files).

### Contract conformance

- Spec G2 types and constructors, G3 error kinds, classification and leakage rules, G4 request mapping and ownership, G4b every rule, and G10b size functions: implemented and covered as mapped above.
- Points decided in the gap analysis (1–8) are reported for review. None changes an approved interface.

### Limits of this evidence

- The tests are offline. They prove the adapter's behaviour with the installed SDK over a mock transport and literal bodies. They do **not** prove:
  - access to the configured model;
  - provider acceptance of the five strict schemas;
  - acceptance of resent `id`/`status`/`phase`/`encrypted_content` items with `store=false`;
  - that `encrypted_content` is present for the chosen model;
  - real error-body shapes or real header formats.
- These remain spec Q-07 and evaluation cases EV01–EV08 (slice E, not authorized).
- The size functions are logical measures. They are not the HTTP body size and not a token bound.
- The SDK still reads some `OPENAI_*` variables during construction (F6), including `OPENAI_CUSTOM_HEADERS`. Only `OPENAI_BASE_URL` is neutralized, by the explicit `base_url`.

Status of the first delivery: slice A implemented and validated offline. The final status is at the end of "Review 07-02-R1".

## Review 07-02-R1

Date: 2026-11-03. Fixes after the first review of slice A. No commit, push, dependency, tool or service change. Slice B was not started.

### Initial state

- HEAD `615b7bdd4036bf01b5957f88575c48b1427086fb`. Untracked files: only the five slice A files.
- SHA-256 before the fix:
  - `openai_provider.py`: `bb3e5dd0…126b7`
  - `test_openai_provider.py`: `934ee44b…453799`
  - `provider.py`: `6c22ee82…0bf4bce`
  - `test_provider_contract.py`: `a6b43ea3…655aa2`
  - this report: `2cab7865…8442a`

### Spec Summary

1. **`failed` body fallback (B3).** `_failed` checks `type(code) is str` before the lookup in `_FAILED_TRANSIENT_CODES`. An `error.code` that is a list, an object or any other non-`str` value gives `ProviderError("provider_failed")`, `transient=False`. The known codes `server_error` and `rate_limit_exceeded` stay transient.
2. **`incomplete` reason fallback (B6).** `_incomplete_reason` checks `type(reason) is str` before the lookup in `_INCOMPLETE_REASONS`. A list or object `reason` gives `status "incomplete"`, `incomplete_reason "other"`. An `incomplete_details` that is not an object is still `response_invalid`. Both fixes are explicit fallbacks, not a generic `TypeError` catch.
3. **Redirects are never followed.**
   - Without `http_client`: the adapter builds `openai.DefaultHttpxClient(follow_redirects=False)`, the SDK's exported default client. The SDK closes it on `close()`, because it becomes the SDK client's `_client`.
   - With a supplied client (owned by the adapter, spec G4): the adapter sets the public attribute `httpx2.Client.follow_redirects = False`.
   - In `_status_failure`, a 3xx status becomes `ProviderError("bad_request")`, not transient, with `status_code` and `request_id` (`x-request-id`) only. `retry_after_seconds` is `None`. Location, body and other headers are not copied.
   - Production patches no SDK or transport internal. Public signature and dependencies are unchanged.
4. **Docs.** Spec: status line, one G3 table row (redirect), one G4 bullet (redirects), section "Review 07-02-R1". Plan: AG24 aligned with C1/I1 and AG105, regressions AG119–AG121 added to slice A, one RED note, one traceability row.

### Gap Analysis

- **Cause of the two `TypeError`s.**
  - `x in frozenset(...)` hashes `x` first. A JSON array decodes to `list` and a JSON object to `dict`; both are unhashable. So `code in _FAILED_TRANSIENT_CODES` and `reason in _INCOMPLETE_REASONS` raised `TypeError: unhashable type`.
  - `_validated` caught `TypeError` in its `response_invalid` branch. So a body that B3 classifies as `provider_failed`, and a body that B6 accepts as `incomplete`/`other`, both became `response_invalid`.
  - Scalars (`true`, `1.5`, `null`, `5`) are hashable, so they were already correct (confirmed by the RED below).
- **Retry vs redirect.**
  - A retry is the SDK loop in `_base_client.request`: `for retries_taken in range(max_retries + 1)`. Each pass builds a new request and calls `_send_request`. With `max_retries=0` there is exactly one pass.
  - A redirect is followed inside that single pass, by `httpx2.Client.send` (`_send_handling_redirects`), when `follow_redirects` is true. So one SDK attempt can send several HTTP requests to other URLs, and `max_retries=0` does not limit them.
  - The SDK default client `_DefaultHttpxClient` sets `kwargs.setdefault("follow_redirects", True)` (`_base_client.py:974`).
  - The SDK passes a per-request `follow_redirects` only when `FinalRequestOptions.follow_redirects` is set. `Responses.create` does not expose that option.
  - When the redirect is not followed, `httpx2.Response.raise_for_status` raises for a 3xx. Its message holds the URL and the Location; it is not copied. `_should_retry` is false for 3xx, and `max_retries=0` allows no second pass anyway. The SDK then raises `APIStatusError` with the 3xx status.
  - Before the fix, a supplied client with `follow_redirects=True` followed the redirect to the `Location` host. The mock answered 200 there, so `create` returned a success from the redirected URL ("DID NOT RAISE").
- **Public interface or dependency change: none.** `openai.DefaultHttpxClient` is in `openai.__all__`. `follow_redirects` is a plain public attribute set in `httpx2.Client.__init__`.
- **Accepted side effect.** Without `http_client`, the adapter now builds the client through the public SDK class instead of inside `SyncAPIClient`. The request timeout is still the client-level 30 s, with `extensions["timeout"]` 30.0 for all phases (asserted in `test_ag121_adapter_built_client_still_answers_and_uses_timeout`). Connection limits are the SDK defaults of that class.

### Implementation Plan (as executed)

1. Confirmed HEAD and the five untracked files (hashes above).
2. Added AG119 (9 rows), AG120 (6 rows) and AG121 (5 + 5 + 2 + 1 cases) to `test_openai_provider.py`, then ran them against the unchanged adapter (RED).
3. Fixed `_failed`, `_incomplete_reason`, `_status_failure` (3xx branch) and the constructor (redirects disabled).
4. Ran GREEN, the review mutations, the slice A mutation suite again and the gates. Updated the documentation, then ran Ruff again.

### Test Checklist

| Case | Tests |
| --- | --- |
| AG119 | `test_ag119_failed_error_code_of_any_type`. Not transient: `["server_error"]`, `{"value":"server_error"}`, `[]`, `{}`, `true`, `1.5`, missing code. Transient: `"rate_limit_exceeded"`, `"server_error"`. `__context__ is None` in every row. The earlier AG99 rows still pass unchanged. |
| AG120 | `test_ag120_incomplete_reason_of_any_type_is_other`. `["max_output_tokens"]`, `{"value":"max_output_tokens"}`, `[]`, `{}`, `true`, `1.5` → `incomplete`/`"other"`, text kept. The earlier AG18/AG99 rows (known reasons, unknown string, `null`, `5`, missing, non-object details invalid) still pass unchanged. |
| AG121 | Four tests: `test_ag121_supplied_client_following_redirects_is_not_followed` (301/302/303/307/308, `httpx2.Client(follow_redirects=True)`); `test_ag121_sdk_default_style_client_is_not_followed` (same statuses, a supplied `openai.DefaultHttpxClient`, with `follow_redirects is True` asserted before use); `test_ag121_client_built_by_adapter_is_not_followed` (301/307, no `http_client`; the test replaces `openai.DefaultHttpxClient` with a subclass that only injects `MockTransport`); `test_ag121_adapter_built_client_still_answers_and_uses_timeout`. See the assertions below. |
| AG24 | Plan text aligned. Tests unchanged: `status "done"` and `5` are invalid; `incomplete`/`in_progress` are covered by `test_ag105_incomplete_items_make_response_incomplete`. |

Each AG121 redirect case asserts:

- `bad_request`, not transient, `status_code` kept, `request_id "req_redirect"`;
- `retry_after_seconds None`, although the response sends `retry-after: 1` and `x-should-retry: true`;
- exactly one request, to `https://api.openai.com/v1/responses`, and the scripted 200 for the redirect target is never used;
- no `SENTINEL-LOCATION`, body sentinel, fake `sk-` value or key in `str`, `repr`, `args`, `__dict__`, attributes or traceback;
- `__cause__` and `__context__` are `None`;
- the client is closed after the context manager exits.

**RED.** Command `uv run --frozen pytest tests/unit/test_openai_provider.py -q -rs -k "ag119 or ag120 or ag121"`, run against the unchanged adapter. Exit 1, **21 failed, 7 passed**, 293 deselected.

| Group | Failed | Failure |
| --- | --- | --- |
| AG119, list/object rows | 4 | `('response_invalid', False) != ('provider_failed', False)` |
| AG120, list/object rows | 4 | `ProviderError: The model provider returned an invalid response.` |
| AG121, supplied client and SDK default client | 10 | `Failed: DID NOT RAISE ProviderError`: the redirect was followed to the mock target |
| AG121, adapter-built client | 2 | `('unexpected', False, None) != ('bad_request', False, 301)` |
| AG121, timeout case | 1 | `ProviderError: Unexpected failure…` |

In the 3 adapter-built rows, the adapter did not use `openai.DefaultHttpxClient`, so the SDK built its own client with a real transport. The autouse socket guard raised at `create_connection`, so no DNS lookup and no connection happened.

Already green (7): AG119 `true`, `1.5`, `rate_limit_exceeded`, `server_error`, `{}`; AG120 `true`, `1.5`. These are the preserved scalar and known-code behaviours.

**GREEN.** Both slice A files: exit 0, **430 passed** (402 + 28). No test was changed to fit the code.

### Test Strategy

- The approach is unchanged from slice A: real SDK over `httpx2.MockTransport`, autouse `OPENAI_*` removal and socket guard, key `test-key-not-a-secret`, literal bodies and expected values.
- The only test-side replacement is `openai.DefaultHttpxClient` in AG121: a subclass that injects the mock transport. Production is not patched.
- Review mutations (scratchpad `mutate_r1.py`): one textual change at a time, restore in `finally`, SHA-256 checked. `openai_provider.py` measured `ca5ca099…2fd691d` before the mutations and after the restore (`True`).

| ID | Mutation | Result | First failing test |
| --- | --- | --- | --- |
| R01 | adapter-built client without `follow_redirects=False` | killed | `test_ag121_client_built_by_adapter_is_not_followed[301]` |
| R02 | supplied client's `follow_redirects` left unchanged | killed | `test_ag121_supplied_client_following_redirects_is_not_followed[301]` |
| R03 | type check removed in `_failed` | killed | `test_ag119_…[{"code":["server_error"]}-False]` |
| R04 | type check removed in `_incomplete_reason` | killed | `test_ag120_…[["max_output_tokens"]]` |
| R05 | 3xx branch removed (falls to `bad_request` with a parsed `retry-after`) | killed | `test_ag121_supplied_…[301]` (`retry_after_seconds` 1.0) |
| R06 | redirect classified `server_error` (transient) | killed | `test_ag121_supplied_…[301]` |

The slice A suite `mutate.py` was re-run on the final bytes: M01–M20 and M22 killed; M21 is still the equivalent survivor described above. Restore verified.

### Compliance Report

| Command | Exit | Result |
| --- | --- | --- |
| `uv run --frozen pytest tests/unit/test_openai_provider.py -q -rs -k "ag119 or ag120 or ag121"` (RED) | 1 | 21 failed, 7 passed |
| `uv run --frozen pytest tests/unit/test_provider_contract.py tests/unit/test_openai_provider.py -q -rs` | 0 | 430 passed |
| `uv run --frozen pytest tests/unit/test_pricing.py -q` | 0 | 37 passed |
| `uv run --frozen pytest -q -rs` | 0 | **2131 passed, 2 skipped** (1701 + 430; same two Windows skips) |
| `uv run --frozen ruff format --no-cache <two changed .py files>` | 0 | 2 files left unchanged |
| `uv run --frozen ruff check --no-cache .` (before docs) | 0 | All checks passed |
| `uv run --frozen ruff format --check --no-cache .` (before docs) | 0 | 101 files already formatted |
| `uv run --frozen python <scratchpad>/mutate_r1.py` | 0 | 6/6 killed, restore verified |
| `uv run --frozen python <scratchpad>/mutate.py` | 0 | 21 killed, M21 equivalent, restore verified |

Ruff check and format check were run again after the documentation (results in the hand-off message).

SHA-256 after this review:

| File | Change | SHA-256 |
| --- | --- | --- |
| `src/emporio_agent/openai_provider.py` | changed | `ca5ca099fc894ed135df97c8b34ddc7cface04c630892eb4d4855f2292fd691d` |
| `tests/unit/test_openai_provider.py` | changed | `1294a849afeda06a10b68119036d294ab4bf3ee4a97719990facf56336792cb5` |
| `docs/specs/07-agent.md` | changed: status line, G3 row, G4 bullet, review section | `a6ab052820da4514423ce809094383c00fa164e53c507fad57042ee472943c94` |
| `docs/plans/07-agent.md` | changed: AG24, AG119–AG121, RED note, traceability row | `4e2157074eade3f76e49495e8526bbb1373880ab61f3cfc7ebcd9548271b177e` |
| `src/emporio_agent/provider.py` | unchanged | `6c22ee8294d826578c17d7ec56b006487c275401a62f6f1865a19ce0200f4bce` |
| `tests/unit/test_provider_contract.py` | unchanged | `a6b43ea3344256d6aa5707903b7aae09013013517d7a7bd2907bfcbbcb655aa2` |
| this report | changed | given in the hand-off message |

No other file changed.

Limits:

- The redirect tests use a mock transport. They prove that the adapter's HTTP clients do not follow redirects and that the error is fixed. They do not prove how the real provider or a proxy behaves.
- Every slice A limit above still applies: model access, schema acceptance and acceptance of resent items are not proven (Q-07, EV01–EV08).

Status: slice A with review 07-02-R1 applied, validated offline. Waiting for review. Slice B was not started.
