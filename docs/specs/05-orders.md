# Slice 05 specification: customer-scoped order lookup

Status: proposed contract for review, 2026-11-03, revision R0. Not implemented. No test in this document has been written or executed.
Baseline: branch `feature/data-preparation`, commit `43248ae`, clean tree (confirmed before editing); last approved validation 1176 passed, 2 skipped on Windows (from the task brief; not re-run for this document).

Sources, in precedence order:

1. `docs/escopo-projeto.md` v0.4: RF06–RF10, "Regras comerciais determinísticas", "Pedidos e identidade de demonstração", "Contratos propostos" (`get_order`), "Segurança e limites operacionais", T12–T14 and T18.
2. `docs/architecture-and-stack.md` and ADR `docs/decisions/0001-modular-synchronous-cli.md`.
3. `docs/specs/02-data-preparation.md` (R1) and task contracts `02-03` to `02-08` (schema, read-only connection, privacy).
4. `docs/specs/03-catalog.md` and the implemented `src/emporio_agent/services/catalog.py` (conventions reused, not called).
5. Current code: `src/emporio_agent/data/schema.py`, `readonly.py`, `parsing.py`, `errors.py`.

No private CSV file was read for this document. Every example uses invented data. Facts about the supplied data (row counts, empty-field counts) are quoted from the Slice 02 specification.

Test matrix and slices: `docs/plans/05-orders.md` (cases O01–O58).

## Spec Summary

Add one synchronous, read-only order service in `emporio_agent.services.orders`. It returns one order of the customer bound to the application session, with a minimal safe projection: order ID, status, registered items (current catalog name and quantity), historical total, tracking code, estimated delivery and a date comparison against an injected business date. Any case without access (no session, missing order, order of another customer) returns the same `None`, decided in SQL, without reading another customer's data.

The future agent tool `get_order`, its Pydantic schema and JSON serialization are not part of this slice. This document fixes only the mapping that tool must follow (section "Future tool boundary").

Out of scope: production authentication, the conversational session object, CLI commands, the agent loop, prompts and answer wording, OpenAI, HTTP, return eligibility, delay confirmation, changes to the schema, the importer, the catalog, the policies, existing tests, dependencies or earlier documents.

Rule labels follow Slices 03 and 04:

- **[A]** approved rule restated from a higher source (named in the table).
- **[F]** fact observed in the current code or environment during this task.
- **[P-nn]** proposed decision for a gap. Needs reviewer approval. Must not contradict an [A] rule.

## Approved rules carried into this slice

| ID | Rule | Source |
| --- | --- | --- |
| A1 | In a demonstration session bound to a customer, the order lookup returns status, products, quantities, registered total, tracking code and estimated delivery when available. | RF06 |
| A2 | Selecting a customer in the CLI is a local demonstration convention, not production authentication. Real authentication is a future extension. | Scope, "Pedidos e identidade" |
| A3 | The customer identifier lives in the application context. The model tool receives only `order_id`; it never accepts a `customer_id` from the LLM. | Scope, "Pedidos e identidade"; architecture, API boundary |
| A4 | The service restricts the lookup to `order_id` and the session customer. No session, a missing order and another customer's order reveal no data and do not confirm that another customer's order exists. | Scope; T13 |
| A5 | Only the needed order fields go to the model. Full customer record, phone and email stay out of the model context. | Scope; RF10 |
| A6 | The order total is historical: return the registered total, never recompute it with current catalog prices. | Commercial rules; T12 |
| A7 | Estimated delivery is a registered estimate. If the date has passed, say so without claiming a confirmed delay or inventing a new date. | Commercial rules; T14 |
| A8 | Never infer the actual receipt date from `estimated_delivery` or the order date. Return eligibility is guidance from policies, not a calculator. | Commercial rules; T11 |
| A9 | The clock is injectable for tests. Dates are real at execution; examples with a simulated date must state it. | Commercial rules |
| A10 | `get_order`: input `order_id` positive integer, customer from the session; output minimal order data of the session customer or a generic unavailability response; no name, phone or email. | Scope, contracts |
| A11 | Results distinguish `ok`, `not_found`, `invalid_input`, `unavailable`, `internal_error`, without stack traces or sensitive data. The order lookup uses `unavailable` for every no-access case. | Scope, contracts |
| A12 | Money uses `Decimal` with two places; tool outputs represent money as decimal text. | Commercial rules |
| A13 | Read-only access, parameterized queries, no SQL from the model, no table or column name from input. Source text is untrusted data, never instructions. | Scope security; architecture; Slice 02 |
| A14 | The order lookup includes both order ID and session customer ID in the data access boundary; the service emits only allowed fields; avoid N+1 loading of items. | Architecture, persistence review |
| A15 | Orders without items are allowed by preparation; no cross-field check between status, tracking code and dates is enforced. | Slice 02 R1, reviewer decision 02-01-R1 |
| A16 | No claim of prompt-injection immunity. Attempts to change the customer through text must not execute a forbidden action. | Scope security; T18 |

