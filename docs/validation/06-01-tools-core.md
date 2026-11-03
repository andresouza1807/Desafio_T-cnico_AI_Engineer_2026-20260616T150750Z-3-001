# Validation 06-01: tools core — dispatcher, product detail and quote (slice A)

Date: 2026-11-03. Branch `feature/data-preparation`. Scope: slice A of `docs/plans/06-tools.md`, revision R1, against `docs/specs/06-tools.md`, revision R1. No commit, push, PR or merge. Slice B not started.

Revision 06-02-R1 applied (see the last section, "Revision 06-02-R1"). The sections before it record the original slice A run; statements that the revision corrects are marked in place.

## Spec Summary

Implemented in `src/emporio_agent/tools.py`, slice A only:

- `ToolStatus`, `MAX_ARGUMENTS_CHARS = 131_072`, `MAX_RESULT_BYTES = 32_768`. `MAX_RESULT_BYTES` is declared only; the result-size check belongs to slice D.
- `ToolContext` (frozen, slots): `database_path`, `policy_index_path`, `clock`, `customer`, with type checks only and fixed `TypeError` messages. `policy_index_path`, `clock` and `customer` are carried but not used in slice A.
- `ToolExecution` (`tool`, `status`, `error_code`, `payload`).
- `tool_names()` → `("get_product", "quote_product")`.
- `execute_tool(name, arguments, context)` running the G4 pipeline: caller type checks (`TypeError`), exact name lookup, raw length limit, strict JSON pre-parse (`parse_constant` reject, `parse_float` to `Decimal` — since 06-02-R1 through `_parse_number` under a private trapping context, duplicate-key hook at any depth), object root, unknown keys, missing fields, Pydantic strict model validation (`strict=True`, `extra="forbid"`, `frozen=True`) mapped by `loc` only.
- Argument models `_GetProductArguments` (`product_id: int`) and `_QuoteProductArguments` (`product_id: int`, `payment_method: str`).
- One `connect_readonly(context.database_path)` per execution that reached step 10, inside a `with` block; serialization runs after the block exits.
- Status mapping: `None` → `not_found`/`product_not_found`; `CatalogInputError` → `invalid_input` with the service `code`/`field`; `DatabaseUnavailableError` → `unavailable`/`data_unavailable`; every other `Exception`, including serialization failures → `internal_error`/`internal_error`.
- Envelope `tool, status, data, error`, compact JSON (`ensure_ascii=False`, `allow_nan=False`, `(",", ":")`). Money and percent are two-place text with no extra rounding. `prepared_at` is ISO 8601. `specs` holds the stored JSON values.

The module does not import `openai`. It reads no environment variable, uses no network, logging or cache, and keeps no module-level mutable state. No search, policy or order tool, model, stub or runner exists.

## Gap Analysis

I compared the R1 contract with the current services before writing tests. The specification was not changed, and no service, data-layer or contract change was needed.

