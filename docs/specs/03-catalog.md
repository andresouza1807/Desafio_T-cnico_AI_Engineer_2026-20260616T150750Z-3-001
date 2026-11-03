# Slice 03 specification: read-only catalog service

Status: proposed contract for review, 2026-11-02, revision R1 (answers review 03-01-R1: `invalid_row` coverage). Not implemented. No test in this document has been written or executed.
Baseline: branch `feature/data-preparation`, commit `1d994b1`, clean tree; last approved validation 757 passed, 2 skipped (Windows).

Sources, in precedence order:

1. `docs/escopo-projeto.md` v0.4: RF02, RF03, RF04, "Regras comerciais determinísticas", "Gap Analysis" (catalog conflicts), "Contratos propostos" (`search_products`, `get_product`, `quote_product`), T03–T08.
2. `docs/architecture-and-stack.md` and ADR `docs/decisions/0001-modular-synchronous-cli.md`.
3. `docs/specs/01-pricing.md` and the approved `src/emporio_agent/services/pricing.py`.
4. `docs/specs/02-data-preparation.md` (R1), task contracts `02-03` to `02-08`, `docs/validation/02-data-preparation.md` and its "Limits".
5. Current code: `src/emporio_agent/data/schema.py`, `preparation.py`, `readonly.py`, `parsing.py`.

All source paths named in the task brief exist. No equivalent source had to be substituted.

Test matrix and slices: `docs/plans/03-catalog.md` (cases C01–C76).

## Spec Summary

Add one synchronous, read-only catalog service in `emporio_agent.services.catalog`. It reads the prepared SQLite database through a connection opened by `connect_readonly` and prices products through `quote_price`. It exposes three domain functions:

- `get_product`: one product with availability, effective price, quality notes, a safe text projection and provenance.
- `search_products`: filter by name text, category, budget and availability; sort; paginate in pages of five.
- `quote_product`: one unit quote for `regular` or `pix`, with the non-cumulative rule made explicit.

The future agent tools of the same names will call these functions. The tools, their Pydantic schemas and their JSON serialization are not part of this slice. This document only fixes the mapping the tools must follow (section "Future tool boundary").

Out of scope: OpenAI, the agent loop, CLI, store policies, orders, authentication, HTTP/FastAPI, changes to the importer, the schema or the database file, and LLM-based disambiguation.

Every rule below is labelled:

- **[A]** approved rule, quoted or restated from a higher source (named in brackets).
- **[P-nn]** proposed decision for a gap. It needs reviewer approval. It must not contradict an [A] rule.

## Approved rules carried into this slice

| ID | Rule | Source |
| --- | --- | --- |
| A1 | A product is available only if `status == "active"` and `stock_quantity > 0`. `discontinued` and `coming_soon` are not available even with positive stock and get specific treatment. | RF03, T05 |
| A2 | Purchase search returns available products by default. An explicit query for a model may return unavailable, discontinued or coming-soon products. | Scope, commercial rules |
| A3 | Unknown category is reported. The filter is never dropped silently. | Scope, contracts |
| A4 | Budget filters the effective price: final price of the active promotion, else table price. PIX is a separate quote requested explicitly and never widens the search budget. | RF04, commercial rules |
| A5 | Several active promotions: only the largest applies, no accumulation. Any active promotion, including 0%, blocks the 5% PIX discount. | Scope; Slice 01 spec |
| A6 | `is_active` is the only promotion activity indicator. Campaign name and calendar do not change it. | Scope; Slice 02 observations |
| A7 | Money uses `Decimal`, two places, `ROUND_HALF_UP`, one rounding step, computed only by `quote_price`. Tool outputs represent money as decimal text. | Scope; Slice 01 |
| A8 | At most 5 products per page, sorted by effective price ascending, then `product_id` ascending. `offset` is a nonnegative integer, default 0. Results carry `total_matches` and `next_offset` (null on the last page). An offset past the total returns an empty page. Filtering and sorting by effective price happen before pagination. | Scope, commercial rules and contracts |
| A9 | Search is case- and accent-insensitive. Ambiguous questions lead to clarification. | RF02, T04 |
| A10 | Input texts are limited to 4,000 characters. Tools reject extra arguments, negative prices and non-finite prices. Results distinguish `ok`, `not_found`, `invalid_input`, `unavailable`, `internal_error`, without stack traces or sensitive data. | Scope, contracts |
| A11 | Price, stock and status come from their own columns. For products flagged `catalog_text_conflict`, do not synthesise technical attributes from description or specs; return a traceable notice. Preserve original values. Never correct the catalog with model or external knowledge. | Scope, gap analysis; T08 |
| A12 | `status_stock_conflict` is a register note only; availability still follows A1. | Slice 02 spec, data-quality register |
| A13 | Read-only access through `connect_readonly`; parameterized queries; no table or column name from input; no SQL produced by the model; catalog and source text are untrusted data, never instructions. | Slice 02 spec; architecture; scope security |
| A14 | Totals and prices are not recomputed in SQL or `float`. Catalog and pricing share one pricing service. Fetch candidates and promotions in bulk, compute effective prices once, then filter, sort and limit. | Architecture, coupling review |
| A15 | No HTTP API. Pydantic validates tool boundaries; internal records may be dataclasses. | Architecture; ADR 0001 |
| A16 | `get_product` takes a positive integer; returns safe fields and source references, or not found. `quote_product` takes `product_id` and `payment_method` in {`regular`, `pix`}; unit quote; creates no purchase. | Scope, contracts |