Facts from the current code:

| ID | Fact |
| --- | --- |
| F1 | `orders` columns: `order_id`, `customer_id`, `order_date`, `status`, `total_cents`, `payment_method`, `tracking_code`, `estimated_delivery`, `notes`. Source column `total_brl` is stored as integer cents in `total_cents` (`schema.py`). |
| F2 | `order_items` columns: `order_id`, `product_id`, `quantity`; primary key `(order_id, product_id)`; `quantity >= 1`. No unit price, subtotal or product name is stored per item. |
| F3 | `ORDER_STATUSES = {pending, confirmed, shipped, delivered, cancelled}`. `tracking_code`, `estimated_delivery` and `notes` are the only nullable order columns; non-null text is non-blank; dates are `YYYY-MM-DD` checked by `date(x) IS x`. |
| F4 | `connect_readonly` opens with `mode=ro`, sets `PRAGMA query_only = ON`, `row_factory = sqlite3.Row`, checks schema version 1 and raises `DatabaseUnavailableError` (`missing`, `open_failed`, `schema_mismatch`). |
| F5 | `read_preparation_info` returns `prepared_at` (UTC) and six `SourceFileInfo` records (base file name, SHA-256, size, row count). |
| F6 | `parsing.cents_to_decimal`, `parsing.parse_date` and `SQLITE_MAX_INTEGER` exist and are pure. |
| F7 | On this Windows environment `zoneinfo.ZoneInfo("America/Sao_Paulo")` raises `ZoneInfoNotFoundError`: no system time zone database and no `tzdata` package in `pyproject.toml` or `uv.lock`. |
| F8 | Slice 02 reports, for the supplied data: 20 orders, 22 order items, every order with at least one item, `tracking_code` and `estimated_delivery` empty on 12 rows, `notes` empty on 16 rows, and two orders whose total differs from current price × quantity. |

## Gap Analysis

### G1. Trusted customer context — [P-01]

```python
@dataclass(frozen=True, slots=True)
class CustomerContext:
    customer_id: int
```

