"""Slice 06-A unit cases TL01-TL15, TL18, TL24 and TL32 (docs/plans/06-tools.md).

Every call goes through execute_tool with a ToolContext whose database and index paths
do not exist, so any premature resource access would answer data_unavailable. A local
spy wraps the real connect_readonly in emporio_agent.tools only and counts opens.
Expected payloads are literal JSON text written by hand; no production serializer is
used as an oracle. Nothing here mocks parsing, validation or persistence.
"""

import decimal
import json
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from importlib import import_module
from pathlib import Path

import pytest

FIXED = datetime(2026, 11, 3, 15, 0, tzinfo=UTC)
LIMIT = 131_072


def tools():
    # Imported inside each call so the initial RED is a failed test, not failed collection.
    return import_module("emporio_agent.tools")


def fixed_clock() -> datetime:
    return FIXED


@pytest.fixture
def absent(tmp_path):
    directory = tmp_path / "pasta-ausente-u8k"
    return tools().ToolContext(
        database_path=directory / "ausente.sqlite3",
        policy_index_path=directory / "ausente-indice.json",
        clock=fixed_clock,
        customer=None,
    )


@pytest.fixture
def opens(monkeypatch):
    """Delegating spy: counts connect_readonly calls made by the tools module."""
    module = tools()
    real = module.connect_readonly
    count = []

    @contextmanager
    def observed(database_path):
        count.append(database_path)
        with real(database_path) as connection:
            yield connection

    monkeypatch.setattr(module, "connect_readonly", observed)
    return count


def strict_constant(name: str) -> object:
    raise AssertionError(f"non-finite constant in payload: {name}")


def error_payload(tool: str | None, status: str, code: str, field: str | None) -> str:
    tool_text = "null" if tool is None else f'"{tool}"'
    field_text = "null" if field is None else f'"{field}"'
    return (
        f'{{"tool":{tool_text},"status":"{status}","data":null,'
        f'"error":{{"code":"{code}","field":{field_text}}}}}'
    )


def assert_rejected(execution, tool, code, field):
    """Exact literal envelope and metadata of an invalid_input result."""
    assert execution.payload == error_payload(tool, "invalid_input", code, field)
    assert execution.tool == tool
    assert execution.status == "invalid_input"
    assert execution.error_code == code
    json.loads(execution.payload, parse_constant=strict_constant)


def run(name, arguments, context):
    return tools().execute_tool(name, arguments, context)


# --- TL01: registry -------------------------------------------------------------------


def test_tl01_registry_of_slice_a():
    first = tools().tool_names()
    assert first == ("search_products", "get_product", "quote_product")
    assert type(first) is tuple
    assert tools().tool_names() == first


# --- TL02: unknown names are never echoed ---------------------------------------------

UNKNOWN_NAMES = [
    ("", None),
    ("GET_ORDER", "GET_ORDER"),
    ("get_order ", "get_order"),
    ("get_order\n", "get_order"),
    ("shell", "shell"),
    ("getOrder", "getOrder"),
    ("x" * 10_000, "xxxxxxxx"),
    ("get_product\u0000", "get_product"),
    ("Get_Product", "Get_Product"),
    (" get_product", "get_product"),
]


@pytest.mark.parametrize(("name", "distinctive"), UNKNOWN_NAMES)
def test_tl02_unknown_name(name, distinctive, absent, opens):
    execution = run(name, '{"product_id": 202}', absent)

    assert_rejected(execution, None, "unknown_tool", None)
    if distinctive is not None:
        assert distinctive not in execution.payload
    assert opens == []


# --- TL03: caller type errors ---------------------------------------------------------


def test_tl03_execute_tool_caller_types(absent):
    with pytest.raises(TypeError) as name_error:
        tools().execute_tool(1, "{}", absent)
    with pytest.raises(TypeError) as other_name_error:
        tools().execute_tool(None, "{}", absent)
    with pytest.raises(TypeError) as arguments_error:
        tools().execute_tool("get_product", b"{}", absent)
    with pytest.raises(TypeError) as other_arguments_error:
        tools().execute_tool("get_product", {"product_id": 1}, absent)
    with pytest.raises(TypeError) as context_error:
        tools().execute_tool("get_product", "{}", object())
    with pytest.raises(TypeError) as other_context_error:
        tools().execute_tool("get_product", "{}", None)

    # Fixed messages: independent of the rejected value, which never appears.
    assert str(name_error.value) == str(other_name_error.value)
    assert str(arguments_error.value) == str(other_arguments_error.value)
    assert str(context_error.value) == str(other_context_error.value)
    assert "product_id" not in str(other_arguments_error.value)
    assert "object at" not in str(context_error.value)