| # | Point | Decision taken in the implementation (within R1) |
| --- | --- | --- |
| 1 | G4 orders invalid JSON (step 3) before a duplicate key (step 3b). A raising hook would report `duplicate_key` for `{"a":1,"a":2} x`. | The hook records duplicates and keeps parsing. `invalid_json` wins. `duplicate_key` is checked after a successful parse, before the root-type check, so `[{"a":1,"a":2}]` gives `duplicate_key` (step 3b before step 4). TL18 covers both. |
| 2 | G9: "`-0.00` is serialized as `"0.00"` (`copy_abs` after the check)" does not say whether `copy_abs` applies to non-zero values. | `copy_abs` applies only to a zero value. A non-zero value keeps its sign. No service returns negative money or percent. |
| 3 | G2/G3: "fixed message" for `TypeError`; the text is not fixed by the contract. | Messages are constant per parameter, such as `"name must be a str"`. They never include the rejected value. TL03 checks that two different bad values give the same message and that the value does not appear. |
| 4 | G4 step 7 maps every Pydantic error to `invalid_type`. | Extra and missing keys are rejected before Pydantic, so only type errors can remain. The first rejected field follows schema order, not error order; TL18 sends the fields in reversed order. Only `loc` is read, and errors are requested with `include_input=False` and `include_context=False`. |
| 5 | General precedence (G4 note): service domain errors run after the open. | As approved. `product_id` `0`, `-1`, `2**63` open the database once and return the service `out_of_range` (TL16, spy count 2 for two calls). With the database absent, `product_id: 0` returns `data_unavailable` (TL29). |
| 6 | `CatalogInputError.code` is passed unchanged (G7). | Reachable through these two tools: `out_of_range` (`product_id`) and `unknown_value` (`payment_method`). `invalid_type` from the service cannot be reached because step 7 runs first. |
| 7 | F5 `specs_nan`: the catalog `_specs` uses plain `json.loads`, which accepts `NaN`. | The service returns `{"x": nan}`. `json.dumps(allow_nan=False)` raises `ValueError`, which maps to `internal_error`. Nothing is replaced. The quote of the same product stays `ok` because a quote never reads `specs_json` (TL26). |
| 8 | Plan: variant `specs_nan` "with the existing `catalog_corrupt` technique". | `tests/integration/conftest.py` gets one appended statement that adds the key `"specs_nan"` to `_CATALOG_VARIANTS` (technique `check`, product 202). No existing line or entry changed. The diff is 101 insertions and 0 deletions. No earlier test iterates that dict; it is only indexed. |
| 9 | Plan: `connect_spy` lives in `tests/integration/conftest.py`, but TL32 is a unit case. | Unit tests cannot see the integration conftest. `tests/unit/test_tool_arguments.py` therefore has its own local delegating spy fixture (`opens`), built with the same technique. The integration tests use `tools_kit.connect_spy`. |
| 10 | Envelope `MAX_RESULT_BYTES` check. | Not implemented: slice D. The constant exists only as the plan allows. |
| 11 | Born-green cases expected by the plan (TL16, TL17). | Not observable separately. The only RED stage run was module absence (see Test Checklist). No types-only intermediate module was written, so no per-case behavioral RED was fabricated. The mutation runs below give behavioral detection evidence. |

Limits recorded, with no action taken: Spec 03–05 headers still say "Not implemented" (noted in the spec). `ToolContext` accepts `Path` subclasses (`WindowsPath`) by `isinstance`.

## Implementation Plan (as executed)

1. Checked Git state: HEAD `c5d860b3f041fa4619b34b27214049b5ceb8be8a`, unchanged from the brief. The only untracked files were `docs/specs/06-tools.md` and `docs/plans/06-tools.md`, with no other change. Ran the baseline gates.
2. Appended `tools_kit` (`context`, `run`, `connect_spy`), the `ConnectSpy` helper and variant `specs_nan` to `tests/integration/conftest.py`. The block imports `emporio_agent.tools` lazily, so earlier modules never depend on it at collection time.
3. Wrote `tests/unit/test_tool_arguments.py` and `tests/integration/test_tools_products.py`.
4. RED: ran both files before `tools.py` existed.
5. Implemented `src/emporio_agent/tools.py`.
6. GREEN: one test-classification defect was found and fixed in the test (see below). Then ran the mutations, the full regression run and the gates.
7. Wrote this report and repeated Ruff.

## Test Checklist (TL → test)

Unit, `tests/unit/test_tool_arguments.py`, 127 collected:

