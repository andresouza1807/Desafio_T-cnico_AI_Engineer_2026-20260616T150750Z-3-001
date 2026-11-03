"""Slice 03-B integration cases (docs/plans/03-catalog.md, test matrix "Slice B").

C21-C34, C37-C41, C44-C48, C52-C55 and the database parts of C42, C43 and C50.
Real SQLite files built in tmp_path by the real prepare_database from the synthetic
catalog_kit sources (and, for C28, the existing prep_kit default rows), opened with
connect_readonly. Expected IDs and money values are literals calculated by hand in the
plan or read with independent parameterized SQL through a separate mode=ro connection;
quote_price, availability_of and normalize_search_text are never the oracle. Nothing
here mocks SQL, filters, pricing, relations or persistence.
"""

import dataclasses
import sqlite3
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal, localcontext
from pathlib import Path

import pytest

from emporio_agent.data.preparation import SourceFileInfo, prepare_database
from emporio_agent.data.readonly import connect_readonly
from emporio_agent.services.catalog import (
    MAX_PRICE_BRL,
    MAX_TEXT_LENGTH,
    PAGE_SIZE,
    AppliedPromotion,
    CategoryRef,
    ProductSummary,
    Provenance,
    SearchResult,
    search_products,
)

# Plan, "Expected orderings".
AVAILABLE_ORDER = [211, 214, 212, 210, 201, 206, 213, 202, 204, 205, 203, 216, 135, 217]
ALL_ORDER = [211, 214, 212, 209, 208, 207, 210, 201, 206, 213, 202, 204, 205, 203, 216, 135, 217]
VIOLOES_ORDER = [211, 214, 212, 210, 201, 206, 213, 202, 204, 205, 203]

VIOLOES = CategoryRef(category_id=1, name="Violões")
TECLAS = CategoryRef(category_id=2, name="Teclas")
BAIXOS = CategoryRef(category_id=3, name="Baixos")
ALL_CATEGORIES = (VIOLOES, TECLAS, BAIXOS)

ALLOWED_TABLES = {
    "products",
    "categories",
    "promotions",
    "data_quality_notes",
    "preparation_metadata",
    "source_files",
}
FORBIDDEN_TABLES = {"customers", "orders", "order_items"}
COMBINING_ACUTE = "\N{COMBINING ACUTE ACCENT}"


@pytest.fixture
def database(tmp_path, catalog_kit) -> Path:
    return catalog_kit.build(tmp_path)


def search(path: Path, **arguments) -> SearchResult:
    with connect_readonly(path) as connection:
        return search_products(connection, **arguments)


def ids(result: SearchResult) -> list[int]:
    return [item.product_id for item in result.products]


def all_pages(path: Path, **arguments) -> list[SearchResult]:
    pages = []
    offset = 0
    with connect_readonly(path) as connection:
        while offset is not None:
            assert len(pages) < 10, "pagination does not end"
            result = search_products(connection, offset=offset, **arguments)
            pages.append(result)
            offset = result.next_offset
    return pages


def all_items(path: Path, **arguments) -> list[ProductSummary]:
    return [item for page in all_pages(path, **arguments) for item in page.products]


def all_ids(path: Path, **arguments) -> list[int]:
    return [item.product_id for item in all_items(path, **arguments)]


def item_of(path: Path, product_id: int, **arguments) -> ProductSummary:
    found = [item for item in all_items(path, **arguments) if item.product_id == product_id]
    assert len(found) == 1
    return found[0]


def money(value: Decimal) -> str:
    # Exact text: equal value and two decimal places.
    return format(value, "f")


def assert_page(result: SearchResult, expected_ids, total, next_offset) -> None:
    assert ids(result) == expected_ids
    assert result.total_matches == total
    assert result.next_offset == next_offset


def assert_not_resolved(result: SearchResult, resolution: str, candidates) -> None:
    assert result.products == ()
    assert result.total_matches == 0
    assert result.next_offset is None
    assert result.category_resolution == resolution
    assert result.category is None
    assert result.candidate_categories == candidates


# --- C21, C22: default search and available_only -------------------------------------