def test_tl03_tool_context_types(tmp_path):
    good = tmp_path / "x.sqlite3"
    cases = [
        {"database_path": str(good), "policy_index_path": good},
        {"database_path": good, "policy_index_path": str(good)},
        {"database_path": good, "policy_index_path": good, "clock": "agora"},
        {"database_path": good, "policy_index_path": good, "customer": 1},
    ]
    for overrides in cases:
        arguments = {"clock": fixed_clock, "customer": None, **overrides}
        with pytest.raises(TypeError) as caught:
            tools().ToolContext(**arguments)
        assert str(tmp_path) not in str(caught.value)
        assert "agora" not in str(caught.value)

    orders = import_module("emporio_agent.services.orders")
    context = tools().ToolContext(
        database_path=good,
        policy_index_path=good,
        clock=fixed_clock,
        customer=orders.CustomerContext(1),
    )
    assert context.customer == orders.CustomerContext(1)
    with pytest.raises(AttributeError):
        context.customer = None


# --- TL04-TL07: JSON parsing ----------------------------------------------------------


@pytest.mark.parametrize(
    "arguments",
    [
        "",
        " ",
        "{",
        "{'product_id': 1}",
        '{"product_id": 1} x',
        '﻿{"product_id": 1}',
        '{"product_id": 1,}',
        '{"product_id": 1',
        '{"product_id": 01}',
    ],
)
def test_tl04_invalid_json(arguments, absent, opens):
    assert_rejected(run("get_product", arguments, absent), "get_product", "invalid_json", None)
    assert opens == []


@pytest.mark.parametrize(
    "arguments", ["[]", "1", '"x"', "null", "true", '[{"product_id": 1}]', "1.5"]
)
def test_tl05_root_not_object(arguments, absent, opens):
    execution = run("get_product", arguments, absent)
    assert_rejected(execution, "get_product", "arguments_not_object", None)
    assert opens == []


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_tl06_non_finite_constants(constant, absent, opens):
    execution = run("get_product", f'{{"product_id": {constant}}}', absent)
    assert_rejected(execution, "get_product", "invalid_json", None)
    execution = run("quote_product", f'{{"product_id": 1, "payment_method": {constant}}}', absent)
    assert_rejected(execution, "quote_product", "invalid_json", None)
    assert opens == []


@pytest.mark.parametrize("number", ["1.0", "5.5", "-0.0", "1e400", "1E2", "202.0", "2e0"])
def test_tl07_fraction_or_exponent_is_not_an_id(number, absent, opens):
    execution = run("get_product", f'{{"product_id": {number}}}', absent)
    assert_rejected(execution, "get_product", "invalid_type", "product_id")
    execution = run("quote_product", f'{{"product_id": {number}, "payment_method": "pix"}}', absent)
    assert_rejected(execution, "quote_product", "invalid_type", "product_id")
    assert opens == []


# --- TL08: duplicate keys at any depth ------------------------------------------------


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("get_product", '{"product_id": 1, "product_id": 2}'),
        ("get_product", '{"product_id": 202, "product_id": 202}'),
        ("get_product", '{"x": 1, "x": 2}'),
        ("get_product", '{"product_id": {"a": 1, "a": 2}}'),
        ("get_product", '{"product_id": 1, "x": [{"k": 1, "k": 1}]}'),
        (
            "quote_product",
            '{"product_id": 1, "payment_method": "pix", "payment_method": "regular"}',
        ),
    ],
)
def test_tl08_duplicate_key(name, arguments, absent, opens):
    assert_rejected(run(name, arguments, absent), name, "duplicate_key", None)
    assert opens == []


# --- TL09: unexpected keys are never echoed -------------------------------------------