- `__post_init__` accepts only `type(customer_id) is int` and `1 <= customer_id <= SQLITE_MAX_INTEGER`. Otherwise it raises `TypeError` or `ValueError` with a fixed message that never contains the value.
- Built only by application code (the future session, started by the operator's CLI choice). Never built from model arguments, tool results, catalog text or policy text.
- The service receives `CustomerContext | None`. `None` means "no customer session". Any other type raises `TypeError` (caller defect).
- The service does not check that the customer row exists and never reads the `customers` table. A context naming a customer without orders, or an unknown ID, simply matches no order.
- The type lives in `emporio_agent/services/orders.py` for now. The future `session.py` will hold an instance; moving it is a refactor, not a contract change.

Justification: A3 places identity in the application context. A dedicated type, rather than a bare `int` argument, makes it impossible to confuse the authorized customer with the requested order ID at a call site, and gives tests one object to construct. Not reading `customers` is the simplest way to guarantee that name, phone, email and city never leave the database (A5).

### G2. Service interface versus future tool — [P-02]

```python
def get_order(
    connection: sqlite3.Connection,
    order_id: int,
    *,
    customer: CustomerContext | None,
    clock: Callable[[], datetime],
) -> OrderView | None: ...
```

- `customer` and `clock` are keyword-only and required: no default identity, no hidden real clock in tests.
- The future tool `get_order` exposes exactly one argument, `order_id`. The tool layer passes `customer` from the session it holds and `clock` from bootstrap. Its schema has no `customer_id`, no `customer` and forbids extra properties (A3, A10).
- No other public function reads orders. There is no "find order by ID" helper without a customer filter.

### G3. Argument validation and order of checks — [P-03]

Order, fixed:

1. `order_id`: `type(x) is int` and `1 <= x <= SQLITE_MAX_INTEGER`. `bool`, `float`, `str`, `Decimal`, `None` → `OrderInputError("order_id", "invalid_type")`. `0`, negatives, `2**63` → `OrderInputError("order_id", "out_of_range")`.
2. `customer`: `None` → return `None`. Not `None` and not `CustomerContext` → `TypeError` (caller defect, not `invalid_input`).
3. Order query (G4). No row → return `None`.
4. `clock()` called exactly once; result must be a timezone-aware `datetime` (G9).
5. Items query (G6), row validation (G11), provenance (G10).

Steps 1 and 2 run before any database access. A closed connection never raises `ProgrammingError` for an invalid `order_id` or a missing session. Validating `order_id` before the session check lets the caller correct a malformed argument; the outcome depends only on the argument value, never on stored data, so it reveals nothing about orders. `invalid_input` carries the field and code only, never the value.

Same bounds and codes as `catalog.product_id` (Slice 03 P-05), so the tool layer maps both the same way.

### G4. Authorization in SQL — [P-04]

```sql
SELECT order_id, status, total_cents, tracking_code, estimated_delivery
FROM orders
WHERE order_id = ? AND customer_id = ?
```

- Both values are bound with `?`. Only `order_id` and `customer.customer_id` are ever bound.
- There is no query by `order_id` alone, followed by a Python ownership check. A missing order and another customer's order take the same code path: one query, zero rows, `None`.
- The service never selects `customer_id`, `order_date`, `payment_method` or `notes`. `customer_id` is read by SQLite only to evaluate the filter.
- The items query (G6) runs only after the order query returned a row, and repeats the customer filter through a join.

### G5. Generic no-access result — [P-05]

- No session, missing order and another customer's order all return `None`.
- `None` carries nothing: no owner, status, items, tracking code, estimate or existence hint.
- No exception is raised for these cases, so no message text or exception type can differ between them.
- Limit: the three cases do not run identical work (no session runs no query). This slice makes no claim about response time equality; see "Risks".

### G6. Items, quantities and product names — [P-06]

```sql
SELECT oi.product_id, oi.quantity, p.name
FROM order_items AS oi
JOIN orders AS o ON o.order_id = oi.order_id
LEFT JOIN products AS p ON p.product_id = oi.product_id
WHERE oi.order_id = ? AND o.customer_id = ?
ORDER BY oi.product_id
```

- One query for all items (A14, no N+1).
- Items sorted by `product_id` ascending. The schema has no line number, so source order cannot be preserved; `product_id` is unique inside an order (F2), so the order is total and deterministic.
- `LEFT JOIN`: a missing product row gives `name = NULL`, which fails row validation (`invalid_row`) instead of dropping the item silently. Preparation enforces the reference, so this path only appears on a damaged file.
- `quantity` is returned exactly as stored.
- An order without items (allowed, A15) returns `items = ()`. The service never infers products from the total or from other tables.
- The product name comes from the current `products` row. The schema has no name snapshot per order (F2). Every item carries the constant `name_source = "current_catalog"` to make that explicit. The caller must not present it as the name printed at purchase time.
- The service reads only `products.name`. No description, specs, price, stock, status, promotion or quality note is read for orders, and catalog functions such as `get_product` are not called.

### G7. Historical total and absent historical values — [P-07]

- `total = cents_to_decimal(total_cents)`, two places, from `orders.total_cents` only.
- No recomputation from current prices, promotions or PIX. The service never reads `products.price_cents` or `promotions`.
- No unit price and no subtotal per item: the schema stores none (F2). The projection has no field for them, so nothing can be invented downstream.
- The total is returned even when `items == ()`.

### G8. Free text: `notes`, tracking code, product names — [P-08]

- `orders.notes` is neither selected nor returned. It is free text, absent from RF06, may contain personal data (addresses, phone numbers, names) and the service cannot classify it safely. The Slice 02 data shows notes on 4 of 20 orders (F8); their content was not read for this document.
- `tracking_code` is returned as stored (RF06 requires it). It is an untrusted string: never parsed, never followed as a URL or an instruction.
- Product names are returned as stored. Untrusted strings, like catalog text (Slice 03).
- Size: the schema sets no maximum length for these columns and no item count limit. The scope sets 4,000 characters only for input texts. This slice invents no output limit and never truncates. The need for an output size bound on tool results (context cost, injection surface) is recorded for the tool slice (see "Open questions" Q3).

### G9. Dates, business time zone and the estimate comparison — [P-09]

- `clock` returns the current instant. A naive `datetime` raises `ValueError("clock must return a timezone-aware datetime")`, the same rule and text as `prepare_database`. Any other type raises `TypeError`.
- Business date: `clock().astimezone(BUSINESS_TIMEZONE).date()`.
- `BUSINESS_TIMEZONE: Final = timezone(timedelta(hours=-3), "UTC-03:00")`, a fixed offset.
  - Reason: the store is in Brazil and the scope plans with `America/Sao_Paulo`. Brazil has had no daylight saving time since 2019, so the current offset of that zone is constantly UTC−03:00.
  - `ZoneInfo("America/Sao_Paulo")` is not usable on this Windows environment without adding `tzdata` (F7); dependency changes are out of scope.
  - Limit: if Brazil reintroduces daylight saving time, the fixed offset becomes wrong for those periods. Recorded in "Risks".
- `estimated_delivery` is parsed with `parsing.parse_date` and returned as `datetime.date`, or `None`.
- `estimate_status`, a pure comparison:

| `estimated_delivery` | Compared with business date | `estimate_status` |
| --- | --- | --- |
| `None` | — | `not_registered` |
| earlier | `<` | `passed` |
| equal | `==` | `due_today` |
| later | `>` | `upcoming` |

- `passed` means only "the registered estimate is before the business date". It is computed for every status, including `delivered` and `cancelled`. It is not a delay, not a confirmation that the order was not received, and never produces a new date (A7).
- No receipt date field exists. `delivered` and `cancelled` are status values only; the service creates no date from them (A8).
- No return eligibility, warranty window or deadline is computed (A8).
- `business_date` is echoed in the result, so an answer can state which date the comparison used (A9).
- `order_date` is not returned (G12).

### G10. Source references — [P-10]

Available without schema change and returned:

- `prepared_at` (UTC) from `read_preparation_info`.
- `SourceFileInfo` for `orders`, `order_items` and `products`, in that order: base file name, SHA-256, size, row count. Never `customers`.
- Record keys: `order_id` and each item's `product_id`.

Not available and never invented: CSV line numbers, original money text, the product name at purchase time, the source of `estimated_delivery`.

### G11. Minimum row validation and data errors — [P-11]

The prepared database already enforces types, `CHECK` constraints and references (Slice 02). The service still checks every value it uses, so a damaged or hand-edited file gives `invalid_row` instead of a wrong answer or an unrelated Python exception. Same principle as Slice 03 G15.

| Table | Field | Rule |
| --- | --- | --- |
| orders | `order_id` | `type is int` and equal to the requested ID |
| orders | `status` | member of `ORDER_STATUSES` |
| orders | `total_cents` | `type is int`, `>= 0` (then `cents_to_decimal`) |
| orders | `tracking_code` | `None`, or `type is str` with non-blank `strip()` |
| orders | `estimated_delivery` | `None`, or `type is str` accepted by `parse_date` |
| order_items | `product_id` | `type is int`, `>= 1` |
| order_items | `quantity` | `type is int`, `>= 1` |
| products | `name` (joined) | `type is str` with non-blank `strip()`; `NULL` means missing product |

- Only rows of the authorized order are read, so a damaged row of another customer's order cannot fail this customer's lookup, and cannot be revealed through an error.
- Any check failure, `FieldError`, `TypeError` or `ValueError` raised by the helpers becomes `OrderDataError("invalid_row")` with `from None`.
- Not checked by the service (preparation guarantees): `customer_id` reference, `order_date`, `payment_method`, `notes`, uniqueness of `(order_id, product_id)`, cross-field consistency (A15).

### G12. Allowed and excluded fields — [P-12]

Allowed (returned):

| Field | Source | Why |
| --- | --- | --- |
| `order_id` | `orders.order_id` | identifies the order in the answer |
| `status` | `orders.status` | RF06 |
| `items[].product_id` | `order_items.product_id` | stable reference for the item |
| `items[].name` | `products.name`, current | RF06 "products"; labelled `current_catalog` |
| `items[].quantity` | `order_items.quantity` | RF06 |
| `total` | `orders.total_cents` | RF06, historical |
| `tracking_code` | `orders.tracking_code` | RF06, when available |
| `estimated_delivery` | `orders.estimated_delivery` | RF06, when available |
| `estimate_status` | computed (G9) | A7 |
| `business_date` | injected clock (G9) | A9 traceability |
| `provenance` | metadata (G10) | RF10 references |

Excluded (never selected, never returned):

| Field | Reason |
| --- | --- |
| `orders.customer_id` | authorization input only; not needed in the answer (A3) |
| `customers.*` (name, phone, email, city) | A5; table never read |
| `orders.notes` | free text, possible personal data, not in RF06 (G8) |
| `orders.payment_method` | not in RF06 |
| `orders.order_date` | not in RF06; A8 forbids inferring receipt from it (Q1) |
| product description, specs, price, stock, status, promotions, quality notes | not part of an order answer; current values would be read as historical |

### G13. Errors and future status mapping — [P-13]

| Situation | Raised or returned by | Future tool status | Public payload |
| --- | --- | --- | --- |
| order found for the session customer | service, `OrderView` | `ok` | projection (G12) |
| invalid `order_id` | service, `OrderInputError(field, code)` | `invalid_input` | field and code only |
| no session, missing order, other customer's order | service, `None` | `unavailable` | fixed reason `order_not_accessible` |
| database missing, not openable, wrong schema, malformed metadata | `connect_readonly` or `read_preparation_info`, `DatabaseUnavailableError` | `unavailable` | fixed reason `data_unavailable` |
| `sqlite3.Error` other than `ProgrammingError` during an order query | service, `OrderDataError("query_failed")`, `from None` | `internal_error` | no detail |
| row validation failure (G11) | service, `OrderDataError("invalid_row")`, `from None` | `internal_error` | no detail |
| `sqlite3.ProgrammingError`, `TypeError` from a wrong `customer` type, invalid clock | unchanged (caller defect) | `internal_error` | no detail |

- `not_found` is never used for orders (A11).
- The two `unavailable` reasons differ by cause, but neither depends on who owns `order_id`: `data_unavailable` happens for every order ID, `order_not_accessible` is identical for missing and foreign orders. They let the agent say "try later" versus "not available in this session" without confirming another customer's order. Alternative, if the reviewer prefers: one reason for both.
- `OrderInputError` and `OrderDataError` carry codes only. `str()`, `repr()`, `args`, the documented attributes and `traceback.format_exception_only` never contain an argument value, a customer ID, a cell value, a directory path, SQL text or an original SQLite message. Same guarantee scope as Slice 02 "Privacy" (full traceback and `__context__` excluded).
- After any error the caller's connection stays open and unchanged.

### G14. Connection lifecycle and no write — [P-14]

- The caller owns the connection, obtained from `connect_readonly`. The service never opens, closes or commits it and never changes `connection.row_factory`; it uses its own cursors with `row_factory = None`, like the catalog.
- Recommended tool use: one `with connect_readonly(path) as connection:` per tool call, never held for a whole conversation (Slice 02 D44, Windows replacement).
- Only fixed `SELECT` statements. No `PRAGMA` change, no `ATTACH`, no temporary table, no write. `query_only` (F4) is a second barrier, not the only one.
- Stateless: no cache, no module-level mutable state, no memory of a previous customer or order between calls.

### G15. Cases left to the agent — [P-15]

The service returns facts. The future agent instructions, not this service, must:

- ask for the order number when the customer does not give one;
- tell an anonymous session that order lookup needs a demonstration customer session started by the operator, without suggesting that a message can choose the customer;
- treat `order_not_accessible` without saying whether the order exists;
- present `passed` as "the registered estimate was <date>", without saying "late" or giving a new date;
- ask for the purchase channel and the actual receipt date for return questions (A8, T11);
- present item names as current catalog names.

### Conflicts with higher sources

None found. Notes for the reviewer:

- The scope names `orders.total_brl`; the database stores the same value as `orders.total_cents` (F1). The rule "return the registered total, never recompute" is kept; only the column name differs.
- The scope lists `not_found` among result statuses; A11 already reserves `unavailable` for no-access order cases. This slice never returns `not_found`.
- The scope plans with `America/Sao_Paulo`; P-09 uses a fixed UTC−03:00 offset because of F7. It is the same offset today; the difference matters only if daylight saving time returns.
- The architecture lists `repositories/sqlite.py`. As in Slice 03 P-13, no repository module is created: two fixed statements in the service module do not justify a shared layer yet.

## Service Contract

Module: `emporio_agent.services.orders`. Synchronous. No network, logging, environment variables, file access other than the given connection, global mutable state or writes.

### Dependencies

- `sqlite3.Connection` from `emporio_agent.data.readonly.connect_readonly`.
- `emporio_agent.data.readonly.read_preparation_info`; `emporio_agent.data.preparation.SourceFileInfo`.
- `emporio_agent.data.parsing`: `SQLITE_MAX_INTEGER`, `cents_to_decimal`, `parse_date`.
- `emporio_agent.data.schema.ORDER_STATUSES`.
- Standard library only. No dependency on `services.catalog` or `services.pricing`. No new third-party dependency.

### Types

```python
type OrderStatus = Literal["pending", "confirmed", "shipped", "delivered", "cancelled"]
type EstimateStatus = Literal["not_registered", "passed", "due_today", "upcoming"]

BUSINESS_TIMEZONE: Final = timezone(timedelta(hours=-3), "UTC-03:00")


@dataclass(frozen=True, slots=True)
class CustomerContext:
    customer_id: int  # validated in __post_init__


@dataclass(frozen=True)
class OrderItem:
    product_id: int
    name: str  # current catalog name, untrusted data
    quantity: int
    name_source: Literal["current_catalog"] = "current_catalog"


@dataclass(frozen=True)
class OrderProvenance:
    prepared_at: datetime  # UTC
    sources: tuple[SourceFileInfo, ...]  # orders, order_items, products


@dataclass(frozen=True)
class OrderView:
    order_id: int
    status: OrderStatus
    items: tuple[OrderItem, ...]  # sorted by product_id; may be empty
    total: Decimal  # historical, from total_cents
    tracking_code: str | None  # untrusted data
    estimated_delivery: date | None
    estimate_status: EstimateStatus
    business_date: date
    provenance: OrderProvenance


class OrderInputError(ValueError):
    field: str  # "order_id"
    code: str  # invalid_type, out_of_range
    # str(): "<field>: <code>"; args == (str(self),)


class OrderDataError(Exception):
    reason: str  # query_failed, invalid_row
    # str(): "<reason>"; args == (str(self),)


def get_order(
    connection: sqlite3.Connection,
    order_id: int,
    *,
    customer: CustomerContext | None,
    clock: Callable[[], datetime],
) -> OrderView | None: ...
```

`OrderView` has no field for customer data, notes, payment method, order date, unit price or subtotal.

### Examples

Invented data from the planned fixture in `docs/plans/05-orders.md`. Clock `2026-11-03T15:00:00+00:00`, business date `2026-11-03`.

`get_order(connection, 5001, customer=CustomerContext(1), clock=fixed)`:

```text
order_id = 5001, status = "shipped"
items = (10, "Violão Teste Alfa", 2, current_catalog), (11, "Teclado Teste Beta", 1, current_catalog)
total = 1500.00   (stored; current table prices give 2200.00, current effective prices 2100.00)
tracking_code = "TRK-5001", estimated_delivery = 2026-11-03, estimate_status = "due_today"
business_date = 2026-11-03, provenance.sources = orders, order_items, products
```

`get_order(connection, 5002, customer=CustomerContext(1), clock=fixed)`: `items = ()`, `total = 250.00`, `tracking_code = None`, `estimated_delivery = None`, `estimate_status = "not_registered"`.

`get_order(connection, 6001, customer=CustomerContext(1), clock=fixed)`, `get_order(connection, 9999, customer=CustomerContext(1), clock=fixed)` and `get_order(connection, 5001, customer=None, clock=fixed)`: `None`, all three.

`get_order(connection, True, customer=CustomerContext(1), clock=fixed)`: `OrderInputError`, `field = "order_id"`, `code = "invalid_type"`.

## Future tool boundary (not implemented here)

The tool slice owns Pydantic schemas and JSON. It must follow this mapping:

- Input schema: exactly `{"order_id": integer}`, `extra="forbid"`, JSON integer only (no boolean, no string, no float). No `customer_id` property under any name.
- `customer` comes from the session object held by the application. `clock` comes from bootstrap and returns `datetime.now(UTC)`.
- Recommended order inside the tool: validate `order_id`, return `unavailable` / `order_not_accessible` when the session has no customer, then open `connect_readonly` and call the service.
- Output: money as decimal text with two places (`format(total, "f")`); dates as ISO `YYYY-MM-DD`; `estimate_status` and `name_source` as given; tracking code and names in data fields, never in instructions.
- Status mapping: section G13. No exception message reaches the model.
- Size: no truncation. A bound for tool result size is an open question for the tool slice (Q3).
- The tool description must state that `passed` is a date comparison, not a confirmed delay, and that item names are current catalog names.

## Security

- Authorization is part of the SQL filter, not a Python check after reading.
- Only fixed `SELECT` statements with `?` parameters. Bound values: `order_id` and `customer.customer_id` only.
- Never read: `customers`, `orders.notes`, `orders.payment_method`, `orders.order_date`, product text other than `name`, prices, promotions.
- Text from the database is returned unchanged as data and never interpreted.
- Errors carry codes only (G13).
- Not claimed: production authentication, protection against an operator who chooses another customer, equal response time across no-access cases, prompt-injection immunity of the future model.

## Risks

| Risk | Treatment in this slice | Residual |
| --- | --- | --- |
| Model asks for another customer's order by text | Tool has no identity argument; SQL filter | Model behavior is tested only in the evaluation stage |
| Missing versus foreign order distinguishable | Same `None`, same query path | Timing not measured or claimed |
| Notes leak personal data | Column never selected | Useful delivery notes are not shown |
| Current product name presented as historical | `name_source = "current_catalog"` | Depends on agent wording |
| `passed` estimate read as a confirmed delay | Neutral code, rule A7 in tool description | Depends on agent wording |
| Fixed UTC−03:00 offset | Documented; injectable clock | Wrong if Brazil reintroduces daylight saving time |
| Large tracking code, name or item count | Returned in full, no truncation | Output bound pending (Q3) |
| Demonstration identity treated as real authentication | Stated as convention (A2) | README must repeat it |

## Open questions for the reviewer

- Q1. Return `order_date`? Excluded now (not in RF06, A8). Including it later is additive.
- Q2. Keep two `unavailable` reasons (`order_not_accessible`, `data_unavailable`) or merge them?
- Q3. Output size bound for tool results (names, tracking code, item count): decide in the tool slice; this slice neither invents nor applies one.
- Q4. Business time zone: accept the fixed UTC−03:00 offset, or add `tzdata` in a separate dependency change and use `ZoneInfo("America/Sao_Paulo")`.
- Q5. Return the `catalog_text_conflict` note per item? Excluded now: the order view shows only the cadastral name, never technical attributes.

## Implementation Plan

See `docs/plans/05-orders.md`: slice A (context, validation, authorized order query, header projection and dates), slice B (items, current names, historical total, provenance), slice C (integrated isolation, data errors, no write, leakage). Each slice writes its tests first, records RED, then implements, and stops for review.

## Test Checklist

Cases O01–O58 in `docs/plans/05-orders.md`. Traceability:

| Requirement | Cases |
| --- | --- |
| RF06, T12 (allowed fields, historical total, items) | O01, O16, O17, O24–O30, O36, O38 |
| RF06, T13 (no access, no existence leak) | O02–O07, O37, O39, O41–O43, O53 |
| T13, T18 (identity cannot change through arguments or text) | O08–O10, O34, O35, O44 |
| T14 (absent tracking/estimate, passed estimate, no new date) | O18, O19, O21–O23 |
| A8 (no receipt date, no eligibility) | O20 |
| A5, P-08, P-12 (sentinels, notes, customer data, columns read) | O31–O33, O40, O45 |
| P-03 (validation before database, check order) | O11–O15 |
| P-11, P-13 (data errors, leakage) | O46–O55 |
| P-14 (connection lifecycle, no write) | O56–O58 |

## Test Strategy

Unit tests for `CustomerContext`, argument validation, the business date and the estimate comparison. Integration tests on real SQLite databases built by `prepare_database` from synthetic CSV in `tmp_path`, opened with `connect_readonly`. No mock of authorization, SQL, relations or persistence. A real SQLite authorizer observes the statements and columns actually read, without denying them. Expected values are written by hand or read with independent SQL; production functions are never the oracle. Details in the plan.

These tests do not prove real authentication, the future tool schema, or the behavior of the future model.

## Compliance Report

- Spec vs sources: sixteen [A] rules cite their source; eight [F] facts come from the code and environment. Fifteen [P] decisions fill gaps; none contradicts an [A] rule. No conflict with a higher source; four naming or scope notes recorded.
- Spec vs contract: each requirement maps to a type, field, function, error code or the tool mapping.
- Spec vs tests: 58 cases planned; none written or executed.
- Persistence impact: none. No schema, importer, catalog, policy or database change.
- API impact: no HTTP endpoint. The future tool exposes `order_id` only.
- Status: PROPOSED. Awaiting reviewer approval before tests and code.
