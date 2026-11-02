"""Unit pricing from store payment and non-cumulative promotion policies."""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Literal

CENT = Decimal("0.01")
HUNDRED = Decimal("100")


@dataclass(frozen=True)
class Promotion:
    discount_percent: Decimal
    is_active: bool


@dataclass(frozen=True)
class PriceQuote:
    list_price: Decimal
    final_price: Decimal
    discount_percent: Decimal
    discount_source: Literal["none", "pix", "promotion"]


def quote_price(
    list_price: Decimal,
    *,
    payment_method: str = "regular",
    promotions: Sequence[Promotion] = (),
) -> PriceQuote:
    """Calculate one product's price without combining promotional and PIX discounts."""
    if not isinstance(list_price, Decimal) or not list_price.is_finite() or list_price < 0:
        raise ValueError("Table price must be a finite nonnegative Decimal.")
    if payment_method not in ("regular", "pix"):
        raise ValueError("Payment method must be regular or pix.")

    active_discounts = []
    percent_precision = 1
    for promotion in promotions:
        percent = promotion.discount_percent
        if not isinstance(percent, Decimal) or not percent.is_finite() or not 0 <= percent <= 100:
            raise ValueError("Promotion percentage must be a finite Decimal from 0 to 100.")
        if type(promotion.is_active) is not bool:
            raise ValueError("Promotion activity must be a bool.")
        percent_precision = max(percent_precision, len(percent.as_tuple().digits))
        if promotion.is_active:
            active_discounts.append(percent)

    discount = Decimal("0")
    source: Literal["none", "pix", "promotion"] = "none"
    if active_discounts:
        discount, source = max(active_discounts), "promotion"
    elif payment_method == "pix":
        discount, source = Decimal("5"), "pix"

    # Preserve sufficient precision before the one business rounding step.
    amount_digits = list_price.as_tuple()
    with localcontext() as context:
        context.prec = max(
            28,
            len(amount_digits.digits) + max(0, amount_digits.exponent) + percent_precision + 8,
        )
        normalized_price = list_price.quantize(CENT)
        if normalized_price != list_price:
            raise ValueError("Table price must be exactly representable in cents.")
        final_price = (normalized_price * (HUNDRED - discount) / HUNDRED).quantize(
            CENT, rounding=ROUND_HALF_UP
        )
    return PriceQuote(normalized_price, final_price, discount, source)
