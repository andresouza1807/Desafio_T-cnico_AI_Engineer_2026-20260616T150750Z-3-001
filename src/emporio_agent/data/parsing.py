"""Pure parsers for source CSV fields and exact Decimal conversions.

Every parser receives the raw cell text, without trimming. A rejection raises
FieldError with a code only; the rejected text never reaches the error.
"""

import json
import re
from datetime import date
from decimal import (
    Context,
    Decimal,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
    Rounded,
    localcontext,
)
from typing import Final

from emporio_agent.data.errors import FieldError, IssueCode

SQLITE_MAX_INTEGER: Final = 2**63 - 1
MAX_MONEY_TEXT_LENGTH: Final = 40

_ID = re.compile(r"[1-9][0-9]*")
_COUNT = re.compile(r"0|[1-9][0-9]*")
_MONEY = re.compile(r"(0|[1-9][0-9]*)(\.[0-9]{1,2})?")
_PERCENT = re.compile(r"(0|[1-9][0-9]{0,2})(\.[0-9]{1,2})?")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_FLAG = re.compile(r"[01]")
_MAX_INTEGER_DIGITS = len(str(SQLITE_MAX_INTEGER))
_HUNDRED = Decimal(100)

# Used only through localcontext(), which works on a copy: the caller's context is
# neither read nor changed, and any rounding raises instead of passing silently.
_EXACT = Context(prec=60, traps=[InvalidOperation, DivisionByZero, Overflow, Inexact, Rounded])


def _require(text: str) -> None:
    if text == "":
        raise FieldError(IssueCode.VALUE_REQUIRED)


def _parse_integer(text: str, grammar: re.Pattern[str]) -> int:
    _require(text)
    if grammar.fullmatch(text) is None:
        raise FieldError(IssueCode.INVALID_INTEGER)
    # The length check keeps int() away from arbitrarily long digit strings.
    if len(text) > _MAX_INTEGER_DIGITS or int(text) > SQLITE_MAX_INTEGER:
        raise FieldError(IssueCode.OUT_OF_RANGE)
    return int(text)


def parse_id(text: str) -> int:
    return _parse_integer(text, _ID)


def parse_count(text: str) -> int:
    return _parse_integer(text, _COUNT)


def parse_money_cents(text: str) -> int:
    _require(text)
    if _MONEY.fullmatch(text) is None:
        raise FieldError(IssueCode.INVALID_MONEY)
    if len(text) > MAX_MONEY_TEXT_LENGTH:
        raise FieldError(IssueCode.MONEY_OUT_OF_RANGE)
    with localcontext(_EXACT):
        cents = int(Decimal(text) * 100)
    if cents > SQLITE_MAX_INTEGER:
        raise FieldError(IssueCode.MONEY_OUT_OF_RANGE)
    return cents


def parse_percent_basis_points(text: str) -> int:
    _require(text)
    if _PERCENT.fullmatch(text) is None:
        raise FieldError(IssueCode.INVALID_PERCENT)
    with localcontext(_EXACT):
        value = Decimal(text)
        if value > _HUNDRED:
            raise FieldError(IssueCode.INVALID_PERCENT)
        return int(value * 100)


def parse_date(text: str) -> str:
    _require(text)
    if _DATE.fullmatch(text) is None:
        raise FieldError(IssueCode.INVALID_DATE)
    try:
        date.fromisoformat(text)
    except ValueError:
        raise FieldError(IssueCode.INVALID_DATE) from None
    return text


def parse_optional_date(text: str) -> str | None:
    return None if text == "" else parse_date(text)


def parse_flag(text: str) -> int:
    _require(text)
    if _FLAG.fullmatch(text) is None:
        raise FieldError(IssueCode.INVALID_FLAG)
    return int(text)


def parse_enum(text: str, allowed: frozenset[str]) -> str:
    _require(text)
    if text not in allowed:
        raise FieldError(IssueCode.INVALID_ENUM)
    return text


def _reject_non_finite(constant: str) -> None:
    raise ValueError("non-finite JSON constant")


def _unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def parse_json_object(text: str) -> str:
    _require(text)
    try:
        value = json.loads(text, object_pairs_hook=_unique_keys, parse_constant=_reject_non_finite)
    except ValueError:
        raise FieldError(IssueCode.INVALID_JSON) from None
    if not isinstance(value, dict):
        raise FieldError(IssueCode.INVALID_JSON)
    return text


def parse_text(text: str) -> str:
    if not text.strip():
        raise FieldError(IssueCode.VALUE_REQUIRED)
    return text


def parse_optional_text(text: str) -> str | None:
    if text == "":
        return None
    if not text.strip():
        raise FieldError(IssueCode.VALUE_BLANK)
    return text


def cents_to_decimal(cents: int) -> Decimal:
    if type(cents) is not int:
        raise TypeError("cents must be an int")
    with localcontext(_EXACT):
        return Decimal(cents).scaleb(-2)


def basis_points_to_percent(bp: int) -> Decimal:
    if type(bp) is not int:
        raise TypeError("basis points must be an int")
    with localcontext(_EXACT):
        return Decimal(bp) / _HUNDRED
