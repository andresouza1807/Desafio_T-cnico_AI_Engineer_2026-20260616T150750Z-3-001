"""Provider-neutral model contract: items, request, response, errors and sizes (spec 07 G2, G3).

No SDK import and no SDK object: items are immutable dataclasses whose provider payloads are
canonical JSON strings (`raw`) created and read only by the adapter. Every text field of an
item is a `str` that encodes to UTF-8, so the size functions (spec G10b) never fail on a
constructed item. Sizes are logical UTF-8 byte counts, not HTTP body sizes and not tokens.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol, get_args, runtime_checkable

__all__ = [
    "ConversationItem",
    "FunctionCall",
    "FunctionCallOutput",
    "ModelProvider",
    "ModelRequest",
    "ModelResponse",
    "ProviderError",
    "ProviderErrorKind",
    "ProviderItem",
    "Usage",
    "UserMessage",
    "item_size_bytes",
    "static_size_bytes",
]


def _require_text(message: str, *values: object) -> None:
    """Raise ValueError(message) unless every value is a str that encodes to UTF-8.

    The message is fixed by the caller and never includes the rejected value. The error is
    raised outside the `except` block so no UnicodeEncodeError (which holds the text) is
    chained as `__context__`.
    """
    for value in values:
        if type(value) is not str:
            raise ValueError(message)
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            failed = True
        else:
            failed = False
        if failed:
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class UserMessage:
    text: str

    def __post_init__(self) -> None:
        _require_text("UserMessage.text must be a UTF-8 encodable str", self.text)


@dataclass(frozen=True, slots=True)
class ProviderItem:
    """Opaque provider item resent unchanged (reasoning or assistant message)."""

    kind: Literal["reasoning", "assistant_message"]
    raw: str  # canonical JSON of the provider item; only the adapter reads it

    def __post_init__(self) -> None:
        if self.kind not in ("reasoning", "assistant_message"):
            raise ValueError("ProviderItem.kind must be reasoning or assistant_message")
        _require_text("ProviderItem.raw must be a UTF-8 encodable str", self.raw)


@dataclass(frozen=True, slots=True)
class FunctionCall:
    call_id: str
    name: str
    arguments: str
    raw: str  # canonical JSON of the provider item, resent unchanged

    def __post_init__(self) -> None:
        _require_text(
            "FunctionCall text fields must be UTF-8 encodable str values",
            self.call_id,
            self.name,
            self.arguments,
            self.raw,
        )


@dataclass(frozen=True, slots=True)
class FunctionCallOutput:
    call_id: str
    output: str  # the ToolExecution.payload envelope, unchanged

    def __post_init__(self) -> None:
        _require_text(
            "FunctionCallOutput text fields must be UTF-8 encodable str values",
            self.call_id,
            self.output,
        )


type ConversationItem = UserMessage | ProviderItem | FunctionCall | FunctionCallOutput


@dataclass(frozen=True, slots=True)
class ModelRequest:
    instructions: str
    items: tuple[ConversationItem, ...]
    tools: tuple[Mapping[str, object], ...]  # from provider_tool_definitions()
    allow_tool_calls: bool  # False -> tool_choice "none" (spec G9)


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cached_input_tokens: int | None
    reasoning_tokens: int | None


@dataclass(frozen=True, slots=True)
class ModelResponse:
    response_id: str | None
    model: str  # as reported by the provider
    status: Literal["completed", "incomplete"]
    incomplete_reason: str | None
    output: tuple[ProviderItem | FunctionCall, ...]  # provider order kept
    text: str  # output_text of final-eligible assistant messages only
    commentary_present: bool
    refusal: bool
    usage: Usage | None  # None when absent or invalid; never zero-filled
    usage_invalid: bool  # usage present but malformed
    request_id: str | None
    # Effort reported in the response body (spec 07 B8, Review 07-10-D5). None = not reported
    # or ignored as invalid; never filled with the requested or configured effort.
    reasoning_effort_reported: str | None = None


type ProviderErrorKind = Literal[
    "timeout",
    "connection",
    "rate_limited",
    "server_error",
    "quota",
    "authentication",
    "permission",
    "bad_request",
    "model_unavailable",
    "provider_failed",
    "response_invalid",
    "unexpected",
]

_KINDS: frozenset[str] = frozenset(get_args(ProviderErrorKind.__value__))
_TRANSIENT_KINDS = frozenset({"timeout", "connection", "rate_limited", "server_error"})
_MESSAGES: dict[str, str] = {
    "timeout": "The model provider did not answer in time.",
    "connection": "Could not connect to the model provider.",
    "rate_limited": "The model provider rate limit was reached.",
    "server_error": "The model provider reported a temporary server error.",
    "quota": "The model provider quota or spending limit was reached.",
    "authentication": "The model provider rejected the credentials.",
    "permission": "The model provider denied access to the requested resource.",
    "bad_request": "The model provider rejected the request.",
    "model_unavailable": "The configured model is not available.",
    "provider_failed": "The model provider reported a failed response.",
    "response_invalid": "The model provider returned an invalid response.",
    "unexpected": "Unexpected failure while calling the model provider.",
}


class ProviderError(Exception):
    """Fixed provider failure (spec G3). Carries codes and metadata, never provider text.

    `transient` is derived from `kind`, except for `provider_failed`, where the adapter
    decides it from the failed body (default False). `request_id` is the only header value
    copied verbatim; `retry_after_seconds` is a parsed number.
    """

    kind: ProviderErrorKind
    transient: bool
    status_code: int | None
    request_id: str | None
    retry_after_seconds: float | None

    def __init__(
        self,
        kind: ProviderErrorKind,
        *,
        transient: bool | None = None,
        status_code: int | None = None,
        request_id: str | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        if kind not in _KINDS:
            raise ValueError("ProviderError kind is not a known kind")
        if kind == "provider_failed":
            derived = bool(transient)
        else:
            derived = kind in _TRANSIENT_KINDS
            if transient is not None and transient is not derived:
                raise ValueError("ProviderError transient flag contradicts its kind")
        super().__init__(_MESSAGES[kind])
        self.kind = kind
        self.transient = derived
        self.status_code = status_code
        self.request_id = request_id
        self.retry_after_seconds = retry_after_seconds


@runtime_checkable
class ModelProvider(Protocol):
    @property
    def model(self) -> str: ...  # configured model identifier

    def create(self, request: ModelRequest) -> ModelResponse: ...  # one attempt, no retry


def _utf8_len(text: str) -> int:
    return len(text.encode("utf-8"))


def item_size_bytes(item: ConversationItem) -> int:
    """Logical UTF-8 size of one conversation item (spec G10b)."""
    if isinstance(item, UserMessage):
        return _utf8_len(item.text)
    if isinstance(item, ProviderItem | FunctionCall):
        return _utf8_len(item.raw)
    if isinstance(item, FunctionCallOutput):
        return _utf8_len(item.call_id) + _utf8_len(item.output)
    raise TypeError("item_size_bytes expects a conversation item")


def static_size_bytes(instructions: str, tools: tuple[Mapping[str, object], ...]) -> int:
    """UTF-8 size of the instructions plus the compact JSON of the tool schemas (spec G10b)."""
    schemas = json.dumps(list(tools), ensure_ascii=False, separators=(",", ":"))
    return _utf8_len(instructions) + _utf8_len(schemas)
