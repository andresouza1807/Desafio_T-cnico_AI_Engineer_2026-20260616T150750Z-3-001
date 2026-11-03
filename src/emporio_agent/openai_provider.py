"""OpenAI Responses API adapter (spec 07 G3, G4, G4b). The only module that imports `openai`.

One `create` sends exactly one HTTP request (`max_retries=0`); retry policy belongs to the
agent loop. Success bodies are read as raw bytes and validated field by field; `.parse()`
and SDK response objects are never used. Every SDK failure becomes a fixed `ProviderError`
raised `from None` outside any `except` block, so no SDK message, body, header (other than
`x-request-id`), URL or key reaches the caller. Importing this module creates no client.
"""

import json
import math
import re
import unicodedata
from typing import Self

import httpx2
import openai

from emporio_agent.provider import (
    FunctionCall,
    FunctionCallOutput,
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderItem,
    Usage,
    UserMessage,
)

__all__ = [
    "DEFAULT_BASE_URL",
    "MAX_OUTPUT_ITEMS",
    "MAX_OUTPUT_TOKENS",
    "MAX_RESPONSE_BODY_BYTES",
    "OpenAIResponsesProvider",
]

DEFAULT_BASE_URL = "https://api.openai.com/v1"
MAX_OUTPUT_TOKENS = 8000
MAX_RESPONSE_BODY_BYTES = 1_048_576
MAX_OUTPUT_ITEMS = 64
_MAX_IDENTIFIER_CODE_POINTS = 256

_QUOTA_CODES = frozenset(
    {
        "insufficient_quota",
        "credit_balance_exhausted",
        "organization_spend_limit_exceeded",
        "project_spend_limit_exceeded",
        "organization_usage_limit_exceeded",
    }
)
_FAILED_TRANSIENT_CODES = frozenset({"server_error", "rate_limit_exceeded"})
_INCOMPLETE_REASONS = frozenset({"max_output_tokens", "max_messages", "content_filter", "steered"})
_PENDING_ITEM_STATUSES = frozenset({"in_progress", "incomplete"})
_RETRY_AFTER_NUMBER = re.compile(r"[0-9]+(?:\.[0-9]+)?")


class _Invalid(Exception):
    """Internal marker: the HTTP 200 body broke a spec G4b rule."""


# --- constructor validation -----------------------------------------------------------------


def _plain_text(value: object) -> bool:
    return type(value) is str and value != "" and value == value.strip()


def _validate_arguments(
    api_key: object,
    model: object,
    timeout_seconds: object,
    max_output_tokens: object,
    base_url: object,
) -> str | None:
    """Return a fixed message for the first invalid argument, or None."""
    if not _plain_text(api_key):
        return "api_key must be a non-blank str without surrounding whitespace"
    if not _plain_text(model):
        return "model must be a non-blank str without surrounding whitespace"
    if type(timeout_seconds) not in (int, float) or not (
        math.isfinite(timeout_seconds) and timeout_seconds > 0
    ):
        return "timeout_seconds must be a finite positive number"
    if type(max_output_tokens) is not int or max_output_tokens < 1:
        return "max_output_tokens must be a positive int"
    if not _plain_text(base_url):
        return "base_url must be a non-blank str without surrounding whitespace"
    return None


# --- request mapping ------------------------------------------------------------------------


def _input_item(item: object) -> dict[str, object]:
    if isinstance(item, UserMessage):
        return {"type": "message", "role": "user", "content": item.text}
    if isinstance(item, ProviderItem | FunctionCall):
        return json.loads(item.raw)
    if isinstance(item, FunctionCallOutput):
        return {"type": "function_call_output", "call_id": item.call_id, "output": item.output}
    raise TypeError("ModelRequest.items must contain conversation items")


# --- error mapping (spec G3) ----------------------------------------------------------------


