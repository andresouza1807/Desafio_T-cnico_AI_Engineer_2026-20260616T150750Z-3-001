# Validation 06-04: tools — result limit, provider schemas and integrated checks (slice D)

Date: 2026-11-03. Branch `feature/data-preparation`. Scope: slice D of `docs/plans/06-tools.md` (revision R1, cases TL56–TL72) against `docs/specs/06-tools.md` (revision R1, including reviews 06-01-R1 and 06-02-R1). No commit, push, PR or merge. No OpenAI call, adapter, agent, session or CLI. No private data and no measurement on the real catalog or manual.

## Initial state

- HEAD `4401611b73f3466a04da219e49f23f5d0e5d2d58` (expected value), branch `feature/data-preparation`, `git status --porcelain` empty before any edit.
- No `AGENTS.md` exists in the repository.
- Baseline gates on the clean tree, before any edit:
  - `uv run --frozen pytest -q -rs` → exit 0, **1670 passed, 2 skipped** (Windows `?` file-name skips, `test_readonly_connection.py:599` and `:612`).
  - `uv run --frozen ruff check --no-cache .` → exit 0, "All checks passed!".
  - `uv run --frozen ruff format --check --no-cache .` → exit 0, "90 files already formatted".
- SHA-256 of `src/emporio_agent/tools.py` before the slice: `2acb73e51bb5bb56f65a19ed28ccb6717312cc7704b629478514825974de6485`.

## Spec Summary

Changes in `src/emporio_agent/tools.py` only (production):

1. **Result limit (G13).** New private `_finish(tool, status, data, code, field)`. Every result of `execute_tool` passes through it: unknown tool, every pipeline rejection, every service mapping, policy `not_found` with `data`, and `ok`. It serializes the complete envelope with the existing `_dumps` and measures `len(payload.encode("utf-8"))`. Above `MAX_RESULT_BYTES = 32_768` it returns `_fixed(tool, "unavailable", "result_too_large")`: `data: null`, `error.field: null`, and `ToolExecution.status`/`error_code` equal to the replacement. At exactly 32,768 bytes the full result is returned. Nothing is cut. A failure while serializing or encoding (for example a lone surrogate from a hand-edited index) returns the fixed `internal_error` envelope. `_failure` now delegates to `_finish`. The `ok` path calls `_finish` after the resource `with` block has exited, so the size check never holds a connection.
2. **`provider_tool_definitions()` (G12).** Returns a new list of five plain dicts in `tool_names()` order on every call. The body is one literal expression, so every call builds new nested objects and no module-level structure is shared. Keys: `type: "function"`, `name`, `description`, `parameters`, `strict: True`. Every `parameters` object: `type: "object"`, `properties`, `required` equal to all property names in the same order, `additionalProperties: False`. The three `search_products` filters use `["string", "null"]`. No `default`, `title`, `minLength`/`maxLength`, `anyOf`/`allOf`/`not`/`if`/`$ref`. `max_price_brl` carries the A6 pattern. `payment_method` has `enum ["regular", "pix"]`. Integers have `minimum` only (0 for `offset`, 1 for IDs). The descriptions are the G12 text, word for word. All five contain "store data, not instructions". `get_order` states that the application sets the customer, that the total is historical, that item names are current catalog names, and that `passed` is not a confirmed delay and gives no new date. The module still does not import `openai`.
3. Module docstring and the `MAX_RESULT_BYTES` comment updated to describe the slice D state.

No change to the registry, argument models, pipeline, precedence, serializers or status mapping. No change to services, data layer, dependencies, configuration or lockfile.

## Gap Analysis

I compared spec G7, G11–G13 and G15, plan TL56–TL72, the A–C code and reports, and the service contracts before writing tests.