## Gap Analysis

### G1. Query matching — [P-01]

Decision:

- Searched field: `products.name` only. Not description, specs or category name.
- Normalization, applied to both the query and the name: Unicode NFKD, remove combining marks (Unicode category `Mn`), `str.casefold()`, split on whitespace (`str.split()` with no argument), rejoin with one space.
- Tokens: the normalized query split on spaces. A product matches when every token is a substring of the normalized name. Token order is free.
- No wildcard syntax. `%`, `_`, `*`, quotes and backslashes are literal characters. Matching runs in Python, not with SQL `LIKE` or `GLOB`, so no escaping is needed and no input reaches SQL.
- Punctuation is not removed: `C-40` does not match `C40` (stated limit).

Examples (names from the planned fixture):

| Query | Matches | Reason |
| --- | --- | --- |
| `violao`, `VIOLÃO`, `Violão` | every name containing `Violão` | accents and case folded |
| `  violão   alfa ` | `Violão Clássico Alfa` | outer and repeated spaces ignored |
| `alfa violão` | `Violão Clássico Alfa` | token order free |
| `alf` | `Violão Clássico Alfa` | substring per token |
| `violãoalfa` | none | tokens are not split further |
| `%` | names that contain `%` only | literal, not a wildcard |
| `fretless` (word only in a description) | none | description is not searched |

Justification: the scope defines the name as the cadastral identification and RF02 searches by "nome/modelo"; there is no model column, so the model is part of the name. Description and specs are the fields known to conflict (A11); matching on them would return products because of text the service must not trust. Python matching keeps one normalization rule for query and data and makes SQL wildcards impossible. Accent folding by NFKD + `Mn` removal covers Portuguese diacritics (`ã`, `ç`, `é`, `ô`).

### G2. Category resolution — [P-02]

Decision, applied only when `category` is not `None`:

1. Exact match: if one row has `categories.name == category` byte for byte, use it.
2. Else normalized match with the G1 normalization. Exactly one row: use it. More than one row: `ambiguous`. None: `unknown`.
3. `unknown` and `ambiguous` keep the filter: the page is empty, `total_matches = 0`, `next_offset = None`. The result lists candidate categories: all categories for `unknown`, the colliding ones for `ambiguous`, in `category_id` order.
4. All categories are candidates, including categories without products. A resolved category without products gives an empty page.

Examples: with `1 Violões`, `2 Teclas`, `3 Baixos`: `violões`, ` VIOLÕES `, `violoes` resolve to 1; `Ukuleles` is `unknown` with candidates 1, 2, 3. If the database also had `4 violoes` (allowed: Slice 02 enforces only exact uniqueness): `VIOLÕES` is `ambiguous` with candidates 1 and 4; `Violões` resolves to 1 and `violoes` resolves to 4 by exact match.

