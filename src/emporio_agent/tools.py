"""Typed agent tools: the only boundary between the future agent loop and the services.

Slice 06-A: dispatcher core, product detail and product quote. The model controls only
the tool name and a raw JSON argument string; paths, clock and customer come from an
application-owned ToolContext. Arguments are parsed and validated strictly before any
resource is opened, each execution opens at most one read-only connection and closes
it on every exit path, and every outcome becomes one deterministic JSON envelope with
fixed codes only. Business rules stay in the services. No network, no environment, no
logging, no cache. See docs/specs/06-tools.md.
"""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Context, Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from emporio_agent.data.preparation import SourceFileInfo
from emporio_agent.data.readonly import DatabaseUnavailableError, connect_readonly
from emporio_agent.services import catalog
from emporio_agent.services.catalog import (
    AppliedPromotion,
    CatalogInputError,
    CategoryRef,
    ProductDetail,
    ProductQuote,
    ProductSummary,
    Provenance,
)
from emporio_agent.services.orders import CustomerContext

type ToolStatus = Literal["ok", "not_found", "invalid_input", "unavailable", "internal_error"]

MAX_ARGUMENTS_CHARS: Final = 131_072  # code points of the raw argument string (spec G13)
MAX_RESULT_BYTES: Final = 32_768  # UTF-8 bytes of the envelope; enforced in slice D

_CENT: Final = Decimal("0.01")
# Private context for the two-place check: the caller's decimal context is never used.
_DECIMAL_CONTEXT: Final = Context(prec=60)
# Private context for JSON number parsing: always traps InvalidOperation. Decimal(text)
# is exact and ignores precision; localcontext() works on a copy, so flags never leak.
_NUMBER_CONTEXT: Final = Context(traps=[InvalidOperation])


