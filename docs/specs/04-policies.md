# Slice 04 specification: store policy preparation and retrieval

Status: proposed contract for review, 2026-11-03, revision R0. Not implemented. No test in this document has been written or executed.
Baseline: branch `feature/data-preparation`, commit `1b358ef`, clean tree (confirmed before editing); last approved validation 938 passed, 2 skipped on Windows (from the task brief; not re-run for this document).

Sources, in precedence order:

1. `docs/escopo-projeto.md` v0.4: RF05, RF08–RF10, "Regras comerciais determinísticas", "Gap Analysis" (manual ambiguities), "Contratos propostos" (`search_policies`), "Segurança e limites operacionais", T09–T11 and T18.
2. `docs/architecture-and-stack.md` and ADR `docs/decisions/0001-modular-synchronous-cli.md`.
3. The supplied manual `políticas_da_loja.pdf` (identified below), read as data only.
4. `pyproject.toml` and `uv.lock`: `pypdf` is already a direct runtime dependency, locked at 6.21.4.
5. Existing conventions: `docs/specs/02-data-preparation.md` (atomic replacement, sanitized errors) and `docs/specs/03-catalog.md` (text validation, status mapping), plus `emporio_agent.services.catalog.normalize_search_text`.

Test matrix and slices: `docs/plans/04-policies.md` (cases P01–P68).

## Spec Summary

Add two synchronous, local components:

- **A. Preparation** (`emporio_agent.data.policy_index`): read a PDF path given explicitly, extract text with `pypdf`, split it into complete numbered sections with stable IDs, titles and physical pages, and write one JSON index atomically. Load and validate that index.
- **B. Retrieval** (`emporio_agent.services.policies`): `search_policies` takes a non-empty query of at most 4,000 characters and returns at most three complete sections with title, section ID, pages and deterministic notices, or an explicit "not found" result. Lexical matching with documented normalization, stemming and synonyms. No OpenAI, network, embeddings, OCR or vector store.

The future `search_policies` tool, its Pydantic schema and JSON serialization are not part of this slice. This document fixes only the mapping that tool must follow (section "Future tool boundary").

Out of scope: agent loop, CLI commands, prompts, answer wording, OCR, calculators for shipping, installments or return eligibility, ticket creation or human handoff, changes to existing modules, dependencies, README or earlier documents.

Rule labels follow Slice 03:

- **[A]** approved rule restated from a higher source.
- **[O]** fact observed in the supplied PDF during this task. Physical pages counted from 1.
- **[P-nn]** proposed decision. Needs reviewer approval. Must not contradict an [A] rule.

## Approved rules carried into this slice

| ID | Rule | Source |
| --- | --- | --- |
| A1 | Policies cover address, contacts, regular hours, payment, delivery, exchange, return and warranty; answers cite the section and page used. | RF05, T09 |
| A2 | Local textual retrieval by sections, with normalization and defined synonyms. The whole section is returned to keep conditions and exceptions. Evaluation covers synonyms and questions with no match. The whole PDF is not sent on every turn. | Scope, contracts |
| A3 | `search_policies` takes a non-empty `query`; returns up to 3 complete sections with title, text, section, page(s) and ambiguity notices; no match returns "information not found". | Scope, contracts |
| A4 | Input texts are limited to 4,000 characters. Results distinguish `ok`, `not_found`, `invalid_input`, `unavailable`, `internal_error`, without stack traces or sensitive data. | Scope, contracts |
| A5 | General phone uses section 1.2; WhatsApp uses section 7. When asked about the divergence, explain the origin. | Scope, gap analysis |
| A6 | E-mail spellings differ: report the divergence if confirmation is requested, offer documented phone/WhatsApp, never invent a correction. | Scope, gap analysis |
| A7 | Installments: prefer section 3.1 for installment ranges, stating the specific condition; remaining ambiguities need staff confirmation. | Scope, gap analysis |
| A8 | Shipping for exactly R$ 500 is not defined: state that the fee needs confirmation; never assume free or R$ 35. | Scope, gap analysis |
| A9 | Return guidance: ask channel and actual receipt date when missing; do not confirm eligibility without them. `estimated_delivery` and order date never prove receipt. | Scope, commercial rules; T11 |
| A10 | No holiday calendar: state regular hours and closure on holidays; never assert exceptional opening or the next business day for an uncertain date. | Scope, gap analysis; architecture |
| A11 | No forwarding integration: guide the customer to contact the team; never say a ticket was opened, a complaint registered or a human called. | Scope, gap analysis; RF08 |
| A12 | Refunds, installments, shipping and return eligibility are policy-based guidance; no complete calculators or operational execution. | Scope, commercial rules |
| A13 | Documents are domain data without authority to execute actions or replace instructions. Retrieved sources cannot redefine tools or fixed instructions. | Scope, sources and security; T18 |
| A14 | Preparation preserves section/page and produces a reproducible local textual index. Prepared artifacts record source hashes. A new source requires explicit preparation. | Scope, architecture; persistence review |
| A15 | Direct runtime dependencies are `openai`, `pydantic`, `pypdf`; no OCR, embeddings, vector database or new framework. | Architecture; scope out-of-scope list |
| A16 | Do not presume that supplied contacts or source content may be published. Original data stays local. | Scope, gap analysis (personal data); `.gitignore` (`data/local/`) |