| TL | Test function(s) | Items |
| --- | --- | --- |
| TL01 | `test_tl01_registry_of_slice_a` | 1 |
| TL02 | `test_tl02_unknown_name` (10 names, incl. `""`, `"GET_ORDER"`, trailing space/newline, 10,000 × `x`, `NUL`) | 10 |
| TL03 | `test_tl03_execute_tool_caller_types`, `test_tl03_tool_context_types` | 2 |
| TL04 | `test_tl04_invalid_json` | 9 |
| TL05 | `test_tl05_root_not_object` | 7 |
| TL06 | `test_tl06_non_finite_constants` | 3 |
| TL07 | `test_tl07_fraction_or_exponent_is_not_an_id` (`1.0`, `5.5`, `-0.0`, `1e400`, `1E2`, `202.0`, `2e0`) | 7 |
| TL08 | `test_tl08_duplicate_key` (top level, unknown key, nested object, inside array, quote) | 6 |
| TL09 | `test_tl09_unexpected_field` (10 keys, both tools) | 10 |
| TL10 | `test_tl10_missing` | 4 |
| TL11 | `test_tl11_boolean_is_not_an_id` | 4 |
| TL12 | `test_tl12_wrong_scalar_type` | 5 |
| TL13 | `test_tl13_null_is_not_absent` | 3 |
| TL14 | `test_tl14_container_is_not_a_scalar` | 4 |
| TL15 | `test_tl15_deep_nesting_and_huge_integer` | 1 |
| TL18 | `test_tl18_first_failure_in_pipeline_order`, `test_tl18_unknown_name_before_size` | 11 |
| TL24 | `test_tl24_two_places`, `test_tl24_two_places_rejects` | 17 |
| TL32 | `test_tl32_invalid_arguments_open_nothing` (22 cases of TL04–TL15, spy count 0, directory not created), `test_tl32_valid_arguments_reach_the_resource` (control) | 23 |

Integration, `tests/integration/test_tools_products.py`, 31 collected:

| TL | Test function(s) | Items |
| --- | --- | --- |
| TL16 | `test_tl16_product_id_out_of_range` (`0`, `-1`, `2**63`, `-(2**63)`, both tools), `test_tl16_largest_id_is_not_found` | 5 |
| TL17 | `test_tl17_unknown_payment_method` (`"PIX"`, `""`, `" pix"`, `"pix "`, `"credit_3x"`, `"Regular"`, `"boleto"`) | 7 |
| TL22 | `test_tl22_envelope_for_each_status` (all five statuses, spy counts and closure) | 1 |
| TL23 | `test_tl23_money_is_two_place_text` | 1 |
| TL25 | `test_tl25_provenance` | 1 |
| TL26 | `test_tl26_specs_nan_is_internal_error` | 1 |
| TL27 | `test_tl27_deterministic_compact_payload` | 1 |
| TL28 | `test_tl28_injection_text_only_in_data` | 1 |
| TL29 | `test_tl29_database_unavailable` (missing, `schema_v2`), `test_tl29_unavailable_hides_domain_error` | 3 |
| TL30 | `test_tl30_data_failure_is_internal_error` (`invalid_row` via `price_text`, `query_failed` via `no_products_table`) | 2 |
| TL31 | `test_tl31_error_payloads_leak_nothing` (12 structural cases with the database present and spy count 0, plus 7 service/data cases) | 1 |
| TL33 | `test_tl33_full_product_detail` (202 literal, 201 description/specs), `test_tl33_unavailable_products_keep_their_data` (207, 208, 209) | 4 |
| TL34 | `test_tl34_conflict_withheld_and_missing` | 1 |
| TL35 | `test_tl35_quotes` (201 pix/regular, 210 pix/regular, 202 pix, 999), `test_tl35_full_promotion_quotes_zero` (211) | 2 |

Not implemented here (other slices): TL19–TL21, TL36–TL72.

Additional inputs inside the planned TL IDs, with no new TL number: nested and in-array duplicates (TL08); `"database_path"` and `"clock"` as extra keys (TL09); product 210 regular and products 202 and 211 pix (TL35); products 207–209 (TL33); a reversed field order (TL18).

### RED (real)

