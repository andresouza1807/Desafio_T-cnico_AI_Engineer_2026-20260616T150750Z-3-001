"""Field parser contract from docs/specs/02-data-preparation.md (cases D20-D34).

Pure behavior: no files, no database, no mocks. Expected values are written by hand
from the specification, not computed with the code under test.
"""

import traceback
from decimal import ROUND_DOWN, Decimal, getcontext, localcontext
from importlib import import_module

import pytest

SQLITE_MAX_INTEGER = 9223372036854775807
SENTINEL = "SENTINEL-PII-7781"


def parsing():
    # Import inside a call so a missing module is a failed test, not failed collection.
    return import_module("emporio_agent.data.parsing")


def errors():
    return import_module("emporio_agent.data.errors")


def rejection(parser_name, *args):
    """Call a parser that must fail and return its FieldError."""
    field_error = errors().FieldError
    parser = getattr(parsing(), parser_name)
    with pytest.raises(field_error) as caught:
        parser(*args)
    return caught.value


def assert_code(parser_name, text, expected_code, *extra):
    error = rejection(parser_name, text, *extra)
    assert isinstance(error.code, errors().IssueCode)
    assert error.code.value == expected_code


# FieldError and IssueCode contract (spec "Service Contract"; prerequisite for D60).

EXPECTED_ISSUE_CODES = {
    "source_dir_missing",
    "file_missing",
    "file_unreadable",
    "encoding_invalid",
    "csv_malformed",
    "header_missing",
    "header_duplicate",
    "header_mismatch",
    "field_count",
    "value_required",
    "value_blank",
    "invalid_integer",
    "out_of_range",
    "invalid_money",
    "money_out_of_range",
    "invalid_percent",
    "invalid_date",
    "invalid_flag",
    "invalid_enum",
    "invalid_json",
    "duplicate_key",
    "broken_reference",
    "target_directory_missing",
    "target_write_failed",
    "database_build_failed",
    "integrity_check_failed",
}


def test_issue_codes_match_specification():
    issue_code = errors().IssueCode
    assert {member.value for member in issue_code} == EXPECTED_ISSUE_CODES
    assert issue_code.INVALID_MONEY == "invalid_money"


def test_field_error_public_representation():
    module = errors()
    error = module.FieldError(module.IssueCode.INVALID_MONEY)
    assert isinstance(error, ValueError)
    assert error.code is module.IssueCode.INVALID_MONEY
    assert error.args == ("invalid_money",)
    assert str(error) == "invalid_money"
    assert repr(error) == "FieldError('invalid_money')"
    assert vars(error) == {"code": module.IssueCode.INVALID_MONEY}


@pytest.mark.parametrize(
    ("parser_name", "args"),
    [
        ("parse_id", (SENTINEL,)),
        ("parse_count", (SENTINEL,)),
        ("parse_money_cents", (SENTINEL,)),
        ("parse_percent_basis_points", (SENTINEL,)),
        ("parse_date", (SENTINEL,)),
        ("parse_optional_date", (SENTINEL,)),
        ("parse_flag", (SENTINEL,)),
        ("parse_enum", (SENTINEL, frozenset({"active"}))),
        ("parse_json_object", ('{"note": "' + SENTINEL + '"',)),
        ("parse_json_object", ('{"a": "' + SENTINEL + '", "a": 1}',)),
        ("parse_json_object", ('["' + SENTINEL + '"]',)),
    ],
)
def test_field_error_public_surfaces_hide_rejected_value(parser_name, args):
    # Covers str, repr, args, attributes and format_exception_only only.
    # The full traceback and __context__ are outside the guarantee (spec "Privacy").
    error = rejection(parser_name, *args)
    surfaces = [
        str(error),
        repr(error),
        repr(error.args),
        repr(vars(error)),
        "".join(traceback.format_exception_only(error)),
    ]
    for surface in surfaces:
        assert SENTINEL not in surface
    assert str(error) == error.code.value
    assert error.__cause__ is None
    if error.__context__ is not None:
        assert error.__suppress_context__ is True


# D20: exact cents.


