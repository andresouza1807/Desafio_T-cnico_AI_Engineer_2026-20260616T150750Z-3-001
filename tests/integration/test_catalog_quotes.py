"""Slice 03-C integration cases C56-C63 and C65-C67 (docs/plans/03-catalog.md).

Real SQLite files built in tmp_path by the real prepare_database from the synthetic
catalog_kit sources, opened with connect_readonly. Expected money values and IDs are
literals calculated by hand in the plan ("Hand-calculated effective prices" and the
PIX line below it, "Expected orderings"); quote_price and availability_of are never the
oracle. Nothing here mocks SQL, pricing, availability, relations or persistence.
"""

import dataclasses
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from emporio_agent.data.readonly import connect_readonly
from emporio_agent.services.catalog import (
    AppliedPromotion,
    ProductQuote,
    ProductSummary,
    Provenance,
    get_product,
    quote_product,
    search_products,
)

# Plan, fixture table: table price of every product.
LIST_PRICES = {
    135: "1500.00",
    201: "899.90",
    202: "1100.00",
    203: "1050.00",
    204: "1000.00",
    205: "1000.01",
    206: "899.90",
    207: "500.00",
    208: "450.00",
    209: "300.00",
    210: "700.00",
    211: "250.00",
    212: "333.33",
    213: "899.90",
    214: "19.90",
    216: "1200.00",
    217: "2500.00",
}
# Plan, "Hand-calculated effective prices (regular)".
REGULAR_PRICES = {
    135: "1350.00",
    201: "899.90",
    202: "990.00",
    203: "1050.00",
    204: "1000.00",
    205: "1000.01",
    206: "899.90",
    207: "500.00",
    208: "450.00",
    209: "300.00",
    210: "700.00",
    211: "0.00",
    212: "266.66",
    213: "899.90",
    214: "19.90",
    216: "1200.00",
    217: "2500.00",
}
# Plan, "Expected orderings": all products, available_only=False.
ALL_ORDER = [211, 214, 212, 209, 208, 207, 210, 201, 206, 213, 202, 204, 205, 203, 216, 135, 217]

ALLOWED_TABLES = {
    "products",
    "categories",
    "promotions",
    "data_quality_notes",
    "preparation_metadata",
    "source_files",
}
FORBIDDEN_TABLES = {"customers", "orders", "order_items"}

# (product_id, payment_method) of every quote in C56-C66, in matrix order.
MATRIX_QUOTES = [
    (201, "regular"),  # C56
    (201, "pix"),  # C57
    (214, "pix"),
    (217, "pix"),
    (202, "pix"),  # C58
    (202, "regular"),
    (210, "pix"),  # C59
    (211, "pix"),  # C60
    (212, "pix"),  # C61
    (208, "regular"),  # C62
    (207, "pix"),
    (209, "regular"),
    (999, "pix"),  # C63
    (999, "regular"),
    (135, "regular"),  # C66
]


@pytest.fixture
def database(tmp_path, catalog_kit) -> Path:
    return catalog_kit.build(tmp_path)


def quote_of(path: Path, product_id: int, payment_method: str) -> ProductQuote | None:
    with connect_readonly(path) as connection:
        return quote_product(connection, product_id, payment_method=payment_method)


def money(value: Decimal) -> str:
    # Exact text: equal value and two decimal places.
    return format(value, "f")


def assert_quote(
    quote: ProductQuote | None,
    *,
    product_id: int,
    payment_method: str,
    final: str,
    discount: str,
    source: str,
    promotion: AppliedPromotion | None,
    pix_not_combined: bool,
) -> ProductQuote:
    assert type(quote) is ProductQuote
    assert type(quote.product) is ProductSummary
    assert quote.product.product_id == product_id
    assert quote.payment_method == payment_method
    assert money(quote.list_price) == LIST_PRICES[product_id]
    assert money(quote.final_price) == final
    assert quote.discount_percent == Decimal(discount)
    assert quote.discount_source == source
    assert quote.promotion == promotion
    assert quote.pix_not_combined is pix_not_combined
    # The summary always carries the regular price; final_price the requested one.
    assert money(quote.product.list_price) == LIST_PRICES[product_id]
    assert money(quote.product.effective_price) == REGULAR_PRICES[product_id]
    assert quote.product.promotion == promotion
    assert type(quote.provenance) is Provenance
    return quote


# --- C56: regular quote without promotion -------------------------------------------