Command: `uv run --frozen pytest tests/unit/test_tool_arguments.py tests/integration/test_tools_products.py -q -rs`, run before `src/emporio_agent/tools.py` existed.

Result: exit 1, `27 failed, 131 errors`. All 158 failures and errors had one cause: `ModuleNotFoundError: No module named 'emporio_agent.tools'`. The errors come from fixtures that import the module (`absent`, `opens`, `tools_kit.context`); the failures come from tests that import it in the body. No failure had another cause.

### GREEN (real)

- First run after implementation: `1 failed, 157 passed`. Failure: `test_tl31_error_payloads_leak_nothing`, `assert spy.opens == 0` (1 open). Cause: a **test classification defect**. `payment_method: "VALOR-k6"` is a valid JSON string, so the service answers `unknown_value` after opening the database, as the approved general precedence requires; it is not a structural failure. Fix: I moved that case from the structural list to the service list in the same test. Production code did not change.
- Second run: exit 0, `158 passed` (127 unit, 31 integration).

### Mutations (after GREEN)

Each mutation was applied to `src/emporio_agent/tools.py` by exact string replacement, then both slice A files were run. After each run the original bytes were restored and checked by SHA-256. SHA-256 after restoration: `683aa73264c85fb61ebfc70820a913fc883cb264035a4fa3598393708af53579`, the same as before the first mutation.

| Mutation | Result | Failing tests |
| --- | --- | --- |
| M1 `strict=False` (lax: `true` → `1`, `"202"` → `202`) | detected | TL07, TL11, TL12, TL18, TL22, TL31, TL32 |
| M2 duplicate keys accepted (`if duplicates.found` → `if False`) | detected | TL08, TL18, TL31, TL32 |
| M3a non-finite constants accepted (no `parse_constant`) | detected | TL06 |
| M3b `parse_float=Decimal` removed (floats reach Pydantic) | **survived** in this run; **detected** after 06-02-R1 | — (after the revision: TL15 extreme-exponent cases) |
| M3c fraction/exponent coerced to `int` (`parse_float=lambda t: int(Decimal(t))`) | detected | TL07, TL31, TL32 |
| M4a unknown tool name echoed in `tool` | detected | TL02, TL18, TL31 |
| M4b unexpected key echoed in `field` | detected | TL09, TL18, TL31 |
| M5 `connect_readonly` opened before structural validation | detected | TL04–TL16, TL18, TL22, TL26, TL30, TL31, TL32 |
| M6 Pydantic `msg` exposed as code | detected | TL07, TL11–TL14, TL18, TL22, TL31 |
| M7a money as JSON number (`float`) | detected | TL23, TL24, TL26, TL33, TL34, TL35 |
| M7b money without two places (`format(value, "f")`) | detected | TL23, TL24, TL33, TL34, TL35 |

Original conclusion, **withdrawn by 06-02-R1**: "M3b is an equivalent mutant". That conclusion held only for the numbers the tests used (`1.0`, `5.5`, `-0.0`, `1e400`). With those, a `float` reaches Pydantic and strict mode gives the same `invalid_type`. It is not equivalent for all inputs. With `parse_float=Decimal`, an exponent outside the decimal implementation limits (`1e999999999999999999999`) raised `decimal.InvalidOperation` out of `execute_tool`. Without the `Decimal` conversion the same text becomes `inf` and gives `invalid_type`. The two variants therefore behave differently, and the original tests did not exercise that input. See "Revision 06-02-R1".

## Test Strategy