EXTRA_KEYS = [
    "customer_id",
    "customerId",
    "PRODUCT_ID",
    "product_id ",
    "__proto__",
    "",
    "k" * 4000,
    "$where",
    "database_path",
    "clock",
]


@pytest.mark.parametrize("key", EXTRA_KEYS)
def test_tl09_unexpected_field(key, absent, opens):
    arguments = json.dumps({"product_id": 202, key: 2})
    execution = run("get_product", arguments, absent)
    assert_rejected(execution, "get_product", "unexpected_field", None)
    if key.strip():
        assert key.strip() not in execution.payload
    quote = json.dumps({"product_id": 202, "payment_method": "pix", key: 2})
    assert_rejected(run("quote_product", quote, absent), "quote_product", "unexpected_field", None)
    assert opens == []


# --- TL10-TL14: presence and JSON types -----------------------------------------------


@pytest.mark.parametrize(
    ("name", "arguments", "field"),
    [
        ("get_product", "{}", "product_id"),
        ("quote_product", '{"product_id": 201}', "payment_method"),
        ("quote_product", '{"payment_method": "pix"}', "product_id"),
        ("quote_product", "{}", "product_id"),
    ],
)
def test_tl10_missing(name, arguments, field, absent, opens):
    assert_rejected(run(name, arguments, absent), name, "missing", field)
    assert opens == []


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("get_product", '{"product_id": true}'),
        ("get_product", '{"product_id": false}'),
        ("quote_product", '{"product_id": true, "payment_method": "pix"}'),
        ("quote_product", '{"product_id": false, "payment_method": "regular"}'),
    ],
)
def test_tl11_boolean_is_not_an_id(name, arguments, absent, opens):
    assert_rejected(run(name, arguments, absent), name, "invalid_type", "product_id")
    assert opens == []


@pytest.mark.parametrize(
    ("name", "arguments", "field"),
    [
        ("get_product", '{"product_id": "202"}', "product_id"),
        ("get_product", '{"product_id": "0x10"}', "product_id"),
        ("quote_product", '{"product_id": "201", "payment_method": "pix"}', "product_id"),
        ("quote_product", '{"product_id": 201, "payment_method": 1}', "payment_method"),
        ("quote_product", '{"product_id": 201, "payment_method": true}', "payment_method"),
    ],
)
def test_tl12_wrong_scalar_type(name, arguments, field, absent, opens):
    assert_rejected(run(name, arguments, absent), name, "invalid_type", field)
    assert opens == []


@pytest.mark.parametrize(
    ("name", "arguments", "field"),
    [
        ("get_product", '{"product_id": null}', "product_id"),
        ("quote_product", '{"product_id": null, "payment_method": "pix"}', "product_id"),
        ("quote_product", '{"product_id": 201, "payment_method": null}', "payment_method"),
    ],
)
def test_tl13_null_is_not_absent(name, arguments, field, absent, opens):
    assert_rejected(run(name, arguments, absent), name, "invalid_type", field)
    assert opens == []


@pytest.mark.parametrize(
    ("name", "arguments", "field"),
    [
        ("get_product", '{"product_id": {"$gt": 0}}', "product_id"),
        ("get_product", '{"product_id": [202]}', "product_id"),
        ("quote_product", '{"product_id": 201, "payment_method": ["pix"]}', "payment_method"),
        ("quote_product", '{"product_id": 201, "payment_method": {"v": "pix"}}', "payment_method"),
    ],
)
def test_tl14_container_is_not_a_scalar(name, arguments, field, absent, opens):
    execution = run(name, arguments, absent)
    assert_rejected(execution, name, "invalid_type", field)
    assert "$gt" not in execution.payload
    assert opens == []


# --- TL15: parser resource limits -----------------------------------------------------


def test_tl15_deep_nesting_and_huge_integer(absent, opens):
    deep = "[" * 100_000
    assert len(deep) <= LIMIT
    assert_rejected(run("get_product", deep, absent), "get_product", "invalid_json", None)

    nested = '{"product_id": ' + "[" * 100_000
    assert_rejected(run("get_product", nested, absent), "get_product", "invalid_json", None)

    digits = "9" * 5000
    execution = run("get_product", f'{{"product_id": {digits}}}', absent)
    assert_rejected(execution, "get_product", "invalid_json", None)
    assert "99999" not in execution.payload
    assert opens == []