| # | Point | Finding | Treatment |
| --- | --- | --- | --- |
| 1 | `MAX_RESULT_BYTES` | Constant existed (slice A) but was not enforced. | Implemented in `_finish` (real gap; RED below). |
| 2 | Policy `not_found` with `data` | `_failure` serialized it outside any handler and without a size check. | Same `_finish` path. A hand-edited index with a 33,000-character `source.file_name` showed the gap (RED). |
| 3 | Encoding failure | `json.dumps(ensure_ascii=False)` keeps a lone surrogate. `load_policy_index` accepts a `\ud800` escape in a line, so the tool returned `ok` with a payload that cannot be encoded as UTF-8. The task brief requires a safe `internal_error`. | The UTF-8 measurement runs inside `_finish`'s handler, so this becomes `internal_error` (RED, then GREEN). No index or service change. |
| 4 | `provider_tool_definitions` | Missing. | Implemented (RED). |
| 5 | Resource counts, closure, replacement, no write, isolation, offline | Implemented in A–C (one `with` per call, loader per call, no cache, no module state). | Proven here. Born green, except one TL57 row (`result_too_large` did not exist yet). |
| 6 | Installed SDK `FunctionToolParam.__required_keys__` | Empty `frozenset()` in `openai` 3.42.1 on Python 3.14: the module uses `from __future__ import annotations`, so `Required[...]` is not seen when the class is built. Spec F9 lists four required keys. | TL66 reads `Required` from `typing.get_type_hints(..., include_extras=True)` (result: `name`, `parameters`, `strict`, `type`) and also validates each definition with `pydantic.TypeAdapter(FunctionToolParam)` in strict mode. The installed type also has an optional `async` key; it is not used. |
| 7 | Plan TL72 asks for "the TL45 call" | TL72 is a unit case in `tests/unit/test_tool_schemas.py`. Unit tests cannot use `orders_kit` (an integration fixture). | The child process calls `get_order` 1000 for customer 1 on a database prepared by the real `prepare_database` from the committed synthetic fixture `tests/fixtures/data/valid`. It is an equivalent real order lookup, but not the TL45 literal. |
| 8 | Plan TL59 asks for snapshots "before and after every case of TL33–TL55" | Earlier test functions cannot be re-run inside one test without editing them. | TL59 replays representative calls of TL33–TL55 (catalog, policies, and orders for C1, C2, C3 and no session, including invalid and injection inputs) between two snapshots. |
| 9 | Plan big-fixture signatures | Plan: `big_catalog(tmp_path, description_chars, char="a")`, `big_order(tmp_path, item_count)`. | Appended `tools_big` fixture. `catalog(base, description_chars, char="a", prefix="")` (`prefix` builds multibyte bodies at an exact byte count). `order(base, c1_items=300, c2_items=450)` (one database with both customers, as TL64 needs). `catalog_variant` renames product 202 (TL60). |

No contract change, no limit change and no file outside the allowed list was needed. One approved difference is recorded again here (spec G5, G12, review Q3). The provider schema requires every `search_products` field. Internal validation accepts absent optional fields and applies the service defaults. TL69 asserts both sides.

## Implementation Plan (as executed)

1. Confirmed HEAD, clean tree and baseline gates (above).
2. Appended `tools_big` to `tests/integration/conftest.py` (110 lines added, 0 removed; `index_spy` from slice C reused).
3. Wrote `test_tools_limits.py`, `test_tools_resources.py` and `test_tool_schemas.py`, then ran them against the unchanged `tools.py` (RED below).
4. Implemented `_finish`/`_fixed` and `provider_tool_definitions()` in `tools.py`.
5. GREEN, mutations with byte restore, regression and gates.
6. This report. Stop for review.

## Test Checklist

### Map TL56–TL72 → tests