Justification: A3 forbids dropping the filter. Picking one colliding category or merging both would be a silent choice. The exact-match step gives the agent a way to select one colliding category by repeating its exact name from the candidates. Slice 02 left case-insensitive matching to this slice (decision "Category name").

### G3. Absent versus empty text — [P-03]

- `None` means "no filter".
- A string whose normalized form is empty (`""`, `"   "`, a string of combining marks only such as `"́"`) is rejected with code `blank`.
- Length is counted in code points of the raw string, before normalization. 4,000 is accepted; 4,001 is rejected with `too_long`. The length check runs before normalization, so normalization never runs on oversized input.
- Same rules for `query` and `category`.

Justification: an empty string usually signals a caller mistake. Accepting it as "no filter" would let the agent believe it filtered when it browsed the whole catalog. A10 sets the 4,000 limit but not the unit; code points is what `len(str)` measures and what a tool schema `maxLength` measures.

### G4. Budget representation — [P-04]

Domain type: `decimal.Decimal | None`. Rules, checked in this order:

| Input | Result | Code |
| --- | --- | --- |
| `None` | no budget | — |
| not exactly `Decimal` (`int`, `bool`, `float`, `str`) | rejected | `invalid_type` |
| `NaN`, `sNaN`, `Infinity`, `-Infinity` | rejected | `not_finite` |
| `< 0`, for example `Decimal("-0.01")` | rejected | `negative` |
| `> Decimal("92233720368547758.07")`, for example `Decimal("1E+30")` | rejected | `out_of_range` |
| not exactly representable in cents, for example `Decimal("999.999")` | rejected, never rounded | `excess_precision` |
| `Decimal("0")`, `Decimal("-0")`, `Decimal("0.00")` | accepted as 0.00 | — |
| `Decimal("1000")`, `Decimal("1000.000")` | accepted as 1000.00 | — |

The comparison is inclusive: a product is inside the budget when `effective_price <= max_price_brl`. The upper bound equals the largest money value Slice 02 can store (`SQLITE_MAX_INTEGER` cents). Quantization uses a private `decimal` context with enough precision, so a caller's global context (for example precision 3) cannot change the result. The echoed value in the result is the quantized, nonnegative value (`0.00`, never `-0.00`).

Justification: A10 rejects negative and non-finite prices. Rejecting sub-cent values matches the Slice 01 rule for table prices ("exactly representable in cents") and avoids a hidden rounding choice. `int` is rejected because `bool` is an `int` subclass and one exact domain type is simpler; the future tool accepts decimal text and converts it (section "Future tool boundary").

### G5. Other scalar arguments — [P-05]

| Argument | Accepted | Rejected (code) |
| --- | --- | --- |
| `product_id` | `type(x) is int` and `1 <= x <= 2**63 - 1` | `bool`, `float`, `str`, `Decimal`, `None` (`invalid_type`); `0`, negatives, `2**63` (`out_of_range`) |
| `available_only` | `type(x) is bool` | `0`, `1`, `"true"`, `None` (`invalid_type`) |
| `offset` | `type(x) is int` and `x >= 0`; no upper bound | `bool`, `float`, `str`, `None` (`invalid_type`); negatives (`out_of_range`) |
| `payment_method` | exactly `"regular"` or `"pix"` | non-`str` (`invalid_type`); other strings, including `"PIX"`, `""`, `"credit_3x"` (`unknown_value`) |

The `product_id` upper bound is the SQLite integer limit; a larger Python `int` would raise `OverflowError` when bound as a parameter. `offset` is never sent to SQL (pagination runs in Python), so it needs no upper bound; a huge offset returns an empty page. `payment_method` has no default and no case folding: PIX must be requested explicitly (A4) and the tool enum fixes the spelling.

Validation runs before any database access, in parameter order, and fails on the first invalid argument.

### G6. Missing versus unavailable product — [P-06]

- Missing `product_id`: `get_product` and `quote_product` return `None`. The tool maps this to `not_found`.
- Existing but not available: returned normally, with `availability` and `is_available = False`. Never `None`, never an error.
- `quote_product` on an unavailable product returns the quote with the availability fields. The quote states the registered price; it is not an offer to sell.

Justification: RF03 asks for specific messages for discontinued and coming-soon products, so the caller needs the state, not a refusal. A2 allows explicit model lookups of unavailable products. A16 says a quote creates no purchase.