- pytest. The unit tests go through `execute_tool` with a `ToolContext` whose paths do not exist, so any premature open would show up as `data_unavailable`, as a non-zero spy count or as a created directory.
- The integration tests use real SQLite built by the real `prepare_database` from the synthetic `catalog_kit` CSV, and the real `catalog` services and `quote_price`. Damaged databases come only from the existing `catalog_corrupt`/`build(edit=...)` techniques plus the appended `specs_nan`.
- The spies delegate to the real `connect_readonly`. They count opens, keep the yielded connections and check closure with `SELECT 1` → `sqlite3.ProgrammingError`. They never change a result.
- Oracles:
  - hand-written literal envelopes for errors (f-string JSON text);
  - literal dicts from the Spec 03 examples and the fixture (prices `1100.00`/`990.00`/`854.91`/`1350.00`/`700.00`/`0.00`/`19.90`, promotions 301/302/303/308);
  - provenance SHA-256 and sizes computed by `hashlib` from the CSV bytes written by the fixture, with row counts `3, 17, 9`;
  - independent `json.loads` with a raising `parse_constant`;
  - a standard-library canonical re-dump for compactness.

  No production serializer is used as an oracle; TL24 calls the private helper `_two_places` directly, as the plan allows.
- No mock of prices, authorization, SQL or persistence. No private data, key, OpenAI or network. Import check: `import emporio_agent.tools` leaves `openai` out of `sys.modules`. This was checked once by hand, not as a test; TL72 belongs to slice D.

## Compliance Report

Initial state: HEAD `c5d860b3f041fa4619b34b27214049b5ceb8be8a`, the same as the brief. `git status` showed only `?? docs/plans/06-tools.md` and `?? docs/specs/06-tools.md`. Baseline gates:

| Command | Exit | Result |
| --- | --- | --- |
| `uv run --frozen pytest -q -rs` | 0 | 1300 passed, 2 skipped |
| `uv run --frozen ruff check --no-cache .` | 0 | All checks passed |
| `uv run --frozen ruff format --check --no-cache .` | 0 | 81 files already formatted |

Final gates (after GREEN and mutation restoration):

| Command | Exit | Result |
| --- | --- | --- |
| `uv run --frozen pytest tests/unit/test_tool_arguments.py tests/integration/test_tools_products.py -q -rs` | 0 | 158 passed |
| `uv run --frozen pytest tests/unit/test_pricing.py -q` | 0 | 37 passed |
| `uv run --frozen pytest -q -rs` | 0 | 1458 passed, 2 skipped (1300 + 158) |
| `uv run --frozen ruff check --no-cache .` | 0 | All checks passed |
| `uv run --frozen ruff format --check --no-cache .` | 0 | 84 files already formatted |

Skips (unchanged from the baseline): `tests\integration\test_readonly_connection.py:599` and `:612`, "Windows does not allow '?' in file names".

Ruff and Markdown (corrected in 06-02-R1; the original sentence "The report itself is Markdown, so Ruff does not check it" was wrong). Proven scope with Ruff 0.17.3 in this configuration:

- `ruff format --check --no-cache .` includes `.md` files. The verbose run lists `docs/validation/06-01-tools-core.md` and `README.md` as "Included path via `include`". This report is counted in the formatted-file total, which grew from 84 to 85.
- `ruff check --no-cache docs/validation/06-01-tools-core.md` answers "No Python files found under the given path(s)". The lint step does not check this Markdown file.
- Markdown files listed in `.gitignore` are skipped, for example `docs/escopo-projeto.md`.

Not proven: which Markdown constructs the formatter rewrites. Final Ruff results are in "Revision 06-02-R1".

Files:

| Path | Change |
| --- | --- |
| `src/emporio_agent/tools.py` | created |
| `tests/unit/test_tool_arguments.py` | created |
| `tests/integration/test_tools_products.py` | created |
| `tests/integration/conftest.py` | appended only (+101, −0): `TOOLS_FIXED`, `_CATALOG_VARIANTS["specs_nan"]`, `tools_run`, `ConnectSpy`, `tools_connect_spy`, fixture `tools_kit` |
| `docs/validation/06-01-tools-core.md` | created (this report) |

