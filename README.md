# Empório da Música — AI customer-service assistant

Python technical challenge for the Artefact AI Engineer role.

## Current status

Development is incremental. The first verified slice provides a reproducible environment and a pure unit-pricing service. The conversational agent, CSV ingestion, policy retrieval, customer-scoped order lookup and CLI are not implemented yet. Installing the OpenAI SDK does not establish a working model integration.

The intended assistant will answer in Brazilian Portuguese using the supplied catalog and store policies. Submission documentation is in English. Recorded Portuguese conversations will include clearly labeled English translations.

## Architecture

A modular monolith with synchronous execution and a local CLI. Business rules, repositories, tool contracts and the OpenAI adapter have separate responsibilities inside one application. The LLM chooses tools and composes responses; prices and access checks are enforced by application code.

This fits a small local dataset and makes installation, debugging and testing straightforward. The challenge explicitly permits a CLI. Its weaker visual presentation and synchronous request blocking are accepted MVP trade-offs. A future web adapter can reuse the same services if the scope changes.

See [ADR 0001](docs/decisions/0001-modular-synchronous-cli.md) and the [pricing specification](docs/specs/01-pricing.md).

## Setup and offline checks

Requirements: Python 3.14 and [uv](https://docs.astral.sh/uv/). From the repository directory:

```powershell
uv sync --frozen
uv run --frozen pytest -q
uv run --frozen ruff check .
uv run --frozen ruff format --check .
```

Dependencies are pinned in uv.lock. The pricing tests do not require an API key, network access or source datasets after environment installation. The local .venv is ignored by Git.

## First implemented behavior

- Table prices use Decimal and must be finite, nonnegative and exactly representable in cents.
- Only active promotions apply; the largest active percentage is used without accumulation.
- PIX provides 5% when no promotion is active. PIX never stacks with an active promotion, including an active 0% offer.
- Final prices use ROUND_HALF_UP to two decimal places. Original inputs remain unchanged.
- Invalid payment choices and malformed percentages/activity values raise ValueError.

No product sale, reservation, refund, order change or external action is performed.

## Planned OpenAI configuration

The approved integration uses the official Python SDK and Responses API. The initial candidate model is gpt-5.4-mini-2026-03-17, configurable through OPENAI_MODEL and subject to actual account access and evaluation.

```powershell
$env:OPENAI_API_KEY = "your-own-key"
$env:OPENAI_MODEL = "gpt-5.4-mini-2026-03-17"
```

These variables are for the future integration; no chat command is available in this increment. .env.example documents variable names. Automatic .env loading is not implemented. Never commit or paste a real key into documentation, conversations or example files.

## Decisions, evidence and limitations

- [Architecture rationale](docs/decisions/0001-modular-synchronous-cli.md)
- [Slice 01 contract](docs/specs/01-pricing.md)
- [Execution checklist](docs/plans/01-pricing.md)
- [Observed validation](docs/validation/01-pricing.md)

Verification currently covers 37 pricing tests and static checks on the development machine. It does not establish real-provider behavior, retrieval accuracy, data import correctness, authentication, concurrent-user operation or production readiness. No customer dataset or source PDF is required or bundled in this increment.

## Use of AI coding assistance

OpenAI Codex assisted with reading the challenge materials, inspecting the supplied data, drafting the scope and architecture, deriving tests and implementing the first pricing slice. The workflow followed specification → service contract → failing tests → implementation → passing checks. An independent Codex review checked this slice for material contract and financial correctness issues. The candidate reviews the decisions and is responsible for explaining and submitting the result. Assistant contributions and validation boundaries will be updated as the project progresses.
