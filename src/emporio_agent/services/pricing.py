"""Unit pricing from store payment and non-cumulative promotion policies.

Also a card installment simulation in integer cents (Spec 01 Review 01-10-INSTALLMENTS).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Final, Literal

CENT = Decimal("0.01")
HUNDRED = Decimal("100")

type InstallmentStatus = Literal["simulated", "below_minimum", "needs_confirmation"]

MIN_INSTALLMENTS: Final = 2
MAX_INSTALLMENTS: Final = 12
# Approved prevalence: the ranges of the installment rules (4x-6x and 7x-12x), in cents.
_RANGE_MINIMUM_CENTS: Final = ((4, 6, 8_000), (7, 12, 10_000))
# Up to 3x: "no minimum, except below R$ 50,00" is ambiguous; below it, no amounts.
_UNCLEAR_FLOOR_CENTS: Final = 5_000


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


@dataclass(frozen=True)
class InstallmentGroup:
    count: int
    amount: Decimal


@dataclass(frozen=True)
class InstallmentSimulation:
    installments: int
    total: Decimal
    status: InstallmentStatus
    minimum_installment: Decimal | None  # None for 2x-3x: no defined minimum
    schedule: tuple[InstallmentGroup, ...]  # empty unless status is "simulated"


def _cents(card_price: object) -> int:
    if not isinstance(card_price, Decimal) or not card_price.is_finite() or card_price < 0:
        raise ValueError("Card price must be a finite nonnegative Decimal.")
    with localcontext() as context:
        context.prec = max(28, len(card_price.as_tuple().digits) + 8)
        cents = card_price * HUNDRED
        if cents != cents.to_integral_value():
            raise ValueError("Card price must be exactly representable in cents.")
        return int(cents)


def _money(cents: int) -> Decimal:
    # Exact: string construction never rounds, whatever the context precision.
    return Decimal(f"{cents}e-2")


def simulate_installments(card_price: Decimal, installments: int) -> InstallmentSimulation:
    """Split a card price into equal installments in integer cents; a simulation only.

    The first installments carry the remainder, one cent each, so the schedule sums to
    the price exactly. Without a met minimum, no amount is returned.
    """
    cents = _cents(card_price)
    if type(installments) is not int or not MIN_INSTALLMENTS <= installments <= MAX_INSTALLMENTS:
        raise ValueError("Installments must be an int from 2 to 12.")

    minimum = next(
        (floor for low, high, floor in _RANGE_MINIMUM_CENTS if low <= installments <= high), None
    )
    base, remainder = divmod(cents, installments)
    total = _money(cents)
    minimum_installment = None if minimum is None else _money(minimum)

    status: InstallmentStatus = "simulated"
    if minimum is not None and base < minimum:
        status = "below_minimum"
    elif minimum is None and base < _UNCLEAR_FLOOR_CENTS:
        status = "needs_confirmation"
    if status != "simulated":
        return InstallmentSimulation(installments, total, status, minimum_installment, ())

    schedule = (InstallmentGroup(remainder, _money(base + 1)),) if remainder else ()
    schedule += (InstallmentGroup(installments - remainder, _money(base)),)
    return InstallmentSimulation(installments, total, status, minimum_installment, schedule)