@pytest.mark.parametrize(
    ("text", "cents"),
    [
        ("599.9", 59990),
        ("19.90", 1990),
        ("0", 0),
        ("0.0", 0),
        ("0.00", 0),
        ("1", 100),
        ("10.5", 1050),
        ("123.45", 12345),
    ],
)
def test_d20_money_to_exact_cents(text, cents):
    result = parsing().parse_money_cents(text)
    assert type(result) is int
    assert result == cents


# D21: money grammar rejections.


@pytest.mark.parametrize(
    "text",
    [
        "-1",
        "+1",
        "1e3",
        "NaN",
        "Infinity",
        "1,50",
        "1.000,00",
        " 1",
        "1 ",
        "1\n",
        "1.",
        ".5",
        "01",
        "00",
        "1.234",
        "0.001",
        "R$1",
        "١",  # ARABIC-INDIC DIGIT ONE
    ],
)
def test_d21_money_rejected_by_grammar(text):
    assert_code("parse_money_cents", text, "invalid_money")


def test_d21_empty_money_is_required():
    assert_code("parse_money_cents", "", "value_required")


# D22: SQLite INTEGER limit and grammar/length precedence.


def test_d22_largest_money_value_fits_sqlite_integer():
    assert parsing().parse_money_cents("92233720368547758.07") == SQLITE_MAX_INTEGER


@pytest.mark.parametrize(
    "text",
    [
        "92233720368547758.08",
        "92233720368547759",
        "1" + "0" * 39,  # 40 characters: passes the length rule, fails the value limit
        "1" + "0" * 40,  # 41 characters, valid grammar
        "9" * 5000,
    ],
)
def test_d22_money_above_sqlite_limit(text):
    assert_code("parse_money_cents", text, "money_out_of_range")


def test_d22_grammar_is_checked_before_length():
    text = "-" + "1" * 40
    assert len(text) == 41
    assert_code("parse_money_cents", text, "invalid_money")


# D23: no float path.


@pytest.mark.parametrize(("text", "cents"), [("0.1", 10), ("1.15", 115), ("0.29", 29)])
def test_d23_float_trap_values_are_exact(text, cents):
    assert parsing().parse_money_cents(text) == cents


def test_d23_cents_to_decimal_has_two_fraction_digits():
    module = parsing()
    value = module.cents_to_decimal(115)
    assert value == Decimal("1.15")
    assert value.as_tuple().exponent == -2
    assert str(module.cents_to_decimal(59990)) == "599.90"
    assert str(module.cents_to_decimal(0)) == "0.00"
    assert str(module.cents_to_decimal(SQLITE_MAX_INTEGER)) == "92233720368547758.07"


@pytest.mark.parametrize("value", [1.15, True, "115", Decimal("115")])
def test_d23_cents_to_decimal_accepts_only_int(value):
    with pytest.raises(TypeError):
        parsing().cents_to_decimal(value)


def test_d23_conversions_ignore_and_preserve_caller_decimal_context():
    module = parsing()
    with localcontext() as context:
        context.prec = 3
        context.rounding = ROUND_DOWN
        context.clear_flags()
        assert module.parse_money_cents("92233720368547758.07") == SQLITE_MAX_INTEGER
        assert module.parse_percent_basis_points("12.34") == 1234
        assert module.cents_to_decimal(SQLITE_MAX_INTEGER) == Decimal("92233720368547758.07")
        assert module.basis_points_to_percent(1234) == Decimal("12.34")
        assert getcontext() is context
        assert context.prec == 3
        assert context.rounding == ROUND_DOWN
        assert not any(context.flags.values())


def test_d23_converted_values_are_accepted_by_pricing():
    # Unit part of D09: converted values feed the existing pricing service unchanged.
    module = parsing()
    pricing = import_module("emporio_agent.services.pricing")
    price = module.cents_to_decimal(59990)
    promotion = pricing.Promotion(module.basis_points_to_percent(1000), True)
    quote = pricing.quote_price(price, promotions=[promotion])
    assert quote.final_price == Decimal("539.91")
    pix = pricing.quote_price(module.cents_to_decimal(1990), payment_method="pix")
    assert pix.final_price == Decimal("18.91")


# D24: exact percentages in basis points.