| Case | Test(s) |
| --- | --- |
| TL56 | `test_tools_resources.py::test_tl56_resource_counts` (14 tool/outcome rows plus unknown tool; per-call open and load deltas) |
| TL57 | `test_tools_resources.py::test_tl57_connections_closed_on_every_outcome` (ok, not_found, service `unknown_value`, invalid row `price_text`, serialization failure `specs_nan`, `result_too_large`) |
| TL58 | `test_tools_resources.py::test_tl58_index_replaced_between_calls` (`os.replace` on Windows after a call; new text and new SHA-256 at the next call; 2 loads) |
| TL59 | `test_tools_resources.py::test_tl59_no_write_and_no_temporary_file` (SHA-256 of every file under `tmp_path`, directory tree, `catalog_kit.snapshot` table contents of both databases, index bytes; no `-journal`/`-wal`/`-shm`/`.tmp`) |
| TL60 | `test_tools_resources.py::test_tl60_replaced_database_is_read_again`, `::test_tl60_replaced_orders_database_is_read_again` |
| TL61 | `test_tools_limits.py::test_tl61_argument_limit_exact_and_plus_one`, `::test_tl61_escaped_worst_case_is_not_rejected_by_the_raw_limit` |
| TL62 | `test_tools_limits.py::test_tl62_exact_limit_is_returned_whole`, `::test_tl62_one_byte_over_is_replaced_whole` |
| TL63 | `test_tools_limits.py::test_tl63_multibyte_exact_limit`, `::test_tl63_multibyte_one_byte_over`, `::test_tl63_characters_under_limit_bytes_over` |
| TL64 | `test_tools_limits.py::test_tl64_large_authorized_order_and_foreign_order` |
| TL65 | `test_tools_limits.py::test_tl65_search_page_and_policy_sections` |
| G13 on policy paths | `test_tools_limits.py::test_policy_not_found_with_data_is_measured`, `::test_policy_ok_over_limit_is_replaced`, `::test_policy_lone_surrogate_is_internal_error` |
| TL66 | `test_tool_schemas.py::test_tl66_function_tool_param_compatibility` |
| TL67 | `test_tool_schemas.py::test_tl67_strict_schema_rules` |
| TL68 | `test_tool_schemas.py::test_tl68_literal_properties`, `::test_tl68_property_names_equal_internal_model_fields`, `::test_tl68_enum_and_pattern_match_contract_sources`, `::test_tl68_nullable_set_matches_internal_validation` |
| TL69 | `test_tool_schemas.py::test_tl69_provider_valid_samples_reach_the_resource`, `::test_tl69_provider_invalid_samples_are_rejected`, `::test_tl69_absent_optional_fields_differ_by_design` |
| TL70 | `test_tool_schemas.py::test_tl70_independent_calls_and_descriptions`, `::test_tl70_tools_module_does_not_import_openai` |
| TL71 | `test_tools_resources.py::test_tl71_interleaved_customers` |
| TL72 | `test_tool_schemas.py::test_tl72_isolated_offline_subprocess` |

31 new tests: 12 in `test_tools_limits.py`, 7 in `test_tools_resources.py`, 12 in `test_tool_schemas.py`. TL01 and `tests/unit/test_tool_arguments.py` are unchanged (`git diff --quiet HEAD` on that file: no difference).

### Size calibration (TL62–TL64)

- Product 202's description is `prefix + "a" * n`. For each candidate `n` the test builds the database, then writes the expected envelope by hand: the literal product summary, plus provenance from the SHA-256 and size of the CSV bytes it wrote. It serializes that envelope with the standard `json.dumps(ensure_ascii=False, allow_nan=False, separators=(",", ":"))` (the G9 format) and adjusts `n` by the byte difference. It stops when the size is exact. The products CSV size is part of provenance, so this loop is needed. The production `_dumps` is never used.
- Exact case: payload == independent expected text, 32,768 UTF-8 bytes, description length 31,683 characters. The data object alone is smaller than the limit, so a data-only measurement is also detected.
- Multibyte case: prefix `"ç" * 12000`. At the exact limit the payload has 32,768 bytes and fewer than 20,768 characters; at +1 byte the character count is still under 32,768.
- Characters under, bytes over: 16,500 × `"ç"`.
- TL64: 300 items for C1 with names `Item Sintético NNNN`. The independent expected envelope is between 20,000 and 32,768 bytes and is returned byte-identical. All 300 items are sorted, although the CSV rows were written in descending order. C2's 450-item order is above the limit (independent size) and returns `result_too_large`. C1 asking for C2's order, and for a missing order, gets the literal `order_not_accessible` payload, so size is never evaluated on unauthorized data.

### RED (tests written, `tools.py` unchanged, SHA `2acb73e5…`)

| File | Result | Failed |
| --- | --- | --- |
| `test_tools_limits.py` | 7 failed, 5 passed | `test_tl62_one_byte_over_is_replaced_whole`, `test_tl63_multibyte_one_byte_over`, `test_tl63_characters_under_limit_bytes_over`, `test_tl64_large_authorized_order_and_foreign_order`, `test_policy_not_found_with_data_is_measured`, `test_policy_ok_over_limit_is_replaced`, `test_policy_lone_surrogate_is_internal_error` (the payloads were `ok`/`not_found` envelopes above the limit, and `ok` with a lone surrogate) |
| `test_tools_resources.py` | 1 failed, 6 passed | `test_tl57_connections_closed_on_every_outcome`, only on its `result_too_large` row (`('ok', None) == ('unavailable', 'result_too_large')`); the five earlier rows passed |
| `test_tool_schemas.py` | 10 genuine failures, 1 passed | every TL66–TL70 test except the import check: `AttributeError: module 'emporio_agent.tools' has no attribute 'provider_tool_definitions'` |

TL72 failed twice during RED. Both times the cause was the test, not the product:

1. The first child script called `provider_tool_definitions()`. That made TL72 depend on TL66–TL70. I removed the call.
2. The first socket guard replaced `socket.socket` with a function. `Path.as_uri()` imports `urllib.request` and then `ssl`, and `ssl` subclasses `socket.socket` at import, so the child raised `TypeError` and `execute_tool` returned `internal_error`. No connection was attempted. The guard is now a subclass whose constructor records and raises. `create_connection`, `getaddrinfo` and `socketpair` are also replaced.

After both fixes TL72 passed against the unchanged `tools.py`, so it is listed as born green.

Genuine RED: 18 failures. RED was not fabricated: no test was weakened or changed after implementation to turn green.

### Born green (passed before any `tools.py` change)

TL56, TL58, TL59, TL60 (both), TL61 (both), TL62 exact, TL63 multibyte exact, TL65, TL70 import check, TL71, TL72 — 13 tests. TL57 is born green on five of its six rows. The two exact-limit cases are born green because without a limit every size passes. They are kept because they detect `>=` (mutation M03).

### GREEN

`uv run --frozen pytest tests/unit/test_tool_schemas.py tests/integration/test_tools_limits.py tests/integration/test_tools_resources.py -q` → 31 passed. `tools.py` SHA-256 after implementation: `7fbbe5dfe855dbe05710108201f146b079c4a1598c40f4e6cce23367e56cbb18`.

## Test Strategy

- Real services, real SQLite from `prepare_database` on synthetic CSV (`catalog_kit`, `orders_kit`, `tools_big`, `tests/fixtures/data/valid`), and real indexes from `prepare_policy_index` on `policy_kit` PDFs. No mock of SQL, prices, authorization, ranking or persistence.
- Spies: the `tools_kit.connect_spy` and `index_spy` from earlier slices delegate to the real functions. They count calls and check closure (`SELECT 1` → `ProgrammingError`).
- Oracles: hand-written literal envelopes, independent SHA-256 and size of written bytes, standard-library `json.dumps` under the G9 format for sizes, independent reads of the index JSON for section text and titles, `mode=ro` snapshots. TL65 uses the ranking literal of unit test P45 (`3.1`, `5.1`, `2`; omitted `3`); the ranking is not recomputed.
- Edited index (policy size and surrogate cases): built by the real `prepare_policy_index`, then one value changed with the standard `json` module. The real `load_policy_index` accepts the file.
- Schemas: literal expectations from G12; a small strict-subset checker in the test module (type with null option, required equals keys, no extras, enum, pattern, minimum) for provider-valid and provider-invalid samples; black-box alignment through `execute_tool` with absent files; `tools._REGISTRY` (private) is read only to compare property names with the internal model fields, as TL68 requires.
- `openai` is imported only inside TL66. TL72 runs `sys.executable -I` in a subprocess with every `OPENAI*` variable removed and a socket guard installed before the package import. The child reports `"openai" in sys.modules` after the import and after the call.

## Compliance Report

### Mutations (after GREEN)

Script: session scratchpad `mutate.py`, outside the repository. Each mutation changes `src/emporio_agent/tools.py`, runs the three slice D test files, writes the original bytes back and checks SHA-256 `7fbbe5df…cbb18`. All 16 restores matched.

| Mutation | Result | Detecting tests |
| --- | --- | --- |
| M01 limit removed | detected (7 failed) | TL57, TL62 +1, TL63 ×2, TL64, policy not_found, policy ok |
| M02 count characters (`len(payload)`) | detected (4) | TL62 +1, TL63 +1, TL63 chars-under, lone surrogate |
| M03 `>=` instead of `>` | detected (2) | TL62 exact, TL63 exact |
| M04 measure `data` only | detected (2) | TL62 +1, TL63 +1 |
| M05 partial cut to the limit | detected (7) | same as M01 |
| M06 replaced payload, original metadata | detected (7) | same as M01 (`status`/`error_code` checks) |
| M07 `additionalProperties: true` (get_product) | detected (1) | TL67 |
| M08 `required` without `offset` | detected (3) | TL67, TL69 ×2 |
| M09 `["integer", "boolean"]` for `product_id` | detected (4) | TL67, TL68 ×2, TL69 |
| M10 shared module-level schema, shallow `list()` copy | detected (1) | TL70 |
| M11 result cache keyed without customer | detected (6) | TL56, TL58, TL60 ×2, TL64, TL71 |
| M12 stale index cache by path | detected (1) | TL58 |
| M13 stale product result cache by ID | detected (7) | TL57, TL60, TL62 ×2, TL63 ×3 |
| M14 `get_product` connection left open | detected (9) | TL56, TL57, TL60, TL61, TL62 ×2, TL63 ×3 |
| M15 socket created in `execute_tool` | detected (1) | TL72 |
| M16 `import openai` in `tools.py` | detected (2) | TL70 import check, TL72 |