## Inspected source

- Located by name in the data directory named by the scope (`Desafio Técnico AI Engineer 2026/data/`). One file named `políticas_da_loja.pdf` exists there; a search of the candidate's `Downloads` folder found no other copy or version.
- Base name `políticas_da_loja.pdf`, 180,519 bytes, SHA-256 `b7527f96896929f4ba929de117651c6717e25b7a3b92b34bc54d5ba32f6156f7`. The hash matches the one recorded by the earlier static review (`docs/revisao-prompt-injection.md`).
- 8 physical pages, not encrypted. Metadata: title `políticas_da_loja.docx`, producer `Skia/PDF m150 Google Docs Renderer`.
- Inspection used `pypdf` 6.21.4 from the project environment, in a scratch script outside the repository. No content was executed, no link followed. CSV files were not read.

## Gap Analysis

### Observed facts about the PDF — [O]

| ID | Observation |
| --- | --- |
| O1 | The PDF has an outline (bookmarks) with 28 entries: 10 top-level numbered sections (`1.` to `10.`) and 18 subsections (`1.1` to `8.3`). Each entry title ends with one space. Each resolves to a physical page. |
| O2 | Every outline title, after whitespace compaction, equals exactly one extracted text line on its destination page, in outline order. |
| O3 | The body of section 7.2 contains numbered steps `1.` to `5.` (pages 6–7) that look like top-level headings. A heading regex on the text alone would split section 7.2 wrongly. |
| O4 | Default `extract_text()` returns one word per line (words separated by `" \n"`) and splits the phone in section 1.2 into `3341`, `-`, `4444` on separate lines. `extract_text(extraction_mode="layout")` returns visual lines, with the phone intact. |
| O5 | Layout mode renders tables as one line per row, columns separated by runs of spaces. A wrapped cell continues on the next line (section 3 table, page 3: the PIX observation ends on a separate line `tabela`). |
| O6 | Pages 2–8 start with the running header `Empório da Música Manual de Políticas e Procedimentos` and end with the footer `Página N`, where N equals the physical page. Page 1 is a cover page with neither. |
| O7 | Page 1 holds the cover: store name, slogan, address, phone `(67) 3341-4444`, e-mail `contato@emporiodamusica.com.br`, version 2.1 (June 2025) and the line `Documento interno — Uso restrito da equipe de atendimento`. No outline entry points to it. |
| O8 | Sections crossing pages: 5.2 (heading at the bottom of page 4, body on page 5), 7.2 (pages 6–7), 9 (pages 7–8). Sections 5, 6 and 8 have a heading and no own text before their first subsection. |
| O9 | Section 10 (page 8) ends with two trailer lines: the slogan and `Última atualização: Junho de 2025 — Versão 2.1`. |
| O10 | Non-ASCII characters in the extracted text: Portuguese letters with diacritics, `º`, `–` (en dash), `—` (em dash), `•` (bullet). Unicode NFC normalization changes nothing in the layout text. |
| O11 | Section sizes after cleaning: 143 to 777 characters of body per non-empty numbered section; about 9,700 characters for all numbered bodies. The three largest bodies (7.1, 5.2, 7.3) total about 2,200 characters. |
| O12 | Sections 7.1, 7.3 and 10 contain instructions addressed to staff and to the virtual assistant (tone, "never confirm availability without checking stock", "consult management"). The earlier static review found no prompt injection in this PDF. |
| O13 | The cover (page 1) and section 10 (page 8) state the manual is an internal document restricted to the customer-service team. |

### Ambiguities verified against the PDF

| ID | Fact in the PDF | Source decision | Service treatment |
| --- | --- | --- | --- |
| AM1 | Section 1.2 (page 2) lists `Telefone / WhatsApp` `(67) 3341-4444`; the cover (page 1) shows the same number; section 7 (page 6) names WhatsApp `(67) 3321-4500` as the main remote channel. | A5 | Notice `contact_numbers_differ`; companions 1.2 ↔ 7 for phone/WhatsApp queries. |
| AM2 | Cover (page 1): `contato@emporiodamusica.com.br`. Section 1.2 (page 2): `contato@empóriodamusica.com.br` (with `ó`). | A6 | Notice `email_spelling_differs`; companions 1.2 ↔ front matter for e-mail queries. Both spellings kept byte-for-byte. |
| AM3 | Section 3 table (page 3): credit card `Até 12x sem juros`, `Parcela mínima de R$ 100,00`. Section 3.1 (page 3): up to 3x with no minimum installment "(exceto abaixo de R$ 50,00)", 4x–6x minimum R$ 80,00, 7x–12x minimum R$ 100,00; combined payment above R$ 2.000,00. | A7 | Notice `installment_rules_differ`; companions 3 ↔ 3.1. |
| AM4 | Section 5.1 (page 4): free shipping for orders "acima de R$ 500,00"; fixed fee R$ 35,00 for orders "abaixo de R$ 500,00". Exactly R$ 500,00 is not covered. | A8 | Notice `free_shipping_threshold_gap` whenever 5.1 is returned. No amount parsing. |
| AM5 | Section 4.1 (page 4), "Direito de Arrependimento (Compras Online)": 7 calendar days after receipt of the product. | A9 | Notice `withdrawal_needs_channel_and_receipt_date` whenever 4.1 is returned. |
| AM6 | Section 2 (page 2): weekday and Saturday hours; `Domingo e Feriados` closed; hours "may be extended" on special dates. Section 7.1 (page 6): outside hours the assistant must state the return time. No holiday calendar anywhere. | A10 | Notice `no_holiday_calendar` when 2 or 7.1 is returned. |
| AM7 | Section 7.3 (page 7): register the complaint, forward to the person in charge, return within 24 business hours. Section 4.2 (page 4): the store may intermediate a warranty claim on request. Section 7 (page 6): human attendants or virtual assistant. Section 10 (page 8): consult management when in doubt. | A11 | Notice `no_handoff_integration` when 4.2, 7, 7.3 or 10 is returned. |