def test_c21_default_search_returns_available_products(database):
    result = search(database)

    assert type(result) is SearchResult
    assert PAGE_SIZE == 5
    assert_page(result, [211, 214, 212, 210, 201], 14, 5)
    assert result.offset == 0
    assert result.available_only is True
    assert result.max_price_brl is None
    assert result.category_resolution == "not_requested"
    assert result.category is None
    assert result.candidate_categories == ()
    for item in result.products:
        assert type(item) is ProductSummary
        assert item.availability == "available"
        assert item.is_available is True
    assert all_ids(database) == AVAILABLE_ORDER


def test_c22_available_only_false_includes_unavailable(database):
    result = search(database, available_only=False)

    assert_page(result, [211, 214, 212, 209, 208], 17, 5)
    assert result.available_only is False
    iota, theta = result.products[3], result.products[4]
    assert (iota.availability, iota.is_available) == ("coming_soon", False)
    assert (theta.availability, theta.is_available) == ("discontinued", False)
    assert all_ids(database, available_only=False) == ALL_ORDER
    eta = item_of(database, 207, available_only=False)
    assert (eta.availability, eta.is_available) == ("out_of_stock", False)


# --- C23-C27: category and budget on the effective price ----------------------------


def test_c23_category_and_budget_filter_before_pagination(database):
    first = search(database, category="Violões", max_price_brl=Decimal("1000.00"))
    assert_page(first, [211, 214, 212, 210, 201], 9, 5)
    assert first.category_resolution == "resolved"
    assert first.category == VIOLOES

    second = search(database, category="Violões", max_price_brl=Decimal("1000.00"), offset=5)
    assert_page(second, [206, 213, 202, 204], 9, None)
    assert second.offset == 5


def test_c24_budget_boundaries_are_inclusive(database):
    below = all_ids(database, category="Violões", max_price_brl=Decimal("999.99"))
    exact = all_ids(database, category="Violões", max_price_brl=Decimal("1000.00"))
    above = all_ids(database, category="Violões", max_price_brl=Decimal("1000.01"))

    assert len(below) == 8
    assert len(exact) == 9
    assert len(above) == 10
    assert 204 not in below
    assert 204 in exact
    assert 205 not in exact
    assert 205 in above


def test_c25_budget_uses_promotion_price(database):
    inside = search(database, category="Violões", max_price_brl=Decimal("990.00"))
    outside = search(database, category="Violões", max_price_brl=Decimal("989.99"))

    assert inside.total_matches == 8
    assert 202 in all_ids(database, category="Violões", max_price_brl=Decimal("990.00"))
    folk = item_of(database, 202, category="Violões", max_price_brl=Decimal("990.00"))
    assert money(folk.list_price) == "1100.00"
    assert money(folk.effective_price) == "990.00"
    assert outside.total_matches == 7
    assert 202 not in all_ids(database, category="Violões", max_price_brl=Decimal("989.99"))


def test_c26_pix_price_never_widens_the_budget(database):
    found = all_items(database, category="Violões", max_price_brl=Decimal("1000.00"))

    assert 203 not in [item.product_id for item in found]  # PIX 997.50 is not used
    alfa = [item for item in found if item.product_id == 201]
    assert len(alfa) == 1
    assert money(alfa[0].effective_price) == "899.90"  # not 854.91


def test_c27_zero_budget(database):
    result = search(database, max_price_brl=Decimal("0.00"))

    assert_page(result, [211], 1, None)
    assert money(result.products[0].effective_price) == "0.00"
    assert money(result.max_price_brl) == "0.00"


# --- C28-C31: promotions in search results ------------------------------------------


def test_c28_largest_promotion_only(database, tmp_path, prep_kit):
    mu = item_of(database, 212, category="Violões")
    assert money(mu.effective_price) == "266.66"  # 333.33 x 0.80 = 266.664
    assert mu.promotion == AppliedPromotion(
        promotion_id=305, discount_percent=Decimal("20"), description="Promo Vinte"
    )

    # Existing prep_kit default rows: P11 19.90 with an active 12.5% promotion.
    source = tmp_path / "prep-src"
    prep_kit.write_sources(source)
    target_dir = tmp_path / "prep-db"
    target_dir.mkdir()
    target = target_dir / "loja.sqlite3"
    prepare_database(source, target, clock=lambda: datetime(2026, 11, 2, 12, 0, tzinfo=UTC))

    result = search(target, query="encordoamento", available_only=False)
    assert_page(result, [11], 1, None)
    strings = result.products[0]
    assert money(strings.list_price) == "19.90"
    assert money(strings.effective_price) == "17.41"  # 19.90 x 0.875 = 17.4125
    assert strings.promotion == AppliedPromotion(
        promotion_id=102, discount_percent=Decimal("12.5"), description="Campanha Teste"
    )
    assert strings.availability == "out_of_stock"


