# Slice 01 specification: project environment and unit pricing

Status: executable contract for the first implementation slice, 2026-11-02.
Sources: scope v0.3, RF04, tests T06/T07, store policy sections 3 and 6.2; ADR 0001.

## Spec Summary

Prepare a reproducible Python 3.14 environment and implement deterministic unit-price calculation, independently of OpenAI, SQLite and the CLI.

Rules: only active promotions apply; active promotions do not accumulate; select the largest active percentage. When any promotion is active, PIX does not add its 5% reduction. Without an active promotion, PIX applies 5%. Regular payment without promotion uses the table price. Quantize final amounts to cents using ROUND_HALF_UP. Even an active 0% promotion prevents adding PIX, because the source defines non-cumulativity by active promotion, not by positive savings.

## Gap Analysis

- Multiple simultaneous promotions: use largest percentage, as already proposed in the scope.
- Domain input uses Decimal rather than float. A table price must be finite, nonnegative and exactly representable in cents; reject sub-cent source prices instead of silently changing source data.
- Promotion percentage must be a finite Decimal between 0 and 100 inclusive; inactive malformed promotions are rejected too, rather than hidden by filtering.
- Payment choices are exactly regular and pix. is_active must be a real bool.
- Return no user-facing prose in this domain object; explanation and references belong to the tool boundary.

## Service Contract

Module: emporio_agent.services.pricing.

Promotion: immutable record with discount_percent: Decimal and is_active: bool.

PriceQuote: immutable record with list_price: Decimal, final_price: Decimal, discount_percent: Decimal and discount_source: one of none, pix, promotion.

quote_price(list_price: Decimal, *, payment_method: str = 'regular', promotions: Sequence[Promotion] = ()) -> PriceQuote.

Raises ValueError for invalid domain input. No mutation of inputs, file access, database writes, logging, network requests or dependency on environment variables.

## Implementation Plan

1. Link the user-provided local directory to the existing remote and use a feature branch. Preserve README/LICENSE and local documents.
2. Record ADR and this contract before tests/code.
3. Declare runtime dependencies openai, pydantic 2 and pypdf; dev dependencies pytest and Ruff. Resolve exact compatible versions into uv.lock under Python 3.14.
4. Write pricing tests, execute RED, implement only the specified records/function, execute GREEN and static checks.
5. Record evidence and remaining MVP work without claiming a functioning agent.

## Test Checklist

- [ ] Regular table price is unchanged, with two decimal places.
- [ ] PIX without promotion: 599.90 becomes 569.91.
- [ ] Active 10% promotion: 599.90 becomes 539.91 for both payment choices.
- [ ] Inactive promotions do not affect regular or PIX prices.
- [ ] Largest active percentage wins without accumulation; inactive larger percentage is ignored.
- [ ] HALF_UP example: PIX on 19.90 yields 18.91.
- [ ] Zero-price product, 100% promotion and active 0% promotion behave as specified.
- [ ] Invalid prices, percentages, payment choices and activity types raise ValueError.
- [ ] Pricing does not mutate the caller's list of promotions.

## Test Strategy

pytest unit tests with explicit, independently calculated expected values. No mocks: this is pure domain behavior. Full data import, API integration, identity checks and LLM evaluations belong to later slices.

## Compliance Report

Contract and test scenarios precede implementation. Persistence impact: none in this slice. API impact: no HTTP endpoints. Record actual RED/GREEN and locked versions after execution; none are claimed by this specification itself.