# Review 06-02-R1: exponents outside the limits of the decimal implementation
# (64-bit: decimal.MAX_EMAX = 999999999999999999, decimal.MIN_ETINY =
# -1999999999999999997). Probed: 1e-1999999999999999997 converts, 1e-1999999999999999998
# raises decimal.InvalidOperation.
EXTREME_NUMBERS = [
    "1e999999999999999999999",
    "1e-999999999999999999999",
    "-1e999999999999999999999",
    "1.5E+1000000000000000000",
    "1e-1999999999999999998",
    "0e999999999999999999999",
]


def all_signals_context(**changes) -> decimal.Context:
    context = decimal.Context(**changes)
    for signal in context.traps:
        context.traps[signal] = True
    return context


@pytest.mark.parametrize("number", EXTREME_NUMBERS)
def test_tl15_extreme_exponent_is_invalid_json(number, absent, opens):
    cases = [
        ("get_product", f'{{"product_id": {number}}}'),
        ("quote_product", f'{{"product_id": {number}, "payment_method": "pix"}}'),
        ("quote_product", f'{{"product_id": 201, "payment_method": {number}}}'),
        # Step 3 comes before duplicates (3b), unknown keys (5) and missing fields (6).
        ("get_product", f'{{"product_id": 1, "product_id": {number}}}'),
        ("get_product", f'{{"product_id": 1, "x": [{number}]}}'),
        ("quote_product", f'{{"x": {{"y": {number}}}}}'),
    ]
    before = decimal.getcontext()
    flags = dict(before.flags)
    traps = dict(before.traps)
    for name, arguments in cases:
        execution = run(name, arguments, absent)
        assert_rejected(execution, name, "invalid_json", None)
        for detail in ("Decimal", "decimal", "Invalid", "999999", "000000"):
            assert detail not in execution.payload
    assert opens == []
    # The caller's decimal context is the same object, with unchanged flags and traps.
    assert decimal.getcontext() is before
    assert dict(before.flags) == flags
    assert dict(before.traps) == traps


@pytest.mark.parametrize("number", EXTREME_NUMBERS)
def test_tl15_extreme_exponent_ignores_caller_traps(number, absent, opens):
    """Without the InvalidOperation trap, Decimal(text) would return NaN silently."""
    with decimal.localcontext() as caller:
        caller.traps[decimal.InvalidOperation] = False
        caller.clear_flags()
        execution = run("get_product", f'{{"product_id": {number}}}', absent)
        assert_rejected(execution, "get_product", "invalid_json", None)
        assert caller.traps[decimal.InvalidOperation] is False
        assert not any(caller.flags.values())
        assert decimal.getcontext() is caller
    assert opens == []


@pytest.mark.parametrize(
    "caller",
    [
        decimal.Context(traps=[]),
        all_signals_context(prec=1, Emax=1, Emin=-1),
    ],
    ids=["no_traps", "all_traps_tiny"],
)
def test_tl15_representable_numbers_ignore_caller_context(caller, absent, opens):
    """Numbers inside the implementation limits stay invalid_type under any caller context."""
    numbers = [
        "1.0",
        "5.5",
        "-0.0",
        "1e400",
        "1E2",
        "1e999999999999999999",
        "1e-999999999999999999",
        "2e-1000000000000000000",
        "1e-1999999999999999997",
    ]
    with decimal.localcontext(caller) as active:
        active.clear_flags()
        for number in numbers:
            execution = run("get_product", f'{{"product_id": {number}}}', absent)
            assert_rejected(execution, "get_product", "invalid_type", "product_id")
        assert not any(active.flags.values())
    assert opens == []


# --- TL18: precedence of the pipeline -------------------------------------------------