Unchanged: services, data layer, earlier tests, static fixtures, README, specs, plans, earlier reports, `pyproject.toml`, `uv.lock` and configuration. In the original slice A run the approved documents were not edited (they were edited later, only as 06-02-R1 authorizes; see the last section). They stay untracked. Original hashes: `docs/specs/06-tools.md` SHA-256 `b3617bdd4cfcc2ec28387b16b1008cdacb5641c1340e59637fc5258aff1ff3e6`, `docs/plans/06-tools.md` SHA-256 `575366cd75945002fdc446c00f016e85bdb8c4f6c865d89656ff77f39a725530`. TL01 registry literal: created in this slice as `("get_product", "quote_product")`.

Validation limits:

- The result-size limit, provider schemas, the no-write snapshot, rebuild staleness and the offline subprocess check are slice D (TL56–TL72). In slice A, connection closure is checked in TL16, TL17, TL22, TL26, TL29, TL30 and TL31, and it is not checked on every outcome listed in TL57.
- `ToolContext.clock`, `customer` and `policy_index_path` are type-checked but not used until slice C.
- Error payloads are proven byte for byte. Timing is not compared.
- There is no per-case behavioral RED (see Gap Analysis 11). The mutation table is the behavioral detection evidence.
- Only synthetic fixtures were used. No prepared real database, private CSV or policy PDF was read.

Status: slice A complete; waiting for review. Slice B not started.

## Revision 06-02-R1

Scope: one confirmed defect in G4 step 3. `execute_tool("get_product", '{"product_id":1e999999999999999999999}', context)` let `decimal.InvalidOperation` escape, and the exponent `-999999999999999999999` reproduced it too. B not started. No commit.

### Spec Summary (revision)

Authorized clarification, applied to `docs/specs/06-tools.md`: the step 3 row, a note under G4 and a new section "Review 06-02-R1". A failure of the `Decimal` representation during parsing is `invalid_input` / `invalid_json` / field `null`. It belongs to step 3, before duplicates, field validation and any resource. Numbers that convert (`1.0`, `1e400`, …) stay `invalid_type`.

### Gap Analysis (revision)

Probed with Python 3.14.3 `decimal` (C implementation, 64-bit):

- `Decimal(text)` is exact and ignores precision. It signals `decimal.InvalidOperation` (`[<class 'decimal.InvalidOperation'>]`) when the exponent is outside the implementation limits.
  - Upper limit: `decimal.MAX_EMAX = 999999999999999999`. `1e999999999999999999` converts, while `1e1000000000000000000` and `1e999999999999999999999` fail.
  - Lower limit: `decimal.MIN_ETINY = -1999999999999999997`. `1e-1999999999999999997` converts and `1e-1999999999999999998` fails; `2e-1000000000000000000` converts. `0e999999999999999999999` fails too.
- `InvalidOperation` derives from `ArithmeticError`, not from `ValueError`, so the `except ValueError, RecursionError` around `json.loads` did not catch it.
- The behavior depends on the caller's context. Under the default context, which traps `InvalidOperation`, the exception escapes. When the caller disables that trap, `Decimal(text)` silently returns `NaN` and sets `InvalidOperation` in the **caller's** flags; the tool then answered `invalid_type` / `product_id`.
- Fix (`src/emporio_agent/tools.py` only):
  - `parse_float=_parse_number`. It converts with `Decimal(text)` inside `localcontext(_NUMBER_CONTEXT)`, where `_NUMBER_CONTEXT = Context(traps=[InvalidOperation])`.
  - `localcontext` works on a copy, so neither the module constant nor the caller's context is mutated.
  - `_parse_number` turns `InvalidOperation` into `ValueError`, which the existing step 3 handler maps to `invalid_json`.
  - The global context is not changed. `_DECIMAL_CONTEXT`, used by `_two_places`, is unchanged.
  - The failure is not turned into `internal_error`.
- Precedence is unchanged. Parsing still ends before duplicate detection, so `{"product_id": 1, "product_id": 1e999…}` gives `invalid_json`. The name and size checks still come first.

### Implementation Plan (revision, as executed)

