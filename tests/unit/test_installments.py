"""Review 10-10 installment simulation IN01-IN09 (Spec 01 Review 01-10-INSTALLMENTS).

Pure domain cases. Expected amounts are literals calculated by hand in integer cents
(divmod of the price in cents by the count); simulate_installments is never its own
oracle. Money is Decimal; no float is used anywhere.
"""

from decimal import Decimal

import pytest

from emporio_agent.services.pricing import (
    InstallmentGroup,
    InstallmentSimulation,
    simulate_installments,
)

D = Decimal


def groups(*pairs: tuple[int, str]) -> tuple[InstallmentGroup, ...]:
    return tuple(InstallmentGroup(count, D(amount)) for count, amount in pairs)


def assert_sum_is_total(simulation: InstallmentSimulation) -> None:
    assert sum(group.count for group in simulation.schedule) == simulation.installments
    assert sum(group.count * group.amount for group in simulation.schedule) == simulation.total


# --- IN01: reported regression, R$ 9.351,00 -------------------------------------------


@pytest.mark.parametrize(
    ("installments", "schedule", "minimum"),
    [
        # 935100 = 7 * 133585 + 5: five installments carry one more cent.
        (7, ((5, "1335.86"), (2, "1335.85")), "100.00"),
        # 935100 = 8 * 116887 + 4.
        (8, ((4, "1168.88"), (4, "1168.87")), "100.00"),
        # 935100 = 12 * 77925: exact division.
        (12, ((12, "779.25"),), "100.00"),
    ],
)
def test_in01_regression_price_sums_exactly(installments, schedule, minimum):
    simulation = simulate_installments(D("9351.00"), installments)
    assert simulation == InstallmentSimulation(
        installments=installments,
        total=D("9351.00"),
        status="simulated",
        minimum_installment=D(minimum),
        schedule=groups(*schedule),
    )
    assert_sum_is_total(simulation)


def test_in01_regression_values_from_the_report_are_not_produced():
    seven = simulate_installments(D("9351.00"), 7)
    eight = simulate_installments(D("9351.00"), 8)
    # The model's 7 x 1335.86 = 9351.02 and 8 x 1168.88 = 9351.04 never come back.
    assert groups((7, "1335.86")) != seven.schedule
    assert groups((8, "1168.88")) != eight.schedule
    assert D("1335.86") * 7 == D("9351.02")
    assert D("1168.88") * 8 == D("9351.04")


# --- IN02: exact division and remainder -----------------------------------------------


@pytest.mark.parametrize(
    ("price", "installments", "schedule"),
    [
        ("900.00", 3, ((3, "300.00"),)),
        ("1000.00", 3, ((1, "333.34"), (2, "333.33"))),  # 100000 = 3 * 33333 + 1
        ("1000.01", 2, ((1, "500.01"), (1, "500.00"))),  # 100001 = 2 * 50000 + 1
        ("2500.00", 6, ((4, "416.67"), (2, "416.66"))),  # 250000 = 6 * 41666 + 4
        ("1350.00", 7, ((5, "192.86"), (2, "192.85"))),  # 135000 = 7 * 19285 + 5
        ("1499.99", 11, ((3, "136.37"), (8, "136.36"))),  # 149999 = 11 * 13636 + 3
    ],
)
def test_in02_division_and_cent_remainder(price, installments, schedule):
    simulation = simulate_installments(D(price), installments)
    assert simulation.status == "simulated"
    assert simulation.schedule == groups(*schedule)
    assert simulation.total == D(price)
    assert_sum_is_total(simulation)


@pytest.mark.parametrize("installments", range(2, 13))
def test_in02_every_count_sums_to_the_price(installments):
    simulation = simulate_installments(D("9999.99"), installments)
    assert simulation.status == "simulated"
    assert_sum_is_total(simulation)
    amounts = [group.amount for group in simulation.schedule]
    assert amounts == sorted(amounts, reverse=True)  # larger installments first
    assert max(amounts) - min(amounts) <= D("0.01")
    assert all(amount.as_tuple().exponent == -2 for amount in amounts)