def test_c29_zero_percent_promotion_is_shown(database):
    kappa = item_of(database, 210)

    assert money(kappa.effective_price) == "700.00"
    assert kappa.promotion == AppliedPromotion(
        promotion_id=302, discount_percent=Decimal("0"), description="Campanha Zero"
    )


def test_c30_tied_promotions_in_search(database):
    bass = item_of(database, 135)

    assert money(bass.effective_price) == "1350.00"
    assert bass.promotion == AppliedPromotion(
        promotion_id=308, discount_percent=Decimal("10"), description="Promo Baixo A"
    )


def test_c31_inactive_promotion_never_applied(database):
    below = search(database, category="Teclas", max_price_brl=Decimal("2499.99"))
    exact = search(database, category="Teclas", max_price_brl=Decimal("2500.00"))

    assert_page(below, [216], 1, None)
    assert_page(exact, [216, 217], 2, None)
    piano = exact.products[1]
    assert money(piano.effective_price) == "2500.00"
    assert piano.promotion is None


# --- C32-C34: pagination ------------------------------------------------------------


def test_c32_pages_follow_price_then_id(database):
    pages = [search(database, category="Violões", offset=offset) for offset in (0, 5, 10)]

    assert_page(pages[0], [211, 214, 212, 210, 201], 11, 5)
    assert_page(pages[1], [206, 213, 202, 204, 205], 11, 10)
    assert_page(pages[2], [203], 11, None)
    joined = [product_id for page in pages for product_id in ids(page)]
    assert joined == VIOLOES_ORDER
    assert len(set(joined)) == 11
    # The 899.90 tie group crosses the first page boundary in product_id order.
    assert joined.index(201) < joined.index(206) < joined.index(213)


def test_c33_arbitrary_offset(database):
    result = search(database, category="Violões", offset=3)

    assert_page(result, [210, 201, 206, 213, 202], 11, 8)
    assert result.offset == 3


@pytest.mark.parametrize(
    ("offset", "expected"),
    [(10, [203]), (11, []), (12, []), (10**6, [])],
    ids=["last_page", "at_total", "past_total", "huge"],
)
def test_c34_offset_at_and_past_the_end(database, offset, expected):
    result = search(database, category="Violões", offset=offset)

    assert_page(result, expected, 11, None)
    assert result.offset == offset


# --- C37-C41: query matching --------------------------------------------------------


@pytest.mark.parametrize("query", ["violao", "VIOLÃO", "Violão"])
def test_c37_case_and_accent_insensitive(database, query):
    assert all_ids(database, query=query) == VIOLOES_ORDER


@pytest.mark.parametrize(
    ("query", "expected"),
    [("omega", [214]), ("ÔMEGA", [214]), ("epsilon", [205])],
)
def test_c37_accented_names(database, query, expected):
    assert all_ids(database, query=query) == expected


@pytest.mark.parametrize(
    ("query", "expected"),
    [("  violão   alfa  ", [201]), ("alfa violão", [201]), ("violãoalfa", [])],
    ids=["spaces", "order_free", "not_split"],
)
def test_c38_tokens(database, query, expected):
    assert all_ids(database, query=query) == expected


def test_c39_substring_tokens(database):
    assert all_ids(database, query="alf") == [201]
    # Theta, Eta, Zeta, Beta, in effective price order.
    assert all_ids(database, query="eta", available_only=False) == [208, 207, 206, 202]
    assert all_ids(database, query="eta") == [206, 202]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("%", [210, 213]),
        ("_", [213]),
        ("100%", [213]),
        ('"aspas"', [213]),
        ("'; DROP TABLE products; --", []),
        ("*", []),
    ],
    ids=["percent", "underscore", "hundred_percent", "quotes", "sql_text", "star"],
)
def test_c40_special_characters_are_literal(database, catalog_kit, query, expected):
    before = catalog_kit.snapshot(database)

    assert all_ids(database, query=query) == expected

    assert catalog_kit.snapshot(database) == before