def test_c56_regular_quote_without_promotion(database):
    quote = assert_quote(
        quote_of(database, 201, "regular"),
        product_id=201,
        payment_method="regular",
        final="899.90",
        discount="0",
        source="none",
        promotion=None,
        pix_not_combined=False,
    )
    assert quote.product.availability == "available"
    assert quote.product.is_available is True
    with connect_readonly(database) as connection:
        assert quote.provenance == get_product(connection, 201).provenance


# --- C57: PIX 5% when no promotion is active ----------------------------------------


@pytest.mark.parametrize(
    ("product_id", "final"),
    [(201, "854.91"), (214, "18.91"), (217, "2375.00")],
    ids=["201_half_up", "214_half_up", "217_inactive_promotion_ignored"],
)
def test_c57_pix_discount_without_active_promotion(database, product_id, final):
    assert_quote(
        quote_of(database, product_id, "pix"),
        product_id=product_id,
        payment_method="pix",
        final=final,
        discount="5",
        source="pix",
        promotion=None,
        pix_not_combined=False,
    )


# --- C58: an active promotion blocks PIX; the flag tells why ------------------------


@pytest.mark.parametrize(
    ("payment_method", "flag"),
    [("pix", True), ("regular", False)],
)
def test_c58_promotion_is_not_combined_with_pix(database, payment_method, flag):
    assert_quote(
        quote_of(database, 202, payment_method),
        product_id=202,
        payment_method=payment_method,
        final="990.00",  # not 940.50 (990.00 x 0.95)
        discount="10",
        source="promotion",
        promotion=AppliedPromotion(301, Decimal("10"), "Promo Folk"),
        pix_not_combined=flag,
    )


# --- C59: an active 0% promotion still blocks PIX -----------------------------------


def test_c59_zero_percent_promotion_blocks_pix(database):
    assert_quote(
        quote_of(database, 210, "pix"),
        product_id=210,
        payment_method="pix",
        final="700.00",  # not 665.00 (700.00 x 0.95)
        discount="0",
        source="promotion",
        promotion=AppliedPromotion(302, Decimal("0"), "Campanha Zero"),
        pix_not_combined=True,
    )


# --- C60: a 100% promotion ----------------------------------------------------------


def test_c60_full_promotion_under_pix(database):
    assert_quote(
        quote_of(database, 211, "pix"),
        product_id=211,
        payment_method="pix",
        final="0.00",
        discount="100",
        source="promotion",
        promotion=AppliedPromotion(303, Decimal("100"), "Brinde Total"),
        pix_not_combined=True,
    )


# --- C61: largest active promotion only, no accumulation ----------------------------


def test_c61_largest_promotion_only_under_pix(database):
    assert_quote(
        quote_of(database, 212, "pix"),
        product_id=212,
        payment_method="pix",
        final="266.66",  # not 216.66 (15% + 20%), not 226.66 (15% then 20%)
        discount="20",
        source="promotion",
        promotion=AppliedPromotion(305, Decimal("20"), "Promo Vinte"),
        pix_not_combined=True,
    )


# --- C62: unavailable products are quoted with their state --------------------------


@pytest.mark.parametrize(
    ("product_id", "payment_method", "final", "discount", "source", "availability", "notes"),
    [
        (208, "regular", "450.00", "0", "none", "discontinued", ("status_stock_conflict",)),
        (207, "pix", "475.00", "5", "pix", "out_of_stock", ()),
        (209, "regular", "300.00", "0", "none", "coming_soon", ("status_stock_conflict",)),
    ],
    ids=["208_discontinued", "207_out_of_stock", "209_coming_soon"],
)
def test_c62_unavailable_product_is_quoted(
    database, product_id, payment_method, final, discount, source, availability, notes
):
    quote = assert_quote(
        quote_of(database, product_id, payment_method),
        product_id=product_id,
        payment_method=payment_method,
        final=final,
        discount=discount,
        source=source,
        promotion=None,
        pix_not_combined=False,
    )
    assert quote.product.availability == availability
    assert quote.product.is_available is False
    assert quote.product.quality_notes == notes


# --- C63 (database part): missing product -------------------------------------------


@pytest.mark.parametrize("product_id", [999, 2**63 - 1], ids=["999", "max_int"])
@pytest.mark.parametrize("payment_method", ["regular", "pix"])
def test_c63_missing_product_returns_none(database, product_id, payment_method):
    assert quote_of(database, product_id, payment_method) is None


# --- C65: search, detail and quote agree with the hand-calculated table -------------


