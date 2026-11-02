"""Contract examples from docs/specs/01-pricing.md; no model or storage mocks."""

from decimal import Decimal
from importlib import import_module

import pytest


def calculate(price, *, method="regular", promotions=()):
    # Import inside a call so the initial RED is a failed test, not failed collection.
    pricing = import_module("emporio_agent.services.pricing")
    offers = [pricing.Promotion(Decimal(percent), active) for percent, active in promotions]
    return pricing.quote_price(price, payment_method=method, promotions=offers)


@pytest.mark.parametrize(
    ("method", "offers", "expected_price", "expected_percent", "source"),
    [
        ("regular", (), "599.90", "0", "none"),
        ("pix", (), "569.91", "5", "pix"),
        ("regular", (("10", True),), "539.91", "10", "promotion"),
        ("pix", (("10", True),), "539.91", "10", "promotion"),
        ("regular", (("40", False),), "599.90", "0", "none"),
        ("pix", (("40", False),), "569.91", "5", "pix"),
        (
            "pix",
            (("10", True), ("20", True), ("90", False)),
            "479.92",
            "20",
            "promotion",
        ),
        ("pix", (("0", True),), "599.90", "0", "promotion"),
        ("regular", (("100", True),), "0.00", "100", "promotion"),
    ],
)
def test_policy_price_examples(method, offers, expected_price, expected_percent, source):
    quote = calculate(Decimal("599.90"), method=method, promotions=offers)
    assert quote.list_price == Decimal("599.90")
    assert quote.final_price == Decimal(expected_price)
    assert quote.discount_percent == Decimal(expected_percent)
    assert quote.discount_source == source
    assert str(quote.final_price) == expected_price


def test_half_cent_rounds_up():
    assert calculate(Decimal("19.90"), method="pix").final_price == Decimal("18.91")


def test_zero_price_is_valid():
    assert str(calculate(Decimal("0"), method="pix").final_price) == "0.00"


@pytest.mark.parametrize(
    "price",
    [Decimal("-1"), Decimal("NaN"), Decimal("Infinity"), Decimal("0.001"), 599.9, "599.90"],
)
def test_rejects_invalid_table_prices(price):
    with pytest.raises(ValueError):
        calculate(price)


@pytest.mark.parametrize("percent", ["-1", "100.01", "NaN", "Infinity"])
@pytest.mark.parametrize("active", [True, False])
def test_rejects_invalid_promotions_even_when_inactive(percent, active):
    with pytest.raises(ValueError):
        calculate(Decimal("100.00"), promotions=((percent, active),))


@pytest.mark.parametrize("method", ["credit", "PIX", "", None])
def test_rejects_unsupported_payment_choices(method):
    with pytest.raises(ValueError):
        calculate(Decimal("100.00"), method=method)


@pytest.mark.parametrize("active", [0, 1, "false", None])
def test_rejects_non_boolean_activity(active):
    with pytest.raises(ValueError):
        calculate(Decimal("100.00"), promotions=(("10", active),))


@pytest.mark.parametrize("percent", [10, 10.0, "10"])
def test_service_rejects_non_decimal_discount(percent):
    pricing = import_module("emporio_agent.services.pricing")
    with pytest.raises(ValueError):
        pricing.quote_price(Decimal("100.00"), promotions=[pricing.Promotion(percent, True)])


def test_does_not_change_callers_promotions():
    pricing = import_module("emporio_agent.services.pricing")
    offers = [pricing.Promotion(Decimal("10"), True), pricing.Promotion(Decimal("20"), True)]
    before = list(offers)
    pricing.quote_price(Decimal("100.00"), promotions=offers)
    assert offers == before