def test_c41_only_the_name_is_searched(database):
    assert all_ids(database, query="fretless") == []  # description of 135 only
    assert all_ids(database, query="ash") == []  # specs of 135 only
    result = search(database, query="marca a")
    assert_page(result, [135], 1, None)
    assert result.products[0].quality_notes == ("catalog_text_conflict",)


# --- C42, C43: absent text and length (database part) -------------------------------


def test_c42_none_means_no_filter(database):
    result = search(database, query=None, category=None)

    assert result.total_matches == 14
    assert result.category_resolution == "not_requested"


def test_c43_text_at_the_limit_is_accepted(database):
    assert MAX_TEXT_LENGTH == 4000
    decomposed = ("e" + COMBINING_ACUTE) * 2000
    assert len(decomposed) == 4000

    for text in ("a" * 4000, decomposed):
        by_query = search(database, query=text)
        assert_page(by_query, [], 0, None)
        assert by_query.category_resolution == "not_requested"

        by_category = search(database, category=text)
        assert_not_resolved(by_category, "unknown", ALL_CATEGORIES)


# --- C44-C48: category resolution and combined filters ------------------------------


@pytest.mark.parametrize("category", ["violões", " VIOLÕES ", "violoes"])
def test_c44_category_resolved_by_normalized_name(database, category):
    result = search(database, category=category)

    assert result.category_resolution == "resolved"
    assert result.category == VIOLOES
    assert result.candidate_categories == ()
    assert result.total_matches == 11


def test_c44_unknown_category_keeps_the_filter(database):
    unknown = search(database, category="Ukuleles")
    assert_not_resolved(unknown, "unknown", ALL_CATEGORIES)

    with_query = search(database, query="violão", category="Ukuleles")
    assert_not_resolved(with_query, "unknown", ALL_CATEGORIES)


def test_c45_colliding_categories(tmp_path, catalog_kit):
    path = catalog_kit.build(tmp_path, edit="collision")
    assert catalog_kit.query(path, "SELECT category_id, name FROM categories ORDER BY 1") == [
        (1, "Violões"),
        (2, "Teclas"),
        (3, "Baixos"),
        (4, "violoes"),
    ]

    ambiguous = search(path, category="VIOLÕES")
    assert_not_resolved(ambiguous, "ambiguous", (VIOLOES, CategoryRef(4, "violoes")))

    exact_first = search(path, category="Violões")
    assert exact_first.category_resolution == "resolved"
    assert exact_first.category == VIOLOES
    assert exact_first.total_matches == 11

    exact_second = search(path, category="violoes")
    assert exact_second.category_resolution == "resolved"
    assert exact_second.category == CategoryRef(4, "violoes")
    assert_page(exact_second, [], 0, None)


def test_c46_resolved_category_without_matches(database):
    result = search(database, category="Baixos", max_price_brl=Decimal("1000.00"))

    assert result.category_resolution == "resolved"
    assert result.category == BAIXOS
    assert_page(result, [], 0, None)


def test_c47_query_category_and_budget_together(database):
    arguments = {"query": "violao", "category": "Violões", "max_price_brl": Decimal("900.00")}

    first = search(database, **arguments)
    assert_page(first, [211, 214, 212, 210, 201], 7, 5)
    second = search(database, offset=5, **arguments)
    assert_page(second, [206, 213], 7, None)


def test_c48_no_product_invented(database):
    result = search(database, query="piano", category="Violões")

    assert result.category_resolution == "resolved"
    assert result.category == VIOLOES
    assert_page(result, [], 0, None)


# --- C50: budget echo and the caller's decimal context ------------------------------


def test_c50_budget_quantized_and_caller_context_ignored(database):
    assert Decimal("92233720368547758.07") == MAX_PRICE_BRL
    cases = [
        (Decimal("1000.000"), 9, "1000.00"),
        (Decimal("-0"), 1, "0.00"),
        (Decimal("92233720368547758.07"), 14, "92233720368547758.07"),
    ]

    def run() -> list[tuple[int, str, bool, list[int]]]:
        observed = []
        for value, _, _ in cases:
            result = search(database, max_price_brl=value)
            observed.append(
                (
                    result.total_matches,
                    money(result.max_price_brl),
                    result.max_price_brl.is_signed(),
                    ids(result),
                )
            )
        return observed

    expected = [
        (9, "1000.00", False, [211, 214, 212, 210, 201]),
        (1, "0.00", False, [211]),
        (14, "92233720368547758.07", False, [211, 214, 212, 210, 201]),
    ]
    assert run() == expected

    with localcontext() as context:
        context.prec = 3
        context.rounding = ROUND_DOWN
        context.clear_flags()
        traps = dict(context.traps)
        assert run() == expected
        assert context.prec == 3
        assert context.rounding == ROUND_DOWN
        assert dict(context.traps) == traps
        assert not any(context.flags.values())