### G7. Availability states — [P-07]

`availability_of(status, stock_quantity)`:

| status | stock | availability | is_available |
| --- | --- | --- | --- |
| `active` | > 0 | `available` | True |
| `active` | 0 | `out_of_stock` | False |
| `discontinued` | any | `discontinued` | False |
| `coming_soon` | any | `coming_soon` | False |

Any other status raises `CatalogDataError("invalid_row")` (the schema `CHECK` already prevents it; see G15). `status_stock_conflict` does not change the table (A12).

### G8. Responsibility for `available_only` — [P-08]

- The service applies `available_only` exactly as passed, echoes it in the result and never changes it, retries with another value or merges results.
- Default `True` (A2).
- The caller (future agent tool description and instructions) passes `False` only when the customer explicitly names a model or asks about unavailable, discontinued or coming-soon products. An empty purchase search is reported as empty; a second call with `False` is allowed only for such an explicit request, and its unavailable items are presented as not purchasable.
- With `False`, every item still carries `availability` and `is_available`.

### G9. Effective price and promotion shown — [P-09]

- For each product the service calls `quote_price(list_price, payment_method="regular", promotions=all_promotions_of_product)`. Inactive promotions are passed too; `quote_price` ignores them and still validates them, as approved.
- `effective_price = quote.final_price`. This equals the A4 definition: largest active promotion, else table price.
- Shown promotion: when `discount_source == "promotion"`, the active promotion whose percentage equals `quote.discount_percent`; on a tie, the lowest `promotion_id`. This only identifies a record; no price is recomputed. If no such record exists, the service raises `CatalogDataError("invalid_row")`.
- Inactive promotions are never returned. Their campaign names (for example "Black Friday") could be read as current offers (A6).

### G10. Products with `catalog_text_conflict` — [P-10]

Search summaries never carry `description` or `specs` for any product.

`get_product` detail for a product with the `catalog_text_conflict` note:

- `description = None`, `specs = None`, `withheld_fields = ("description", "specs")`.
- `name`, category, prices, stock, status, availability and notes are returned (own columns, A11).
- `quality_notes` contains `"catalog_text_conflict"`; provenance gives the products source file and its SHA-256.

The database keeps the original text (no write). The withheld fields are a projection rule, not a correction. The name is still the cadastral name; the caller must say that technical details need confirmation by the store team and must not infer attributes from the name either.

Products without that note return `description` (unchanged text) and `specs` (read-only mapping parsed from `specs_json`). Both remain untrusted data.

Justification: A11 forbids technical synthesis from description and specs for these products. Withholding is the only projection that makes synthesis from those fields impossible downstream. A boolean flag next to the full text would leave the decision to the model.

### G11. Source references available — [P-11]

Available in the current SQLite (schema version 1) and returned:

- `preparation_metadata.prepared_at` (UTC) through `read_preparation_info`.
- `source_files` rows for `categories`, `products` and `promotions`: base file name, SHA-256, size, row count (`SourceFileInfo`, reused).
- Record keys: `product_id`, `category_id`, `promotion_id`.
- Quality note codes from `data_quality_notes`. `catalog_text_conflict` comes from the curated constant `KNOWN_CATALOG_TEXT_CONFLICTS` (manual review of 2026-11-02, `schema.py`); `status_stock_conflict` is computed at preparation.

Not available and never invented: CSV line numbers, the original text of money cells (only cents are stored), per-field review notes, policy pages. No column is added to the database for provenance.

### G12. Database errors and leakage — [P-12]

| Situation | Raised by | Propagated as | Future tool status |
| --- | --- | --- | --- |
| File missing, not openable, wrong schema version, malformed metadata | `connect_readonly` / `read_preparation_info` | `DatabaseUnavailableError`, unchanged | `unavailable` |
| `sqlite3.Error` other than `ProgrammingError` during a catalog query (for example a missing `products` table with schema version 1) | catalog service | `CatalogDataError("query_failed")`, `from None` | `internal_error` |
| A row read by the call fails the minimum row validation of G15 (P-15) | catalog service | `CatalogDataError("invalid_row")`, `from None` | `internal_error` |
| `sqlite3.ProgrammingError` (closed connection, misuse) | sqlite3 | unchanged; caller defect | `internal_error` |
| Invalid argument | catalog service | `CatalogInputError` | `invalid_input` |

