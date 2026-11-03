# Validation 06-02: tools — product search (slice B)

Date: 2026-11-03. Branch `feature/data-preparation`. Scope: slice B of `docs/plans/06-tools.md` (revision R1) against `docs/specs/06-tools.md` (revision R1, including reviews 06-01-R1 and 06-02-R1). No commit, push, PR or merge. Slice C not started.

## Initial state

- HEAD `21490fab5c5d6a9f7d44995d26d3707b6cdfe8fc` (expected value), branch `feature/data-preparation`, `git status --porcelain` empty before any edit.
- No `AGENTS.md` exists in the repository.
- Baseline gates were run on a temporary detached worktree of that same commit (outside the repository, removed afterwards; `git worktree list` shows only the main worktree):
  - `uv run --frozen pytest -q -rs` → exit 0, **1472 passed, 2 skipped** (Windows `?` file-name skips in `test_readonly_connection.py:599` and `:612`).
  - `uv run --frozen ruff check --no-cache .` → exit 0, "All checks passed!".
  - `uv run --frozen ruff format --check --no-cache .` → exit 0, "85 files already formatted".
- SHA-256 of `src/emporio_agent/tools.py` before the slice: `a4fe898c4691e8d3ede77040c46ebdb88baa5f44a7d2a407ff4743dfabea9bbf`.

## Spec Summary

Added in `src/emporio_agent/tools.py`. Nothing else in production changed.

