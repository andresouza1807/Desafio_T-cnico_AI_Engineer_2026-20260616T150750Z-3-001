"""Slice 06-D integration cases TL61-TL65 and the result-size paths (docs/plans/06-tools.md).

Argument and result limits through execute_tool against real SQLite files built by the
real prepare_database from synthetic sources (catalog_kit, tools_big) and real policy
indexes built by the real prepare_policy_index from policy_kit PDFs. Every expected
size is computed from a literal envelope written in this module and serialized with the
standard library json module under the contracted format (spec G9); the production
serializer never computes an expected value. Expected contents are compared in full.
Nothing here mocks SQL, ranking, authorization or persistence.
"""

import hashlib
import json

import pytest

from emporio_agent.services.orders import CustomerContext

LIMIT_BYTES = 32_768
LIMIT_CHARS = 131_072
C1 = CustomerContext(1)
C2 = CustomerContext(2)
PROVENANCE_TABLES = (("categories", 3), ("products", 17), ("promotions", 9))


def too_large(tool):
    return (
        f'{{"tool":"{tool}","status":"unavailable","data":null,'
        '"error":{"code":"result_too_large","field":null}}'
    )


def contract_json(envelope):
    """Independent rendering of the contracted format (spec G9), standard library only."""
    return json.dumps(envelope, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def utf8_size(envelope):
    return len(contract_json(envelope).encode("utf-8"))


def assert_too_large(execution, tool):
    assert execution.payload == too_large(tool)
    assert execution.tool == tool
    assert execution.status == "unavailable"
    assert execution.error_code == "result_too_large"


def assert_ok_exact(execution, tool, envelope):
    assert execution.tool == tool
    assert execution.status == "ok"
    assert execution.error_code is None
    assert execution.payload == contract_json(envelope)


@pytest.fixture
def spy(monkeypatch, tools_kit):
    return tools_kit.connect_spy(monkeypatch)


# --- Independent envelope of get_product 202 ------------------------------------------


def catalog_provenance(base, catalog_kit):
    sources = []
    for table, row_count in PROVENANCE_TABLES:
        written = (base / catalog_kit.SOURCE_DIR / catalog_kit.file_name(table)).read_bytes()
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


def product_202_envelope(base, catalog_kit, description):
    return {
        "tool": "get_product",
        "status": "ok",
        "data": {
            "product": {
                "product_id": 202,
                "name": "Violão Folk Beta",
                "category": {"category_id": 1, "name": "Violões"},
                "list_price": "1100.00",
                "effective_price": "990.00",
                "promotion": {
                    "promotion_id": 301,
                    "discount_percent": "10.00",
                    "description": "Promo Folk",
                },
                "stock_quantity": 2,
                "status": "active",
                "availability": "available",
                "is_available": True,
                "quality_notes": [],
            },
            "description": description,
            "specs": {},
            "withheld_fields": [],
            "provenance": catalog_provenance(base, catalog_kit),
        },
        "error": None,
    }


def sized_catalog(tmp_path, tools_big, catalog_kit, target, prefix=""):
    """Build product 202 with description prefix + "a" * n so that the independent
    expected envelope has exactly target UTF-8 bytes. The CSV size inside provenance
    depends on n, so n is re-derived from each built source until the size is exact."""
    count = 1
    for attempt in range(6):
        base = tmp_path / f"calibra-{target}-{attempt}"
        database = tools_big.catalog(base, count, "a", prefix)
        envelope = product_202_envelope(base, catalog_kit, prefix + "a" * count)
        delta = target - utf8_size(envelope)
        if delta == 0:
            return database, envelope
        count += delta
        assert count >= 1
    raise AssertionError("calibration did not converge")


def get_202(tools_kit, database):
    return tools_kit.run("get_product", '{"product_id": 202}', tools_kit.context(database))


# --- TL61: argument limit -------------------------------------------------------------


def padded_get_product(total):
    head, tail = '{"product_id": 202', "}"
    return head + " " * (total - len(head) - len(tail)) + tail


def test_tl61_argument_limit_exact_and_plus_one(tmp_path, catalog_kit, tools_kit, spy):
    exact = padded_get_product(LIMIT_CHARS)
    over = padded_get_product(LIMIT_CHARS + 1)
    assert (len(exact), len(over)) == (LIMIT_CHARS, LIMIT_CHARS + 1)

    # Absent database: the exact limit is parsed and reaches the resource.
    execution, body = tools_kit.run("get_product", exact, tools_kit.context())
    assert (execution.status, execution.error_code) == ("unavailable", "data_unavailable")
    assert spy.opens == 1

    execution, body = tools_kit.run("get_product", over, tools_kit.context())
    assert execution.payload == (
        '{"tool":"get_product","status":"invalid_input","data":null,'
        '"error":{"code":"arguments_too_large","field":null}}'
    )
    assert spy.opens == 1  # rejected before any resource

    database = catalog_kit.build(tmp_path)
    execution, body = tools_kit.run("get_product", exact, tools_kit.context(database))
    assert execution.status == "ok"
    assert body["data"]["product"]["product_id"] == 202
    assert spy.opens == 2
    assert spy.all_closed()


def test_tl61_escaped_worst_case_is_not_rejected_by_the_raw_limit(
    tmp_path, catalog_kit, tools_kit, spy
):
    escaped = "\\ud834\\udd1e" * 4000  # 4,000 code points of U+1D11E as surrogate escapes
    arguments = (
        f'{{"query": "{escaped}", "category": "{escaped}", "max_price_brl": "1000.00",'
        ' "available_only": true, "offset": 0}'
    )
    assert 96_000 < len(arguments) < LIMIT_CHARS
    assert json.loads(arguments)["query"] == "\U0001d11e" * 4000

    execution, body = tools_kit.run(
        "search_products", arguments, tools_kit.context(catalog_kit.build(tmp_path))
    )
    assert execution.status == "ok"
    assert body["data"]["products"] == []
    assert body["data"]["category_resolution"] == "unknown"
    assert "\U0001d11e" not in execution.payload  # never echoed
    assert spy.opens == 1
    assert spy.all_closed()


# --- TL62: complete envelope, exactly at the limit and one byte over ------------------


def test_tl62_exact_limit_is_returned_whole(tmp_path, catalog_kit, tools_kit, tools_big, spy):
    database, envelope = sized_catalog(tmp_path, tools_big, catalog_kit, LIMIT_BYTES)
    description = envelope["data"]["description"]
    assert len(description) > 31_000

    execution, body = get_202(tools_kit, database)

    assert_ok_exact(execution, "get_product", envelope)
    assert len(execution.payload.encode("utf-8")) == LIMIT_BYTES
    assert body["data"]["description"] == description
    # The data object alone is smaller than the limit: the whole envelope is measured.
    assert len(contract_json(envelope["data"]).encode("utf-8")) < LIMIT_BYTES
    assert spy.all_closed()


def test_tl62_one_byte_over_is_replaced_whole(tmp_path, catalog_kit, tools_kit, tools_big, spy):
    database, envelope = sized_catalog(tmp_path, tools_big, catalog_kit, LIMIT_BYTES + 1)
    # The data object alone still fits: a data-only measure would return ok.
    assert len(contract_json(envelope["data"]).encode("utf-8")) < LIMIT_BYTES

    execution, body = get_202(tools_kit, database)

    assert_too_large(execution, "get_product")
    assert body == {
        "tool": "get_product",
        "status": "unavailable",
        "data": None,
        "error": {"code": "result_too_large", "field": None},
    }
    assert "aaaa" not in execution.payload
    assert "Violão" not in execution.payload
    assert spy.opens == 1
    assert spy.all_closed()


# --- TL63: UTF-8 bytes, not characters ------------------------------------------------


def test_tl63_multibyte_exact_limit(tmp_path, catalog_kit, tools_kit, tools_big):
    database, envelope = sized_catalog(tmp_path, tools_big, catalog_kit, LIMIT_BYTES, "ç" * 12_000)
    expected = contract_json(envelope)
    assert len(expected.encode("utf-8")) == LIMIT_BYTES
    assert len(expected) < LIMIT_BYTES - 12_000  # characters differ from bytes

    execution, body = get_202(tools_kit, database)

    assert_ok_exact(execution, "get_product", envelope)
    assert body["data"]["description"].startswith("ç" * 12_000)


def test_tl63_multibyte_one_byte_over(tmp_path, catalog_kit, tools_kit, tools_big):
    database, envelope = sized_catalog(
        tmp_path, tools_big, catalog_kit, LIMIT_BYTES + 1, "ç" * 12_000
    )
    assert len(contract_json(envelope)) < LIMIT_BYTES  # a character count would accept it

    execution, _ = get_202(tools_kit, database)

    assert_too_large(execution, "get_product")


def test_tl63_characters_under_limit_bytes_over(tmp_path, catalog_kit, tools_kit, tools_big):
    base = tmp_path / "cedilha"
    database = tools_big.catalog(base, 16_500, "ç")
    envelope = product_202_envelope(base, catalog_kit, "ç" * 16_500)
    expected = contract_json(envelope)
    assert len(expected) < LIMIT_BYTES < len(expected.encode("utf-8"))

    execution, _ = get_202(tools_kit, database)

    assert_too_large(execution, "get_product")


# --- TL64: large authorized order, foreign order --------------------------------------


def big_order_provenance(base, tools_big, c1_items, c2_items):
    counts = (("orders", 2), ("order_items", c1_items + c2_items), ("products", c2_items))
    sources = []
    for table, row_count in counts:
        written = (
            base / tools_big.SOURCE_DIR / f"desafio_tecnico_ai_eng - {table}.csv"
        ).read_bytes()
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


def big_order_envelope(base, tools_big, order_id, items, total, tracking, c1_items, c2_items):
    return {
        "tool": "get_order",
        "status": "ok",
        "data": {
            "order_id": order_id,
            "status": "shipped",
            "items": [
                {
                    "product_id": product_id,
                    "name": tools_big.item_name(product_id),
                    "quantity": tools_big.item_quantity(product_id),
                    "name_source": "current_catalog",
                }
                for product_id in range(1, items + 1)
            ],
            "total": total,
            "tracking_code": tracking,
            "estimated_delivery": "2026-11-05",
            "estimate_status": "upcoming",
            "business_date": "2026-11-03",
            "provenance": big_order_provenance(base, tools_big, c1_items, c2_items),
        },
        "error": None,
    }


NOT_ACCESSIBLE = (
    '{"tool":"get_order","status":"unavailable","data":null,'
    '"error":{"code":"order_not_accessible","field":null}}'
)


def test_tl64_large_authorized_order_and_foreign_order(tmp_path, tools_kit, tools_big, spy):
    base = tmp_path / "pedido-grande"
    database = tools_big.order(base, 300, 450)
    c1_expected = big_order_envelope(
        base, tools_big, tools_big.ORDER_C1, 300, "100.00", "TRK-7001", 300, 450
    )
    c2_expected = big_order_envelope(
        base, tools_big, tools_big.ORDER_C2, 450, "200.00", "TRK-8001", 300, 450
    )
    # Independent calibration: C1 fits, C2 does not.
    assert 20_000 < utf8_size(c1_expected) <= LIMIT_BYTES < utf8_size(c2_expected)

    def lookup(customer, order_id):
        context = tools_kit.context(database_path=database, customer=customer)
        return tools_kit.run("get_order", json.dumps({"order_id": order_id}), context)

    own, body = lookup(C1, tools_big.ORDER_C1)
    assert_ok_exact(own, "get_order", c1_expected)
    assert len(body["data"]["items"]) == 300
    assert [entry["product_id"] for entry in body["data"]["items"]] == list(range(1, 301))

    foreign, _ = lookup(C1, tools_big.ORDER_C2)
    missing, _ = lookup(C1, 9999)
    assert foreign.payload == NOT_ACCESSIBLE
    assert missing.payload == NOT_ACCESSIBLE
    assert (foreign.status, foreign.error_code) == ("unavailable", "order_not_accessible")

    large, _ = lookup(C2, tools_big.ORDER_C2)
    assert_too_large(large, "get_order")
    assert "Item Sintético" not in large.payload

    assert spy.opens == 4
    assert spy.all_closed()


# --- TL65: pagination and section limit are kept, not truncation ----------------------


def stored_section(document, section_id):
    """Independent read of one section of the index JSON written by prepare."""
    by_id = {section["section_id"]: section for section in document["sections"]}
    section = by_id[section_id]
    return section["title"], "\n".join(text for _, text in section["lines"])


def test_tl65_search_page_and_policy_sections(tmp_path, catalog_kit, policy_kit, tools_kit):
    database = catalog_kit.build(tmp_path / "catalogo")
    execution, body = tools_kit.run("search_products", "{}", tools_kit.context(database))
    assert execution.status == "ok"
    data = body["data"]
    assert len(data["products"]) == 5
    assert data["page_size"] == 5
    assert data["next_offset"] == 5
    assert data["total_matches"] > 5

    index_path = tools_kit.policy_index(policy_kit, tmp_path / "politicas")
    execution, body = tools_kit.run(
        "search_policies", '{"query": "00"}', tools_kit.context(policy_index_path=index_path)
    )
    assert execution.status == "ok"
    data = body["data"]
    assert [section["section_id"] for section in data["sections"]] == ["3.1", "5.1", "2"]
    assert data["omitted_section_ids"] == ["3"]
    more = [notice for notice in data["notices"] if notice["code"] == "more_sections_matched"]
    assert len(more) == 1
    assert more[0]["section_ids"] == ["3"]
    document = json.loads(index_path.read_text(encoding="utf-8"))
    for section in data["sections"]:
        title, text = stored_section(document, section["section_id"])
        assert section["title"] == title
        assert section["text"] == text  # whole section text, never cut


# --- Result limit on policy paths, including not_found with data ----------------------


def edited_index(tools_kit, policy_kit, directory, edit):
    """Real index built by prepare, then one value changed with the standard json module."""
    index_path = tools_kit.policy_index(policy_kit, directory)
    document = json.loads(index_path.read_text(encoding="utf-8"))
    edit(document)
    index_path.write_text(json.dumps(document, ensure_ascii=True), encoding="utf-8")
    return index_path


def long_file_name(document):
    document["source"]["file_name"] = "m" * 33_000 + ".pdf"


def test_policy_not_found_with_data_is_measured(tmp_path, policy_kit, tools_kit):
    index_path = edited_index(tools_kit, policy_kit, tmp_path, long_file_name)
    context = tools_kit.context(policy_index_path=index_path)

    execution, _ = tools_kit.run(
        "search_policies", '{"query": "qual a capital da França?"}', context
    )

    assert_too_large(execution, "search_policies")
    assert "mmmm" not in execution.payload


def test_policy_ok_over_limit_is_replaced(tmp_path, policy_kit, tools_kit):
    def long_line(document):
        section = next(item for item in document["sections"] if item["section_id"] == "5.1")
        section["lines"][0][1] += " frete" * 6_000

    index_path = edited_index(tools_kit, policy_kit, tmp_path, long_line)
    context = tools_kit.context(policy_index_path=index_path)

    execution, _ = tools_kit.run("search_policies", '{"query": "frete grátis"}', context)

    assert_too_large(execution, "search_policies")


def test_policy_lone_surrogate_is_internal_error(tmp_path, policy_kit, tools_kit):
    def surrogate(document):
        section = next(item for item in document["sections"] if item["section_id"] == "5.1")
        section["lines"][0][1] += " \ud800"

    index_path = edited_index(tools_kit, policy_kit, tmp_path, surrogate)
    assert "\\ud800" in index_path.read_text(encoding="utf-8")
    context = tools_kit.context(policy_index_path=index_path)

    execution, _ = tools_kit.run("search_policies", '{"query": "frete grátis"}', context)

    assert execution.payload == (
        '{"tool":"search_policies","status":"internal_error","data":null,'
        '"error":{"code":"internal_error","field":null}}'
    )
    assert (execution.status, execution.error_code) == ("internal_error", "internal_error")
    execution.payload.encode("utf-8")  # the payload itself is always encodable