`CatalogInputError` and `CatalogDataError` carry codes only. `str()`, `repr()`, `args`, the documented attributes (`field` and `code`; `reason`) and `traceback.format_exception_only` never contain an argument value, a cell value, a directory path, SQL text or an original SQLite, `json` or `decimal` message. `CatalogDataError` is raised with `from None` (`__cause__` None, `__suppress_context__` True). The guarantee scope is the same as Slice 02 "Privacy" (full tracebacks and `__context__` excluded). After either error the caller's connection stays open and unchanged; the service never closes it.

### G13. Service and repository boundary — [P-13]

- One module, `emporio_agent/services/catalog.py`, holds types, validation, private query helpers and the three functions. No repository package, no `Protocol`, no generic persistence layer.
- SQL is a set of module constants with `?` placeholders. Only `product_id` and the constant `'product'` entity are ever bound. Searches read whole tables (65 products in the sample) and filter in Python, so the availability rule exists once, in `availability_of`, and not again in SQL.
- The architecture's `repositories/sqlite.py` is a responsibility name, not a requirement (architecture: "not a requirement to create an empty file or class for every name"). Split it out only when a second service (orders) needs shared queries.

Justification: one consumer, one small dataset, real SQLite in tests. An abstraction now would have a single implementation and no test benefit.

### G14. Cases left to the agent — [P-14]

The service returns facts and states. It does not interpret intent. The future agent must ask the customer when:

- a model query returns more than one product and the customer asked for one model;
- `category_resolution` is `unknown` or `ambiguous` (candidates are returned for that question);
- a purchase search is empty and the customer may accept other criteria;
- the payment method of a quote is not stated;
- a follow-up ("e no PIX?") has no single product in context (RF07);
- the customer asks technical details of a product with `catalog_text_conflict`;
- a budget phrase cannot be turned into one decimal value without guessing.

No LLM call, fuzzy ranking, synonym table or spelling correction is part of this service.

### G15. Minimum row validation — [P-15]

The prepared database already enforces types (`STRICT`), `CHECK` constraints, keys and references (Slice 02). The service still checks the values it uses, so a damaged or hand-edited file gives `invalid_row` instead of a wrong price, a wrong availability or an unrelated Python exception. This is a minimum check of used fields, not a full integrity check.

Validated explicitly, on every row the call reads:

| Table | Field | Rule | Check owner |
| --- | --- | --- | --- |
| products | `product_id`, `category_id` | `type(value) is int` | catalog |
| products | `name` | `type(value) is str` | catalog |
| products | `price_cents` | `type(value) is int` | catalog; negative values reach `quote_price`, which raises `ValueError` |
| products | `stock_quantity` | `type(value) is int` and `>= 0` | catalog |
| products | `status` | member of `PRODUCT_STATUSES` | catalog (`availability_of`) |
| products | `description` | `type(value) is str` | catalog, only when returned (`get_product` without `catalog_text_conflict`) |
| products | `specs_json` | `str`; `json.loads` succeeds; result is a `dict` | catalog, only when returned (same condition) |
| categories | `category_id`, `name` | `int`, `str` | catalog |
| promotions | `promotion_id`, `product_id`, `discount_basis_points` | `type(value) is int` | catalog |
| promotions | `discount_basis_points` range | after `basis_points_to_percent`, `quote_price` rejects values outside 0–100 | pricing (`ValueError`) |
| promotions | `is_active` | value is `0` or `1` (`int`), converted to `bool` | catalog; `bool(2)` is never used |
| promotions | `description` | `type(value) is str` | catalog |
| data_quality_notes | `entity_id`; `code` | `int`; member of `QUALITY_NOTE_CODES` | catalog |

Rules:

- "Rows the call reads": `get_product` and `quote_product` read the target product, its category, all its promotions (active and inactive, because `quote_price` validates both) and its notes. `search_products` reads every category, product, promotion and product note, and validates all of them before filtering. A malformed row of another product therefore fails a search but not `get_product` of a valid product.
- `search_products` does not read `description` or `specs_json`, so malformed text there never fails a search. `get_product` of a product with `catalog_text_conflict` does not parse `specs_json` (withheld, P-10).
- Any `ValueError` or `TypeError` from `cents_to_decimal`, `basis_points_to_percent`, `quote_price`, `json.loads` or the checks above becomes `CatalogDataError("invalid_row")` with `from None`. The pricing code is called, never reimplemented.
- JSON is parsed with the standard `json.loads` defaults. A non-object result (`[1, 2]`, `"text"`, `null`, `1`) is `invalid_row`.

Depends on the prepared database, not checked by the service:

- unique keys and references (a product whose category row is missing is not returned by the inner join);
- non-blank text, `created_at`, duplicate JSON keys, value types inside specs;
- `source_files` content beyond what `read_preparation_info` checks (Slice 02, T6);
- agreement between quality notes and the curated constant.

How tests create invalid rows: section "Invalid-row variants" of the plan builds them in real SQLite files before the read-only open. Nothing fakes SQL results or replaces `quote_price`.

### Conflicts with higher sources

None found. Notes for the reviewer:

- A10 lists rejected budgets (negative, non-finite). P-04 adds `excess_precision` and `out_of_range`; it rejects more, never accepts what A10 rejects.
- A10 states "Tools reject extra arguments". In the domain, keyword-only Python signatures raise `TypeError` for unknown keywords; the tool schema enforces `extra="forbid"`.
- Architecture lists `repositories/sqlite.py`; P-13 defers it with the architecture's own allowance.

## Service Contract

Module: `emporio_agent.services.catalog`. Synchronous. No network, logging, environment variables, file access other than the given connection, global mutable state or writes.

### Dependencies

- `sqlite3.Connection` passed by the caller, obtained from `emporio_agent.data.readonly.connect_readonly`.
- `emporio_agent.data.readonly.read_preparation_info` and `SourceFileInfo` for provenance.
- `emporio_agent.data.parsing.cents_to_decimal` and `basis_points_to_percent` for exact conversion.
- `emporio_agent.data.schema` constants: `PRODUCT_STATUSES`, `QUALITY_ENTITY_PRODUCT`, `STATUS_STOCK_CONFLICT`, `CATALOG_TEXT_CONFLICT`.
- `emporio_agent.services.pricing.quote_price` and `Promotion`.

No new third-party dependency.

### Connection lifecycle

- The caller owns the connection. The service never opens, closes or commits it, and never changes `connection.row_factory`; it uses its own cursors with `row_factory = None`, like `read_preparation_info`.
- Recommended use by the future tool layer: one `with connect_readonly(path) as connection:` per tool call. Do not hold a connection for a whole conversation: on Windows an open handle makes the preparation `os.replace` fail (Slice 02, D44).
- Each function reads everything it needs inside one call. The database is a local snapshot; no concurrent writer exists by design.

### Types

```python
type Availability = Literal["available", "out_of_stock", "discontinued", "coming_soon"]
type QualityNoteCode = Literal["catalog_text_conflict", "status_stock_conflict"]
type CategoryResolution = Literal["not_requested", "resolved", "unknown", "ambiguous"]
type PaymentMethod = Literal["regular", "pix"]


@dataclass(frozen=True)
class CategoryRef:
    category_id: int
    name: str


@dataclass(frozen=True)
class AppliedPromotion:
    promotion_id: int
    discount_percent: Decimal
    description: str  # campaign text, untrusted data


@dataclass(frozen=True)
class Provenance:
    prepared_at: datetime  # UTC
    sources: tuple[SourceFileInfo, ...]  # categories, products, promotions


@dataclass(frozen=True)
class ProductSummary:
    product_id: int
    name: str
    category: CategoryRef
    list_price: Decimal
    effective_price: Decimal
    promotion: AppliedPromotion | None
    stock_quantity: int
    status: str
    availability: Availability
    is_available: bool
    quality_notes: tuple[QualityNoteCode, ...]  # sorted


@dataclass(frozen=True)
class ProductDetail:
    summary: ProductSummary
    description: str | None  # None when withheld
    specs: Mapping[str, object] | None  # read-only top level; None when withheld
    withheld_fields: tuple[str, ...]  # () or ("description", "specs")
    provenance: Provenance


@dataclass(frozen=True)
class SearchResult:
    products: tuple[ProductSummary, ...]  # at most PAGE_SIZE
    total_matches: int
    offset: int
    next_offset: int | None
    available_only: bool
    max_price_brl: Decimal | None  # quantized echo
    category_resolution: CategoryResolution
    category: CategoryRef | None  # set when resolved
    candidate_categories: tuple[CategoryRef, ...]  # unknown or ambiguous only
    provenance: Provenance


@dataclass(frozen=True)
class ProductQuote:
    product: ProductSummary
    payment_method: PaymentMethod
    list_price: Decimal
    final_price: Decimal
    discount_percent: Decimal
    discount_source: Literal["none", "pix", "promotion"]
    promotion: AppliedPromotion | None
    pix_not_combined: bool  # pix requested and an active promotion applied
    provenance: Provenance
```

