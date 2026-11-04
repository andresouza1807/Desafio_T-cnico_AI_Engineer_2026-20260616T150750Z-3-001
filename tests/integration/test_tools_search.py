"""Slice 06-B integration cases TL19-TL21 and TL36-TL39 (docs/plans/06-tools.md).

search_products through execute_tool against real SQLite files built in tmp_path by the
real prepare_database from the synthetic catalog_kit sources, opened through the real
connect_readonly and observed by the delegating tools_kit spy. Expected values are
literals from Spec 03, Spec 06 and the catalog fixture (the same pages as
tests/integration/test_catalog_search.py), plus independent SQL reads and SHA-256
values of the written source bytes. No production serializer, quote_price, availability
rule or search function is ever the oracle. Nothing here mocks prices, SQL or
persistence.
"""

import decimal
import hashlib
import json
import re

import pytest

TOOL = "search_products"
MONEY = re.compile(r"^\d+\.\d{2}$")
ENVELOPE_KEYS = ["tool", "status", "data", "error"]
SEARCH_KEYS = [
    "products",
    "total_matches",
    "offset",
    "page_size",
    "next_offset",
    "available_only",
    "max_price_brl",
    "category_resolution",
    "category",
    "candidate_categories",
    "provenance",
]
SUMMARY_KEYS = [
    "product_id",
    "name",
    "category",
    "list_price",
    "effective_price",
    "promotion",
    "stock_quantity",
    "status",
    "availability",
    "is_available",
    "quality_notes",
]
VIOLOES = {"category_id": 1, "name": "Violões"}
TECLAS = {"category_id": 2, "name": "Teclas"}
BAIXOS = {"category_id": 3, "name": "Baixos"}
COLLISION = {"category_id": 4, "name": "violoes"}
# Literal pages of the catalog fixture (Spec 03 examples, plan 03 pages).
DEFAULT_PAGE = [211, 214, 212, 210, 201]
ALL_PAGE = [211, 214, 212, 209, 208]
VIOLOES_1000 = [211, 214, 212, 210, 201, 206, 213, 202, 204]


@pytest.fixture
def database(tmp_path, catalog_kit):
    return catalog_kit.build(tmp_path)


@pytest.fixture
def ctx(database, tools_kit):
    return tools_kit.context(database_path=database)


@pytest.fixture
def spy(monkeypatch, tools_kit):
    return tools_kit.connect_spy(monkeypatch)


def search(tools_kit, context, arguments, *, ensure_ascii=True):
    return tools_kit.run(TOOL, json.dumps(arguments, ensure_ascii=ensure_ascii), context)


def error_payload(status, code, field=None):
    field_text = "null" if field is None else f'"{field}"'
    return (
        f'{{"tool":"{TOOL}","status":"{status}","data":null,'
        f'"error":{{"code":"{code}","field":{field_text}}}}}'
    )


def assert_error(execution, status, code, field=None):
    assert execution.payload == error_payload(status, code, field)
    assert execution.tool == TOOL
    assert execution.status == status
    assert execution.error_code == code


def expected_provenance(tmp_path, catalog_kit, categories=3):
    """Independent oracle: hash and size of the source bytes written by the fixture."""
    sources = []
    for table, row_count in (("categories", categories), ("products", 17), ("promotions", 9)):
        written = (tmp_path / catalog_kit.SOURCE_DIR / catalog_kit.file_name(table)).read_bytes()
        sources.append(
            {
                "table_name": table,
                "file_name": f"desafio_tecnico_ai_eng - {table}.csv",
                "sha256": hashlib.sha256(written).hexdigest(),
                "size_bytes": len(written),
                "row_count": row_count,
            }
        )
    return {"prepared_at": "2026-11-02T12:00:00+00:00", "sources": sources}


def summary(product_id, name, list_price, effective_price, promotion, stock):
    return {
        "product_id": product_id,
        "name": name,
        "category": VIOLOES,
        "list_price": list_price,
        "effective_price": effective_price,
        "promotion": promotion,
        "stock_quantity": stock,
        "status": "active",
        "availability": "available",
        "is_available": True,
        "quality_notes": [],
    }