Additional observed differences not covered by an approved rule (open questions Q2 and Q3): section 4.2 counts 30 days "após a compra"; section 8.1 counts 90 days "a partir da data de recebimento"; section 4.3 states "7 dias" with no reference event and without "corridos".

### Decisions

#### P-01. Extraction mode and line cleaning

Extraction: `page.extract_text(extraction_mode="layout")` for every page, in physical order. Default mode is rejected because of O4.

Cleaning rules, applied in this order. They are the only editorial changes; each is a pure function tested on its own:

| Rule | Change |
| --- | --- |
| N1 | Split page text with `str.splitlines()`. Drop lines where `line.strip() == ""`. |
| N2 | Compact whitespace: `" ".join(line.split())`. Removes indentation and column padding (O5). |
| N3 | Unicode NFC. |
| N4 | Running header: a line that is the first line of two or more pages is removed from each page where it is the first line. A single-page PDF never loses a line through this rule. |
| N5 | Footer: the last line of page `p` is removed when it matches `^Página (\d+)$` and the number equals `p`. A different number, or the same text elsewhere on the page, is kept. |

Not changed: words, digits, currency, punctuation, dashes, bullets, quotes, case, line breaks inside paragraphs, hyphenation, table row order. No dehyphenation, reflow or table reconstruction.

Stated limits: table column boundaries become single spaces; a wrapped table cell appears as a separate following line (O5). Text produced by layout mode can change with another `pypdf` version; the index records the version (P-05).

#### P-02. Section identification

- The outline is the structure source. Text-only heading detection is rejected because of O3.
- Flatten the outline depth-first. For entry `k` (1-based, document order): normalize the title with N2 and N3; parse `^(\d+(?:\.\d+)*)\.?\s+(\S.*)$`. Group 1 is `section_id` (for example `4.1`, `10`), group 2 is `title`.
- Validation (any failure is `outline_invalid` with `outline_entry = k`): the title parses; `section_id` is unique; outline depth equals `section_id.count(".") + 1`; a nested entry's ID starts with its parent's ID followed by `.`; the destination resolves to a page of the document.
- Heading location: scan the cleaned line stream from a cursor that starts at the first line. The heading of entry `k` is the first line at or after the cursor, on the destination page, whose text equals the normalized title (`section_id` prefix included, as printed). The cursor moves past it. Not found: `outline_mismatch` with `outline_entry = k`.
- A section body is every cleaned line after its heading line and before the next heading line of any level, across pages.
- Front matter: lines before the first heading form one unit with `section_id = "front_matter"`, `level = 0`, `title = None`, no parent. It exists only when such lines exist. In the supplied PDF it is the cover (O7), needed for AM1 and AM2.
- Coverage invariant: every cleaned line belongs to exactly one unit, as a heading or as a body line.
- Sections with an empty body (5, 6 and 8 in the supplied PDF) stay in the index as ancestors. They are never returned by retrieval.
- A heading that wraps over two lines is not found and fails preparation. Fail closed; no fuzzy match.

#### P-03. Retrievable unit

One unit per outline entry with a non-empty body, plus the front matter. The unit text is its own body only, not its subsections. Subsections carry `parent_id` and `heading_path` (ancestor titles, then own title).

Alternative rejected: whole top-level sections. Section 4 with its four subsections fills most of page 4, so three such units would approach half the manual. Missing conditions in sibling subsections are handled by curated companions (P-10).

Section 10 keeps its trailer lines (O9). Removing them would need a rule specific to one document.

#### P-04. Failures of the source

Fail closed. No partial index is written (P-06).