`ProductQuote.list_price`, `final_price`, `discount_percent` and `discount_source` are copied from the `PriceQuote` returned by `quote_price`. `pix_not_combined` is `payment_method == "pix" and discount_source == "promotion"`.

### Functions and errors

```python
PAGE_SIZE: Final = 5
MAX_TEXT_LENGTH: Final = 4000
MAX_PRICE_BRL: Final = Decimal("92233720368547758.07")


def availability_of(status: str, stock_quantity: int) -> Availability: ...


def normalize_search_text(text: str) -> str: ...


def get_product(connection: sqlite3.Connection, product_id: int) -> ProductDetail | None: ...


def search_products(
    connection: sqlite3.Connection,
    *,
    query: str | None = None,
    category: str | None = None,
    max_price_brl: Decimal | None = None,
    available_only: bool = True,
    offset: int = 0,
) -> SearchResult: ...


def quote_product(
    connection: sqlite3.Connection,
    product_id: int,
    *,
    payment_method: PaymentMethod,
) -> ProductQuote | None: ...


class CatalogInputError(ValueError):
    field: str  # argument name
    code: str  # invalid_type, out_of_range, too_long, blank,
    # not_finite, negative, excess_precision, unknown_value
    # str(): "<field>: <code>"; args == (str(self),)


class CatalogDataError(Exception):
    reason: str  # query_failed or invalid_row
    # str(): "<reason>"; args == (str(self),)
```

Algorithm of `search_products`:

1. Validate arguments (G3–G5).
2. Read provenance, categories, products with their category, all promotions and product quality notes, with fixed SQL.
3. Resolve the category (G2). If `unknown` or `ambiguous`, return an empty page.
4. For every product, compute availability (G7) and effective price (G9).
5. Keep products that pass all requested filters: `is_available` when `available_only`; resolved `category_id`; query tokens (G1); `effective_price <= max_price_brl`.
6. Sort by `(effective_price, product_id)`. `total_matches = len(matches)`.
7. Page = `matches[offset : offset + PAGE_SIZE]`. `next_offset = offset + PAGE_SIZE` if that is lower than `total_matches`, else `None`.

### Examples

Values come from the planned fixture in `docs/plans/03-catalog.md`; prices are calculated by hand.

`get_product(connection, 202)` — table price 1100.00, active promotion 301 of 10%:

```text
summary.product_id = 202, name = "Violão Folk Beta", category = (1, "Violões")
summary.list_price = 1100.00, effective_price = 990.00 (1100.00 × 0.90)
summary.promotion = (301, 10, "Promo Folk"), stock_quantity = 2, status = "active"
summary.availability = "available", is_available = True, quality_notes = ()
withheld_fields = (), provenance.sources = categories, products, promotions files
```

`get_product(connection, 135)` — flagged product: `description = None`, `specs = None`, `withheld_fields = ("description", "specs")`, `quality_notes = ("catalog_text_conflict",)`, `effective_price = 1350.00` (two active 10% promotions, not 1215.00).

`search_products(connection, category="violões", max_price_brl=Decimal("1000.00"))`:

```text
total_matches = 9, offset = 0, next_offset = 5, category_resolution = "resolved"
products = 211 (0.00), 214 (19.90), 212 (266.66), 210 (700.00), 201 (899.90)
excluded: 205 (1000.01), 203 (1050.00; PIX would be 997.50), 207-209 (not available)
```