# --- C52: authorizer during searches ------------------------------------------------


def test_c52_authorizer_sees_only_catalog_reads(tmp_path, catalog_kit):
    plain = catalog_kit.build(tmp_path / "plain")
    collision = catalog_kit.build(tmp_path / "collision", edit="collision")
    actions: list[tuple[int, str | None, str | None]] = []

    def authorizer(action, arg1, arg2, _database, _source):
        actions.append((action, arg1, arg2))
        return sqlite3.SQLITE_OK

    calls = [
        (plain, {}),  # C21
        (plain, {"query": "%"}),  # C40
        (plain, {"query": "'; DROP TABLE products; --"}),  # C40
        (collision, {"category": "VIOLÕES"}),  # C45
        (collision, {"category": "violoes"}),  # C45
    ]
    for path, arguments in calls:
        with connect_readonly(path) as connection:
            connection.set_authorizer(authorizer)
            search_products(connection, **arguments)
            connection.set_authorizer(None)

    assert actions, "authorizer recorded nothing"
    assert {action for action, _, _ in actions} <= {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ}
    reads = {(arg1, arg2) for action, arg1, arg2 in actions if action == sqlite3.SQLITE_READ}
    tables = {table for table, _ in reads}
    assert tables <= ALLOWED_TABLES
    assert {"products", "categories", "promotions", "data_quality_notes"} <= tables
    assert not tables & FORBIDDEN_TABLES
    # Spec P-15: search does not read description or specs_json.
    assert ("products", "description") not in reads
    assert ("products", "specs_json") not in reads


# --- C53: untrusted text and summary projection -------------------------------------


def test_c53_summaries_carry_no_description_or_specs(database, catalog_kit):
    result = search(database, query="ignore")

    assert_page(result, [216], 1, None)
    assert result.products[0].name == catalog_kit.INJECTION_NAME
    assert catalog_kit.query(
        database, "SELECT name FROM products WHERE product_id = ?", (216,)
    ) == [(catalog_kit.INJECTION_NAME,)]
    names = [field.name for field in dataclasses.fields(ProductSummary)]
    assert "description" not in names
    assert "specs" not in names
    assert "specs_json" not in names

    bass = item_of(database, 135)
    assert bass.quality_notes == ("catalog_text_conflict",)
    assert not hasattr(bass, "description")
    assert not hasattr(bass, "specs")


# --- C54: availability and stored values against independent SQL -------------------


def test_c54_items_match_independent_queries(database, catalog_kit):
    items = all_items(database, available_only=False)
    assert [item.product_id for item in items] == ALL_ORDER

    for item in items:
        stored = catalog_kit.query(
            database,
            "SELECT stock_quantity, status, price_cents FROM products WHERE product_id = ?",
            (item.product_id,),
        )
        assert len(stored) == 1
        stock, status, cents = stored[0]
        assert item.stock_quantity == stock
        assert item.status == status
        assert money(item.list_price) == f"{cents // 100}.{cents % 100:02d}"

    available = catalog_kit.query(
        database,
        "SELECT product_id FROM products WHERE status = ? AND stock_quantity > ?",
        ("active", 0),
    )
    assert {item.product_id for item in items if item.is_available} == {row[0] for row in available}


# --- C55: provenance ----------------------------------------------------------------


def test_c55_search_provenance(database, catalog_kit):
    provenance = search(database).provenance

    assert type(provenance) is Provenance
    assert provenance.prepared_at == datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
    assert [source.table_name for source in provenance.sources] == [
        "categories",
        "products",
        "promotions",
    ]
    stored = {
        row[0]: row[1:]
        for row in catalog_kit.query(
            database,
            "SELECT table_name, file_name, sha256, size_bytes, row_count FROM source_files",
        )
    }
    for source in provenance.sources:
        assert type(source) is SourceFileInfo
        assert (source.file_name, source.sha256, source.size_bytes, source.row_count) == stored[
            source.table_name
        ]
        assert source.file_name == catalog_kit.file_name(source.table_name)