def default_page_summaries():
    """Literal summaries of 211, 214, 212, 210, 201 (catalog fixture, Spec 03 prices)."""
    return [
        summary(
            211,
            "Violão Lambda",
            "250.00",
            "0.00",
            {"promotion_id": 303, "discount_percent": "100.00", "description": "Brinde Total"},
            1,
        ),
        summary(214, "Violão Ômega", "19.90", "19.90", None, 1),
        summary(
            212,
            "Violão Mu",
            "333.33",
            "266.66",
            {"promotion_id": 305, "discount_percent": "20.00", "description": "Promo Vinte"},
            1,
        ),
        summary(
            210,
            "Violão Kappa 0%",
            "700.00",
            "700.00",
            {"promotion_id": 302, "discount_percent": "0.00", "description": "Campanha Zero"},
            2,
        ),
        summary(201, "Violão Clássico Alfa", "899.90", "899.90", None, 4),
    ]


def search_data(
    products,
    total,
    offset,
    next_offset,
    provenance,
    *,
    available_only=True,
    max_price=None,
    resolution="not_requested",
    category=None,
    candidates=(),
):
    return {
        "products": products,
        "total_matches": total,
        "offset": offset,
        "page_size": 5,
        "next_offset": next_offset,
        "available_only": available_only,
        "max_price_brl": max_price,
        "category_resolution": resolution,
        "category": category,
        "candidate_categories": list(candidates),
        "provenance": provenance,
    }


def ids(payload):
    return [product["product_id"] for product in payload["data"]["products"]]


def assert_ok(execution, payload):
    assert execution.tool == TOOL
    assert execution.status == "ok"
    assert execution.error_code is None
    assert list(payload) == ENVELOPE_KEYS
    assert payload["tool"] == TOOL
    assert payload["status"] == "ok"
    assert payload["error"] is None
    assert list(payload["data"]) == SEARCH_KEYS
    for product in payload["data"]["products"]:
        assert list(product) == SUMMARY_KEYS
        assert MONEY.fullmatch(product["list_price"])
        assert MONEY.fullmatch(product["effective_price"])
    # The query text is never echoed under any key (spec G8, G14).
    assert "query" not in payload["data"]


# --- TL19: defaults, authorized nulls, strict types ----------------------------------


def test_tl19_defaults_and_authorized_nulls(ctx, tmp_path, catalog_kit, tools_kit, spy):
    empty, empty_payload = search(tools_kit, ctx, {})
    nulls, _ = search(tools_kit, ctx, {"query": None, "category": None, "max_price_brl": None})
    explicit, _ = search(
        tools_kit,
        ctx,
        {
            "query": None,
            "category": None,
            "max_price_brl": None,
            "available_only": True,
            "offset": 0,
        },
    )

    assert empty.payload == nulls.payload == explicit.payload
    assert_ok(empty, empty_payload)
    assert empty_payload["data"]["available_only"] is True
    assert empty_payload["data"]["offset"] == 0
    assert empty_payload["data"]["max_price_brl"] is None
    assert empty_payload["data"]["category_resolution"] == "not_requested"
    assert ids(empty_payload) == DEFAULT_PAGE
    assert spy.opens == 3
    assert spy.all_closed()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("available_only", 1),
        ("available_only", 0),
        ("available_only", None),
        ("available_only", "true"),
        ("available_only", [True]),
        ("offset", None),
        ("offset", True),
        ("offset", False),
        ("offset", 1.0),
        ("offset", "0"),
        ("offset", [0]),
        ("query", 1),
        ("query", True),
        ("query", ["Folk"]),
        ("category", 1),
        ("category", {"name": "Violões"}),
        ("max_price_brl", 1000),
        ("max_price_brl", 1000.0),
        ("max_price_brl", True),
        ("max_price_brl", ["1000"]),
    ],
)
def test_tl19_strict_types(field, value, ctx, tools_kit, spy):
    """null is not the default for available_only and offset; no coercion anywhere."""
    execution, _ = search(tools_kit, ctx, {field: value})
    assert_error(execution, "invalid_input", "invalid_type", field)
    assert spy.opens == 0