@pytest.mark.parametrize(
    ("name", "arguments", "code", "field"),
    [
        # oversized + invalid JSON
        ("get_product", "{" + " " * LIMIT, "arguments_too_large", None),
        # oversized + valid JSON, exactly one character over the limit
        ("get_product", '{"product_id": 1}'.ljust(LIMIT + 1), "arguments_too_large", None),
        # invalid JSON after a duplicate key
        ("get_product", '{"product_id": 1, "product_id": 2} x', "invalid_json", None),
        # duplicate + extra
        ("get_product", '{"product_id": 1, "product_id": 2, "x": 1}', "duplicate_key", None),
        # duplicate + root not object is impossible; duplicate inside an array root
        ("get_product", '[{"a": 1, "a": 2}]', "duplicate_key", None),
        # extra + missing
        ("get_product", '{"x": 1}', "unexpected_field", None),
        # extra + wrong type
        ("get_product", '{"product_id": true, "x": 1}', "unexpected_field", None),
        # missing + wrong type
        ("quote_product", '{"product_id": true}', "missing", "payment_method"),
        # wrong type in two fields: first field in schema order
        (
            "quote_product",
            '{"product_id": true, "payment_method": 1}',
            "invalid_type",
            "product_id",
        ),
        (
            "quote_product",
            '{"payment_method": 1, "product_id": true}',
            "invalid_type",
            "product_id",
        ),
    ],
)
def test_tl18_first_failure_in_pipeline_order(name, arguments, code, field, absent, opens):
    assert_rejected(run(name, arguments, absent), name, code, field)
    assert opens == []


def test_tl18_unknown_name_before_size(absent, opens):
    execution = run("shell", "{" * (LIMIT + 1), absent)
    assert_rejected(execution, None, "unknown_tool", None)
    assert opens == []


# --- TL24: two-place decimal text -----------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("1.5"), "1.50"),
        (Decimal("-0.00"), "0.00"),
        (Decimal("-0"), "0.00"),
        (Decimal("10"), "10.00"),
        (Decimal("12.5"), "12.50"),
        (Decimal("990.00"), "990.00"),
        (Decimal("92233720368547758.07"), "92233720368547758.07"),
        (Decimal("1E+1"), "10.00"),
    ],
)
def test_tl24_two_places(value, expected):
    assert tools()._two_places(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        Decimal("1.005"),
        Decimal("NaN"),
        Decimal("sNaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
        Decimal("0.001"),
        1.5,
        10,
        "10.00",
    ],
)
def test_tl24_two_places_rejects(value):
    with pytest.raises((TypeError, ValueError)):
        tools()._two_places(value)


# --- TL32: invalid arguments never reach a resource -----------------------------------

TL32_CASES = [
    ("get_product", ""),
    ("get_product", "{"),
    ("get_product", '﻿{"product_id": 1}'),
    ("get_product", "[]"),
    ("get_product", "null"),
    ("get_product", '{"product_id": NaN}'),
    ("get_product", '{"product_id": 1e400}'),
    ("get_product", '{"product_id": 1.0}'),
    ("get_product", '{"product_id": 1, "product_id": 2}'),
    ("get_product", '{"product_id": 202, "customer_id": 2}'),
    ("get_product", "{}"),
    ("get_product", '{"product_id": true}'),
    ("get_product", '{"product_id": "202"}'),
    ("get_product", '{"product_id": null}'),
    ("get_product", '{"product_id": [202]}'),
    ("get_product", "[" * 100_000),
    ("get_product", '{"product_id": ' + "9" * 5000 + "}"),
    ("get_product", " " * (LIMIT + 1)),
    ("quote_product", '{"product_id": 201}'),
    ("quote_product", '{"product_id": 201, "payment_method": 1}'),
    ("quote_product", '{"product_id": 201, "payment_method": null}'),
    ("quote_product", '{"product_id": 201, "payment_method": "pix", "payment_method": "pix"}'),
]


@pytest.mark.parametrize(("name", "arguments"), TL32_CASES)
def test_tl32_invalid_arguments_open_nothing(name, arguments, absent, opens):
    execution = run(name, arguments, absent)

    assert execution.status == "invalid_input"
    assert execution.error_code != "data_unavailable"
    assert "data_unavailable" not in execution.payload
    assert opens == []
    assert not Path(absent.database_path).parent.exists()


def test_tl32_valid_arguments_reach_the_resource(absent, opens):
    """Control: the same absent context does answer data_unavailable once valid."""
    execution = run("get_product", '{"product_id": 202}', absent)
    assert execution.payload == error_payload(
        "get_product", "unavailable", "data_unavailable", None
    )
    assert len(opens) == 1