@pytest.mark.parametrize(
    ("text", "basis_points"),
    [
        ("10", 1000),
        ("12.5", 1250),
        ("12.34", 1234),
        ("0", 0),
        ("0.01", 1),
        ("100", 10000),
        ("100.00", 10000),
    ],
)
def test_d24_percent_to_basis_points(text, basis_points):
    result = parsing().parse_percent_basis_points(text)
    assert type(result) is int
    assert result == basis_points


@pytest.mark.parametrize(
    ("basis_points", "percent"),
    [(1250, Decimal("12.5")), (1000, Decimal("10")), (0, Decimal("0")), (1, Decimal("0.01"))],
)
def test_d24_basis_points_to_percent(basis_points, percent):
    assert parsing().basis_points_to_percent(basis_points) == percent


@pytest.mark.parametrize("value", [12.5, True, "1250"])
def test_d24_basis_points_to_percent_accepts_only_int(value):
    with pytest.raises(TypeError):
        parsing().basis_points_to_percent(value)


# D25: percent rejections, never rounded.


@pytest.mark.parametrize(
    "text",
    [
        "100.01",
        "101",
        "999",
        "1000",
        "-5",
        "+5",
        "12.345",
        "5%",
        "NaN",
        "Infinity",
        "1e1",
        "05",
        ".5",
        "5.",
        " 5",
        "5 ",
    ],
)
def test_d25_percent_rejected(text):
    assert_code("parse_percent_basis_points", text, "invalid_percent")


def test_d25_empty_percent_is_required():
    assert_code("parse_percent_basis_points", "", "value_required")


# D26: identifiers.


@pytest.mark.parametrize(
    ("text", "value"), [("1", 1), ("145", 145), (str(SQLITE_MAX_INTEGER), SQLITE_MAX_INTEGER)]
)
def test_d26_valid_ids(text, value):
    result = parsing().parse_id(text)
    assert type(result) is int
    assert result == value


@pytest.mark.parametrize(
    "text",
    ["0", "-1", "+1", "01", "1.0", " 1", "1 ", "1\n", "abc", "1_000", "１"],
)
def test_d26_invalid_ids(text):
    assert_code("parse_id", text, "invalid_integer")


@pytest.mark.parametrize("text", ["9223372036854775808", "1" + "0" * 19, "9" * 5000])
def test_d26_ids_above_sqlite_limit(text):
    assert_code("parse_id", text, "out_of_range")


def test_d26_empty_id_is_required():
    assert_code("parse_id", "", "value_required")


# D27: stock and quantity.


@pytest.mark.parametrize(("text", "value"), [("0", 0), ("5", 5)])
def test_d27_valid_stock(text, value):
    assert parsing().parse_count(text) == value


@pytest.mark.parametrize("text", ["-1", "00", "01", "1.0", " 0"])
def test_d27_invalid_stock(text):
    assert_code("parse_count", text, "invalid_integer")


def test_d27_stock_above_sqlite_limit():
    assert_code("parse_count", "9223372036854775808", "out_of_range")


def test_d27_empty_stock_is_required():
    assert_code("parse_count", "", "value_required")


def test_d27_quantity_uses_id_grammar():
    # Spec: quantity has the same grammar as id (at least 1).
    assert parsing().parse_id("1") == 1
    assert_code("parse_id", "0", "invalid_integer")


# D28: calendar dates.


@pytest.mark.parametrize("text", ["2026-02-28", "2024-02-29", "2026-12-31"])
def test_d28_valid_dates_are_returned_unchanged(text):
    assert parsing().parse_date(text) == text


@pytest.mark.parametrize(
    "text",
    [
        "2026-02-29",
        "2026-02-30",
        "2026-13-01",
        "2026-00-10",
        "2026-01-00",
        "0000-01-01",
        "2026-2-1",
        "20260228",
        "28/02/2026",
        "2026-02-28T00:00",
        " 2026-02-28",
        "2026-02-28\n",
        "2026-W09-6",
    ],
)
def test_d28_invalid_dates(text):
    assert_code("parse_date", text, "invalid_date")


def test_d28_empty_date_is_required():
    assert_code("parse_date", "", "value_required")


# D29: optional date.


def test_d29_empty_optional_date_is_none():
    assert parsing().parse_optional_date("") is None


def test_d29_optional_date_keeps_valid_text():
    assert parsing().parse_optional_date("2026-02-28") == "2026-02-28"