1. Checked the state: same HEAD `c5d860b3f041fa4619b34b27214049b5ceb8be8a`, and only the slice A files plus the two approved documents in the status.
2. Added the regressions to `tests/unit/test_tool_arguments.py` (TL15 extension) and recorded RED.
3. Fixed numeric parsing in `tools.py`.
4. Ran GREEN, the mutations and the gates.
5. Updated the spec (step 3), the plan (TL15 row) and this report.
6. Repeated Ruff.

### Test Checklist (revision)

New items, all under TL15. They use the real `execute_tool` and the existing local delegating spy `opens`.

The six extreme numbers: `1e999999999999999999999`, `1e-999999999999999999999`, `-1e999999999999999999999`, `1.5E+1000000000000000000`, `1e-1999999999999999998`, `0e999999999999999999999`.

| Test | Items | Checks |
| --- | --- | --- |
| `test_tl15_extreme_exponent_is_invalid_json` | 6 | Inputs: `get_product` and `quote_product`; the number as `product_id`, as `payment_method`, after a duplicate key, nested in an array under an unknown key, and nested in an object. Each case checks the exact payload `invalid_input` / `invalid_json` / `null`. It also checks that no `Decimal`, `decimal`, `Invalid` or digit run appears, and that the spy count is 0. The caller context stays the same object with unchanged flags and traps. |
| `test_tl15_extreme_exponent_ignores_caller_traps` | 6 | Caller context with the `InvalidOperation` trap disabled gives `invalid_json`, never `NaN` → `invalid_type`. The caller's flags stay clear and its trap setting is untouched. |
| `test_tl15_representable_numbers_ignore_caller_context` | 2 | Two caller contexts: no traps, and every trap on with `prec=1, Emax=1, Emin=-1`. Numbers `1.0`, `5.5`, `-0.0`, `1e400`, `1E2`, `1e999999999999999999`, `1e-999999999999999999`, `2e-1000000000000000000` and `1e-1999999999999999997` stay `invalid_type` / `product_id`, with no flag set. |

TL15 now has 15 items (1 + 6 + 6 + 2). The unit file has 141 items, up from 127. The existing JSON, duplicate and number cases (TL04–TL08, TL18, TL32) are unchanged and pass.

RED (real). `tools.py` was unchanged (SHA-256 `683aa732…f53579`). Command `pytest tests/unit/test_tool_arguments.py -k tl15`: exit 1, `12 failed, 3 passed`.

- 6 failures in `…_is_invalid_json[*]`: `decimal.InvalidOperation: [<class 'decimal.InvalidOperation'>]` raised out of `execute_tool`.
- 6 failures in `…_ignores_caller_traps[*]`: payload `invalid_type` / `product_id` instead of `invalid_json` / `null`, because `NaN` was returned silently.
- 3 passed: the existing deep-nesting test and the two representable-number tests. Born green as expected, because they cover numbers that convert.
- A first RED attempt used `2e-1000000000000000000`. That number turned out to be representable: it gave `invalid_type`, not an exception. After probing the real lower limit, I replaced it with `1e-1999999999999999998` and moved it to the representable list. This was a test-data defect; production code did not change.

GREEN: `tools.py` SHA-256 `a4fe898c…abea9bbf`. Both slice A files: exit 0, `172 passed` (141 unit, 31 integration). The reported reproduction now returns `{"tool":"get_product","status":"invalid_input","data":null,"error":{"code":"invalid_json","field":null}}` for both exponent signs.

### Test Strategy (revision)

- Real `execute_tool` and the existing delegating spy; literal payload oracles.
- The caller context is checked by object identity and by comparing `dict(flags)` and `dict(traps)` before and after.
- Production code never touches the global decimal context; the tests change only their own `localcontext`.
- No mock.

### Compliance Report (revision)