def test_c65_search_detail_and_quote_agree(database):
    seen: list[int] = []
    search_prices: dict[int, str] = {}
    next_offsets = []
    with connect_readonly(database) as connection:
        for offset in (0, 5, 10, 15):
            page = search_products(connection, available_only=False, offset=offset)
            assert page.total_matches == 17
            next_offsets.append(page.next_offset)
            for item in page.products:
                seen.append(item.product_id)
                search_prices[item.product_id] = money(item.effective_price)
                assert money(item.list_price) == LIST_PRICES[item.product_id]

        assert next_offsets == [5, 10, 15, None]
        assert seen == ALL_ORDER
        assert len(set(seen)) == 17
        assert set(seen) == set(REGULAR_PRICES)

        for product_id in seen:
            detail = get_product(connection, product_id)
            quote = quote_product(connection, product_id, payment_method="regular")
            expected = REGULAR_PRICES[product_id]
            assert search_prices[product_id] == expected, product_id
            assert money(detail.summary.effective_price) == expected, product_id
            assert money(quote.final_price) == expected, product_id
            assert money(quote.product.effective_price) == expected, product_id
            assert money(detail.summary.list_price) == LIST_PRICES[product_id], product_id
            assert money(quote.list_price) == LIST_PRICES[product_id], product_id
            assert quote.product == detail.summary, product_id


# --- C66: flagged product, no text in the quote -------------------------------------


def test_c66_flagged_product_quote_has_no_text(database, catalog_kit):
    quote = assert_quote(
        quote_of(database, 135, "regular"),
        product_id=135,
        payment_method="regular",
        final="1350.00",  # two active 10% promotions, not 1215.00
        discount="10",
        source="promotion",
        promotion=AppliedPromotion(308, Decimal("10"), "Promo Baixo A"),
        pix_not_combined=False,
    )
    assert quote.product.quality_notes == ("catalog_text_conflict",)

    quote_fields = {field.name for field in dataclasses.fields(ProductQuote)}
    summary_fields = {field.name for field in dataclasses.fields(ProductSummary)}
    for fields in (quote_fields, summary_fields):
        assert not fields & {"description", "specs", "specs_json", "withheld_fields"}
    text = repr(quote)
    assert catalog_kit.CONFLICT_DESCRIPTION not in text
    assert "Fretless" not in text
    assert "madeira" not in text
    assert "Ash" not in text


# --- C67: only catalog reads, database unchanged ------------------------------------


def test_c67_quotes_read_only_catalog_tables(database):
    quote_actions: list[tuple[int, str | None, str | None]] = []
    other_actions: list[tuple[int, str | None, str | None]] = []
    current = quote_actions

    def authorizer(action, arg1, arg2, _database, _source):
        current.append((action, arg1, arg2))
        return sqlite3.SQLITE_OK

    with connect_readonly(database) as connection:
        connection.set_authorizer(authorizer)
        for product_id, payment_method in MATRIX_QUOTES:
            quote_product(connection, product_id, payment_method=payment_method)
        for product_id in ALL_ORDER:  # C65 quotes
            quote_product(connection, product_id, payment_method="regular")
        current = other_actions
        for offset in (0, 5, 10, 15):  # C65 search and detail calls
            search_products(connection, available_only=False, offset=offset)
        for product_id in ALL_ORDER:
            get_product(connection, product_id)
        connection.set_authorizer(None)

    for actions in (quote_actions, other_actions):
        assert actions, "authorizer recorded nothing"
        assert {action for action, _, _ in actions} <= {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ}
        tables = {arg1 for action, arg1, _ in actions if action == sqlite3.SQLITE_READ}
        assert tables <= ALLOWED_TABLES
        assert {"products", "categories", "promotions", "data_quality_notes"} <= tables
        assert not tables & FORBIDDEN_TABLES

    # A quote carries no text, so it never reads description or specs_json.
    quote_reads = {(a1, a2) for action, a1, a2 in quote_actions if action == sqlite3.SQLITE_READ}
    assert ("products", "description") not in quote_reads
    assert ("products", "specs_json") not in quote_reads


def test_c67_quotes_leave_database_unchanged(database, catalog_kit):
    before = catalog_kit.snapshot(database)
    assert before["listing"] == [catalog_kit.DB_NAME]

    with connect_readonly(database) as connection:
        for product_id, payment_method in MATRIX_QUOTES:
            quote_product(connection, product_id, payment_method=payment_method)
        for offset in (0, 5, 10, 15):
            search_products(connection, available_only=False, offset=offset)
        for product_id in ALL_ORDER:
            get_product(connection, product_id)
            quote_product(connection, product_id, payment_method="regular")
            quote_product(connection, product_id, payment_method="pix")

    after = catalog_kit.snapshot(database)
    assert after == before
    assert after["listing"] == [catalog_kit.DB_NAME]