| Situation | Code |
| --- | --- |
| Source path does not exist | `source_missing` |
| Path is a directory, permission error or other `OSError` on read | `source_unreadable` |
| File larger than `MAX_SOURCE_BYTES = 20 * 1024 * 1024` | `source_too_large` |
| `pypdf` cannot parse the file (empty file, wrong header, truncated) | `not_pdf` |
| `reader.is_encrypted` is true, whatever the password (an empty user password is decrypted automatically by `pypdf`; still rejected) | `encrypted` |
| More than `MAX_PAGES = 100` pages, or zero pages | `page_count_invalid` |
| An exception from `pypdf` while extracting page `p` | `extraction_failed` (page `p`) |
| A page with no line after N1–N5 (image-only page, blank page) | `page_text_missing` (first such page) |
| No outline, or an empty outline | `outline_missing` |
| Outline title invalid, duplicated, mis-nested or destination unresolved | `outline_invalid` (entry `k`) |
| Outline title not found on its destination page after the cursor | `outline_mismatch` (entry `k`) |
| No unit has a non-empty body | `no_retrievable_section` |

Checks run in table order and stop at the first failure: source checks, parsing, encryption, page count, extraction and text of every page, then validation of all outline entries, then heading location, then the retrievable-section check. Within one check, the lowest page or outline entry is reported.

Rejecting every page without text is deliberate: OCR is out of scope and a silent gap could drop a condition. The supplied PDF has text on all 8 pages.

#### P-05. Index format and provenance

Format: one UTF-8 JSON file, written by the standard library `json` module. Justification: the content is small (O11), `json` is already used in the project, the index stays readable for human review, and it needs no new infrastructure. SQLite was rejected: it would add a schema and migrations for 30 rows that are always read in full.

Encoding: `json.dumps(document, ensure_ascii=False, indent=2) + "\n"`, encoded UTF-8, written in binary mode so line endings are `\n` on every OS. Keys in the order shown. No timestamp, so the same PDF bytes and the same `pypdf` version produce byte-identical files (P-06).

```json
{
  "format": "emporio-policy-index",
  "format_version": 1,
  "extraction_rules": "layout-v1",
  "pypdf_version": "6.21.4",
  "source": {
    "file_name": "manual.pdf",
    "sha256": "<64 lowercase hex>",
    "size_bytes": 4096,
    "page_count": 4
  },
  "sections": [
    {
      "section_id": "front_matter",
      "level": 0,
      "title": null,
      "parent_id": null,
      "heading_page": null,
      "lines": [[1, "LOJA MODELO"], [1, "Rua das Flores, 100 — Centro"]]
    },
    {
      "section_id": "4.1",
      "level": 2,
      "title": "Direito de Arrependimento",
      "parent_id": "4",
      "heading_page": 3,
      "lines": [[3, "• Devolução em até 7 (sete) dias corridos após o recebimento,"], [4, "exceto itens da seção 4.4."]]
    }
  ]
}
```

- `file_name` is the base name of the source, never a directory.
- `extraction_rules` names the P-01/P-02 rule set. Changing those rules changes this value; changing the JSON layout changes `format_version`.
- `pages` of a section are derived, not stored: sorted unique of `heading_page` and the page of every line. For 5.2 in the supplied PDF: `(4, 5)`.
- The full manual text is in the index. Publication rules apply to it as to the PDF (P-14).

Validation on load (`load_policy_index`), all strict. Any failure is `index_invalid`, except where noted:

- file absent: `index_missing`; directory or `OSError`: `index_unreadable`; larger than `MAX_INDEX_BYTES = 5 * 1024 * 1024`: `index_invalid`;
- valid UTF-8 and JSON; top-level object with exactly the keys above; `format` equal to `emporio-policy-index`;
- `format_version` is the integer 1, else `index_version_unsupported` (checked right after `format`);
- `extraction_rules` and `pypdf_version` non-empty strings; `source.file_name` non-empty, without `/`, `\` or `:`; `sha256` 64 lowercase hex; `size_bytes` and `page_count` integers ≥ 1 (booleans rejected everywhere an integer is expected);
- each section has exactly the six keys; `section_id` is `front_matter` or matches `^\d+(\.\d+)*$`, unique; `front_matter` only first, with `level` 0, `title`, `parent_id` and `heading_page` null; numbered sections have `level = count(".") + 1`, a non-empty `title`, a `heading_page` in `1..page_count`, and a `parent_id` that is null at level 1 or the ID of an earlier section one level up whose ID prefixes this one;
- every line is a two-item array `[page, text]`: page in `1..page_count`, not lower than the previous line's page in the same section; text a non-empty string without `\n` or `\r`;
- at least one section has lines.

No staleness check against the PDF at load time: retrieval must not need the PDF. A new PDF requires explicit preparation (A14).

#### P-06. Preparation, repetition and atomic write

Same protocol as Slice 02, adapted to one file:

1. Validate the source and the target directory: the target's parent must exist (`target_directory_missing`; no directory is created). The target must not be the source (`target_is_source`, compared with `os.path.samefile` when both exist, and by resolved path otherwise).
2. Read the source once as bytes; compute SHA-256 and size on those bytes; parse from an in-memory stream. The source file is never opened for writing.
3. Build and validate the whole document in memory (P-01 to P-05), including the load validation of P-05 on the produced JSON.
4. Create a temporary file in the target directory with `tempfile.mkstemp(prefix=".<target name>.", suffix=".tmp")`, write, flush, `os.fsync`, close, then `os.replace` onto the target.
5. On any handled failure, delete the temporary file. Initial build: the target does not exist afterwards. Rebuild: the previous target is byte-identical afterwards.

Repetition: same input bytes and `pypdf` version give a byte-identical index. Every run rebuilds; there is no incremental update. Durability after an OS crash is not guaranteed (same limit as Slice 02).

#### P-07. Query validation

Same order and codes as `emporio_agent.services.catalog._check_text`, but `query` is required:

1. `type(query) is not str` → `invalid_type` (rejects `None`, `bytes`, `bool`, numbers, lists).
2. `len(query) > 4000` (code points, raw text) → `too_long`. Exactly 4,000 is accepted.
3. `normalize_search_text(query) == ""` → `blank` (empty or whitespace only).

A query that passes but contains no searchable term (only stopwords, punctuation or non-Latin script) is not an input error: it returns `not_found` with notice `no_searchable_terms`.

#### P-08. Normalization, tokens, stemming and stopwords

Applied identically to query and index text:

1. `normalize_search_text` from `emporio_agent.services.catalog`, reused unchanged: NFKD, drop combining marks, `casefold`, compact whitespace.
2. Tokens: `re.findall(r"[0-9a-z]+", text)`. Punctuation splits tokens: `e-mail` gives `e`, `mail`; `R$ 500,00` gives `r`, `500`, `00`; `3341-4444` gives `3341`, `4444`.
3. Stemming, first matching rule only, tokens made of digits unchanged:

| Rule | Suffix | Replacement | Condition | Example |
| --- | --- | --- | --- | --- |
| S1 | `coes` | `cao` | — | `devolucoes` → `devolucao` |
| S2 | `oes` | `ao` | — | `violoes` → `violao` |
| S3 | `aes` | `ao` | — | `paes` → `pao` |
| S4 | `ais` | `al` | length > 4 | `legais` → `legal` |
| S5 | `eis` | `el` | length > 4 | `papeis` → `papel` |
| S6 | `s` | (removed) | length > 3, not ending in `ss` | `trocas` → `troca`, `juros` → `juro` |

4. Stopwords, compared after stemming (the list is stemmed with the same rules): `a o as os e de da do das dos em no na nos nas um uma uns umas para pra por pelo pela com sem que qual quais como se ao aos ou eu meu minha voce voces vc vcs me te isso esse essa este esta tem ter ha sao ser foi posso pode podem quero queria gostaria saber sobre mais muito onde quando quanto quanta la aqui ai hoje ja nao sim ola oi bom dia boa tarde noite obrigado obrigada favor preciso r loja emporio musica cliente produto`.

The last six words appear across the whole manual and would match every section. `r` comes from `R$`. Removing `nao` and `sem` loses negation; acceptable because retrieval returns whole sections and never decides eligibility.

Limits: no spelling correction, no compound splitting, no phrase matching, no word order. The stemmer is a small rule set, not a Portuguese morphological analyzer: `arrependi` and `arrependimento` stay different tokens and are joined only through the synonym table.

#### P-09. Synonyms (concepts)

`concept_of(token)` maps every term produced by P-08, on both the query side and the index side, to a concept key:

- a token in the table below maps to its concept name; groups are disjoint after folding and stemming;
- a token matching `^\d{1,2}x$` (for example `10x`, `12x`) maps to `parcelamento`;
- any other token is its own concept key.

Terms are listed as written; the implementation folds and stems them with P-08.

| Concept | Terms |
| --- | --- |
| `endereco` | endereço, localização, localizada, localizado, fica, rua |
| `horario` | horário, funcionamento, expediente, abre, abrem, aberto, aberta, fecha, fecham, fechado, feriado, domingo, sábado |
| `pagamento` | pagamento, pagar, pago, forma |
| `pix` | pix |
| `parcelamento` | parcelamento, parcelar, parcela, parcelado, vezes, juros |
| `cartao` | cartão, crédito, débito, bandeira |
| `boleto` | boleto |
| `frete` | frete, entrega, entregar, envio, enviar, correios, sedex, pac, jadlog, transportadora, motoboy, chega, chegar |
| `rastreamento` | rastreamento, rastrear, rastreio |
| `troca` | troca, trocar, substituir, substituição |
| `devolucao` | devolução, devolver, arrependimento, arrepender, arrependi, desistir, desistência, reembolso, estorno |
| `defeito` | defeito, defeituoso, quebrado, quebrou, estragado |
| `garantia` | garantia, assistência, conserto, reparo |
| `telefone` | telefone, fone, ligar, ligação |
| `whatsapp` | whatsapp, zap |
| `email` | email, mail |
| `humano` | humano, atendente, pessoa, gerente, gerência |
| `reclamacao` | reclamação, reclamar, queixa |
| `promocao` | promoção, desconto, oferta, liquidação |
| `privacidade` | privacidade, lgpd, excluir, exclusão |

Examples with expected results on the hand-built index of the plan are in cases P40 and P59. Expected results on the supplied manual are in the plan, section "Real-manual validation".

#### P-10. Relevance, tie-break, limit and companions

Query concepts: `concept_of` of each query term, duplicates removed, first occurrence kept. For each retrievable unit `u` and each query concept `c`:

- weight 3 when `c` is a concept of a token of `u`'s own title;
- else weight 1 when `c` is a concept of a token of `u`'s body or of any ancestor title;
- else 0.

`coverage(u)` = number of concepts with weight > 0. `score(u)` = sum of weights.

Selection:

1. Candidates: units with `coverage ≥ 1`. None: `not_found`.
2. Keep only units whose coverage equals the highest coverage.
3. Keep only units with `2 × score ≥ top score`. A body-only mention (1) is dropped when another unit has the term in its title (3).
4. Order by score descending, then `level` descending (subsection before its parent), then document order.
5. Companions (P-11): walk the ordered list; after each unit insert its companions not yet present. A unit kept by step 3 has `selected_by = "match"`, even when it is first placed as a companion; any other inserted unit has `selected_by = "companion"`. Only retrievable units are inserted.
6. Return the first 3. The remaining IDs, in order, go to `omitted_section_ids` with notice `more_sections_matched`.

Rationale: a companion holds a condition or exception the matched section depends on (A7, A11, `4.4` exceptions); it outranks a weaker plain match. `omitted_section_ids` keeps the rest reachable through a refined query. Units are never added to fill three places.

#### P-11. Curated notes registry

Companions and divergence notices depend on the content of one reviewed manual. They are bound to its SHA-256 so that a different PDF never receives notices that may be wrong.

```python
@dataclass(frozen=True)
class CompanionRule:
    trigger_section_id: str
    companion_section_id: str
    only_for_concepts: frozenset[str]  # empty: always