- `_SearchProductsArguments` (strict, `extra="forbid"`, frozen, as for slice A): `query: str | None = None`, `category: str | None = None`, `max_price_brl: str | None = None`, `available_only: bool = True`, `offset: int = 0`. Absent fields take the service defaults. `null` is valid only for the three filters. `null` for `available_only` or `offset` is `invalid_type` (strict Pydantic, no `None` in the type).
- `_BUDGET_PATTERN`: the A6 text `^(0|[1-9][0-9]*)(\.[0-9]{1,2})?$`, always applied with `re.fullmatch`.
- `_budget(text)`: `None` → `None`. A text that does not match → `invalid_input` / `invalid_format` / `max_price_brl`. A match → `Decimal(text)`. The conversion never uses `float`. It runs before the resource opens (G4 step 8).
- `_run_search_products`: `_budget` first, then one `with connect_readonly(context.database_path)` block that calls `catalog.search_products(connection, query=..., category=..., max_price_brl=budget, available_only=..., offset=...)` with keyword arguments. Serialization runs after the block exits.
- `_search_data(result)`: the G8 fields, in order: `products` (each a `product_summary`, slice A serializer), `total_matches`, `offset`, `page_size` (`catalog.PAGE_SIZE`), `next_offset`, `available_only`, `max_price_brl` (two-place text or `null`, from the service's quantized echo), `category_resolution`, `category` (or `null`), `candidate_categories`, `provenance`. No `query` key. The function takes only the `SearchResult`, so it has no access to the query or category text.
- Registry: `("search_products", "get_product", "quote_product")`.

The slice reuses the slice A pipeline, precedence, envelope, status mapping and serializers unchanged. That includes the review 06-02-R1 fix: JSON numbers outside the `Decimal` limits give `invalid_json` under a private trapping context, and the caller's decimal context is preserved. The module still reads no environment variable and uses no network, logging, cache or module-level mutable state.

## Gap Analysis

I compared the contract (spec G4–G9, G13, G14; Spec 03 G2–G4, A3, A4, A8) with `catalog.search_products` and `SearchResult` before writing tests. The specification was not changed, and no service, contract or out-of-scope file change was needed.

| # | Point | Decision (within R1) |
| --- | --- | --- |
| 1 | G6 places the budget format check at step 8, before any resource. In slice A, steps 1–7 run in `_parse`, and the runner opens the resource. | `_budget` runs at the start of the runner, before `connect_readonly`. A `_ToolFailure` raised there uses the same envelope path as slice A. Tests prove that no connection opens (spy count 0) and that the result is the same with the database present or absent. |
| 2 | Precedence step 7 versus step 8 for search. | Step 7 comes first: `{"max_price_brl":"01","offset":true}` → `invalid_type`/`offset`. Step 8 comes before the service: `{"offset":-1,"max_price_brl":"01"}` → `invalid_format`. |
| 3 | General precedence (G4 note) for `offset < 0` and budget above `MAX_PRICE_BRL`. | As approved, both come from the service after the open: `out_of_range` with one open. With the database absent, the result is `data_unavailable`. Both directions are tested. |
| 4 | `Decimal(text)` and the caller's decimal context. | The pattern accepts only plain ASCII decimal text. `Decimal(str)` is exact and does not depend on precision or exponent limits for such text, so no signal can be raised and the caller's context is not involved. The service still applies its own `_BUDGET_CONTEXT`. No private context was added because nothing needed one. |
| 5 | `Decimal` accepts non-ASCII digits (for example `"١٠٠"`), and `$` accepts a trailing `"\n"` when used with `re.match`. | `fullmatch` together with ASCII `[0-9]` rejects both. They are tested as `invalid_format` cases of the same approved pattern. This is not a new rule. |
| 6 | Unknown and ambiguous categories. | Resolution, candidates and the empty page come from the service unchanged (`ok`). The tool never retries without the category. The category text is never echoed: only the stored category names appear, in `category`/`candidate_categories`. |
| 7 | `max_price_brl` echo. | The tool serializes the service's quantized `SearchResult.max_price_brl` (`"0.5"` → `"0.50"`). It never echoes the raw argument. |
| 8 | Name of the TL01 test function, `test_tl01_registry_of_slice_a`. | Unchanged: the TL01 exception allows only the literal to change. The name is now out of date; recorded here, not edited. |
| 9 | Module docstring of `tools.py` still says "Slice 06-A: dispatcher core, product detail and product quote". | Not edited: the brief limits `tools.py` changes to the model, conversion, runner, serializer and registry. Recommend updating the docstring in a later approved slice. |
| 10 | `MAX_RESULT_BYTES` for search pages. | Not implemented: slice D. |
| 11 | "Serialization after close." | The code serializes after the `with` block. The tests show the connection is closed after `execute_tool` (`spy.all_closed()`), but no test observes the moment of serialization. Recorded as a limit; TL57 (slice D) covers closure on every outcome. |

## Implementation Plan (as executed)

1. Confirmed HEAD and a clean tree. Ran the baseline gates on a temporary worktree of the same commit.
2. Read the spec, the plan, Spec 03, `tools.py`, `catalog.py`, the slice A tests and `tools_kit`/`catalog_kit`.
3. Changed the TL01 literal and created `tests/integration/test_tools_search.py` (TL19–TL21, TL36–TL39, plus slice A pipeline checks applied to search).
4. RED run (below).
5. Implemented `search_products` in `tools.py` (57 insertions, 0 deletions).
6. GREEN run, mutation runs, full gates.
7. Wrote this report and re-ran Ruff.

## Test Checklist

### TLxx → tests (`tests/integration/test_tools_search.py`, 110 collected cases)

| Case | Tests (collected cases) | What is checked |
| --- | --- | --- |
| TL19 | `test_tl19_defaults_and_authorized_nulls` (1), `test_tl19_strict_types` (20), `test_tl19_negative_offset_is_service_out_of_range` (1), `test_tl19_available_only_false_reaches_the_service` (1) | `{}`, the three nulls and explicit defaults give byte-identical payloads (`available_only true`, `offset 0`). `available_only` `1`, `0`, `null`, `"true"`, `[true]`; `offset` `null`, `true`, `false`, `1.0`, `"0"`, `[0]`; text and budget wrong types → `invalid_type` on the named field, 0 opens. `offset` `-1`, `-(2**63)` → service `out_of_range`, 1 open each; with the database absent → `data_unavailable`. `available_only=false` reaches the service: page `211,214,212,209,208`, total 17. |
| TL20 | `test_tl20_accepted_budgets` (5), `test_tl20_max_boundary_returns_every_available_product` (1), `test_tl20_invalid_format` (36), `test_tl20_above_max_is_service_out_of_range` (2), `test_tl20_precedence_type_before_format_before_service` (1); JSON-number budgets are in `test_tl19_strict_types` | Accepted `"0"`, `"0.5"`, `"1000"`, `"1000.00"`, `"92233720368547758.07"` echo `"0.00"`, `"0.50"`, `"1000.00"`, `"1000.00"`, `"92233720368547758.07"` (totals 1, 1, 9, 9, 14). At the exact `MAX_PRICE_BRL` boundary, the total equals an independent SQL count of available products (14). The 10 matrix formats plus `"100\n"`, `"100 "`, `"١٠٠"`, `".5"`, `"1."`, `"+1"`, `"00"`, `"-0"` → `invalid_format`, with the database present and absent, 0 opens, text not echoed. `"92233720368547758.08"`, `"99999999999999999999.99"` → service `out_of_range` (1 open); with the database absent → `data_unavailable`. `1000`, `1000.0`, `true`, `["1000"]` → `invalid_type`. |
| TL21 | `test_tl21_text_length_in_code_points` (12), `test_tl21_blank_after_normalization` (10) | `query` and `category` with `"a"`, `"ç"`, `"𝄞"` × 4000 accepted (`ok`; unknown category for `category`), × 4001 → `too_long` on the named field, both as `\u` escapes and as raw UTF-8. The text is not echoed. `"   "`, `"́"`, `""`, `"\t\n"`, `"́́ ́"` → `blank` on the named field. |
| TL36 | `test_tl36_default_page_full_projection` (1), `test_tl36_query_is_never_echoed` (1) | `{}`: full `data` equal to a literal (summaries 211, 214, 212, 210, 201 with prices, promotions and stock; `total_matches 14`; `offset 0`; `page_size 5`; `next_offset 5`; `not_requested`; provenance with SHA-256 and sizes of the written source bytes). 1 open, closed, deterministic. No `query` key. Sentinel query and `"folk beta"` not in the payload. |
| TL37 | `test_tl37_pagination` (5), `test_tl37_first_page_literal` (1) | `violões` + `"1000.00"` at offsets 0, 5, 9, 10, `10**30`: `211,214,212,210,201` (next 5) / `206,213,202,204` (next null) / empty pages with next null; `total_matches 9` each time; offset echoed; category resolved to `{1, Violões}`; 1 open per call. The first page also checked as a full literal. |
| TL38 | `test_tl38_unknown_category` (4), `test_tl38_ambiguous_category` (1) | `"Ukuleles"` alone, with `available_only=false`, with the maximum budget and with a query: `ok`, `unknown`, `products []`, total 0, next null, candidates 1, 2, 3 (full objects), category text not echoed. Collision database: `"VIOLÕES"` → `ambiguous`, candidates `[1 Violões, 4 violoes]`. The exact names `"Violões"` and `"violoes"` resolve to 1 and 4. |
| TL39 | `test_tl39_budget_uses_effective_price_without_pix` (1) | Independent SQL confirms the fixture premises (203 = 105000, 204 = 100000, 205 = 100001 cents, no promotions). Both pages of `violões`/`"1000.00"` give `VIOLOES_1000`; 203 (PIX 997.50) and 205 (1000.01) are absent, 204 is present. `"997.50"` → total 8. `"1000.01"` → total 10, and 205 is on page 2. |
| Pipeline reuse (A rules on search) | `test_search_pipeline_rejections` (1), `test_search_extreme_exponent_stays_invalid_json` (4), `test_search_status_mapping` (1) | `unexpected_field` (key not echoed), `duplicate_key`, `invalid_json`, `NaN`, `arguments_not_object`, 0 opens. Review 06-02-R1 exponents on `offset`, `max_price_brl`, after a duplicate and nested → `invalid_json`, with the caller context unchanged, with and without the trap. Absent database → `data_unavailable`. Damaged row of another product (`price_text_other`) → `internal_error`. |

TL01 (`tests/unit/test_tool_arguments.py`) now expects the slice B registry.

### TL01 diff (only change to that file)

```diff
@@ -89,7 +89,7 @@ def run(name, arguments, context):

 def test_tl01_registry_of_slice_a():
     first = tools().tool_names()
-    assert first == ("get_product", "quote_product")
+    assert first == ("search_products", "get_product", "quote_product")
     assert type(first) is tuple
     assert tools().tool_names() == first
```

`git diff --numstat`: `1 1 tests/unit/test_tool_arguments.py`.

### RED (real, before any production change)

Command: `uv run --frozen pytest tests/unit/test_tool_arguments.py tests/integration/test_tools_products.py tests/integration/test_tools_search.py -q -rs` → **111 failed, 171 passed**.

- 110 failures: every collected case of `test_tools_search.py`. Each failure shows the `unknown_tool` envelope (`{"tool":null,"status":"invalid_input",...,"code":"unknown_tool"...}`): 76 failed on the payload comparison and 34 on `execution.tool is None`. No other failure cause.
- 1 failure: `test_tl01_registry_of_slice_a` (the registry was still `("get_product", "quote_product")`).
- 0 slice B cases were green at RED. The 171 passing cases are the unchanged slice A tests.
- Born green after integration: the plan lists TL21 (service lengths) and TL38 (service resolution) as possible born-green cases once the tool exists. That cannot be observed separately, because no intermediate stub was written (the brief forbids stubs). Every case failed only on `unknown_tool`, and all passed after the one implementation step. Mutation runs give the behavioral detection evidence.

### GREEN

Same command → exit 0, **282 passed** (171 slice A + 1 TL01 + 110 slice B).

## Test Strategy

- pytest with real services, real SQLite built by `prepare_database` from the synthetic `catalog_kit` CSV in `tmp_path`, and the real `connect_readonly`. `tools_kit` and `catalog_kit` are reused unchanged. Helpers specific to slice B live only in the new test file.
- Oracles: literal IDs, prices, promotions and totals from Spec 03/06 and the fixture. These are the same pages as `test_catalog_search.py`, written by hand. SHA-256 and sizes come from the written source bytes. Independent SQL reads use `catalog_kit.query` (a separate `mode=ro` connection) to count available products and to confirm the 203/204/205 prices. Payloads are parsed independently with a strict `parse_constant`. No production serializer, `quote_price`, availability rule or search function is used as an oracle.
- The `tools_kit.connect_spy` delegating spy observes opens and closure only. There are no mocks of rules, SQL or persistence.
- Earlier test files are unchanged, except for the TL01 literal.

### Mutations

Script (scratchpad, outside the repository): for each mutation, read the original bytes of `tools.py`, apply the textual edit (each edit asserted to match exactly once), run `pytest tests/integration/test_tools_search.py tests/unit/test_tool_arguments.py`, write the original bytes back in a `finally` block, and compare SHA-256 with the baseline.

Baseline SHA-256 of the implemented `tools.py`: `8789277640fc8bc3ee018b4a4487113e97f3d9df6ac68bbbde021b31972fecf2`. After every restore, and at the end of the run, the hash was equal.

| # | Mutation | Result | Failing tests |
| --- | --- | --- | --- |
| M1 | `null` treated as default: `available_only: bool \| None`, `offset: int \| None`, `None` replaced by `True`/`0` | detected, 2 failed | `test_tl19_strict_types` (`available_only=null`, `offset=null`) |
| M2a | pattern `^[0-9]+(\.[0-9]{1,2})?$` (accepts `"01"`) | detected, 5 failed | `test_tl20_invalid_format` (`"01"`, `"00"`), `test_tl20_precedence_type_before_format_before_service` |
| M2b | exponent text accepted (`"1e3"`) | detected, 3 failed | `test_tl20_invalid_format` (`"1e3"` ×2), `test_tl20_precedence_type_before_format_before_service` |
| M3a | `Decimal(float(text))` | detected, 4 failed | `test_tl20_accepted_budgets` (boundary), `test_tl20_max_boundary_returns_every_available_product`, `test_tl38_unknown_category` (max budget), `test_tl39_budget_uses_effective_price_without_pix` |
| M3b | `Decimal(str(float(text)))` | detected, 3 failed | `test_tl20_accepted_budgets`, `test_tl20_max_boundary_returns_every_available_product`, `test_tl38_unknown_category` |
| M4 | filter dropped: on `unknown`/`ambiguous`, search again without the category | detected, 11 failed | `test_tl38_unknown_category`, `test_tl38_ambiguous_category`, `test_tl21_text_length_in_code_points` (category cases) |
| M5 | query echoed (`"query"` key added to `data`) | detected, 34 failed | every `ok` test through `assert_ok`, including `test_tl36_query_is_never_echoed` |
| M6 | `match` instead of `fullmatch` | detected, 2 failed | `test_tl20_invalid_format` (`"100\n"`) |
| M7 | `available_only` not forwarded to the service | detected, 2 failed | `test_tl19_available_only_false_reaches_the_service`, `test_tl38_unknown_category` |

Survivors: none of the nine. Limits: the mutations are textual and limited to the search code. They do not cover the slice A pipeline (covered by the slice A mutation evidence) or the moment of serialization relative to the close (Gap 11). M1 and M7 were added beyond the five requested mutations. No expectation was weakened to kill a mutation.

## Compliance Report

### Commands and results (after implementation)

| Command | Exit | Result |
| --- | --- | --- |
| `uv run --frozen pytest tests/unit/test_tool_arguments.py tests/integration/test_tools_products.py tests/integration/test_tools_search.py -q -rs` | 0 | 282 passed |
| `uv run --frozen pytest tests/unit/test_pricing.py -q` | 0 | 37 passed |
| `uv run --frozen pytest -q -rs` | 0 | 1582 passed, 2 skipped (the same two Windows skips, `test_readonly_connection.py:599`, `:612`) |
| `uv run --frozen ruff check --no-cache .` | 0 | All checks passed! |
| `uv run --frozen ruff format --check --no-cache .` | 0 | 86 files already formatted |

1582 = baseline 1472 + 110 new cases. The TL01 case is modified, not new. `ruff format` was applied once to the new test file only, to wrap two long lines, before the gates above.

### Files

| File | Change |
| --- | --- |
| `src/emporio_agent/tools.py` | +57 / −0: `re` import, `SearchResult` import, `_BUDGET_PATTERN`, `_SearchProductsArguments`, `_search_data`, `_budget`, `_run_search_products`, one registry entry |
| `tests/unit/test_tool_arguments.py` | +1 / −1: TL01 literal only |
| `tests/integration/test_tools_search.py` | new, 110 collected cases |
| `docs/validation/06-02-tools-search.md` | new (this report) |

Checked unchanged with `git diff --quiet HEAD`: `tests/conftest.py`, `tests/integration/conftest.py`, `tests/integration/test_tools_products.py`, `src/emporio_agent/services/`, `src/emporio_agent/data/`, `docs/` (before this report), `pyproject.toml`, `uv.lock`, `README.md`. No dependency, configuration, spec, plan or earlier report changed. No policy, order, provider schema, result-size limit, adapter, agent, session, CLI, private data or OpenAI work was done. No commit, push, PR or merge.

### Limits

- The born-green status of TL21 and TL38 cannot be shown separately (see RED).
- Serialization after the close is shown by code structure and the closed-connection check, not by observing the order of events (Gap 11).
- The `tools.py` docstring and the TL01 test name still mention slice A (Gaps 8 and 9).
- No result-size check on search pages until slice D.

### Ruff after this report

Re-run after writing this file: `ruff check --no-cache .` exit 0 ("All checks passed!"). `ruff format --check --no-cache .` exit 0 ("87 files already formatted"). The count went from 86 to 87 because Ruff also checks this new Markdown file.

Status: slice B implemented and validated. Stopped for review; slice C not started.