After GREEN, I re-ran all mutations. Each had its bytes restored and checked by SHA-256; after restoration the hash is `a4fe898c4691e8d3ede77040c46ebdb88baa5f44a7d2a407ff4743dfabea9bbf`, the same as before.

| Mutation | Result | Failing tests (new or changed) |
| --- | --- | --- |
| M1 lax validation | detected | as before, plus `tl15_representable_numbers_ignore_caller_context` |
| M2 duplicates accepted | detected | as before |
| M3a non-finite constants accepted | detected | TL06 |
| M3b `parse_float` removed | **detected** (before the revision: survived) | both TL15 extreme-exponent tests (`inf` float → `invalid_type`) |
| M3c fraction/exponent coerced to `int` | detected | as before, plus the three TL15 extension tests |
| M4a, M4b echo | detected | as before |
| M5 open before validation | detected | as before, plus the TL15 extension |
| M6 Pydantic message exposed | detected | as before, plus `tl15_representable…` |
| M7a, M7b money | detected | as before |
| M8a plain `parse_float=Decimal` (pre-fix code) | detected | both TL15 extreme-exponent tests |
| M8b private `localcontext` removed (caller context used) | detected | both TL15 extreme-exponent tests (caller flag set; `NaN` with the trap off) |
| M8c `InvalidOperation` mapped to `internal_error` | detected | both TL15 extreme-exponent tests |

Re-evaluation of M3b: not equivalent. The earlier claim rested only on the numbers tested. Inputs outside the `Decimal` limits separate the variants: the pre-fix `Decimal` path raised an exception, while the `float` path gives `inf` → `invalid_type`. With the fix, the correct answer is `invalid_json`, and the tests now kill M3b.

Final gates, after the code change and after every documentation update:

| Command | Exit | Result |
| --- | --- | --- |
| `uv run --frozen pytest tests/unit/test_tool_arguments.py tests/integration/test_tools_products.py -q -rs` | 0 | 172 passed |
| `uv run --frozen pytest tests/unit/test_pricing.py -q` | 0 | 37 passed |
| `uv run --frozen pytest -q -rs` | 0 | 1472 passed, 2 skipped (1300 + 172); skips unchanged (`test_readonly_connection.py:599`, `:612`) |
| `uv run --frozen ruff check --no-cache .` | 0 | All checks passed (Python files only; see "Ruff and Markdown") |
| `uv run --frozen ruff format --check --no-cache .` | 0 | 85 files already formatted (included `.md` files counted) |

Files changed in this revision:

| Path | Change |
| --- | --- |
| `src/emporio_agent/tools.py` | `InvalidOperation` import, `_NUMBER_CONTEXT`, `_parse_number`, `parse_float=_parse_number` |
| `tests/unit/test_tool_arguments.py` | `import decimal`, `EXTREME_NUMBERS`, `all_signals_context`, three TL15 tests; no existing test changed |
| `docs/specs/06-tools.md` | step 3 row, G4 note, section "Review 06-02-R1"; SHA-256 now `ffd6b8f9457e09fd6ce7013826a0abed8adaecc017f92fd93c041287c0fe6371` |
| `docs/plans/06-tools.md` | TL15 row only; SHA-256 now `01b1d0e26e3e0bccfbb9a326fa74f68b5a9f5480289c16be24ace561403923e1` |
| `docs/validation/06-01-tools-core.md` | this section and the in-place corrections (M3b, Ruff and Markdown, document hashes) |

Not changed in this revision: `tests/integration/test_tools_products.py`, `tests/integration/conftest.py`, the services, the data layer, dependencies, configuration and the lockfile.

Limits:

- The decimal limits are those of the 64-bit C implementation probed here. The tests use values far beyond both limits, plus the exact boundary pair on the negative side.
- Only numbers with a fraction or an exponent go through `Decimal`. Integer literals keep the existing 4,300-digit `ValueError` path (TL15).

Status: revision 06-02-R1 applied; waiting for review. Slice B not started.