@dataclass(frozen=True)
class NoticeRule:
    code: NoticeCode
    trigger_section_ids: frozenset[str]
    section_ids: tuple[str, ...]  # sections the notice refers to
    message: str  # fixed text written in this code base, never copied from the index


@dataclass(frozen=True)
class CuratedNotes:
    source_sha256: str
    companions: tuple[CompanionRule, ...]
    notices: tuple[NoticeRule, ...]
```

`REVIEWED_MANUAL_NOTES` is bound to `b7527f96896929f4ba929de117651c6717e25b7a3b92b34bc54d5ba32f6156f7`:

| Companion trigger | Companion | Only for concepts |
| --- | --- | --- |
| 3 | 3.1 | — |
| 3.1 | 3 | — |
| 4.2, 4.3 | 4.4 | — |
| 8.1, 8.2 | 8.3 | — |
| 1.2 | 7 | `telefone`, `whatsapp` |
| 7 | 1.2 | `telefone`, `whatsapp` |
| 1.2 | `front_matter` | `email` |
| `front_matter` | 1.2 | `email` |

| Notice code | Triggered when a returned section is | Refers to | Message (fixed English text) |
| --- | --- | --- | --- |
| `contact_numbers_differ` | `front_matter`, 1.2, 7 | `front_matter`, 1.2, 7 | Phone and WhatsApp numbers differ between sections. Approved rule: general phone from section 1.2, WhatsApp from section 7. Explain the origin if asked. |
| `email_spelling_differs` | `front_matter`, 1.2 | `front_matter`, 1.2 | The e-mail address is spelled differently on page 1 and in section 1.2. Do not correct it; report both origins if confirmation is requested and offer the documented phone or WhatsApp. |
| `installment_rules_differ` | 3, 3.1 | 3, 3.1 | Section 3 and section 3.1 differ on credit card installments. Approved rule: prefer the ranges of section 3.1; other conditions need staff confirmation. |
| `free_shipping_threshold_gap` | 5.1 | 5.1 | Section 5.1 defines orders above and below R$ 500,00 only. A total of exactly R$ 500,00 is not defined and needs staff confirmation. |
| `withdrawal_needs_channel_and_receipt_date` | 4.1 | 4.1 | Section 4.1 applies to online purchases and counts from actual receipt. Ask the purchase channel and the actual receipt date when missing. An estimated delivery date is not proof of receipt. |
| `no_holiday_calendar` | 2, 7.1 | 2 | The manual gives regular hours and closure on holidays, with no holiday calendar or exception dates. Do not state a specific opening or next business day for an uncertain date. |
| `no_handoff_integration` | 4.2, 7, 7.3, 10 | the triggering sections | This system cannot open tickets, register complaints or forward requests. Guide the customer to contact the store; never say a request was forwarded. |

Service notices, independent of the registry:

| Code | When |
| --- | --- |
| `more_sections_matched` | `omitted_section_ids` is not empty |
| `no_matching_section` | status `not_found` because no unit matched |
| `no_searchable_terms` | status `not_found` because the query had no term after P-08 |
| `curated_notes_unavailable` | the index `source.sha256` differs from the registry's; no companion or curated notice is applied |

Rules:

- When the hashes match, every section ID named by the registry must exist in the index, else `PolicyDataError("notes_mismatch")`.
- Notice order follows the two tables above, top to bottom. Each code appears at most once per result.
- Notices are deterministic metadata. They do not decide eligibility, compute fees or installments, or write the customer answer.

What stays for the future tool or agent: asking for the purchase channel and actual receipt date, choosing which number to give, phrasing the answer in Portuguese, saying that information was not found, and never claiming a handoff. No calculator for shipping, installments or eligibility is added anywhere (A12).

#### P-12. Errors and sanitization

```python
class PolicyPreparationError(Exception):
    code: str  # P-04 codes, plus target_directory_missing, target_is_source, write_failed
    file_name: str | None  # source or target base name
    page: int | None  # physical page, 1-based
    outline_entry: int | None  # 1-based position in the flattened outline
    # str(): "<file_name>: page <n>: <code>" with absent parts omitted; args == (str(self),)


