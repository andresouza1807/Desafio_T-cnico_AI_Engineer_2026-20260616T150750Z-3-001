# Slice 01 validation — project bootstrap and unit pricing

Date: 2026-11-02. Scope: local environment, ADR 0001 and the pure pricing contract.

## Spec Summary

Implemented only the service specified in docs/specs/01-pricing.md: table prices, PIX, active promotion selection, non-cumulativity, cent precision, HALF_UP rounding and explicit invalid-input handling. Architecture rationale is recorded separately in ADR 0001.

## Gap Analysis

No blocking discrepancy was found in this slice. The following remain outside its validation boundary: CSV import, database behavior, policy retrieval, identity isolation, real OpenAI calls, conversational context, CLI and the five example conversations.

## Implementation Plan

The user-provided project directory was linked to origin/main at baseline 62076ef, and feature/agent-mvp was created. Existing LICENSE and internal planning documents were preserved. Internal documents containing local paths are ignored by Git; public ADR, specification and validation are in English.

The documented pricing tests were written and run before the service module was created. Then the minimum contracted domain implementation was added and checked. No push, merge, email or form submission occurred in this slice.

## Test Checklist

- [x] Regular table price and two-place output.
- [x] PIX without active promotion.
- [x] Active promotion with regular payment or PIX, without stacking.
- [x] Inactive promotion ignored and largest active discount selected.
- [x] HALF_UP, zero price, 100% and active 0% promotion.
- [x] Rejection of invalid money, percentages, payment and activity values.
- [x] Caller promotion list unchanged.
- [x] Ruff lint and formatting.
- [x] Independent read-only review: no blockers reported; 37 tests independently confirmed passing.

## Test Strategy and observed evidence

Commands were executed inside the actual project directory and its own Python environment.

RED, before creating emporio_agent.services.pricing:

```text
.venv\Scripts\python.exe -m pytest tests/unit/test_pricing.py -q --tb=line
37 failed in 0.06s
Observed pytest exit code: 1
Reason: ModuleNotFoundError: No module named 'emporio_agent.services.pricing'
```

The tests collected successfully and failed on the missing implementation. The raw RED output is preserved as 01-pricing-red.txt alongside this report.

GREEN, after implementing the contracted service:

```text
uv sync --frozen
emporio-agent==0.1.0 built and installed from the local project

uv run --frozen pytest -q
37 passed in 0.05s

uv run --frozen ruff check .
All checks passed!

uv run --frozen ruff format --check .
8 files already formatted
```

Dependency resolution and local package installation succeeded on Python 3.14.3. Selected installed versions: openai 3.42.1, pydantic 2.14.2, pypdf 6.21.4, pytest 9.2.0 and ruff 0.17.3. The lockfile records the complete resolved set. This was the project's initial environment creation, not a separate second-machine reproduction.

## Compliance Report

- Spec vs contract: payment and promotion rules are expressed in a pure service with no external side effects.
- Spec vs tests: 37 unit cases cover the listed price, validation and input-mutation scenarios; real model and data interactions are not claimed.
- Spec vs code: calculations live in the service layer; no prompt, route or database query owns this logic.
- Tests vs code: observed RED before implementation, GREEN after implementation, lint/format checks passed, independent review reported no blockers.
- Assumptions: maximum active promotion wins; active 0% still excludes PIX; sub-cent table prices are rejected. All were documented before implementation.
- Status: COMPLIANT for Slice 01 only; the complete challenge solution remains incomplete.