def test_tl19_negative_offset_is_service_out_of_range(ctx, tools_kit, spy):
    for offset in (-1, -(2**63)):
        execution, _ = search(tools_kit, ctx, {"offset": offset})
        assert_error(execution, "invalid_input", "out_of_range", "offset")
    # Domain ranges are the service's, after the open (spec G4 general precedence).
    assert spy.opens == 2
    assert spy.all_closed()
    execution, _ = search(tools_kit, tools_kit.context(), {"offset": -1})
    assert_error(execution, "unavailable", "data_unavailable")


def test_tl19_available_only_false_reaches_the_service(ctx, tools_kit, spy):
    execution, payload = search(tools_kit, ctx, {"available_only": False})
    assert_ok(execution, payload)
    assert payload["data"]["available_only"] is False
    assert ids(payload) == ALL_PAGE
    assert payload["data"]["total_matches"] == 17
    assert payload["data"]["next_offset"] == 5
    assert spy.opens == 1


# --- TL20: max_price_brl format, exact Decimal conversion and boundary --------------


@pytest.mark.parametrize(
    ("text", "echo", "total"),
    [
        ("0", "0.00", 1),
        ("0.5", "0.50", 1),
        ("1000", "1000.00", 9),
        ("1000.00", "1000.00", 9),
        ("92233720368547758.07", "92233720368547758.07", 14),
    ],
)
def test_tl20_accepted_budgets(text, echo, total, ctx, tools_kit, spy):
    execution, payload = search(tools_kit, ctx, {"max_price_brl": text})
    assert_ok(execution, payload)
    assert payload["data"]["max_price_brl"] == echo
    assert payload["data"]["total_matches"] == total
    assert spy.opens == 1
    assert spy.all_closed()


def test_tl20_max_boundary_returns_every_available_product(ctx, catalog_kit, database, tools_kit):
    # Independent SQL: available = active with positive stock.
    [(available,)] = catalog_kit.query(
        database,
        "SELECT COUNT(*) FROM products WHERE status = 'active' AND stock_quantity > 0",
    )
    assert available == 14
    execution, payload = search(tools_kit, ctx, {"max_price_brl": "92233720368547758.07"})
    assert_ok(execution, payload)
    assert payload["data"]["total_matches"] == available
    assert ids(payload) == DEFAULT_PAGE


INVALID_FORMATS = [
    "-1",
    "01",
    "1.234",
    "1e3",
    "NaN",
    "Infinity",
    " 100",
    "100,00",
    "R$ 100",
    "",
    # Same approved pattern, applied with fullmatch (A6, G6).
    "100\n",
    "100 ",
    "١٠٠",
    ".5",
    "1.",
    "+1",
    "00",
    "-0",
]


@pytest.mark.parametrize("text", INVALID_FORMATS)
@pytest.mark.parametrize("database_state", ["present", "absent"])
def test_tl20_invalid_format(text, database_state, database, tools_kit, spy):
    context = (
        tools_kit.context(database_path=database)
        if database_state == "present"
        else tools_kit.context()
    )
    execution, _ = search(tools_kit, context, {"max_price_brl": text})
    # Tool-owned format check (G4 step 8): before any resource, whatever the database.
    assert_error(execution, "invalid_input", "invalid_format", "max_price_brl")
    assert spy.opens == 0
    if text.strip():
        assert text.strip() not in execution.payload


@pytest.mark.parametrize("text", ["92233720368547758.08", "99999999999999999999.99"])
def test_tl20_above_max_is_service_out_of_range(text, database, tools_kit, spy):
    execution, _ = search(
        tools_kit, tools_kit.context(database_path=database), {"max_price_brl": text}
    )
    assert_error(execution, "invalid_input", "out_of_range", "max_price_brl")
    assert spy.opens == 1
    assert spy.all_closed()
    # The budget range is the service's: with the database absent, data_unavailable.
    execution, _ = search(tools_kit, tools_kit.context(), {"max_price_brl": text})
    assert_error(execution, "unavailable", "data_unavailable")