class PolicyIndexUnavailableError(Exception):
    code: str  # index_missing, index_unreadable, index_invalid, index_version_unsupported
    file_name: str | None  # index base name


class PolicyInputError(ValueError):
    field: str  # "query"
    code: str  # invalid_type, too_long, blank


class PolicyDataError(Exception):
    reason: str  # notes_mismatch
```

Guarantee, as in Slice 02: `str()`, `repr()`, `args`, the documented attributes and `traceback.format_exception_only()` contain no directory path, no extracted PDF text, no outline title and no query text. Errors are raised `from None`. The full traceback and `__context__` are outside this guarantee. Outline problems are located by entry number and page, never by title, because titles are PDF content.

`pypdf` emits warnings through the `pypdf` logger for malformed files. This slice does not configure logging; the CLI slice must keep those records out of customer-facing output.

#### P-13. Security

- The PDF and the index are untrusted data. Text is stored and returned unchanged; it is never evaluated, formatted into instructions, used as a path, URL, SQL or tool name.
- Preparation reads one explicit path and writes one explicit target. No other file, no network, no subprocess, no environment variable.
- Retrieval reads only the in-memory `PolicyIndex`. No file, network or logging.
- Instructions inside the manual (O12) are policy content for the agent to weigh under its fixed instructions; the retriever does not act on them.

#### P-14. Publication of the PDF and the index

The manual is marked internal and restricted (O13), and the scope does not authorize redistribution (A16). Until the candidate decides (Q1):

- The PDF, any prepared index and any full section text stay out of Git. Proposed default index location: `data/local/policy-index.json`, already ignored by `.gitignore`.
- Automated tests use synthetic PDFs built at test time. No fixture file derived from the manual is committed.
- Public documents cite section IDs, pages and short values needed for the ambiguities only, as this document does.
- Committing example conversations that quote sections needs the same decision.

### Conflicts with higher sources

None found. Notes for the reviewer:

- A3 says "texto" for each section; this contract returns `text` as the stored body lines joined with `\n`, plus `title` and `heading_path`.
- The architecture proposes `preparation/pdf_extract.py` and `repositories/policy_index.py`; the code base places preparation and read access under `emporio_agent.data`. P-15 follows the code base.

#### P-15. Module placement

- `src/emporio_agent/data/policy_index.py`: extraction, cleaning, index format, atomic write, load and validation.
- `src/emporio_agent/services/policies.py`: query validation, normalization, concepts, ranking, curated notes.

## Service Contract

Synchronous. No network, logging, environment variables or global mutable state.

### Types

```python
type SectionId = str  # "front_matter" or "1", "4.1", ...
type NoticeCode = Literal[
    "contact_numbers_differ",
    "email_spelling_differs",
    "installment_rules_differ",
    "free_shipping_threshold_gap",
    "withdrawal_needs_channel_and_receipt_date",
    "no_holiday_calendar",
    "no_handoff_integration",
    "more_sections_matched",
    "no_matching_section",
    "no_searchable_terms",
    "curated_notes_unavailable",
]


@dataclass(frozen=True)
class PolicySource:
    file_name: str
    sha256: str
    size_bytes: int
    page_count: int
    format_version: int
    extraction_rules: str
    pypdf_version: str


@dataclass(frozen=True)
class IndexedSection:
    section_id: SectionId
    level: int
    title: str | None
    parent_id: SectionId | None
    heading_page: int | None
    lines: tuple[tuple[int, str], ...]

    @property
    def pages(self) -> tuple[int, ...]: ...


@dataclass(frozen=True)
class PolicyIndex:
    source: PolicySource
    sections: tuple[IndexedSection, ...]  # document order


@dataclass(frozen=True)
class PolicyPreparationResult:
    index_path: Path  # outside the sanitization guarantee
    source: PolicySource
    section_count: int  # numbered sections plus front matter
    retrievable_count: int
    index_sha256: str


@dataclass(frozen=True)
class PolicySection:
    section_id: SectionId
    title: str | None
    heading_path: tuple[str, ...]  # ancestor titles, then own title; () for front matter
    pages: tuple[int, ...]
    text: str  # "\n".join of the stored line texts, unchanged
    selected_by: Literal["match", "companion"]
    matched_terms: tuple[str, ...]  # folded, stemmed terms; () for a companion


@dataclass(frozen=True)
class PolicyNotice:
    code: NoticeCode
    section_ids: tuple[SectionId, ...]
    message: str


@dataclass(frozen=True)
class PolicySearchResult:
    status: Literal["ok", "not_found"]
    sections: tuple[PolicySection, ...]  # 0 to 3
    omitted_section_ids: tuple[SectionId, ...]
    notices: tuple[PolicyNotice, ...]
    source: PolicySource
