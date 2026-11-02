# ADR 0001: Modular monolith, synchronous execution and CLI

Status: accepted for the MVP by the candidate on 2026-11-02.

## Context

The challenge asks for a Python text assistant using the supplied operational tables and store policies. It explicitly accepts a CLI, notebook, API or simple UI. The initial workload is one local conversation over 65 products, 20 orders and an eight-page policy manual. Correct tool use, grounded responses and clear reasoning are central to the requested demonstration.

## Decision

Use a modular monolith with synchronous execution and a command-line interface. Keep the CLI, agent orchestration, tool contracts, business services, repositories and OpenAI adapter separate within one application. The LLM chooses tools and writes customer-facing answers; deterministic code enforces prices, discounts, availability and customer-scoped order access.

These are three independent decisions. A web interface or asynchronous adapter could be introduced without splitting the domain into services deployed separately.

## Rationale

- A single application reduces environment setup and operational overhead for the evaluator. Clear module boundaries still support isolated testing and later interface changes.
- Synchronous execution fits a single local conversation and makes the tool loop, errors, timeouts and retry accounting explicit. No concurrent user workload has been specified.
- A CLI satisfies an explicitly allowed challenge interface and lets the first increment focus on data correctness, retrieval, conversation context and reproducible evidence.
- Domain services remain independent from the terminal and model SDK, so a future thin FastAPI adapter can reuse the same behavior.

## Alternatives and consequences

| Alternative | Benefit | Reason for deferral |
| --- | --- | --- |
| HTTP API and web chat | Stronger visual demonstration for a full-stack role | Adds transport, UI and additional boundary tests; reconsider after the agent is verified or if the candidate changes scope. |
| Async orchestration | Concurrent sessions and overlapping I/O | No current concurrency requirement; would add lifecycle and cancellation complexity. |
| Independently deployed services | Independent scaling and deployment | Dataset and local prototype do not demonstrate that need. |

The CLI has a weaker visual presentation and requires terminal use. Synchronous model calls block that conversation while waiting. The monolith scales its components together. These are accepted MVP limitations, not universal claims that this architecture is best for every workload.

## Review triggers

Revisit the interface if a browser demonstration becomes a requirement. Revisit concurrency if simultaneous sessions become necessary. Revisit deployment boundaries only when a concrete workload or operational requirement justifies them.

## Verification

Architecture acceptance does not prove runtime behavior. Validate domain rules with pytest, repository behavior with real temporary SQLite databases, tool contracts independently, and five conversations with the real configured model. Preserve these validation boundaries in the README.