def _retry_after(headers: httpx2.Headers) -> float | None:
    """Parsed `retry-after-ms` (milliseconds) or numeric `retry-after` (seconds); else None."""
    for name, divisor in (("retry-after-ms", 1000.0), ("retry-after", 1.0)):
        value = headers.get(name)
        if value is None or _RETRY_AFTER_NUMBER.fullmatch(value.strip()) is None:
            continue
        seconds = float(value) / divisor
        if math.isfinite(seconds) and seconds >= 0:
            return seconds
    return None


def _text_or_none(value: object) -> str | None:
    return value if type(value) is str and value != "" else None


def _status_failure(error: openai.APIStatusError) -> ProviderError:
    status = error.status_code
    code = _text_or_none(getattr(error, "code", None))
    error_type = _text_or_none(getattr(error, "type", None))
    if 300 <= status < 400:
        # Redirect not followed (review 07-02-R1): fixed, not transient, nothing copied
        # from Location, body or Retry-After.
        return ProviderError(
            "bad_request", status_code=status, request_id=_text_or_none(error.request_id)
        )
    if status == 429:
        quota = code in _QUOTA_CODES or error_type in _QUOTA_CODES
        kind = "quota" if quota else "rate_limited"
    elif status in (408, 409) or status >= 500:
        kind = "server_error"
    elif status == 401:
        kind = "authentication"
    elif status == 403:
        kind = "permission"
    elif status == 404 or (status == 400 and code == "model_not_found"):
        kind = "model_unavailable"
    else:
        kind = "bad_request"
    return ProviderError(
        kind,
        status_code=status,
        request_id=_text_or_none(error.request_id),
        retry_after_seconds=_retry_after(error.response.headers),
    )


# --- response validation (spec G4b) ---------------------------------------------------------


def _reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _Invalid
        result[key] = value
    return result


def _reject_constant(_name: str) -> object:
    raise _Invalid


def _encodable(text: str) -> bool:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _identifier(value: object) -> str:
    """Non-empty str of at most 256 code points that encodes to UTF-8."""
    if type(value) is not str or not 0 < len(value) <= _MAX_IDENTIFIER_CODE_POINTS:
        raise _Invalid
    if not _encodable(value):
        raise _Invalid
    return value


def _optional_id(item: dict[str, object]) -> None:
    value = item.get("id")
    if value is not None and (type(value) is not str or value == ""):
        raise _Invalid


def _item_status(item: dict[str, object], *, required: bool) -> bool:
    """True when the item is pending (I1); raise for an invalid status."""
    status = item.get("status")
    if status is None and not required:
        return False
    if status == "completed":
        return False
    if status in _PENDING_ITEM_STATUSES:
        return True
    raise _Invalid