# --- IN03: minimum installment boundaries -----------------------------------------------


@pytest.mark.parametrize(
    ("price", "installments", "status", "minimum", "schedule"),
    [
        # 4x-6x: minimum R$ 80,00 per installment.
        ("320.00", 4, "simulated", "80.00", ((4, "80.00"),)),
        ("319.99", 4, "below_minimum", "80.00", ()),
        ("480.00", 6, "simulated", "80.00", ((6, "80.00"),)),
        ("479.99", 6, "below_minimum", "80.00", ()),
        # 480.01 / 6: one installment of 80.01, five of 80.00.
        ("480.01", 6, "simulated", "80.00", ((1, "80.01"), (5, "80.00"))),
        # 7x-12x: minimum R$ 100,00 per installment.
        ("700.00", 7, "simulated", "100.00", ((7, "100.00"),)),
        ("699.99", 7, "below_minimum", "100.00", ()),
        ("1200.00", 12, "simulated", "100.00", ((12, "100.00"),)),
        ("1199.99", 12, "below_minimum", "100.00", ()),
        # 6x uses 80, 7x uses 100: same price, different ranges.
        ("600.00", 6, "simulated", "80.00", ((6, "100.00"),)),
        ("600.00", 7, "below_minimum", "100.00", ()),
        # 2x-3x: no defined minimum; below R$ 50,00 per installment is ambiguous.
        ("100.00", 2, "simulated", None, ((2, "50.00"),)),
        ("99.99", 2, "needs_confirmation", None, ()),
        ("150.00", 3, "simulated", None, ((3, "50.00"),)),
        ("149.99", 3, "needs_confirmation", None, ()),
        ("0.00", 2, "needs_confirmation", None, ()),
        ("0.00", 12, "below_minimum", "100.00", ()),
    ],
)
def test_in03_minimum_boundaries(price, installments, status, minimum, schedule):
    simulation = simulate_installments(D(price), installments)
    assert simulation == InstallmentSimulation(
        installments=installments,
        total=D(price),
        status=status,
        minimum_installment=None if minimum is None else D(minimum),
        schedule=groups(*schedule),
    )


def test_in03_minimum_applies_to_the_smallest_installment():
    # 119999 = 12 * 9999 + 11: eleven of 100.00 and one of 99.99, so not every
    # installment meets the minimum.
    assert simulate_installments(D("1199.99"), 12).schedule == ()


# --- IN04: invalid counts ---------------------------------------------------------------


@pytest.mark.parametrize("installments", [0, 1, 13, -2, 24, 2**63])
def test_in04_count_out_of_range(installments):
    with pytest.raises(ValueError):
        simulate_installments(D("1000.00"), installments)


@pytest.mark.parametrize("installments", [True, False, 2.0, D("2"), "2", None])
def test_in04_count_wrong_type(installments):
    with pytest.raises(ValueError):
        simulate_installments(D("1000.00"), installments)  # type: ignore[arg-type]


# --- IN05: invalid money ----------------------------------------------------------------


@pytest.mark.parametrize(
    "price",
    [
        D("-0.01"),
        D("NaN"),
        D("Infinity"),
        D("10.001"),
        1000.0,
        1000,
        "1000.00",
        None,
    ],
)
def test_in05_invalid_price(price):
    with pytest.raises(ValueError):
        simulate_installments(price, 2)  # type: ignore[arg-type]


def test_in05_price_with_one_place_is_normalized_to_cents():
    simulation = simulate_installments(D("1000.5"), 2)
    assert simulation.total == D("1000.50")
    assert simulation.total.as_tuple().exponent == -2
    assert simulation.schedule == groups((2, "500.25"))


# --- IN06: records are immutable --------------------------------------------------------


def test_in06_records_are_frozen():
    simulation = simulate_installments(D("1000.00"), 3)
    with pytest.raises(AttributeError):
        simulation.status = "below_minimum"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        simulation.schedule[0].amount = D("1")  # type: ignore[misc]
    assert type(simulation.schedule) is tuple