def test_tl20_precedence_type_before_format_before_service(ctx, tools_kit, spy):
    execution, _ = search(tools_kit, ctx, {"max_price_brl": "01", "offset": True})
    assert_error(execution, "invalid_input", "invalid_type", "offset")
    execution, _ = search(tools_kit, ctx, {"offset": -1, "max_price_brl": "01"})
    assert_error(execution, "invalid_input", "invalid_format", "max_price_brl")
    execution, _ = search(tools_kit, ctx, {"query": "", "max_price_brl": "1e3"})
    assert_error(execution, "invalid_input", "invalid_format", "max_price_brl")
    assert spy.opens == 0


# --- TL21: text limits in code points, blank after normalization --------------------


@pytest.mark.parametrize("field", ["query", "category"])
@pytest.mark.parametrize("char", ["a", "ç", "𝄞"])
@pytest.mark.parametrize("ensure_ascii", [True, False], ids=["escaped", "raw"])
def test_tl21_text_length_in_code_points(field, char, ensure_ascii, ctx, tools_kit, spy):
    accepted = char * 4000
    rejected = char * 4001
    assert len(accepted) == 4000
    assert len(rejected) == 4001

    execution, payload = search(tools_kit, ctx, {field: accepted}, ensure_ascii=ensure_ascii)
    assert_ok(execution, payload)
    data = payload["data"]
    assert data["products"] == []
    assert data["total_matches"] == 0
    if field == "category":
        assert data["category_resolution"] == "unknown"
        assert data["candidate_categories"] == [VIOLOES, TECLAS, BAIXOS]
    else:
        assert data["category_resolution"] == "not_requested"
    assert char * 50 not in execution.payload

    execution, _ = search(tools_kit, ctx, {field: rejected}, ensure_ascii=ensure_ascii)
    assert_error(execution, "invalid_input", "too_long", field)
    assert spy.opens == 2
    assert spy.all_closed()


@pytest.mark.parametrize("field", ["query", "category"])
@pytest.mark.parametrize("text", ["   ", "́", "", "\t\n", "́́ ́"])
def test_tl21_blank_after_normalization(field, text, ctx, tools_kit, spy):
    execution, _ = search(tools_kit, ctx, {field: text})
    assert_error(execution, "invalid_input", "blank", field)
    assert spy.opens == 1


# --- TL36: default page, full projection, no query echo ------------------------------


def test_tl36_default_page_full_projection(ctx, tmp_path, catalog_kit, tools_kit, spy):
    execution, payload = search(tools_kit, ctx, {})
    assert_ok(execution, payload)
    assert payload["data"] == search_data(
        default_page_summaries(), 14, 0, 5, expected_provenance(tmp_path, catalog_kit)
    )
    assert all(product["is_available"] for product in payload["data"]["products"])
    assert payload["data"]["page_size"] == 5
    assert spy.opens == 1
    assert spy.all_closed()
    # Compact, deterministic, UTF-8 text kept literal.
    assert '"name":"Violão Ômega"' in execution.payload
    assert search(tools_kit, ctx, {})[0].payload == execution.payload


def test_tl36_query_is_never_echoed(ctx, tools_kit):
    sentinel = "consulta-sentinela-q7z"
    execution, payload = search(tools_kit, ctx, {"query": sentinel})
    assert_ok(execution, payload)
    assert payload["data"]["products"] == []
    assert payload["data"]["total_matches"] == 0
    assert sentinel not in execution.payload

    execution, payload = search(tools_kit, ctx, {"query": "folk beta"})
    assert_ok(execution, payload)
    assert ids(payload) == [202]
    assert payload["data"]["total_matches"] == 1
    assert payload["data"]["next_offset"] is None
    assert "folk beta" not in execution.payload