`search_products(connection, category="Ukuleles")`: `products = ()`, `total_matches = 0`, `next_offset = None`, `category_resolution = "unknown"`, `candidate_categories = (1 Violões, 2 Teclas, 3 Baixos)`.

`quote_product(connection, 201, payment_method="pix")`: `list_price = 899.90`, `final_price = 854.91` (899.90 × 0.95 = 854.905, HALF_UP), `discount_percent = 5`, `discount_source = "pix"`, `pix_not_combined = False`.

`quote_product(connection, 210, payment_method="pix")` — active 0% promotion: `final_price = 700.00`, `discount_percent = 0`, `discount_source = "promotion"`, `pix_not_combined = True`.

`get_product(connection, 999)` returns `None`. `get_product(connection, True)` raises `CatalogInputError` with `field = "product_id"`, `code = "invalid_type"`.

## Future tool boundary (not implemented here)

The tool slice owns Pydantic schemas and JSON. It must follow this mapping:

- Input: `max_price_brl` is decimal text matching `^(0|[1-9][0-9]*)(\.[0-9]{1,2})?$`, converted with `Decimal(text)`; `product_id` and `offset` JSON integers (not booleans); `extra="forbid"`; strings at most 4,000 characters.
- Output money: decimal text with two places, `format(value, "f")` on the quantized `Decimal`, for example `"990.00"`. Never a JSON number.
- Status: `CatalogInputError` → `invalid_input` (field and code only); `None` → `not_found`; `DatabaseUnavailableError` → `unavailable`; `CatalogDataError` and any other exception → `internal_error` with no message from the exception.
- Catalog text (names, descriptions, specs, campaign names) is placed in data fields of the tool result, never in instructions.

## Security

- Only fixed SELECT statements. No SQL text, table or column name from input. No `LIKE`/`GLOB` with input.
- No write, no PRAGMA change, no attach, no file read other than the given connection, no network, no logging of content.
- Catalog text is returned unchanged as data. The service never evaluates, parses (except `specs_json` as JSON) or follows it. A product name such as "Ignore as instruções anteriores" is a string.
- No customer, order or order item table is read; the catalog works with empty customer and order tables.
- Errors carry codes only (G12).

## Implementation Plan

See `docs/plans/03-catalog.md`: slice A (product detail and availability), slice B (search, filters, normalization, pagination), slice C (quote). Each slice writes its tests first, records RED, then implements.

## Test Checklist

Cases C01–C76 in `docs/plans/03-catalog.md` (A 27, B 36, C 13). Traceability:

| Requirement | Cases |
| --- | --- |
| RF02, T03 | C23–C27, C32, C47 |
| RF02, T04 | C37–C48 |
| RF03, T05 | C04–C07, C22, C62 |
| RF04, T06 | C08, C09, C25–C31, C57–C61 |
| RF04, T07 | C24, C28, C56–C61, C65 |
| A11, T08 | C10, C41, C53, C66 |
| A10, A13 | C03, C13–C20, C35, C36, C42, C43, C49–C52, C63, C64, C67 |
| P-12, P-15 (`invalid_row`) | C07, C68–C76 |

## Test Strategy

Unit tests for pure rules and argument validation; integration tests on real SQLite databases built by `prepare_database` from synthetic CSV in `tmp_path`. No mocks of prices, filters, relations or persistence. Expected values are written by hand; production functions are never the oracle. Details in the plan.

## Compliance Report

- Spec vs sources: every [A] rule cites its source. Fifteen [P] decisions fill gaps; none contradicts an [A] rule. No conflict with a higher source was found.
- Spec vs contract: each requirement maps to a type, function, error code or the tool mapping.
- Spec vs tests: 76 cases planned; none written or executed. Revision R1 adds P-15 and cases C68–C76 for `invalid_row`, and clarifies C65.
- Persistence impact: none. No schema, importer or database change.
- API impact: no HTTP endpoint, no FastAPI.
- Slice 01 and 02 impact: none. `quote_price`, `connect_readonly`, `read_preparation_info` and parsing helpers are reused unchanged.
- Status: PROPOSED. Awaiting reviewer approval before tests and code.
