# Project bootstrap and unit pricing implementation plan

> Execution: inline in the user-provided isolated project directory, on feature/agent-mvp. Follow executing-plans, python-fastapi-sdd and test-driven-development. Do not delegate this slice.

Goal: establish the local repository/environment and implement the unit-price contract with RED/GREEN evidence.
Architecture: modular monolith; this increment contains only a pure service. No database, model call or CLI behavior is implemented yet.
Tech stack: installed Python 3.14, uv, Decimal, pytest and Ruff; resolve the approved future runtime libraries into the lockfile.

## Files

- docs/decisions/0001-modular-synchronous-cli.md: accepted architectural rationale.
- docs/specs/01-pricing.md: source of truth for this increment.
- docs/plans/01-pricing.md: this execution checklist.
- pyproject.toml, .python-version, .gitignore, .env.example: reproducible environment and local exclusions.
- tests/unit/test_pricing.py: explicit expected-price and invalid-input cases derived from the spec.
- src/emporio_agent/services/pricing.py: immutable Promotion/PriceQuote records and quote_price.
- Package __init__.py files: import/package discovery only.
- docs/validation/01-pricing.md: observed RED/GREEN, static checks and limits.

## Execution checklist

- [x] Verify the directory and remote before mutation. Retain untracked local planning documents and existing remote files.
- [x] Prepare branch feature/agent-mvp from origin/main; do not push.
- [x] Write the ADR, service specification and this plan before behavior tests.
- [x] Resolve project dependencies with `uv sync --no-install-project`; record incompatible constraints instead of changing runtime silently.
- [x] Create the pricing test file before the pricing module. Its public records and function names must match the service contract.
- [x] Run `uv run --no-project --python .venv/Scripts/python.exe python -m pytest tests/unit/test_pricing.py -q` from the project directory. The first run must fail because the pricing behavior is not implemented. Fix test setup separately if the failure is unrelated.
- [x] Implement the contracted records and calculation. Validate finite Decimal inputs and source cent precision; select the highest active promotion; otherwise apply PIX; quantize once to cents with ROUND_HALF_UP.
- [x] Run `uv sync --frozen` to install the local application, then `uv run --frozen pytest -q`, `uv run --frozen ruff check .` and `uv run --frozen ruff format --check .`.
- [x] Verify module __file__ is inside the requested local project; inspect git status/diff and check the tracked set before any commit.
- [x] Write observed validation evidence. Stop this increment with explicit boundaries: pure pricing works only if verified; the assistant, data ingestion and provider remain unimplemented.

## Acceptance and next slice

Acceptance requires the existing service contract to match tests and implementation, a resolved lockfile, fresh passing offline checks and no original customer data or secrets staged. No push, model call, publication or submission belongs to this slice.

Next slice: CSV preparation and SQLite repositories, including atomic rebuild, read-only access and preserving relationships with synthetic public contacts. Derive its contract and integration tests before implementing it.