class _ToolFailure(Exception):
    """Internal signal: a fixed status, code and field. Never leaves this module."""

    def __init__(self, status: ToolStatus, code: str, field: str | None = None) -> None:
        self.status = status
        self.code = code
        self.field = field
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Application configuration and identity; never built from model arguments."""

    database_path: Path
    policy_index_path: Path
    clock: Callable[[], datetime]
    customer: CustomerContext | None

    def __post_init__(self) -> None:
        if not isinstance(self.database_path, Path):
            raise TypeError("database_path must be a Path")
        if not isinstance(self.policy_index_path, Path):
            raise TypeError("policy_index_path must be a Path")
        if not callable(self.clock):
            raise TypeError("clock must be callable")
        if self.customer is not None and not isinstance(self.customer, CustomerContext):
            raise TypeError("customer must be a CustomerContext or None")


@dataclass(frozen=True)
class ToolExecution:
    tool: str | None  # registered name, or None for an unknown name
    status: ToolStatus
    error_code: str | None
    payload: str  # the JSON envelope, the only value sent to the model


# --- Argument models (JSON types and presence only, spec G5) --------------------------


class _Arguments(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class _GetProductArguments(_Arguments):
    product_id: int


class _QuoteProductArguments(_Arguments):
    product_id: int
    payment_method: str


# --- Serialization (spec G8, G9) ------------------------------------------------------


def _two_places(value: Decimal) -> str:
    """Exact two-place decimal text. Raises instead of rounding."""
    if type(value) is not Decimal:
        raise TypeError("Decimal expected")
    if not value.is_finite():
        raise ValueError("finite Decimal expected")
    with localcontext(_DECIMAL_CONTEXT):
        quantized = value.quantize(_CENT)
        if quantized != value:
            raise ValueError("more than two decimal places")
        if quantized == 0:
            quantized = quantized.copy_abs()  # never "-0.00"
    return format(quantized, "f")


def _category(category: CategoryRef) -> dict[str, object]:
    return {"category_id": category.category_id, "name": category.name}


def _promotion(promotion: AppliedPromotion | None) -> dict[str, object] | None:
    if promotion is None:
        return None
    return {
        "promotion_id": promotion.promotion_id,
        "discount_percent": _two_places(promotion.discount_percent),
        "description": promotion.description,
    }


def _summary(summary: ProductSummary) -> dict[str, object]:
    return {
        "product_id": summary.product_id,
        "name": summary.name,
        "category": _category(summary.category),
        "list_price": _two_places(summary.list_price),
        "effective_price": _two_places(summary.effective_price),
        "promotion": _promotion(summary.promotion),
        "stock_quantity": summary.stock_quantity,
        "status": summary.status,
        "availability": summary.availability,
        "is_available": summary.is_available,
        "quality_notes": list(summary.quality_notes),
    }


def _source(source: SourceFileInfo) -> dict[str, object]:
    return {
        "table_name": source.table_name,
        "file_name": source.file_name,
        "sha256": source.sha256,
        "size_bytes": source.size_bytes,
        "row_count": source.row_count,
    }


def _provenance(provenance: Provenance) -> dict[str, object]:
    return {
        "prepared_at": provenance.prepared_at.isoformat(),
        "sources": [_source(source) for source in provenance.sources],
    }


def _specs(specs: Mapping[str, object] | None) -> dict[str, object] | None:
    # Stored JSON values, unchanged; json.dumps(allow_nan=False) rejects non-finite ones.
    return None if specs is None else dict(specs)


def _detail_data(detail: ProductDetail) -> dict[str, object]:
    return {
        "product": _summary(detail.summary),
        "description": detail.description,
        "specs": _specs(detail.specs),
        "withheld_fields": list(detail.withheld_fields),
        "provenance": _provenance(detail.provenance),
    }


def _quote_data(quote: ProductQuote) -> dict[str, object]:
    return {
        "product": _summary(quote.product),
        "payment_method": quote.payment_method,
        "list_price": _two_places(quote.list_price),
        "final_price": _two_places(quote.final_price),
        "discount_percent": _two_places(quote.discount_percent),
        "discount_source": quote.discount_source,
        "promotion": _promotion(quote.promotion),
        "pix_not_combined": quote.pix_not_combined,
        "provenance": _provenance(quote.provenance),
    }


def _dumps(envelope: dict[str, object]) -> str:
    return json.dumps(envelope, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _envelope(
    tool: str | None,
    status: ToolStatus,
    data: dict[str, object] | None,
    code: str | None,
    field: str | None,
) -> dict[str, object]:
    error = None if status == "ok" else {"code": code, "field": field}
    return {"tool": tool, "status": status, "data": data, "error": error}


def _failure(tool: str | None, failure: _ToolFailure) -> ToolExecution:
    envelope = _envelope(tool, failure.status, None, failure.code, failure.field)
    return ToolExecution(tool, failure.status, failure.code, _dumps(envelope))


# --- Tool runners (spec G11: one read-only connection, closed on every path) ----------


def _run_get_product(arguments: _GetProductArguments, context: ToolContext) -> dict[str, object]:
    with connect_readonly(context.database_path) as connection:
        detail = catalog.get_product(connection, arguments.product_id)
    if detail is None:
        raise _ToolFailure("not_found", "product_not_found")
    return _detail_data(detail)


def _run_quote_product(
    arguments: _QuoteProductArguments, context: ToolContext
) -> dict[str, object]:
    with connect_readonly(context.database_path) as connection:
        quote = catalog.quote_product(
            connection, arguments.product_id, payment_method=arguments.payment_method
        )
    if quote is None:
        raise _ToolFailure("not_found", "product_not_found")
    return _quote_data(quote)


@dataclass(frozen=True)
class _Tool:
    model: type[_Arguments]
    run: Callable[[_Arguments, ToolContext], dict[str, object]]


_REGISTRY: Final[Mapping[str, _Tool]] = {
    "get_product": _Tool(_GetProductArguments, _run_get_product),
    "quote_product": _Tool(_QuoteProductArguments, _run_quote_product),
}


# --- Argument pipeline (spec G4 steps 2-7) --------------------------------------------


class _DuplicateKeys:
    """object_pairs_hook that records a duplicate key at any depth and keeps parsing.

    Recording instead of raising keeps the precedence: invalid JSON (step 3) is reported
    before a duplicate key (step 3b), even when the duplicate comes first in the text.
    """

    def __init__(self) -> None:
        self.found = False

    def __call__(self, pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                self.found = True
            result[key] = value
        return result


def _reject_constant(_name: str) -> object:
    raise ValueError("non-finite constant")


def _parse_number(text: str) -> Decimal:
    """JSON number with a fraction or exponent, as an exact Decimal (spec G4 step 3).

    Decimal(text) signals InvalidOperation when the exponent is outside the limits of
    the decimal implementation; with the caller's trap disabled it would return NaN
    and set the caller's flag. A private context always traps it, the caller's context
    is never read or changed, and the failure becomes a JSON parsing failure.
    """
    try:
        with localcontext(_NUMBER_CONTEXT):
            return Decimal(text)
    except InvalidOperation:
        raise ValueError("number not representable") from None


def _parse(arguments: str, model: type[_Arguments]) -> _Arguments:
    if len(arguments) > MAX_ARGUMENTS_CHARS:
        raise _ToolFailure("invalid_input", "arguments_too_large")
    duplicates = _DuplicateKeys()
    try:
        parsed = json.loads(
            arguments,
            object_pairs_hook=duplicates,
            parse_constant=_reject_constant,
            parse_float=_parse_number,  # no field accepts a Decimal: 1.0 is never an ID
        )
    except ValueError, RecursionError:
        raise _ToolFailure("invalid_input", "invalid_json") from None
    if duplicates.found:
        raise _ToolFailure("invalid_input", "duplicate_key")
    if type(parsed) is not dict:
        raise _ToolFailure("invalid_input", "arguments_not_object")
    fields = model.model_fields
    if any(key not in fields for key in parsed):
        raise _ToolFailure("invalid_input", "unexpected_field")  # the key is never echoed
    for name, info in fields.items():
        if info.is_required() and name not in parsed:
            raise _ToolFailure("invalid_input", "missing", name)
    try:
        return model.model_validate(parsed)
    except ValidationError as error:
        # Only loc is read; msg, input and ctx never leave Pydantic.
        rejected = {
            item["loc"][0]
            for item in error.errors(include_url=False, include_context=False, include_input=False)
            if item["loc"]
        }
        field = next((name for name in fields if name in rejected), None)
        raise _ToolFailure("invalid_input", "invalid_type", field) from None


# --- Public interface -----------------------------------------------------------------


def tool_names() -> tuple[str, ...]:
    """Registered tool names, in registry order."""
    return tuple(_REGISTRY)


def execute_tool(name: str, arguments: str, context: ToolContext) -> ToolExecution:
    """Validate, run one registered tool and return its JSON envelope.

    TypeError for a caller defect (wrong Python types). No exception escapes for any
    model-controlled name or argument; BaseException subclasses that are not Exception
    propagate after the resource is closed.
    """
    if not isinstance(name, str):
        raise TypeError("name must be a str")
    if not isinstance(arguments, str):
        raise TypeError("arguments must be a str")
    if not isinstance(context, ToolContext):
        raise TypeError("context must be a ToolContext")

    tool = _REGISTRY.get(name)
    if tool is None:
        return _failure(None, _ToolFailure("invalid_input", "unknown_tool"))
    try:
        validated = _parse(arguments, tool.model)
    except _ToolFailure as failure:
        return _failure(name, failure)

    try:
        data = tool.run(validated, context)
        payload = _dumps(_envelope(name, "ok", data, None, None))
    except _ToolFailure as failure:
        return _failure(name, failure)
    except CatalogInputError as error:
        return _failure(name, _ToolFailure("invalid_input", error.code, error.field))
    except DatabaseUnavailableError:
        return _failure(name, _ToolFailure("unavailable", "data_unavailable"))
    except Exception:
        return _failure(name, _ToolFailure("internal_error", "internal_error"))
    return ToolExecution(name, "ok", None, payload)