def _raw(item: dict[str, object]) -> str:
    text = json.dumps(item, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if not _encodable(text):
        raise _Invalid
    return text


class _Output:
    def __init__(self) -> None:
        self.items: list[ProviderItem | FunctionCall] = []
        self.text: list[str] = []
        self.commentary = False
        self.refusal = False
        self.pending = False


def _reasoning(item: dict[str, object], out: _Output) -> None:
    value = item.get("id")
    if type(value) is not str or value == "":
        raise _Invalid
    if type(item.get("summary")) is not list:
        raise _Invalid
    encrypted = item.get("encrypted_content")
    if encrypted is not None and type(encrypted) is not str:
        raise _Invalid
    content = item.get("content")
    if content is not None and type(content) is not list:
        raise _Invalid
    out.pending |= _item_status(item, required=False)
    out.items.append(ProviderItem(kind="reasoning", raw=_raw(item)))


def _message(item: dict[str, object], out: _Output) -> None:
    if item.get("role") != "assistant":
        raise _Invalid
    _optional_id(item)
    content = item.get("content")
    if type(content) is not list:
        raise _Invalid
    out.pending |= _item_status(item, required=True)
    phase = item.get("phase")
    if phase is None or phase == "final_answer":
        final = True
    elif phase == "commentary":
        final = False
        out.commentary = True
    else:
        raise _Invalid
    texts: list[str] = []
    for part in content:
        if type(part) is not dict:
            raise _Invalid
        part_type = part.get("type")
        if part_type == "output_text" and type(part.get("text")) is str:
            texts.append(part["text"])
        elif part_type == "refusal" and type(part.get("refusal")) is str:
            out.refusal = True
        else:
            raise _Invalid
    raw = _raw(item)
    if final:
        out.text.extend(texts)
    out.items.append(ProviderItem(kind="assistant_message", raw=raw))


def _function_call(item: dict[str, object], out: _Output) -> None:
    call_id = _identifier(item.get("call_id"))
    name = item.get("name")
    arguments = item.get("arguments")
    if type(name) is not str or type(arguments) is not str:
        raise _Invalid
    _optional_id(item)
    caller = item.get("caller")
    if caller is not None and caller != {"type": "direct"}:
        raise _Invalid
    if item.get("namespace") is not None:
        raise _Invalid
    out.pending |= _item_status(item, required=False)
    out.items.append(FunctionCall(call_id=call_id, name=name, arguments=arguments, raw=_raw(item)))


_ITEM_RULES = {"reasoning": _reasoning, "message": _message, "function_call": _function_call}


def _usage(data: dict[str, object]) -> tuple[Usage | None, bool]:
    """(usage, usage_invalid) following spec G4b U1-U4."""
    if data.get("usage") is None:
        return None, False
    usage = data["usage"]

    def count(container: object, key: str) -> int:
        if type(container) is not dict:
            raise _Invalid
        value = container.get(key)
        if type(value) is not int or value < 0:
            raise _Invalid
        return value

    def detail(key: str, field: str) -> int | None:
        container = usage.get(key)
        return None if container is None else count(container, field)

    try:
        result = Usage(
            input_tokens=count(usage, "input_tokens"),
            output_tokens=count(usage, "output_tokens"),
            total_tokens=count(usage, "total_tokens"),
            cached_input_tokens=detail("input_tokens_details", "cached_tokens"),
            reasoning_tokens=detail("output_tokens_details", "reasoning_tokens"),
        )
    except _Invalid:
        return None, True
    return result, False


def _failed(data: dict[str, object], request_id: str | None) -> ProviderError:
    """B3: transient only for a known str code; any other code type or shape -> not transient."""
    error = data.get("error")
    code = error.get("code") if type(error) is dict else None
    transient = type(code) is str and code in _FAILED_TRANSIENT_CODES
    return ProviderError("provider_failed", transient=transient, request_id=request_id)


def _incomplete_reason(data: dict[str, object]) -> str:
    details = data.get("incomplete_details")
    if details is None:
        return "other"
    if type(details) is not dict:
        raise _Invalid
    reason = details.get("reason")
    # B6: only a known str reason is kept; any other type (list, object, ...) is "other".
    return reason if type(reason) is str and reason in _INCOMPLETE_REASONS else "other"


def _parse_body(content: bytes, request_id: str | None) -> ModelResponse:
    """Validate one HTTP 200 body (spec G4b). Raises _Invalid or ProviderError."""
    if len(content) > MAX_RESPONSE_BODY_BYTES:
        raise _Invalid
    data = json.loads(
        content.decode("utf-8"),
        object_pairs_hook=_reject_duplicates,
        parse_constant=_reject_constant,
    )
    if type(data) is not dict:
        raise _Invalid
    status = data.get("status")
    if status == "failed":
        raise _failed(data, request_id)
    if status not in ("completed", "incomplete"):
        raise _Invalid
    model = _identifier(data.get("model"))
    if any(unicodedata.category(character) == "Cc" for character in model):
        raise _Invalid
    response_id = data.get("id")
    if response_id is not None:
        response_id = _identifier(response_id)
    reason = _incomplete_reason(data) if status == "incomplete" else None
    output = data.get("output")
    if type(output) is not list or len(output) > MAX_OUTPUT_ITEMS:
        raise _Invalid
    out = _Output()
    for item in output:
        if type(item) is not dict or type(item.get("type")) is not str:
            raise _Invalid
        rule = _ITEM_RULES.get(item["type"])
        if rule is None:
            raise _Invalid
        rule(item, out)
    if out.pending and status == "completed":
        status, reason = "incomplete", "item_incomplete"
    usage, usage_invalid = _usage(data)
    return ModelResponse(
        response_id=response_id,
        model=model,
        status=status,
        incomplete_reason=reason,
        output=tuple(out.items),
        text="".join(out.text),
        commentary_present=out.commentary,
        refusal=out.refusal,
        usage=usage,
        usage_invalid=usage_invalid,
        request_id=request_id,
    )


# --- adapter --------------------------------------------------------------------------------


class OpenAIResponsesProvider:
    """Synchronous `ModelProvider` over the OpenAI Responses API (spec G4).

    Arguments are explicit; the adapter never reads the environment itself (the SDK still
    reads some OPENAI_* variables, spec F6). A supplied `http_client` is owned by the adapter
    and closed by `close()`, and also when the arguments are rejected.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float = 30.0,
        max_output_tokens: int = MAX_OUTPUT_TOKENS,
        base_url: str = DEFAULT_BASE_URL,
        http_client: httpx2.Client | None = None,
    ) -> None:
        problem = _validate_arguments(api_key, model, timeout_seconds, max_output_tokens, base_url)
        if problem is not None:
            if http_client is not None:
                http_client.close()
            raise ValueError(problem)
        self._model = model
        self._max_output_tokens = max_output_tokens
        self._closed = False
        # Redirects are never followed (review 07-02-R1). The SDK default client enables
        # them, and max_retries=0 does not stop the extra requests a redirect causes.
        # Both paths use public settings only: the SDK's exported DefaultHttpxClient with
        # follow_redirects=False, or the public follow_redirects attribute of a supplied
        # (owned) httpx2.Client.
        if http_client is None:
            http_client = openai.DefaultHttpxClient(follow_redirects=False)
        else:
            http_client.follow_redirects = False
        self._client = openai.OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=float(timeout_seconds),
            max_retries=0,
            http_client=http_client,
        )

    @property
    def model(self) -> str:
        return self._model

    def __repr__(self) -> str:
        return f"OpenAIResponsesProvider(model={self._model!r})"

    def create(self, request: ModelRequest) -> ModelResponse:
        """One attempt: one HTTP request, no retry. Raises ProviderError on any failure."""
        if not isinstance(request, ModelRequest):
            raise TypeError("create expects a ModelRequest")
        if self._closed:
            raise ProviderError("unexpected") from None
        arguments = {
            "model": self._model,
            "instructions": request.instructions,
            "input": [_input_item(item) for item in request.items],
            "tools": list(request.tools),
            "tool_choice": "auto" if request.allow_tool_calls else "none",
            "parallel_tool_calls": False,
            "store": False,
            "max_output_tokens": self._max_output_tokens,
        }
        failure: ProviderError | None = None
        content = b""
        request_id: str | None = None
        try:
            raw_response = self._client.responses.with_raw_response.create(**arguments)
            content = raw_response.http_response.content
            request_id = _text_or_none(raw_response.http_response.headers.get("x-request-id"))
        except openai.APIStatusError as error:
            failure = _status_failure(error)
        except openai.APITimeoutError:
            failure = ProviderError("timeout")
        except openai.APIConnectionError:
            failure = ProviderError("connection")
        except openai.APIResponseValidationError:
            failure = ProviderError("response_invalid")
        except Exception:
            failure = ProviderError("unexpected")
        if failure is not None:
            raise failure from None
        return self._validated(content, request_id)

    def _validated(self, content: bytes, request_id: str | None) -> ModelResponse:
        failure: ProviderError | None = None
        try:
            return _parse_body(content, request_id)
        except ProviderError as error:
            failure = ProviderError(
                error.kind, transient=error.transient, request_id=error.request_id
            )
        except _Invalid, ValueError, TypeError, RecursionError:
            failure = ProviderError("response_invalid", request_id=request_id)
        except Exception:
            failure = ProviderError("unexpected", request_id=request_id)
        raise failure from None

    def close(self) -> None:
        """Close the SDK client and the supplied http_client. Idempotent."""
        if self._closed:
            return
        self._closed = True
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