# --- TL37: pagination ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("offset", "expected_ids", "next_offset"),
    [
        (0, [211, 214, 212, 210, 201], 5),
        (5, [206, 213, 202, 204], None),
        (9, [], None),
        (10, [], None),
        (10**30, [], None),
    ],
)
def test_tl37_pagination(
    offset, expected_ids, next_offset, ctx, tmp_path, catalog_kit, tools_kit, spy
):
    arguments = {"category": "violões", "max_price_brl": "1000.00", "offset": offset}
    execution, payload = search(tools_kit, ctx, arguments)
    assert_ok(execution, payload)
    data = payload["data"]
    assert ids(payload) == expected_ids
    assert data["total_matches"] == 9
    assert data["offset"] == offset
    assert data["next_offset"] == next_offset
    assert data["page_size"] == 5
    assert data["max_price_brl"] == "1000.00"
    assert data["category_resolution"] == "resolved"
    assert data["category"] == VIOLOES
    assert data["candidate_categories"] == []
    assert data["provenance"] == expected_provenance(tmp_path, catalog_kit)
    assert spy.opens == 1
    assert spy.all_closed()


def test_tl37_first_page_literal(ctx, tmp_path, catalog_kit, tools_kit):
    execution, payload = search(tools_kit, ctx, {"category": "violões", "max_price_brl": "1000.00"})
    assert_ok(execution, payload)
    assert payload["data"] == search_data(
        default_page_summaries(),
        9,
        0,
        5,
        expected_provenance(tmp_path, catalog_kit),
        max_price="1000.00",
        resolution="resolved",
        category=VIOLOES,
    )


# --- TL38: unknown and ambiguous categories keep the filter --------------------------


@pytest.mark.parametrize(
    "extra",
    [{}, {"available_only": False}, {"max_price_brl": "92233720368547758.07"}, {"query": "violão"}],
)
def test_tl38_unknown_category(extra, ctx, tmp_path, catalog_kit, tools_kit, spy):
    execution, payload = search(tools_kit, ctx, {"category": "Ukuleles", **extra})
    assert_ok(execution, payload)
    assert payload["data"] == search_data(
        [],
        0,
        0,
        None,
        expected_provenance(tmp_path, catalog_kit),
        available_only=extra.get("available_only", True),
        max_price="92233720368547758.07" if "max_price_brl" in extra else None,
        resolution="unknown",
        candidates=[VIOLOES, TECLAS, BAIXOS],
    )
    assert "Ukuleles" not in execution.payload
    assert spy.opens == 1


def test_tl38_ambiguous_category(tmp_path, catalog_kit, tools_kit, spy):
    database = catalog_kit.build(tmp_path, "collision")
    context = tools_kit.context(database_path=database)
    provenance = expected_provenance(tmp_path, catalog_kit, categories=4)

    execution, payload = search(tools_kit, context, {"category": "VIOLÕES"})
    assert_ok(execution, payload)
    assert payload["data"] == search_data(
        [], 0, 0, None, provenance, resolution="ambiguous", candidates=[VIOLOES, COLLISION]
    )
    assert "VIOLÕES" not in execution.payload

    # The exact name selects one of the colliding categories.
    execution, payload = search(tools_kit, context, {"category": "Violões"})
    assert_ok(execution, payload)
    assert payload["data"]["category_resolution"] == "resolved"
    assert payload["data"]["category"] == VIOLOES
    assert ids(payload) == DEFAULT_PAGE
    execution, payload = search(tools_kit, context, {"category": "violoes"})
    assert_ok(execution, payload)
    assert payload["data"]["category"] == COLLISION
    assert payload["data"]["products"] == []
    assert payload["data"]["candidate_categories"] == []
    assert spy.opens == 3


# --- TL39: the budget filters the effective price, never the PIX price ---------------


