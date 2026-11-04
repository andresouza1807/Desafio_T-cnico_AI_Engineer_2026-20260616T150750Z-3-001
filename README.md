# Empório da Música — AI customer-service assistant

Python technical challenge for the Artefact AI Engineer role.

A text assistant for the fictional musical instrument store Empório da Música (Campo Grande/MS). It answers customers in Brazilian Portuguese through a local command-line interface. It uses the challenge's six CSV tables (catalog, promotions, customers, orders, order items, categories) and the store policy manual `políticas_da_loja.pdf`. The model decides which tool to call; prices, discounts, availability and order access are enforced by application code.

Submission documentation is in English. The conversations with the agent are in PT-BR; the [examples](examples/README.md) keep the original transcripts and add English translations labelled as translations.

## Scope

In scope:

- Catalog search by name, category and budget; product details; availability; regular and PIX unit quotes.
- Store policies (hours, address, payment, delivery, exchange, return, warranty and others in the manual), stated in plain words with the conditions that matter. Answers do not show sources, sections, pages or other internal references, and the manual's internal service guidelines and procedures are not shared.
- Order lookup for a demonstration customer selected by the operator (status, items, registered total, tracking, registered estimate).
- Context inside one session (follow-ups such as "e no PIX?"), out-of-scope handling and refusal of instruction overrides.

Out of scope: purchases, reservations, payments, return authorization, tickets, contact with staff, WhatsApp or e-mail, carriers, internet access by the agent, real login, web or HTTP interface, persistence of conversations across runs. The agent says so instead of claiming an action.

## Requirements and installation