```

### Functions

```python
MAX_SOURCE_BYTES: Final = 20 * 1024 * 1024
MAX_PAGES: Final = 100
MAX_INDEX_BYTES: Final = 5 * 1024 * 1024
INDEX_FORMAT: Final = "emporio-policy-index"
INDEX_FORMAT_VERSION: Final = 1
EXTRACTION_RULES: Final = "layout-v1"


# emporio_agent.data.policy_index
def clean_page_lines(raw_pages: Sequence[str]) -> tuple[tuple[str, ...], ...]: ...  # N1–N5
def extract_policy_index(source_pdf: Path) -> PolicyIndex: ...  # read only, no write
def prepare_policy_index(source_pdf: Path, index_path: Path) -> PolicyPreparationResult: ...
def load_policy_index(index_path: Path) -> PolicyIndex: ...


MAX_QUERY_LENGTH: Final = 4000
MAX_RESULTS: Final = 3


# emporio_agent.services.policies
def policy_terms(text: str) -> tuple[str, ...]: ...  # P-08; duplicates removed, first kept
def concept_of(term: str) -> str: ...  # P-09
def search_policies(
    index: PolicyIndex,
    query: object,
    *,
    notes: CuratedNotes = REVIEWED_MANUAL_NOTES,
) -> PolicySearchResult: ...
```

`search_policies` validates `query` before reading the index. It never mutates the index and returns equal results for equal inputs.

### Example on the supplied manual

Expected from human reading of the PDF; to be confirmed in Slice C, not by this document.

`search_policies(index, "O frete é grátis para pedido de R$ 500?")`: status `ok`; sections `5.1` (title `Entregas na Região Metropolitana de Campo Grande`, heading path `Política de Frete e Entregas` › own title, pages `(4,)`, all five body lines, including the R$ 500,00 and R$ 35,00 conditions); notices `free_shipping_threshold_gap`. No other section.

## Future tool boundary (not implemented here)

- Input: `query` JSON string, at most 4,000 characters, `extra="forbid"`.
- Output: `status`, `sections` (`section_id`, `title`, `heading_path`, `pages`, `text`, `selected_by`), `omitted_section_ids`, `notices` (`code`, `section_ids`, `message`), `source` (`file_name`, `sha256`, `page_count`).
- Status mapping: `PolicySearchResult.status` → `ok` or `not_found`; `PolicyInputError` → `invalid_input` (field and code only); `PolicyIndexUnavailableError` → `unavailable` (code only; the operator message tells to run preparation); `PolicyDataError` and any other exception → `internal_error` with no exception text.
- Load the index once per tool call with `load_policy_index`; the file is small (O11) and is never held open.
- Section text goes in data fields of the tool result, never in instructions.
- Configuration names reserved for the CLI slice: `EMPORIO_POLICY_PDF`, `EMPORIO_POLICY_INDEX`.

## Implementation Plan

See `docs/plans/04-policies.md`: Slice A (extraction, sections, provenance, index), Slice B (retrieval, synonyms, references, notices), Slice C (integrated validation against the real manual and limits). Each slice writes tests first, records RED, implements, records GREEN and stops for review.

## Test Checklist

Cases P01–P68 in the plan. Traceability:

| Requirement | Cases |
| --- | --- |
| RF05, A1, T09 | P02, P07, P51, P52, P59, P60, P64 |
| A2, A3 (whole sections, limit, synonyms, no match) | P40, P43–P51, P59, P64 |
| A4 (input, statuses) | P42, P48, P62, P68 |
| A5–A8, T10 | P50, P53, P64 |
| A9, T11 | P53, P64 |
| A10, A11, RF08 | P53, P64 |
| A13, T18 | P09, P57, P61, P64 |
| A14 (provenance, reproducibility) | P11–P13, P29, P63, P65 |
| RF09 (failures) | P14–P26, P30–P35, P55, P62 |
| RF10 (evidence, sanitization) | P11, P27, P34, P52, P68 |
| A16, P-14 (publication) | P66 |

## Test Strategy

Unit tests for cleaning rules, terms, validation, ranking and notices on hand-built `PolicyIndex` values. Integration tests on real PDFs generated with `pypdf` at test time, run through the real extraction, real file write, real load and real search. No mock of extraction, ranking or results. Expected values are written by hand in the plan. The supplied manual is checked by an opt-in evaluation script and human review, separate from the offline suite. Details in the plan.

## Compliance Report

- Spec vs sources: 16 approved rules cited; 15 decisions; 13 observed facts with physical pages. No conflict with a higher source.
- Observed vs proposed: [O] rows are facts from this inspection; [P] rows are proposals awaiting approval.
- Spec vs tests: 68 cases planned; none written or executed.
- Persistence impact: one new local JSON file at a path chosen by the caller. No database, schema or importer change.
- API impact: no HTTP endpoint.
- Earlier slices: `normalize_search_text` reused unchanged. No change to `data/`, `services/catalog.py`, `services/pricing.py` or their tests.
- Open questions: in the plan, section "Open questions" (Q1–Q8).
- Status: PROPOSED. Awaiting reviewer approval before tests and code.