def test_tl39_budget_uses_effective_price_without_pix(ctx, database, catalog_kit, tools_kit):
    # Independent SQL: fixture premises (cents) for 203, 204 and 205; no promotion on them.
    prices = dict(
        catalog_kit.query(
            database,
            "SELECT product_id, price_cents FROM products WHERE product_id IN (203, 204, 205)",
        )
    )
    assert prices == {203: 105000, 204: 100000, 205: 100001}
    assert catalog_kit.query(
        database, "SELECT COUNT(*) FROM promotions WHERE product_id IN (203, 204, 205)"
    ) == [(0,)]

    seen = []
    for offset in (0, 5):
        arguments = {"category": "violões", "max_price_brl": "1000.00", "offset": offset}
        execution, payload = search(tools_kit, ctx, arguments)
        assert_ok(execution, payload)
        seen.extend(ids(payload))
    assert seen == VIOLOES_1000
    assert 203 not in seen  # PIX price 997.50 never widens the budget
    assert 205 not in seen  # 1000.01
    assert 204 in seen  # exactly 1000.00

    execution, payload = search(tools_kit, ctx, {"category": "violões", "max_price_brl": "997.50"})
    assert_ok(execution, payload)
    assert payload["data"]["total_matches"] == 8
    assert payload["data"]["max_price_brl"] == "997.50"

    execution, payload = search(
        tools_kit, ctx, {"category": "violões", "max_price_brl": "1000.01", "offset": 5}
    )
    assert_ok(execution, payload)
    assert payload["data"]["total_matches"] == 10
    assert ids(payload) == [206, 213, 202, 204, 205]


# --- Pipeline, precedence and status mapping of slice A applied to search -------------


def test_search_pipeline_rejections(ctx, tools_kit, spy):
    execution, _ = search(tools_kit, ctx, {"query": "folk", "customer_id": 2})
    assert_error(execution, "invalid_input", "unexpected_field")
    assert "customer_id" not in execution.payload
    execution, _ = tools_kit.run(TOOL, '{"offset": 0, "offset": 5}', ctx)
    assert_error(execution, "invalid_input", "duplicate_key")
    execution, _ = tools_kit.run(TOOL, '{"query": "folk",}', ctx)
    assert_error(execution, "invalid_input", "invalid_json")
    execution, _ = tools_kit.run(TOOL, '{"offset": NaN}', ctx)
    assert_error(execution, "invalid_input", "invalid_json")
    execution, _ = tools_kit.run(TOOL, "[]", ctx)
    assert_error(execution, "invalid_input", "arguments_not_object")
    assert spy.opens == 0


@pytest.mark.parametrize(
    "arguments",
    [
        '{"offset": 1e999999999999999999999}',
        '{"max_price_brl": 1e-1999999999999999998}',
        '{"offset": 1, "offset": -1e999999999999999999999}',
        '{"x": [1.5E+1000000000000000000]}',
    ],
)
def test_search_extreme_exponent_stays_invalid_json(arguments, ctx, tools_kit, spy):
    """Review 06-02-R1 preserved: private trapping context, caller context untouched."""
    with decimal.localcontext() as caller:
        caller.traps[decimal.InvalidOperation] = False
        caller.clear_flags()
        execution, _ = tools_kit.run(TOOL, arguments, ctx)
        assert_error(execution, "invalid_input", "invalid_json")
        assert caller.traps[decimal.InvalidOperation] is False
        assert not any(caller.flags.values())
        assert decimal.getcontext() is caller
    before = decimal.getcontext()
    flags = dict(before.flags)
    execution, _ = tools_kit.run(TOOL, arguments, ctx)
    assert_error(execution, "invalid_input", "invalid_json")
    assert decimal.getcontext() is before
    assert dict(before.flags) == flags
    assert spy.opens == 0


def test_search_status_mapping(tmp_path, catalog_kit, tools_kit, spy):
    execution, _ = search(tools_kit, tools_kit.context(), {"category": "violões"})
    assert_error(execution, "unavailable", "data_unavailable")
    damaged = catalog_kit.build(tmp_path / "danificada")
    catalog_kit.corrupt(damaged, "price_text_other")
    execution, _ = search(tools_kit, tools_kit.context(database_path=damaged), {})
    assert_error(execution, "internal_error", "internal_error")
    assert spy.opens == 2
    assert spy.all_closed()