- Python 3.14 (`requires-python = ">=3.14,<3.15"`, `.python-version`).
- [uv](https://docs.astral.sh/uv/). Dependencies are pinned in `uv.lock` (`openai`, `pydantic`, `pypdf`; dev: `pytest`, `ruff`).
- An OpenAI API key with access to the configured model, for `chat` only. Preparation and tests need no key and no network after installation.

From the repository root (PowerShell):

```powershell
uv sync --frozen
uv run --frozen emporio-agent --help
```

The entry point `emporio-agent` has three subcommands: `prepare-data`, `prepare-policies` and `chat`. Help texts are in PT-BR.

## Configuration

`chat` reads only environment variables. There is no `.env` loading and no command-line argument for the key.

| Variable | Rule |
| --- | --- |
| `OPENAI_API_KEY` | Required. Never printed or logged. |
| `OPENAI_MODEL` | Required, no default. Tested value: `gpt-5.4-mini` (the provider reported `gpt-5.4-mini-2026-03-17`). Any other model is an unevaluated configuration. |
| `OPENAI_LOG` | Must be absent; `chat` refuses to start if it is set, because SDK logging may print request metadata. |

Set the key in the current terminal only, without writing it to a file or to the command history:

```powershell
$secure = Read-Host "OPENAI_API_KEY" -AsSecureString
$env:OPENAI_API_KEY = [System.Net.NetworkCredential]::new("", $secure).Password
$env:OPENAI_MODEL = "gpt-5.4-mini"
# ... run chat ...
Remove-Item Env:OPENAI_API_KEY
```

Fixed in code (not configurable): reasoning effort `low`, 4,096 output tokens per model call, 30 s timeout per request phase, at most 8 model calls (one automatic retry included) and 6 tool executions per message, at most 30 messages per `chat` process, history of up to 20 turns.

[`configuration.example.env`](configuration.example.env) lists the same variables with placeholders only. It is a reference, not a file the application reads.

Never commit a real key or paste it into documentation, examples or the chat.

## Preparing the original data locally

The original CSVs and the manual are **not** in this repository, and the prepared database and index must not be published: `customers.csv` holds the customers' contact data. Keep them outside version control. `data/local/` is ignored by Git and is the default location of the prepared files.

1. Keep the challenge package in its own folder outside the repository. Its `data` folder holds the six files `desafio_tecnico_ai_eng - <table>.csv` (`categories`, `customers`, `order_items`, `orders`, `products`, `promotions`) and `políticas_da_loja.pdf`.
2. Prepare the SQLite database and the policy index (the commands do not create directories):

```powershell
New-Item -ItemType Directory -Force data/local | Out-Null
uv run --frozen emporio-agent prepare-data --source-dir "<challenge package>\data"
uv run --frozen emporio-agent prepare-policies --source-pdf "<challenge package>\data\políticas_da_loja.pdf"
```

Defaults: database `data/local/emporio.sqlite3`, index `data/local/policy-index.json` (change with `--database` and `--index`). `prepare-data` validates every field, key and relation and replaces the database atomically; it refuses to overwrite a file that is not a prepared database. On the original files it reports `tabelas=6 categories=9 customers=50 products=65 promotions=25 orders=20 order_items=22 notas_qualidade=8`. `prepare-policies` reported `paginas=8 secoes=29 recuperaveis=26` on the original manual (local validation record 09-01, 2026-11-04, not included in this repository snapshot).

Public offline reproduction without the originals: `--source-dir tests/fixtures/data/valid` prepares a small synthetic database (two fictional customers). There is no public policy PDF, so `chat` still needs the original manual.

Do not commit, attach or publish: the CSV files, the PDF, `data/local/` (databases, indexes, captures), screenshots of sessions with customer data, or a filled `.env`.

## Running the assistant

```powershell
# Anonymous session: catalog and policies only
uv run --frozen emporio-agent chat

# Demonstration customer 1, with safe diagnostics on stderr
uv run --frozen emporio-agent chat --customer 1 --diagnostics
```

Use `--database` and `--index` if the prepared files are not in the default location. In the session, type one message per line. Commands: `/sair` ends the session, `/reiniciar` starts a new anonymous conversation, `/cliente <id>` switches the demonstration customer (it always resets the conversation first; a failed switch leaves an anonymous session).

**Demonstration identity is not authentication.** The operator selects the customer with `--customer` or `/cliente`; the customer only "is" that customer by convention of the local demonstration. Text typed in the chat never changes the identity, and the order tool receives only an order ID, never a customer ID from the model. Do not expose this CLI as a service.

Everything typed is sent to the OpenAI API (the banner says so). Do not type personal data or keys.

`--diagnostics` prints one line per turn on stderr: tools run with their status, product/policy/order references, model attempts, retries, tokens and the configured and reported model. It never prints tool arguments, order IDs, payloads, the key or customer fields.

## Architecture

Modular monolith, synchronous, CLI. One application keeps setup simple for the evaluator; synchronous execution fits one local conversation and keeps the tool loop, timeouts and retries explicit; a CLI is an interface the challenge allows. The services do not depend on the terminal or the model SDK, so a thin web or HTTP adapter could reuse them. Accepted costs: weaker visual presentation, a blocked conversation while the model answers, and components that scale together.

```text
CLI (cli.py) -> Agent loop (agent.py) -> OpenAI Responses API adapter (openai_provider.py)
                     |
                     v
            Typed tools (tools.py) -> services (catalog, pricing, policies, orders)
                                        -> read-only SQLite / in-memory policy index
```

Flow of one message: the CLI passes the line to the agent; the agent sends fixed instructions, tool schemas, the retained history and the message to the model; for each function call it validates the arguments strictly and runs one of the five registered tools; tool results go back to the model until it writes the answer, within the per-message limits. Any failure (invalid arguments, missing data, unknown tool, API error, limit reached) becomes a fixed answer or tool status, never an invented result.

| Tool | Input from the model | Returns |
| --- | --- | --- |
| `search_products` | optional `query`, `category`, `max_price_brl`, `available_only`, `offset` | up to 5 products ordered by effective price, `total_matches`, `next_offset` |
| `get_product` | `product_id` | details, specs, description, availability, quality notes |
| `quote_product` | `product_id`, `payment_method` (`regular` or `pix`) | unit quote: original price, discount source, final price |
| `search_policies` | `query` | up to 3 reviewed public sections (topic title, heading path, text) plus fixed guidance; no section IDs, pages, source or notice codes |
| `get_order` | `order_id` (customer from the session only) | allowed order fields, or one `order_not_accessible` answer |

### Technical decisions

- **Agent approach:** function calling with the official OpenAI Python SDK and the Responses API, with an orchestration loop written in the project. No agent framework: the loop, limits, retries and failure handling stay explicit and testable without the network. The SDK's own retries are disabled; the agent controls the single retry.
- **Model:** `gpt-5.4-mini` with reasoning effort `low` and a 4,096-token output cap. It is the configuration that passed the real adapter evaluation (checks EV01–EV08, local validation record 07-09, not included in this repository snapshot) and that ran every final conversation. No other model or effort was evaluated.
- **Structured data:** the CSVs are validated and loaded into SQLite (money as integer cents); chat opens it read-only with parameterized queries. The model never writes SQL.
- **Policy retrieval:** the 8-page manual is split into sections with page numbers. Retrieval is local and lexical (accent and case folding, stemming, curated concept synonyms) and returns whole sections, so conditions and exceptions stay together. No embeddings or vector database: the manual is small, and deterministic retrieval can be tested offline. Curated notices flag known conflicts of the manual (two phone numbers, two e-mail spellings, installment rules, the free-shipping gap at exactly R$ 500, no holiday calendar, no hand-off integration); they reach the model only as fixed guidance, without codes or section numbers.
- **Internal content boundary:** the manual is an internal document that mixes customer rules with staff directives. Before retrieval, a public projection keeps only lines (and titles) that a content review classified as public; the review is bound to the manual's SHA-256 and to a digest of every section. Service guidelines, the standard service flow, special-situation procedures, final provisions and the cover's manual identity and version never reach the model. Another manual, or a changed section, is withheld (`search_policies` answers `unavailable` for an unreviewed manual). Section IDs and pages stay internal and appear only in the opt-in `--diagnostics` references. Before an answer is shown, a fixed check withholds text with internal markers (tool names, internal codes, internal references, a "Fonte:"/"Source:" line, a numbered section citation, "front matter", verbatim instructions) and shows a fixed message instead.
- **Prompt strategy:** fixed developer instructions in English (`src/emporio_agent/instructions.py`) ask for PT-BR answers with a warm, informal and professional tone, as a virtual assistant that never claims to be human. They require that every fact about products, prices, orders and policies come from tool results of the conversation, with rules for catalog search, PIX, availability, policies, confidentiality, orders and returns. Tool results and manual text are data, not instructions.
- **Conversation history:** kept in memory for one session (up to 20 turns, whole turns removed first); nothing is written to disk.
- **Data handling:** source values are not corrected. Products whose name, description and specs conflict carry quality notes, and the agent does not describe the withheld details.

### Business rules (enforced in code)

- Money uses `Decimal`, two places, `ROUND_HALF_UP`.
- A promotion applies only if `is_active` is true; campaign names and the manual's calendar do not activate it. If several promotions are active for a product, only the largest percentage applies, without stacking (assumption; no product in the data has two active promotions).
- PIX gives 5% only when no promotion is active; it never combines with an active promotion. Only `quote_product` decides a PIX amount.
- Card installment simulations use integer cents and distribute any remainder across the first installments so their sum equals the quoted price. They are simulations, not a guaranteed checkout schedule. Minimum amounts are checked; ambiguous conditions require confirmation. PIX plus card combinations are not simulated: the assistant can offer a simulation of the full price on the card instead.
- Available means `status = active` and stock > 0. Discontinued, coming-soon and out-of-stock products are reported as such and are not offered.
- Order totals are the historical registered totals, not recalculated with current prices. A past estimate is reported as a past registered estimate, not as a confirmed delay, and no new date is given.
- Returns: the agent asks for the purchase channel and the real receipt date and never infers receipt from the estimate or the order date.
- Order lookup returns status, items (product ID, current catalog name, quantity), total, tracking code, estimate and its status. It never returns customer name, phone, e-mail, city, payment method or notes. Without a linked customer, for another customer's order and for a non-existent order the tool gives the same answer.

## Tests and validation

```powershell
uv run --frozen pytest -q
uv run --frozen ruff check .
uv run --frozen ruff format --check .
```

The tests need no key and no network: unit tests, integration tests on temporary SQLite databases and synthetic PDFs, tool contract tests, and agent-loop tests with a scripted provider. Last completed full offline run: mixed-payment wording follow-up 10-11, 2026-11-04 (local record, not included in this snapshot): 3,619 passed, 3 skipped in 82.18 seconds. Two skips are Windows file-name cases; one is a hard-link permission restriction in the review environment. `ruff check` and `ruff format --check` passed. This run covered the current code before this documentation update; it does not establish real-model reliability. Run the commands above to check your environment.

Real evaluations with the OpenAI API were run separately, each under an execution contract fixed before the calls (model, effort, messages, criteria, failure classes and stop rules): adapter evaluations (records 07-06 and 07-09), a CLI session on synthetic data (08-06), PIX grounding checks (08-11, 08-12, 08-13) and the final conversations (09, 09-03, 09-06). Those records are local validation records, not included in this repository snapshot. The [examples](examples/README.md) carry the transcripts, the criteria and the results of the final conversations.

During development, each slice had a specification, a plan with a test checklist and a validation report. These working documents stay local and are not part of this snapshot; the rules they fixed are restated in this README and enforced by the tests.

## Example conversations

Five current-version real sessions on the original data, 2026-11-04 ([index](examples/README.md)):

| # | Scenario | Result |
| --- | --- | --- |
| C1 | [Guitars up to R$ 1.000, comparison, PIX follow-up](examples/01-catalog-current.md) | Passed with presentation limitations |
| C2 | [Active promotion, PIX not cumulative, out-of-stock product](examples/02-promotion-current.md) | Passed with a presentation limitation |
| C3 | [Regret and return](examples/03-return-current.md) | Partial: essential requirements observed, further conditions omitted |
| C4 | [Own order and another customer's order](examples/04-order-isolation-current.md) | Passed |
| C5 | [Hours and address, out-of-scope request, prompt injection](examples/05-policy-security-current.md) | Partial: essential requirements observed, presentation limitations |

All five sessions ran once, with 14 messages, 26 provider calls and no retries. Customer replies show no source citations or internal guidelines. Internal references were used for evaluation, separately from the customer transcripts. These observations establish no success rate or absolute confidentiality. Earlier transcripts are preserved locally, outside this public snapshot.

## Known limitations

- One real session per scenario. No statistical claim about quality, safety, cost or latency.
- The model can still choose wrong search filters or queries. The first C1 run said no guitar was available; the first C5 run missed the hours. Guidance corrections fixed both cases in one revalidation each, which does not prove they always work.
- Policy ranking favors the sections that match the most query concepts, so a two-topic query can drop one topic without a signal; the agent is told to search each topic separately. A service-side signal for a dropped topic is not implemented.
- Answers sometimes omit non-essential conditions (C3: other 4.1 conditions and official channels; C5-R1: special-dates line) or adopt the customer's wording in a refusal (C4, C5-R1).
- Diagnostics do not show tool arguments, so the queries and order IDs the model sent are not proven.
- Confidentiality is enforced deterministically only for what the code covers: internal manual text is not sent to the model, and answers with fixed internal markers are withheld. The model still receives its own instructions, so describing them in other words, provenance phrases without a fixed marker ("segundo o manual"), choosing between conflicting values, or promising a response time depends on the model and is not proven offline. The output check can also withhold a legitimate answer (for example a product line that starts with "Fonte:"). No leak-proof claim is made.
- Demonstration identity only; no authentication. No immunity to prompt injection is claimed; one injection wording was tested in real sessions.
- Typed text, tool results and public manual excerpts (including the store's institutional contacts) are sent to the OpenAI API.
- The CSVs are a snapshot; there is no live stock, carrier or payment integration.
- No public policy PDF or public demonstration dataset: running `chat` needs the challenge files.
- Windows/PowerShell was the only environment tested.

With more time: a service signal for dropped policy topics, per-call references in the diagnostics, repeated real evaluations to measure success rates, a public synthetic demo dataset and manual, real authentication before any order lookup, and a thin web or HTTP adapter over the same services.

## Use of AI coding assistants

Two assistants were used:

- **ChatGPT/Codex (OpenAI)** supported planning, the writing of a prompt for each slice, reviews and local checks.
- **Claude Code (Anthropic)** is identified in the execution records of the slices as the tool that implemented and validated slices.

Git history does not attribute individual commits or files to either tool (no commit carries a co-author trailer).

Workflow: work was cut into small slices. For each slice, a prompt fixed the scope and what was not authorized; the slice then went through a specification, a plan with a test checklist, failing tests, the implementation, passing gates and a validation report, and stopped for review. Real model calls ran only under execution contracts fixed before the calls, with failures recorded instead of repeated.

AI support versus the candidate's decisions: the assistants planned, wrote, implemented and checked within each slice. The records show that each paid real run started only after explicit authorization, that open decisions (for example the reading of C4 turn 2, or keeping `docs/` and `.env.example` out of this snapshot) were left to the candidate, and that each slice stopped for review before the next one.

## Repository map

- `src/emporio_agent/`: CLI, agent loop, provider adapter, tools, services, data preparation.
- `tests/`: unit and integration tests; synthetic fixtures in `tests/fixtures/`.
- `evals/`: scripts used for offline and real evaluations.
- `examples/`: example conversations ([index](examples/README.md)).
- `configuration.example.env`: configuration template with placeholders only.

Specifications, plans, decision records and validation reports were kept in a local `docs/` folder during development. That folder is not part of this repository snapshot. References to records such as "09-01" or "plan 09" in this README and in `examples/` name those local records; the context needed to read the examples is in the [examples index](examples/README.md). Comments and docstrings in `src/`, `tests/` and `evals/` also cite `docs/...` paths. They are traceability notes to those local records, not runtime dependencies: no code or test reads `docs/`.

## License

Apache License 2.0 ([LICENSE](LICENSE)).