@pytest.mark.parametrize("text", [" ", "2026-02-30"])
def test_d29_invalid_optional_dates(text):
    assert_code("parse_optional_date", text, "invalid_date")


# D30: flags.


@pytest.mark.parametrize(("text", "value"), [("0", 0), ("1", 1)])
def test_d30_valid_flags(text, value):
    result = parsing().parse_flag(text)
    assert type(result) is int
    assert result == value


@pytest.mark.parametrize("text", ["true", "False", "2", " 1", "1 ", "01", "-0"])
def test_d30_invalid_flags(text):
    assert_code("parse_flag", text, "invalid_flag")


def test_d30_empty_flag_is_required():
    assert_code("parse_flag", "", "value_required")


# D31: enums. Allowlists copied from spec "Enum allowlists"; schema constants belong to T2.

PRODUCT_STATUSES = frozenset({"active", "discontinued", "coming_soon"})
ORDER_STATUSES = frozenset({"pending", "confirmed", "shipped", "delivered", "cancelled"})
PAYMENT_METHODS = frozenset(
    {"pix", "debit", "boleto"} | {f"credit_{installments}x" for installments in range(1, 13)}
)


@pytest.mark.parametrize(
    ("text", "allowed"),
    [(status, PRODUCT_STATUSES) for status in sorted(PRODUCT_STATUSES)]
    + [(status, ORDER_STATUSES) for status in sorted(ORDER_STATUSES)]
    + [(method, PAYMENT_METHODS) for method in sorted(PAYMENT_METHODS)],
)
def test_d31_allowlisted_values(text, allowed):
    assert parsing().parse_enum(text, allowed) == text


@pytest.mark.parametrize(
    ("text", "allowed"),
    [
        ("Active", PRODUCT_STATUSES),
        (" active", PRODUCT_STATUSES),
        ("active ", PRODUCT_STATUSES),
        ("canceled", ORDER_STATUSES),
        ("credit_13x", PAYMENT_METHODS),
        ("credit_0x", PAYMENT_METHODS),
        ("credit_01x", PAYMENT_METHODS),
        ("cash", PAYMENT_METHODS),
        ("PIX", PAYMENT_METHODS),
    ],
)
def test_d31_values_outside_allowlist(text, allowed):
    assert_code("parse_enum", text, "invalid_enum", allowed)


def test_d31_empty_enum_is_required():
    assert_code("parse_enum", "", "value_required", PAYMENT_METHODS)


# D32: JSON object.


@pytest.mark.parametrize(
    "text",
    ['{"color":"red"}', "{}", '{ "a" : {"b": [1, 2.5, null, true]} }', '{"cor": "ação"}'],
)
def test_d32_json_object_returned_unchanged(text):
    assert parsing().parse_json_object(text) == text


@pytest.mark.parametrize(
    "text",
    [
        "[]",
        '"x"',
        "1",
        "null",
        "{",
        '{"a":1} x',
        "{'a':1}",
        '{"a":1,"a":2}',
        '{"outer":{"a":1,"a":2}}',
        '{"a":NaN}',
        '{"a":Infinity}',
        '{"a":-Infinity}',
        " ",
    ],
)
def test_d32_invalid_json_object(text):
    assert_code("parse_json_object", text, "invalid_json")


def test_d32_empty_json_is_required():
    assert_code("parse_json_object", "", "value_required")


# D33: required text.


@pytest.mark.parametrize("text", ["a", " a ", "Ação", "Guitarra, 6 cordas"])
def test_d33_required_text_returned_unchanged(text):
    assert parsing().parse_text(text) == text


@pytest.mark.parametrize("text", ["", " ", "\t", "\r\n", " "])
def test_d33_blank_required_text(text):
    assert_code("parse_text", text, "value_required")


# D34: optional text.


def test_d34_empty_optional_text_is_none():
    assert parsing().parse_optional_text("") is None


@pytest.mark.parametrize("text", ["x", " x ", "Entregar após 18h"])
def test_d34_optional_text_returned_unchanged(text):
    assert parsing().parse_optional_text(text) == text


@pytest.mark.parametrize("text", ["  ", "\t", " "])
def test_d34_blank_optional_text(text):
    assert_code("parse_optional_text", text, "value_blank")