Survivors: none.

My first M15 called `socket.getaddrinfo("example.invalid", 80)`. That runs a real name lookup for every call, and the run did not finish within 600 s. I stopped the run. The interrupted run left M15 in `tools.py`. I removed exactly the inserted lines, then checked the SHA-256 against the GREEN value `7fbbe5df…` and re-ran the 31 tests. M15 was then replaced by `socket.socket().close()`, which makes no network traffic. The guard in TL72 catches it, and the whole set of 16 mutations was re-run from the restored file.

### Gates (final tree)

| Command | Exit | Result |
| --- | --- | --- |
| `uv run --frozen pytest tests/unit/test_tool_arguments.py tests/unit/test_tool_schemas.py tests/integration/test_tools_products.py tests/integration/test_tools_search.py tests/integration/test_tools_policies.py tests/integration/test_tools_orders.py tests/integration/test_tools_resources.py tests/integration/test_tools_limits.py -q -rs` | 0 | 401 passed |
| `uv run --frozen pytest tests/unit/test_pricing.py -q` | 0 | 37 passed |
| `uv run --frozen pytest -q -rs` | 0 | **1701 passed, 2 skipped** (1670 + 31; the same two Windows `?` skips, `test_readonly_connection.py:599`, `:612`) |
| `uv run --frozen ruff check --no-cache .` | 0 | All checks passed! |
| `uv run --frozen ruff format --check --no-cache .` | 0 | 93 files already formatted |

Ruff is re-run after this report was written (results in the hand-off message).

### Files

| Path | Change | SHA-256 |
| --- | --- | --- |
| `src/emporio_agent/tools.py` | modified: `_fixed`, `_finish`, `_failure` delegates, `ok` path via `_finish`, `provider_tool_definitions()`, docstring/comment | `7fbbe5dfe855dbe05710108201f146b079c4a1598c40f4e6cce23367e56cbb18` |
| `tests/integration/conftest.py` | append only: slice D block (`tools_big`) | `97565e5172b2c0002db47684093e366d8d704e5c8d597848f2073e8bea15c59a` |
| `tests/integration/test_tools_resources.py` | created | `4ceede639f48c27536d06ed26457eec819c62ce5b96358ae86c5c2f79b0b95f7` |
| `tests/integration/test_tools_limits.py` | created | `affa1f5a91a52072bc8fe647cd5eccaaf3d103720c62ec335811d432fbcbcf38` |
| `tests/unit/test_tool_schemas.py` | created | `0869ae58951f5a3999d44793bb7c010d0dccda880fa2252df348d6ca81a70e03` |
| `docs/validation/06-04-tools-limits-schemas.md` | created (this report) | — |

Unchanged: services, data layer, earlier tests (including `tests/unit/test_tool_arguments.py`, SHA `9e1ec0ff…3254`), `tests/conftest.py`, `tests/fixtures/`, `evals/`, specs, plans, earlier reports, README, `pyproject.toml`, `uv.lock`.

### Limits

- Provider schema compatibility is offline only: installed SDK types plus the documented strict-mode subset. Acceptance by the real Responses API is not proven; the adapter slice must confirm it (spec Q2).
- `MAX_RESULT_BYTES` is not measured on the supplied catalog text or the real manual. The optional `evals/` measurement in plan slice D is not authorized and was not run.
- TL58 proves "no handle held" through `os.replace` on Windows. On POSIX, TL57's closure check is the main evidence.
- TL59 replays representative TL33–TL55 calls, not the earlier test functions (Gap 8). TL72 uses an equivalent order lookup on `tests/fixtures/data/valid`, not the TL45 literal (Gap 7).
- The socket guard covers `socket.socket`, `create_connection`, `getaddrinfo` and `socketpair` in the child process. It does not cover native-code networking that bypasses the Python `socket` module.
- Equal no-access payloads are proven byte for byte, not for timing.
- Recorded and unchanged: the RED distribution of slice C (50 order failures + 1 policy + 1 TL01 = 52) stays a limit noted in that slice's report. Report 06-03 was not edited.

Status: slice D implemented and gated. Waiting for review. No commit, push, PR or merge by the AgentDev.
