"""Review 10-10 catalog cases CI01-CI05 (Spec 03 Review 03-10-INSTALLMENTS).

Real SQLite files built in tmp_path by the real prepare_database from the synthetic
catalog_kit sources, opened with connect_readonly. Expected money values are literals
from the catalog fixture ("Hand-calculated effective prices") divided by hand in integer
cents; simulate_installments and quote_price are never the oracle.
"""

from decimal import Decimal
from pathlib import Path

import pytest

from emporio_agent.data.readonly import connect_readonly
from emporio_agent.services.catalog import CatalogInputError, quote_product
from emporio_agent.services.pricing import InstallmentGroup, InstallmentSimulation

D = Decimal


@pytest.fixture
def database(tmp_path, catalog_kit) -> Path:
    return catalog_kit.build(tmp_path)


def quote(path: Path, product_id: object, payment_method: object, **kwargs):
    with connect_readonly(path) as connection:
        return quote_product(connection, product_id, payment_method=payment_method, **kwargs)


# --- CI01: no count keeps the earlier result -------------------------------------------


@pytest.mark.parametrize("method", ["regular", "pix"])
def test_ci01_without_count_installments_is_none(database, method):
    assert quote(database, 202, method).installments is None
    assert quote(database, 202, method, installments=None).installments is None


# --- CI02: promotion preserved in the base ----------------------------------------------


def test_ci02_simulation_uses_the_promotional_regular_price(database):
    # 202: table 1100.00, active 10% promotion, regular 990.00; 6x = 6 x 165.00.
    result = quote(database, 202, "regular", installments=6)
    assert result.final_price == D("990.00")
    assert result.discount_source == "promotion"
    assert result.installments == InstallmentSimulation(
        installments=6,
        total=D("990.00"),
        status="simulated",
        minimum_installment=D("80.00"),
        schedule=(InstallmentGroup(6, D("165.00")),),
    )


def test_ci02_promotion_with_cent_remainder(database):
    # 135: table 1500.00, regular 1350.00; 135000 = 7 * 19285 + 5.
    result = quote(database, 135, "regular", installments=7)
    assert result.installments.schedule == (
        InstallmentGroup(5, D("192.86")),
        InstallmentGroup(2, D("192.85")),
    )
    assert result.installments.total == D("1350.00")


def test_ci02_below_minimum_on_the_promotional_price(database):
    # 990.00 / 12 = 82.50 < 100.00, although 1100.00 / 12 = 91.66 would also fail.
    result = quote(database, 202, "regular", installments=12)
    assert result.installments.status == "below_minimum"
    assert result.installments.minimum_installment == D("100.00")
    assert result.installments.schedule == ()


def test_ci02_boundary_on_the_fixture(database):
    # 216: 1200.00, no promotion; 12 x 100.00 exactly meets the minimum.
    result = quote(database, 216, "regular", installments=12)
    assert result.installments.status == "simulated"
    assert result.installments.schedule == (InstallmentGroup(12, D("100.00")),)


def test_ci02_zero_price_needs_confirmation(database):
    # 211: 100% promotion, regular 0.00.
    result = quote(database, 211, "regular", installments=2)
    assert result.installments.status == "needs_confirmation"
    assert result.installments.schedule == ()


# --- CI03: PIX is paid at once ----------------------------------------------------------


@pytest.mark.parametrize("count", [2, 6, 12])
def test_ci03_pix_with_count_is_rejected(database, count):
    with pytest.raises(CatalogInputError) as raised:
        quote(database, 201, "pix", installments=count)
    assert (raised.value.field, raised.value.code) == ("installments", "not_available_for_pix")


def test_ci03_pix_quote_is_unchanged(database):
    # PIX 5% on 899.90 = 854.905 -> 854.91; no installment data.
    result = quote(database, 201, "pix")
    assert result.final_price == D("854.91")
    assert result.installments is None


# --- CI04: invalid counts ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "code"),
    [
        (True, "invalid_type"),
        (2.0, "invalid_type"),
        ("6", "invalid_type"),
        (D("6"), "invalid_type"),
        (0, "out_of_range"),
        (1, "out_of_range"),
        (13, "out_of_range"),
        (-6, "out_of_range"),
        (2**63, "out_of_range"),
    ],
)
@pytest.mark.parametrize("method", ["regular", "pix"])
def test_ci04_invalid_count(database, value, code, method):
    with pytest.raises(CatalogInputError) as raised:
        quote(database, 202, method, installments=value)
    assert (raised.value.field, raised.value.code) == ("installments", code)


def test_ci04_earlier_arguments_are_checked_first(database):
    with pytest.raises(CatalogInputError) as raised:
        quote(database, 202, "boleto", installments=0)
    assert raised.value.field == "payment_method"


# --- CI05: missing product --------------------------------------------------------------


def test_ci05_missing_product_is_none(database):
    assert quote(database, 999, "regular", installments=6) is None
