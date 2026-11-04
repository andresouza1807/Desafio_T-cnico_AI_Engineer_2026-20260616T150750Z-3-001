"""Slice 07-A adapter cases AG02-AG36 and AG97-AG106 (docs/plans/07-agent.md, spec 07 G3, G4, G4b).

The real openai SDK runs over httpx2.MockTransport: the transport records every request and
returns literal HTTP bodies written in this module, including malformed HTTP 200 payloads.
No socket is opened (autouse guard), every OPENAI_* variable is removed, and the only key is
the literal "test-key-not-a-secret". Each case names the spec rule it exercises; expected
values are literals, never computed by production helpers. Offline tests do not prove model
access or provider acceptance of the schemas or resent items (spec Q-07).
"""

import json
import math
import os
import socket
import traceback
from collections.abc import Callable

import httpx2
import pytest

from emporio_agent.openai_provider import (
    MAX_OUTPUT_ITEMS,
    MAX_OUTPUT_TOKENS,
    MAX_RESPONSE_BODY_BYTES,
    OpenAIResponsesProvider,
)
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
from emporio_agent.tools import provider_tool_definitions

KEY = "test-key-not-a-secret"
MODEL = "gpt-5.4-mini"
SNAPSHOT = "gpt-5.4-mini-2026-03-17"
URL = "https://api.openai.com/v1/responses"
OMIT = object()

TOOL = {
    "type": "function",
    "name": "get_product",
    "description": "Get one product.",
    "parameters": {
        "type": "object",
        "properties": {"product_id": {"type": "integer", "minimum": 1}},
        "required": ["product_id"],
        "additionalProperties": False,
    },
    "strict": True,
}

# Literal output items, compact and in provider key order (raw must equal them, spec T1).
MSG = (
    '{"type":"message","id":"msg_1","role":"assistant","status":"completed",'
    '"content":[{"type":"output_text","text":"Olá! Como posso ajudar?","annotations":[]}]}'
)
REASONING = (
    '{"type":"reasoning","id":"rs_1","summary":[{"type":"summary_text","text":"Resumo"}],'
    '"encrypted_content":"gAAAAB-ENCRYPTED+/=="}'
)
CALL = (
    r'{"type":"function_call","id":"fc_1","call_id":"call_1","name":"get_product",'
    r'"arguments":"{\"product_id\":211}","status":"completed"}'
)
USAGE = (
    '{"input_tokens":120,"input_tokens_details":{"cached_tokens":40},'
    '"output_tokens":30,"output_tokens_details":{"reasoning_tokens":10},"total_tokens":150}'
)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    for name in [name for name in os.environ if name.startswith("OPENAI_")]:
        monkeypatch.delenv(name)

    def guard(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "create_connection", guard)
    monkeypatch.setattr(socket, "getaddrinfo", guard)
    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket.socket, "connect_ex", guard)


# --- helpers (test-only) --------------------------------------------------------------------


class Transport:
    """Records each request; answers with the next scripted response or step function."""

    def __init__(self, *steps: httpx2.Response | Callable[[httpx2.Request], httpx2.Response]):
        self.steps = list(steps)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if not self.steps:
            raise AssertionError("unscripted request")
        step = self.steps.pop(0)
        if isinstance(step, httpx2.Response):
            return step
        return step(request)

    def body(self, index: int = 0) -> dict:
        return json.loads(self.requests[index].content)


def make(transport: Transport, **overrides) -> OpenAIResponsesProvider:
    client = httpx2.Client(transport=httpx2.MockTransport(transport))
    arguments = {"api_key": KEY, "model": MODEL, "http_client": client} | overrides
    return OpenAIResponsesProvider(**arguments)


def request(*items, allow: bool = True, instructions: str = "Responda em português."):
    return ModelRequest(
        instructions=instructions,
        items=items or (UserMessage("Olá"),),
        tools=(TOOL,),
        allow_tool_calls=allow,
    )


def reply(content: bytes, status: int = 200, headers: dict | None = None) -> httpx2.Response:
    return httpx2.Response(status, content=content, headers=headers or {})


def envelope(
    output: object = "[" + MSG + "]",
    *,
    status: object = '"completed"',
    model: object = '"' + SNAPSHOT + '"',
    rid: object = '"resp_1"',
    usage: object = USAGE,
    extra: str = "",
) -> bytes:
    """Literal Responses-API-shaped JSON; OMIT drops a key; values are raw JSON text."""
    parts = []
    for key, value in (
        ("id", rid),
        ("object", '"response"'),
        ("status", status),
        ("model", model),
        ("output", output),
        ("usage", usage),
    ):
        if value is not OMIT:
            parts.append(f'"{key}":{value}')
    return ("{" + ",".join(parts) + extra + "}").encode("utf-8")


def answer(content: bytes, headers: dict | None = None) -> ModelResponse:
    transport = Transport(reply(content, headers=headers))
    with make(transport) as provider:
        result = provider.create(request())
    assert len(transport.requests) == 1
    return result


def failure(response: httpx2.Response | Callable, *, req: ModelRequest | None = None):
    transport = Transport(response)
    with make(transport) as provider, pytest.raises(ProviderError) as info:
        provider.create(req or request())
    assert len(transport.requests) == 1
    return info.value


def invalid(content: bytes) -> None:
    error = failure(reply(content))
    assert error.kind == "response_invalid"
    assert error.transient is False


def output_of(*items: str) -> str:
    return "[" + ",".join(items) + "]"


def message(content: str = '[{"type":"output_text","text":"oi","annotations":[]}]', **fields):
    """Literal assistant message; fields map key -> raw JSON text, OMIT drops the key."""
    base = {"type": '"message"', "id": '"msg_9"', "role": '"assistant"', "status": '"completed"'}
    base.update(fields)
    base["content"] = content
    parts = [f'"{key}":{value}' for key, value in base.items() if value is not OMIT]
    return "{" + ",".join(parts) + "}"


def function_call(**fields) -> str:
    base = {
        "type": '"function_call"',
        "id": '"fc_9"',
        "call_id": '"call_9"',
        "name": '"get_product"',
        "arguments": r'"{\"product_id\":1}"',
        "status": '"completed"',
    }
    base.update(fields)
    parts = [f'"{key}":{value}' for key, value in base.items() if value is not OMIT]
    return "{" + ",".join(parts) + "}"


def reasoning(**fields) -> str:
    base = {"type": '"reasoning"', "id": '"rs_9"', "summary": "[]"}
    base.update(fields)
    parts = [f'"{key}":{value}' for key, value in base.items() if value is not OMIT]
    return "{" + ",".join(parts) + "}"


def error_body(code: str | None = None, type_: str = "invalid_request_error") -> bytes:
    payload = {"error": {"message": "provider message", "type": type_, "param": None, "code": code}}
    return json.dumps(payload).encode()


# --- AG02-AG03: constructor -----------------------------------------------------------------

_BAD_CONSTRUCTOR = [
    ("api_key", ""),
    ("api_key", "   "),
    ("api_key", " SENTINEL-KEY"),
    ("api_key", "SENTINEL-KEY\n"),
    ("api_key", None),
    ("api_key", 5),
    ("model", ""),
    ("model", "  "),
    ("model", "SENTINEL-MODEL "),
    ("model", None),
    ("model", 5),
    ("timeout_seconds", 0),
    ("timeout_seconds", -1),
    ("timeout_seconds", math.nan),
    ("timeout_seconds", math.inf),
    ("timeout_seconds", True),
    ("timeout_seconds", "30"),
    ("max_output_tokens", 0),
    ("max_output_tokens", -5),
    ("max_output_tokens", True),
    ("max_output_tokens", 1.0),
    ("base_url", ""),
    ("base_url", " https://SENTINEL-URL/v1"),
    ("base_url", None),
]


@pytest.mark.parametrize(("field", "value"), _BAD_CONSTRUCTOR, ids=repr)
def test_ag02_constructor_rejects_invalid_arguments(field, value):
    arguments = {"api_key": KEY, "model": MODEL} | {field: value}
    with pytest.raises(ValueError) as info:
        OpenAIResponsesProvider(**arguments)
    message = str(info.value)
    assert "SENTINEL" not in message
    assert KEY not in message
    assert message.isascii()
    assert not any(character.isdigit() for character in message)


@pytest.mark.parametrize("field", ["api_key", "model", "timeout_seconds", "max_output_tokens"])
def test_ag02_message_is_fixed_per_argument(field):
    messages = set()
    for name, value in _BAD_CONSTRUCTOR:
        if name == field:
            with pytest.raises(ValueError) as info:
                OpenAIResponsesProvider(**({"api_key": KEY, "model": MODEL} | {name: value}))
            messages.add(str(info.value))
    assert len(messages) == 1


def test_ag02_invalid_arguments_close_the_supplied_client():
    client = httpx2.Client(transport=httpx2.MockTransport(Transport()))
    with pytest.raises(ValueError):
        OpenAIResponsesProvider(api_key=KEY, model=" ", http_client=client)
    assert client.is_closed


def test_ag03_repr_and_str_show_model_never_key():
    with make(Transport()) as provider:
        assert provider.model == MODEL
        for text in (repr(provider), str(provider)):
            assert MODEL in text
            assert KEY not in text
            assert "not-a-secret" not in text


def test_max_output_tokens_default_is_8000():
    assert MAX_OUTPUT_TOKENS == 8000


# --- AG04-AG10: request ---------------------------------------------------------------------


def test_ag04_one_request_with_timeout_retry_count_and_url():
    transport = Transport(reply(envelope()))
    with make(transport) as provider:
        provider.create(request())
    assert len(transport.requests) == 1
    sent = transport.requests[0]
    assert sent.method == "POST"
    assert str(sent.url) == URL
    assert sent.extensions["timeout"] == {
        "connect": 30.0,
        "read": 30.0,
        "write": 30.0,
        "pool": 30.0,
    }
    assert sent.headers["x-stainless-retry-count"] == "0"


def test_ag05_explicit_base_url_wins_over_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/v1")
    transport = Transport(reply(envelope()))
    with make(transport) as provider:
        provider.create(request())
    assert str(transport.requests[0].url) == URL

    transport = Transport(reply(envelope()))
    with make(transport, base_url="https://proxy.test/v9") as provider:
        provider.create(request())
    assert str(transport.requests[0].url) == "https://proxy.test/v9/responses"


_NOT_SENT = [
    "previous_response_id",
    "conversation",
    "background",
    "stream",
    "include",
    "reasoning",
    "temperature",
    "top_p",
    "truncation",
    "metadata",
    "user",
    "safety_identifier",
    "prompt_cache_key",
    "service_tier",
    "max_tool_calls",
]


def test_ag06_request_body_literal():
    transport = Transport(reply(envelope()))
    with make(transport) as provider:
        provider.create(request(UserMessage("Quanto custa o violão?")))
    body = transport.body()
    assert body == {
        "model": "gpt-5.4-mini",
        "instructions": "Responda em português.",
        "input": [{"type": "message", "role": "user", "content": "Quanto custa o violão?"}],
        "tools": [TOOL],
        "tool_choice": "auto",
        "parallel_tool_calls": False,
        "store": False,
        "max_output_tokens": 8000,
    }
    for name in _NOT_SENT:
        assert name not in body


def test_ag06_max_output_tokens_is_configurable():
    transport = Transport(reply(envelope()))
    with make(transport, max_output_tokens=1234) as provider:
        provider.create(request())
    assert transport.body()["max_output_tokens"] == 1234


def test_ag07_tool_calls_not_allowed_sends_none_and_keeps_tools():
    transport = Transport(reply(envelope()))
    with make(transport) as provider:
        provider.create(request(allow=False))
    body = transport.body()
    assert body["tool_choice"] == "none"
    assert body["tools"] == [TOOL]


def test_ag08_input_mapping_for_each_item_kind_in_order():
    payload = '{"status":"ok","data":{"name":"Violão Yamaha","price":"999.90"}}'
    items = (
        UserMessage("Olá"),
        ProviderItem(kind="reasoning", raw=REASONING),
        ProviderItem(kind="assistant_message", raw=MSG),
        FunctionCall(
            call_id="call_1", name="get_product", arguments='{"product_id":211}', raw=CALL
        ),
        FunctionCallOutput(call_id="call_1", output=payload),
    )
    transport = Transport(reply(envelope()))
    with make(transport) as provider:
        provider.create(request(*items))
    sent = transport.body()["input"]
    assert sent == [
        {"type": "message", "role": "user", "content": "Olá"},
        {
            "type": "reasoning",
            "id": "rs_1",
            "summary": [{"type": "summary_text", "text": "Resumo"}],
            "encrypted_content": "gAAAAB-ENCRYPTED+/==",
        },
        {
            "type": "message",
            "id": "msg_1",
            "role": "assistant",
            "status": "completed",
            "content": [
                {"type": "output_text", "text": "Olá! Como posso ajudar?", "annotations": []}
            ],
        },
        {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "call_1",
            "name": "get_product",
            "arguments": '{"product_id":211}',
            "status": "completed",
        },
        {"type": "function_call_output", "call_id": "call_1", "output": payload},
    ]
    assert sent[4]["output"].encode() == payload.encode()


def test_ag09_tools_are_provider_tool_definitions_unchanged():
    definitions = provider_tool_definitions()
    transport = Transport(reply(envelope()))
    with make(transport) as provider:
        provider.create(
            ModelRequest(
                instructions="i",
                items=(UserMessage("oi"),),
                tools=tuple(provider_tool_definitions()),
                allow_tool_calls=True,
            )
        )
    sent = transport.body()["tools"]
    assert sent == definitions
    assert json.dumps(sent, ensure_ascii=False) == json.dumps(definitions, ensure_ascii=False)


def test_ag10_key_only_in_authorization_header():
    transport = Transport(reply(envelope()))
    with make(transport) as provider:
        provider.create(request())
    headers = transport.requests[0].headers
    assert headers["authorization"] == "Bearer test-key-not-a-secret"
    others = [value for name, value in headers.items() if name.lower() != "authorization"]
    assert not any(KEY in value for value in others)
    assert KEY.encode() not in transport.requests[0].content


# --- AG11-AG20: response mapping ------------------------------------------------------------


def test_ag11_completed_text_response():
    result = answer(envelope(), headers={"x-request-id": "req_123"})
    assert result == ModelResponse(
        response_id="resp_1",
        model=SNAPSHOT,
        status="completed",
        incomplete_reason=None,
        output=(ProviderItem(kind="assistant_message", raw=MSG),),
        text="Olá! Como posso ajudar?",
        commentary_present=False,
        refusal=False,
        usage=Usage(
            input_tokens=120,
            output_tokens=30,
            total_tokens=150,
            cached_input_tokens=40,
            reasoning_tokens=10,
        ),
        usage_invalid=False,
        request_id="req_123",
    )


def test_ag12_function_call_item():
    result = answer(envelope(output_of(CALL)))
    assert result.output == (
        FunctionCall(
            call_id="call_1", name="get_product", arguments='{"product_id":211}', raw=CALL
        ),
    )
    assert json.loads(result.output[0].raw) == {
        "type": "function_call",
        "id": "fc_1",
        "call_id": "call_1",
        "name": "get_product",
        "arguments": '{"product_id":211}',
        "status": "completed",
    }
    assert result.text == ""


def test_ag13_reasoning_keeps_encrypted_content_and_summary():
    result = answer(envelope(output_of(REASONING, MSG)))
    item = result.output[0]
    assert item == ProviderItem(kind="reasoning", raw=REASONING)
    decoded = json.loads(item.raw)
    assert decoded["encrypted_content"].encode() == b"gAAAAB-ENCRYPTED+/=="
    assert decoded["summary"] == [{"type": "summary_text", "text": "Resumo"}]


def test_ag14_commentary_phase_kept_in_raw():
    commentary = message(phase='"commentary"')
    result = answer(envelope(output_of(commentary, CALL)))
    assert result.output[0].raw == commentary
    assert '"phase":"commentary"' in result.output[0].raw


def test_ag15_mixed_output_keeps_provider_order():
    second = function_call(id='"fc_2"', call_id='"call_2"')
    result = answer(envelope(output_of(REASONING, MSG, CALL, second)))
    assert [type(item).__name__ for item in result.output] == [
        "ProviderItem",
        "ProviderItem",
        "FunctionCall",
        "FunctionCall",
    ]
    assert [item.raw for item in result.output] == [REASONING, MSG, CALL, second]
    assert [item.call_id for item in result.output[2:]] == ["call_1", "call_2"]


def test_ag16_text_is_concatenation_without_separator():
    first = message('[{"type":"output_text","text":"A"},{"type":"output_text","text":"b "}]')
    second = message(
        '[{"type":"output_text","text":"C"},{"type":"output_text","text":"d"}]', id='"msg_2"'
    )
    assert answer(envelope(output_of(first, second))).text == "Ab Cd"


def test_ag17_refusal_flag_and_text_excluded():
    content = (
        '[{"type":"output_text","text":"Visível"},{"type":"refusal","refusal":"SENTINEL-REFUSAL"}]'
    )
    result = answer(envelope(output_of(message(content))))
    assert result.refusal is True
    assert result.text == "Visível"
    assert "SENTINEL-REFUSAL" not in result.text


@pytest.mark.parametrize(
    ("details", "reason"),
    [
        (',"incomplete_details":{"reason":"max_output_tokens"}', "max_output_tokens"),
        (',"incomplete_details":{"reason":"content_filter"}', "content_filter"),
        (',"incomplete_details":{"reason":"max_messages"}', "max_messages"),
        (',"incomplete_details":{"reason":"steered"}', "steered"),
        (',"incomplete_details":{"reason":"brand_new"}', "other"),
        ("", "other"),
    ],
)
def test_ag18_incomplete_reason(details, reason):
    result = answer(envelope(status='"incomplete"', extra=details))
    assert result.status == "incomplete"
    assert result.incomplete_reason == reason


def test_ag18_completed_ignores_incomplete_details():
    result = answer(envelope(extra=',"incomplete_details":"not read when completed"'))
    assert (result.status, result.incomplete_reason) == ("completed", None)


def test_ag19_usage_variants():
    assert answer(envelope()).usage == Usage(120, 30, 150, 40, 10)
    missing = answer(envelope(usage=OMIT))
    assert (missing.usage, missing.usage_invalid) == (None, False)
    no_details = answer(envelope(usage='{"input_tokens":7,"output_tokens":8,"total_tokens":15}'))
    assert no_details.usage == Usage(7, 8, 15, None, None)
    zeros = answer(
        envelope(
            usage='{"input_tokens":0,"input_tokens_details":{"cached_tokens":0},"output_tokens":0,'
            '"output_tokens_details":{"reasoning_tokens":0},"total_tokens":0}'
        )
    )
    assert zeros.usage == Usage(0, 0, 0, 0, 0)
    assert zeros.usage_invalid is False


def test_ag20_configured_model_sent_reported_model_returned():
    transport = Transport(reply(envelope()))
    with make(transport) as provider:
        result = provider.create(request())
    assert transport.body()["model"] == "gpt-5.4-mini"
    assert result.model == "gpt-5.4-mini-2026-03-17"
    assert provider.model == "gpt-5.4-mini"


# --- AG21-AG24: failed, invalid status, item types ------------------------------------------


@pytest.mark.parametrize(
    ("error", "transient"),
    [
        ('{"code":"server_error","message":"x"}', True),
        ('{"code":"rate_limit_exceeded","message":"x"}', True),
        ('{"code":"invalid_prompt","message":"x"}', False),
    ],
)
def test_ag21_failed_status_is_provider_failed(error, transient):
    content = envelope(status='"failed"', output="[]", extra=f',"error":{error}')
    found = failure(reply(content, headers={"x-request-id": "req_f"}))
    assert found.kind == "provider_failed"
    assert found.transient is transient
    assert found.request_id == "req_f"


@pytest.mark.parametrize(
    "status", ["null", '"in_progress"', '"queued"', '"cancelled"', '"unknown"']
)
def test_ag22_unusable_status_is_invalid(status):
    invalid(envelope(status=status))


@pytest.mark.parametrize(
    "item",
    [
        '{"type":"web_search_call","id":"ws_1","status":"completed"}',
        '{"type":"custom_tool_call","id":"ct_1","call_id":"c","name":"x","input":"y"}',
        '{"type":"mcp_call","id":"mcp_1","name":"x","arguments":"{}","server_label":"s"}',
        '{"type":"future_item","id":"f_1"}',
        message('[{"type":"future_part","text":"x"}]'),
    ],
)
def test_ag23_unexpected_item_or_part_type_is_invalid(item):
    invalid(envelope(output_of(item)))


@pytest.mark.parametrize(
    "fields",
    [
        {"call_id": OMIT},
        {"call_id": '""'},
        {"call_id": "5"},
        {"call_id": '"' + "c" * 257 + '"'},
        {"name": OMIT},
        {"name": "null"},
        {"arguments": OMIT},
        {"arguments": '{"product_id":1}'},
        {"arguments": "null"},
        {"caller": '{"type":"program"}'},
        {"caller": '"direct"'},
        {"namespace": '"functions"'},
        {"status": '"done"'},
        {"status": "5"},
        {"id": '""'},
        {"id": "5"},
    ],
    ids=repr,
)
def test_ag24_function_call_rules_c1(fields):
    invalid(envelope(output_of(function_call(**fields))))


def test_ag24_empty_name_and_non_json_arguments_returned_unchanged():
    item = function_call(name='""', arguments='"{not json"')
    result = answer(envelope(output_of(item)))
    call = result.output[0]
    assert (call.name, call.arguments, call.raw) == ("", "{not json", item)


def test_ag24_function_call_optional_fields_accepted():
    item = function_call(
        id="null", status=OMIT, caller='{"type":"direct"}', namespace="null", call_id='"c"'
    )
    assert answer(envelope(output_of(item))).output[0].call_id == "c"
    item = function_call(id=OMIT, status="null", caller="null", call_id='"' + "c" * 256 + '"')
    assert answer(envelope(output_of(item))).output[0].call_id == "c" * 256


# --- AG25-AG36: errors ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "kind", "transient"),
    [
        (400, "bad_request", False),
        (401, "authentication", False),
        (403, "permission", False),
        (404, "model_unavailable", False),
        (408, "server_error", True),
        (409, "server_error", True),
        (418, "bad_request", False),
        (422, "bad_request", False),
        (429, "rate_limited", True),
        (500, "server_error", True),
        (502, "server_error", True),
        (503, "server_error", True),
    ],
)
def test_ag25_status_matrix_one_request_each(status, kind, transient):
    body = error_body(type_="requests" if status == 429 else "invalid_request_error")
    found = failure(reply(body, status=status))
    assert (found.kind, found.transient, found.status_code) == (kind, transient, status)


_QUOTA = [
    "insufficient_quota",
    "credit_balance_exhausted",
    "organization_spend_limit_exceeded",
    "project_spend_limit_exceeded",
    "organization_usage_limit_exceeded",
]


@pytest.mark.parametrize("name", _QUOTA)
@pytest.mark.parametrize("where", ["code", "type"])
def test_ag26_quota_is_not_transient(name, where):
    body = error_body(code=name) if where == "code" else error_body(type_=name)
    found = failure(reply(body, status=429))
    assert (found.kind, found.transient, found.status_code) == ("quota", False, 429)


def test_ag26_slow_down_and_rate_limit_are_transient():
    for body in (
        error_body(code="slow_down"),
        error_body(code="rate_limit_exceeded", type_="tokens"),
        b"not json",
    ):
        found = failure(reply(body, status=429))
        assert (found.kind, found.transient) == ("rate_limited", True)


def test_ag27_model_not_found_is_model_unavailable():
    found = failure(reply(error_body(code="model_not_found"), status=400))
    assert (found.kind, found.transient) == ("model_unavailable", False)
    found = failure(reply(error_body(code="model_not_found"), status=404))
    assert (found.kind, found.transient) == ("model_unavailable", False)


def _raiser(exception_type: type, message: str = "transport failure") -> Callable:
    def step(sent: httpx2.Request) -> httpx2.Response:
        raise exception_type(message, request=sent)

    return step


@pytest.mark.parametrize(
    ("exception_type", "kind"),
    [
        (httpx2.ReadTimeout, "timeout"),
        (httpx2.ConnectTimeout, "timeout"),
        (httpx2.WriteTimeout, "timeout"),
        (httpx2.PoolTimeout, "timeout"),
        (httpx2.ConnectError, "connection"),
        (httpx2.ReadError, "connection"),
        (httpx2.RemoteProtocolError, "connection"),
    ],
)
def test_ag28_transport_errors_one_request(exception_type, kind):
    found = failure(_raiser(exception_type))
    assert (found.kind, found.transient, found.status_code) == (kind, True, None)
    assert found.request_id is None


def test_ag29_non_json_and_non_list_output_are_invalid():
    invalid(b"not json")
    invalid(envelope(output='{"type":"message"}'))
    invalid(envelope(output='"texto"'))


SENTINELS = (
    "SENTINEL-BODY",
    "sk-SENTINELFAKE0123456789",
    "PEDIDO-SENTINELA",
    "SENTINEL-HEADER",
    KEY,
)
_SENTINEL_HEADERS = {
    "x-request-id": "req_ALLOWED",
    "x-debug": "SENTINEL-HEADER",
    "openai-organization": "SENTINEL-HEADER-ORG",
    "retry-after": "2",
}
_SENTINEL_ERROR = json.dumps(
    {
        "error": {
            "message": "SENTINEL-BODY key sk-SENTINELFAKE0123456789 PEDIDO-SENTINELA",
            "type": "SENTINEL-BODY",
            "param": "SENTINEL-BODY",
            "code": "SENTINEL-BODY",
        }
    }
).encode()
_LEAK_REQUEST = ModelRequest(
    instructions="PEDIDO-SENTINELA instructions",
    items=(UserMessage("PEDIDO-SENTINELA user"),),
    tools=(TOOL,),
    allow_tool_calls=True,
)


def _exposed(error: ProviderError) -> str:
    parts = [
        str(error),
        repr(error),
        repr(error.args),
        repr(getattr(error, "__dict__", {})),
        "".join(traceback.format_exception(error)),
    ]
    for name in ("kind", "transient", "status_code", "request_id", "retry_after_seconds"):
        parts.append(repr(getattr(error, name)))
    return "\n".join(parts)


@pytest.mark.parametrize(
    "step",
    [
        reply(_SENTINEL_ERROR, status=400, headers=_SENTINEL_HEADERS),
        reply(_SENTINEL_ERROR, status=401, headers=_SENTINEL_HEADERS),
        reply(_SENTINEL_ERROR, status=429, headers=_SENTINEL_HEADERS),
        reply(_SENTINEL_ERROR, status=500, headers=_SENTINEL_HEADERS),
        reply(b"SENTINEL-BODY sk-SENTINELFAKE0123456789", status=503, headers=_SENTINEL_HEADERS),
        reply(
            envelope(
                status='"failed"',
                extra=',"error":{"code":"SENTINEL-BODY","message":"SENTINEL-BODY"}',
            ),
            headers=_SENTINEL_HEADERS,
        ),
        reply(b'{"status":"SENTINEL-BODY"}', headers=_SENTINEL_HEADERS),
        reply(envelope(model='"SENTINEL-BODY\\n"'), headers=_SENTINEL_HEADERS),
        _raiser(httpx2.ConnectError, "SENTINEL-BODY sk-SENTINELFAKE0123456789"),
        _raiser(httpx2.ReadTimeout, "SENTINEL-BODY"),
        _raiser(RuntimeError, "SENTINEL-BODY sk-SENTINELFAKE0123456789"),
    ],
)
def test_ag30_no_leakage_except_request_id(step):
    found = failure(step, req=_LEAK_REQUEST)
    text = _exposed(found)
    for sentinel in SENTINELS:
        assert sentinel not in text
    assert found.request_id in (None, "req_ALLOWED")
    assert found.retry_after_seconds in (None, 2.0)
    assert found.__cause__ is None
    assert found.__suppress_context__ is True
    assert found.__context__ is None


def _rate_limited(headers: dict) -> ProviderError:
    return failure(reply(error_body(type_="requests"), status=429, headers=headers))


@pytest.mark.parametrize(
    ("headers", "seconds"),
    [
        ({"retry-after-ms": "1500"}, 1.5),
        ({"retry-after": "3"}, 3.0),
        ({"retry-after": "0.5"}, 0.5),
        ({"retry-after": "0"}, 0.0),
        ({"retry-after-ms": "250", "retry-after": "7"}, 0.25),
        ({"retry-after-ms": "abc", "retry-after": "7"}, 7.0),
        ({"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}, None),
        ({"retry-after": "-1"}, None),
        ({"retry-after": "nan"}, None),
        ({"retry-after": "inf"}, None),
        ({"retry-after": "1_0"}, None),
        ({"retry-after": "1e3"}, None),
        ({"retry-after": "9" * 400}, None),
        ({"retry-after": ""}, None),
        ({}, None),
    ],
)
def test_ag31_retry_after(headers, seconds):
    found = _rate_limited(headers)
    assert found.retry_after_seconds == seconds
    assert found.kind == "rate_limited"


def test_ag31_retry_after_on_server_error():
    found = failure(reply(error_body(), status=503, headers={"retry-after": "4"}))
    assert (found.kind, found.retry_after_seconds) == ("server_error", 4.0)


def test_ag32_request_id_is_the_only_header_copied():
    headers = {"x-request-id": "req_SENTINEL", "x-trace": "OTHER-SENTINEL"}
    found = failure(reply(error_body(), status=400, headers=headers))
    assert found.request_id == "req_SENTINEL"
    assert "OTHER-SENTINEL" not in _exposed(found)

    result = answer(envelope(), headers=headers)
    assert result.request_id == "req_SENTINEL"
    assert "OTHER-SENTINEL" not in repr(result)

    assert answer(envelope()).request_id is None


def test_ag33_other_exception_is_unexpected():
    found = failure(_raiser(RuntimeError))
    assert (found.kind, found.transient, found.status_code) == ("unexpected", False, None)


def test_ag34_keyboard_interrupt_and_system_exit_propagate():
    def interrupt(sent):
        raise KeyboardInterrupt

    def leave(sent):
        raise SystemExit(3)

    for step, expected in ((interrupt, KeyboardInterrupt), (leave, SystemExit)):
        transport = Transport(step)
        with make(transport) as provider, pytest.raises(expected):
            provider.create(request())
        assert len(transport.requests) == 1


def test_ag35_close_is_idempotent_and_blocks_create():
    transport = Transport(reply(envelope()))
    client = httpx2.Client(transport=httpx2.MockTransport(transport))
    provider = OpenAIResponsesProvider(api_key=KEY, model=MODEL, http_client=client)
    provider.close()
    assert client.is_closed
    provider.close()
    with pytest.raises(ProviderError) as info:
        provider.create(request())
    assert (info.value.kind, info.value.transient) == ("unexpected", False)
    assert transport.requests == []


def test_ag35_context_manager_closes_on_exit_and_on_exception():
    client = httpx2.Client(transport=httpx2.MockTransport(Transport()))
    with OpenAIResponsesProvider(api_key=KEY, model=MODEL, http_client=client) as provider:
        assert isinstance(provider, OpenAIResponsesProvider)
        assert not client.is_closed
    assert client.is_closed

    client = httpx2.Client(transport=httpx2.MockTransport(Transport()))
    with (
        pytest.raises(LookupError),
        OpenAIResponsesProvider(api_key=KEY, model=MODEL, http_client=client),
    ):
        raise LookupError
    assert client.is_closed


@pytest.mark.parametrize(
    ("status", "headers", "kind"),
    [
        (500, {"x-should-retry": "true"}, "server_error"),
        (429, {"retry-after": "0"}, "rate_limited"),
        (400, {"x-should-retry": "true"}, "bad_request"),
    ],
)
def test_ag36_sdk_retries_disabled(status, headers, kind):
    transport = Transport(
        reply(error_body(type_="requests"), status=status, headers=headers),
        reply(envelope()),
    )
    with make(transport) as provider, pytest.raises(ProviderError) as info:
        provider.create(request())
    assert info.value.kind == kind
    assert len(transport.requests) == 1
    assert len(transport.steps) == 1


def test_ag36_timeout_is_not_retried():
    transport = Transport(_raiser(httpx2.ReadTimeout), reply(envelope()))
    with make(transport) as provider, pytest.raises(ProviderError):
        provider.create(request())
    assert len(transport.requests) == 1


# --- AG121: review 07-02-R1, redirects are never followed ---------------------------------

REDIRECT_TARGET = "https://SENTINEL-LOCATION.test/v1/responses"
_REDIRECT_HEADERS = {
    "location": REDIRECT_TARGET,
    "x-request-id": "req_redirect",
    "x-should-retry": "true",
    "retry-after": "1",
}
_REDIRECT_BODY = b"SENTINEL-BODY moved to SENTINEL-LOCATION sk-SENTINELFAKE0123456789"


def _assert_redirect_failure(found: ProviderError, status: int, transport: Transport) -> None:
    assert (found.kind, found.transient, found.status_code) == ("bad_request", False, status)
    assert found.request_id == "req_redirect"
    assert found.retry_after_seconds is None
    text = _exposed(found)
    for sentinel in ("SENTINEL-LOCATION", "SENTINEL-BODY", "sk-SENTINELFAKE", KEY):
        assert sentinel not in text
    assert found.__cause__ is None and found.__context__ is None
    assert len(transport.requests) == 1
    assert str(transport.requests[0].url) == URL
    assert len(transport.steps) == 1  # the scripted target answer is never requested


def _redirect_steps(status: int) -> tuple[httpx2.Response, httpx2.Response]:
    return (
        reply(_REDIRECT_BODY, status=status, headers=_REDIRECT_HEADERS),
        reply(envelope()),
    )


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_ag121_supplied_client_following_redirects_is_not_followed(status):
    transport = Transport(*_redirect_steps(status))
    client = httpx2.Client(transport=httpx2.MockTransport(transport), follow_redirects=True)
    with OpenAIResponsesProvider(api_key=KEY, model=MODEL, http_client=client) as provider:
        with pytest.raises(ProviderError) as info:
            provider.create(request())
    _assert_redirect_failure(info.value, status, transport)
    assert client.is_closed


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_ag121_sdk_default_style_client_is_not_followed(status):
    import openai

    transport = Transport(*_redirect_steps(status))
    client = openai.DefaultHttpxClient(transport=httpx2.MockTransport(transport))
    assert client.follow_redirects is True  # the SDK default configuration
    with OpenAIResponsesProvider(api_key=KEY, model=MODEL, http_client=client) as provider:
        with pytest.raises(ProviderError) as info:
            provider.create(request())
    _assert_redirect_failure(info.value, status, transport)
    assert client.is_closed


@pytest.mark.parametrize("status", [301, 307])
def test_ag121_client_built_by_adapter_is_not_followed(monkeypatch, status):
    """No http_client: the adapter builds its own client through openai.DefaultHttpxClient.

    The test subclass only injects the mock transport; every keyword the adapter passes
    (including follow_redirects) reaches the real SDK default client unchanged.
    """
    import openai

    transport = Transport(*_redirect_steps(status))
    built: list[httpx2.Client] = []

    class MockDefaultClient(openai.DefaultHttpxClient):
        def __init__(self, **kwargs):
            kwargs["transport"] = httpx2.MockTransport(transport)
            super().__init__(**kwargs)
            built.append(self)

    monkeypatch.setattr(openai, "DefaultHttpxClient", MockDefaultClient)
    with OpenAIResponsesProvider(api_key=KEY, model=MODEL) as provider:
        with pytest.raises(ProviderError) as info:
            provider.create(request())
    _assert_redirect_failure(info.value, status, transport)
    assert len(built) == 1
    assert built[0].is_closed


def test_ag121_adapter_built_client_still_answers_and_uses_timeout(monkeypatch):
    import openai

    transport = Transport(reply(envelope()))

    class MockDefaultClient(openai.DefaultHttpxClient):
        def __init__(self, **kwargs):
            kwargs["transport"] = httpx2.MockTransport(transport)
            super().__init__(**kwargs)

    monkeypatch.setattr(openai, "DefaultHttpxClient", MockDefaultClient)
    with OpenAIResponsesProvider(api_key=KEY, model=MODEL) as provider:
        assert provider.create(request()).text == "Olá! Como posso ajudar?"
    sent = transport.requests[0]
    assert str(sent.url) == URL
    assert sent.extensions["timeout"] == {
        "connect": 30.0,
        "read": 30.0,
        "write": 30.0,
        "pool": 30.0,
    }


# --- AG97-AG106: HTTP 200 body validation ---------------------------------------------------


def _padded(total: int) -> bytes:
    base = envelope(extra=',"padding":""')
    return envelope(extra=',"padding":"' + "a" * (total - len(base)) + '"')


def test_ag97_body_size_limit_exact_and_plus_one():
    assert MAX_RESPONSE_BODY_BYTES == 1_048_576
    exact = _padded(1_048_576)
    assert len(exact) == 1_048_576
    assert answer(exact).text == "Olá! Como posso ajudar?"
    over = _padded(1_048_577)
    assert len(over) == 1_048_577
    invalid(over)


@pytest.mark.parametrize(
    "content",
    [
        envelope(extra=',"note":"\xff"').replace(b"\xc3\xbf", b"\xff"),
        b'{"status":"completed","status":"completed","model":"m","output":[]}',
        envelope(extra=',"note":NaN'),
        envelope(extra=',"note":Infinity'),
        envelope(extra=',"note":-Infinity'),
        b"[" + envelope() + b"]",
        b'"texto"',
        b"",
        b"\xef\xbb\xbf" + envelope(),
        envelope(output=output_of('{"type":"message","type":"message"}')),
    ],
    ids=[
        "invalid-utf8",
        "duplicate-status",
        "nan",
        "infinity",
        "minus-infinity",
        "array",
        "string",
        "empty",
        "bom",
        "nested-duplicate",
    ],
)
def test_ag97_body_decoding_rules(content):
    invalid(content)


@pytest.mark.parametrize(
    "model", [OMIT, '""', "5", "null", '"' + "m" * 257 + '"', '"a\\nb"', '"a\\u0000"', '"a\\u007f"']
)
def test_ag98_model_rules_b4(model):
    invalid(envelope(model=model))


def test_ag98_model_limits_accepted():
    assert answer(envelope(model='"' + "ç" * 256 + '"')).model == "ç" * 256
    assert answer(envelope(model='"m"')).model == "m"


@pytest.mark.parametrize(("rid", "expected"), [(OMIT, None), ("null", None), ('"r"', "r")])
def test_ag98_id_accepted_b5(rid, expected):
    assert answer(envelope(rid=rid)).response_id == expected


@pytest.mark.parametrize("rid", ['""', "5", '"' + "r" * 257 + '"', "{}"])
def test_ag98_id_rejected_b5(rid):
    invalid(envelope(rid=rid))


def test_ag98_id_256_code_points_accepted():
    assert answer(envelope(rid='"' + "r" * 256 + '"')).response_id == "r" * 256


@pytest.mark.parametrize(
    "status", [OMIT, "null", "5", '"in_progress"', '"queued"', '"cancelled"', '"unknown"', "true"]
)
def test_ag99_status_rules_b3(status):
    invalid(envelope(status=status))


@pytest.mark.parametrize(
    ("error", "transient"),
    [
        (OMIT, False),
        ("null", False),
        ('{"code":5}', False),
        ('{"code":null}', False),
        ('"server_error"', False),
        ('{"code":"SERVER_ERROR"}', False),
        ('{"code":"server_error"}', True),
    ],
)
def test_ag99_failed_status_transience(error, transient):
    extra = "" if error is OMIT else f',"error":{error}'
    found = failure(reply(envelope(status='"failed"', output=OMIT, extra=extra)))
    assert (found.kind, found.transient) == ("provider_failed", transient)


@pytest.mark.parametrize(
    ("details", "reason"),
    [
        (',"incomplete_details":null', "other"),
        (',"incomplete_details":{}', "other"),
        (',"incomplete_details":{"reason":"new_reason"}', "other"),
        (',"incomplete_details":{"reason":null}', "other"),
        (',"incomplete_details":{"reason":5}', "other"),
    ],
)
def test_ag99_incomplete_details_b6(details, reason):
    assert answer(envelope(status='"incomplete"', extra=details)).incomplete_reason == reason


@pytest.mark.parametrize("details", ['"text"', "[]", "5"])
def test_ag99_incomplete_details_not_object_is_invalid(details):
    invalid(envelope(status='"incomplete"', extra=f',"incomplete_details":{details}'))


# --- AG119-AG120: review 07-02-R1, non-scalar codes and reasons ----------------------------


@pytest.mark.parametrize(
    ("error", "transient"),
    [
        ('{"code":["server_error"]}', False),
        ('{"code":{"value":"server_error"}}', False),
        ('{"code":[]}', False),
        ('{"code":{}}', False),
        ('{"code":true}', False),
        ('{"code":1.5}', False),
        ('{"code":"rate_limit_exceeded"}', True),
        ('{"code":"server_error","message":"x"}', True),
        ("{}", False),
    ],
)
def test_ag119_failed_error_code_of_any_type(error, transient):
    content = envelope(status='"failed"', output="[]", extra=f',"error":{error}')
    found = failure(reply(content))
    assert (found.kind, found.transient) == ("provider_failed", transient)
    assert found.__context__ is None


@pytest.mark.parametrize(
    "reason",
    ['["max_output_tokens"]', '{"value":"max_output_tokens"}', "[]", "{}", "true", "1.5"],
)
def test_ag120_incomplete_reason_of_any_type_is_other(reason):
    extra = f',"incomplete_details":{{"reason":{reason}}}'
    result = answer(envelope(status='"incomplete"', extra=extra))
    assert (result.status, result.incomplete_reason) == ("incomplete", "other")
    assert result.text == "Olá! Como posso ajudar?"


@pytest.mark.parametrize(
    "output",
    [
        OMIT,
        "null",
        "{}",
        output_of(*[reasoning(id=f'"rs_{n}"') for n in range(65)]),
        output_of('"texto"'),
        output_of("5"),
        output_of('{"id":"x"}'),
        output_of('{"type":5}'),
        output_of('{"type":null}'),
    ],
    ids=["absent", "null", "object", "65-items", "string", "number", "no-type", "type-5", "null-t"],
)
def test_ag100_output_rules_b7_x1(output):
    invalid(envelope(output=output))


def test_ag100_exact_item_limit_and_empty_list():
    assert MAX_OUTPUT_ITEMS == 64
    items = [reasoning(id=f'"rs_{n}"') for n in range(64)]
    result = answer(envelope(output_of(*items)))
    assert len(result.output) == 64
    assert [item.raw for item in result.output] == items
    empty = answer(envelope("[]"))
    assert (empty.output, empty.text, empty.status) == ((), "", "completed")


@pytest.mark.parametrize("status", ['"incomplete"', '"in_progress"'])
def test_ag101_incomplete_message_makes_response_incomplete(status):
    result = answer(envelope(output_of(message(status=status))))
    assert (result.status, result.incomplete_reason) == ("incomplete", "item_incomplete")


@pytest.mark.parametrize(
    "item",
    [
        message(status=OMIT),
        message(status="null"),
        message(status='"done"'),
        message(role='"user"'),
        message(role=OMIT),
        message(id='""'),
        message(id="5"),
        message(content='"oi"'),
        message(content="null"),
        message(content=OMIT),
        message(content='["oi"]'),
        message(content='[{"type":"output_text"}]'),
        message(content='[{"type":"output_text","text":5}]'),
        message(content='[{"type":"output_text","text":null}]'),
        message(content='[{"type":"refusal","refusal":null}]'),
        message(content='[{"type":"refusal"}]'),
        message(content='[{"text":"oi"}]'),
    ],
)
def test_ag101_message_rules_m1_m2_m4(item):
    invalid(envelope(output_of(item)))


def test_ag101_message_optional_id_and_empty_content_accepted():
    result = answer(envelope(output_of(message(id=OMIT), message(id="null", content="[]"))))
    assert result.text == "oi"
    assert len(result.output) == 2


@pytest.mark.parametrize(
    ("phase", "text"), [(OMIT, "oi"), ("null", "oi"), ('"final_answer"', "oi")]
)
def test_ag102_final_eligible_phases(phase, text):
    result = answer(envelope(output_of(message(phase=phase))))
    assert (result.text, result.commentary_present) == (text, False)


def test_ag102_commentary_excluded_and_flagged():
    commentary = message(
        '[{"type":"output_text","text":"SENTINEL-COMMENTARY"}]', phase='"commentary"'
    )
    only = answer(envelope(output_of(commentary)))
    assert (only.text, only.commentary_present, only.status) == ("", True, "completed")

    final = message('[{"type":"output_text","text":"Resposta final"}]', phase='"final_answer"')
    both = answer(envelope(output_of(commentary, final)))
    assert both.text == "Resposta final"
    assert both.commentary_present is True
    assert [item.raw for item in both.output] == [commentary, final]
    assert '"phase":"commentary"' in both.output[0].raw
    assert '"phase":"final_answer"' in both.output[1].raw


@pytest.mark.parametrize("phase", ['"draft"', "5", '""', '"COMMENTARY"'])
def test_ag102_unknown_phase_is_invalid(phase):
    invalid(envelope(output_of(message(phase=phase))))


@pytest.mark.parametrize("usage", [OMIT, "null"])
def test_ag103_absent_usage_is_none_not_invalid(usage):
    result = answer(envelope(usage=usage))
    assert (result.usage, result.usage_invalid) == (None, False)


_GOOD_DETAILS = (
    '"input_tokens_details":{"cached_tokens":1},"output_tokens_details":{"reasoning_tokens":2}'
)


@pytest.mark.parametrize(
    "usage",
    [
        '{"input_tokens":true,"output_tokens":1,"total_tokens":2}',
        '{"input_tokens":false,"output_tokens":1,"total_tokens":2}',
        '{"input_tokens":-1,"output_tokens":1,"total_tokens":2}',
        '{"input_tokens":1.0,"output_tokens":1,"total_tokens":2}',
        '{"input_tokens":"5","output_tokens":1,"total_tokens":2}',
        '{"input_tokens":null,"output_tokens":1,"total_tokens":2}',
        '{"output_tokens":1,"total_tokens":2}',
        '{"input_tokens":1,"total_tokens":2}',
        '{"input_tokens":1,"output_tokens":1}',
        '{"input_tokens":1,"output_tokens":true,"total_tokens":2}',
        '{"input_tokens":1,"output_tokens":1,"total_tokens":-2}',
        '{"input_tokens":1,"output_tokens":1,"total_tokens":2,'
        '"input_tokens_details":{"cached_tokens":false}}',
        '{"input_tokens":1,"output_tokens":1,"total_tokens":2,'
        '"input_tokens_details":{"cached_tokens":-1}}',
        '{"input_tokens":1,"output_tokens":1,"total_tokens":2,"input_tokens_details":{}}',
        '{"input_tokens":1,"output_tokens":1,"total_tokens":2,"input_tokens_details":5}',
        '{"input_tokens":1,"output_tokens":1,"total_tokens":2,'
        '"output_tokens_details":{"reasoning_tokens":1.5}}',
        '{"input_tokens":1,"output_tokens":1,"total_tokens":2,"output_tokens_details":[]}',
        "5",
        "[]",
        '"usage"',
        "true",
    ],
)
def test_ag103_invalid_usage_is_none_and_flagged(usage):
    result = answer(envelope(usage=usage))
    assert result.usage is None
    assert result.usage_invalid is True
    assert result.status == "completed"
    assert result.text == "Olá! Como posso ajudar?"


def test_ag103_null_details_are_none():
    usage = (
        '{"input_tokens":3,"output_tokens":4,"total_tokens":7,'
        '"input_tokens_details":null,"output_tokens_details":{"reasoning_tokens":2}}'
    )
    assert answer(envelope(usage=usage)).usage == Usage(3, 4, 7, None, 2)
    usage = '{"input_tokens":3,"output_tokens":4,"total_tokens":7,' + _GOOD_DETAILS + "}"
    assert answer(envelope(usage=usage)).usage == Usage(3, 4, 7, 1, 2)


@pytest.mark.parametrize(
    "item",
    [
        message(r'[{"type":"output_text","text":"a\ud800"}]'),
        message(r'[{"type":"refusal","refusal":"\udfff"}]'),
        function_call(arguments=r'"{\"q\":\"\ud800\"}"'),
        function_call(name=r'"get\ud800"'),
        reasoning(encrypted_content=r'"\ud800"'),
        reasoning(future=r'"\ud800"'),
        reasoning(future=r'{"nested":["\udc00"]}'),
    ],
)
def test_ag104_lone_surrogate_is_invalid(item):
    invalid(envelope(output_of(item)))


@pytest.mark.parametrize("model", [r'"gpt\ud800"', r'"\udc00"'])
def test_ag104_lone_surrogate_in_model_or_id_is_invalid(model):
    invalid(envelope(model=model))
    invalid(envelope(rid=model))


def test_ag104_valid_surrogate_pair_is_accepted():
    result = answer(envelope(output_of(message(r'[{"type":"output_text","text":"🎸"}]'))))
    assert result.text == "🎸"
    assert "🎸" in result.output[0].raw


@pytest.mark.parametrize(
    "item",
    [
        reasoning(id=OMIT),
        reasoning(id='""'),
        reasoning(id="null"),
        reasoning(summary=OMIT),
        reasoning(summary='"resumo"'),
        reasoning(summary="null"),
        reasoning(encrypted_content="5"),
        reasoning(content='"texto"'),
        reasoning(status='"done"'),
        reasoning(status="5"),
    ],
)
def test_ag105_reasoning_rules_r1(item):
    invalid(envelope(output_of(item)))


def test_ag105_reasoning_optional_fields_accepted():
    item = reasoning(encrypted_content="null", content="[]", status='"completed"')
    assert answer(envelope(output_of(item))).output == (ProviderItem(kind="reasoning", raw=item),)
    item = reasoning(content="null", status="null")
    assert answer(envelope(output_of(item))).status == "completed"


@pytest.mark.parametrize(
    "item",
    [
        reasoning(status='"incomplete"'),
        reasoning(status='"in_progress"'),
        function_call(status='"in_progress"'),
        function_call(status='"incomplete"'),
    ],
)
def test_ag105_incomplete_items_make_response_incomplete(item):
    result = answer(envelope(output_of(item)))
    assert (result.status, result.incomplete_reason) == ("incomplete", "item_incomplete")
    assert len(result.output) == 1


def test_ag106_raw_preservation_and_resend():
    rich_reasoning = (
        '{"type":"reasoning","id":"rs_7","summary":[],"encrypted_content":"ENC/ção+==",'
        '"future_key":{"z":1,"a":[true,null,2.5]},"status":"completed"}'
    )
    rich_message = (
        '{"type":"message","id":"msg_7","status":"completed","role":"assistant",'
        '"phase":"commentary","content":[{"type":"output_text","text":"Vou consultar.",'
        '"annotations":[],"logprobs":[]}],"x_new":"kept"}'
    )
    rich_call = (
        r'{"type":"function_call","id":"fc_7","call_id":"call_7","name":"get_product",'
        r'"arguments":"{\"product_id\":211}","status":"completed","caller":{"type":"direct"},'
        r'"namespace":null}'
    )
    first = envelope(output_of(rich_reasoning, rich_message, rich_call))
    transport = Transport(reply(first), reply(envelope()))
    with make(transport) as provider:
        result = provider.create(request(UserMessage("Preço do 211?")))
        assert [item.raw for item in result.output] == [rich_reasoning, rich_message, rich_call]
        payload = '{"status":"ok","data":{"product_id":211}}'
        provider.create(
            request(
                UserMessage("Preço do 211?"),
                *result.output,
                FunctionCallOutput(call_id="call_7", output=payload),
            )
        )
    sent = transport.body(1)["input"]
    compact = [json.dumps(item, ensure_ascii=False, separators=(",", ":")) for item in sent]
    assert compact == [
        '{"type":"message","role":"user","content":"Preço do 211?"}',
        rich_reasoning,
        rich_message,
        rich_call,
        '{"type":"function_call_output","call_id":"call_7","output":'
        + json.dumps(payload, ensure_ascii=False)
        + "}",
    ]


# --- AG122-AG124: optional reasoning effort (spec G4, review 07-07-R1) ----------------------
# None omits the reasoning configuration; "none" would request effort none explicitly and is
# out of this revision's scope (not equivalent to None). Oracles are literal bodies.

_EFFORTS = ["low", "medium", "high", "xhigh"]
_AG06_BODY = {
    "model": "gpt-5.4-mini",
    "instructions": "Responda em português.",
    "input": [{"type": "message", "role": "user", "content": "Quanto custa o violão?"}],
    "tools": [TOOL],
    "tool_choice": "auto",
    "parallel_tool_calls": False,
    "store": False,
    "max_output_tokens": 8000,
}


class _EffortText(str):
    """A str subclass: rejected, the type must be exactly str."""


_BAD_EFFORTS = [
    "none",
    "minimal",
    "max",
    "",
    " low",
    "low ",
    "LOW",
    "Low",
    "SENTINEL-EFFORT",
    True,
    1,
    b"low",
    ["low"],
    {"effort": "low"},
    _EffortText("low"),
]


def test_ag122_allowed_efforts_constant():
    import emporio_agent.openai_provider as adapter

    assert getattr(adapter, "REASONING_EFFORTS", None) == ("low", "medium", "high", "xhigh")


def test_ag122_explicit_none_keeps_the_ag06_body():
    transport = Transport(reply(envelope()))
    with make(transport, reasoning_effort=None) as provider:
        provider.create(request(UserMessage("Quanto custa o violão?")))
    body = transport.body()
    assert body == _AG06_BODY
    assert "reasoning" not in body and "include" not in body


@pytest.mark.parametrize("effort", _EFFORTS)
def test_ag122_each_effort_adds_only_reasoning_effort(effort):
    transport = Transport(reply(envelope()), reply(envelope()))
    with make(transport, reasoning_effort=effort) as provider:
        provider.create(request(UserMessage("Quanto custa o violão?")))
        provider.create(request(UserMessage("Quanto custa o violão?"), allow=False))
    assert transport.body(0) == _AG06_BODY | {"reasoning": {"effort": effort}}
    assert transport.body(1) == _AG06_BODY | {
        "tool_choice": "none",
        "reasoning": {"effort": effort},
    }
    for index in (0, 1):
        assert "include" not in transport.body(index)


@pytest.mark.parametrize("value", _BAD_EFFORTS, ids=repr)
def test_ag123_invalid_effort_rejected_without_echo_and_client_closed(value):
    client = httpx2.Client(transport=httpx2.MockTransport(Transport()))
    with pytest.raises(ValueError) as info:
        OpenAIResponsesProvider(
            api_key=KEY, model=MODEL, reasoning_effort=value, http_client=client
        )
    message = str(info.value)
    assert client.is_closed
    assert message == "reasoning_effort must be None or one of the supported effort values"
    assert "SENTINEL" not in message and KEY not in message


def test_ag123_effort_checked_after_the_other_arguments():
    with pytest.raises(ValueError) as info:
        OpenAIResponsesProvider(api_key=" ", model=MODEL, reasoning_effort="none")
    assert str(info.value) == "api_key must be a non-blank str without surrounding whitespace"


def test_ag124_repr_and_model_unchanged_with_effort():
    with make(Transport(), reasoning_effort="high") as provider:
        assert provider.model == MODEL
        assert repr(provider) == "OpenAIResponsesProvider(model='gpt-5.4-mini')"
        assert "high" not in repr(provider)


def test_ag124_reasoning_received_and_resent_unchanged_with_effort():
    rich_reasoning = (
        '{"type":"reasoning","id":"rs_e1","summary":[],"encrypted_content":"gAAAAB-EFFORT+/==",'
        '"status":"completed","future_key":{"kept":true}}'
    )
    first = envelope(output_of(rich_reasoning, CALL))
    transport = Transport(reply(first), reply(envelope()))
    with make(transport, reasoning_effort="low") as provider:
        result = provider.create(request(UserMessage("Preço do 211?")))
        assert result.output[0] == ProviderItem(kind="reasoning", raw=rich_reasoning)
        payload = '{"status":"ok","data":{"product_id":211}}'
        provider.create(
            request(
                UserMessage("Preço do 211?"),
                *result.output,
                FunctionCallOutput(call_id="call_1", output=payload),
            )
        )
    second = transport.body(1)
    assert second["reasoning"] == {"effort": "low"}
    compact = [
        json.dumps(item, ensure_ascii=False, separators=(",", ":")) for item in second["input"]
    ]
    assert compact[1:3] == [rich_reasoning, CALL]
    assert second["input"][3] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": payload,
    }


# --- RM01-RM05: reported reasoning effort (spec 07 Review 07-10-D5, rule B8) --------------

_REPORTED = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


def test_rm01_reported_efforts_constant_matches_the_sdk_literal():
    import typing

    from openai.types.shared.reasoning_effort import ReasoningEffort

    import emporio_agent.openai_provider as adapter

    assert adapter.REPORTED_REASONING_EFFORTS == _REPORTED
    sdk_values = {
        value for arg in typing.get_args(ReasoningEffort) for value in typing.get_args(arg)
    }
    assert set(adapter.REPORTED_REASONING_EFFORTS) == sdk_values
    # Requested values stay a separate, narrower constant (AG122).
    assert adapter.REASONING_EFFORTS == ("low", "medium", "high", "xhigh")


@pytest.mark.parametrize("effort", _REPORTED)
def test_rm02_each_reported_effort_is_extracted(effort):
    reasoning_json = '{"effort":"' + effort + '","summary":null,"context":"all_turns"}'
    result = answer(envelope(extra=',"reasoning":' + reasoning_json))
    assert result.reasoning_effort_reported == effort
    assert result.text == "Olá! Como posso ajudar?"


def test_rm03_reported_effort_differs_from_requested_effort():
    transport = Transport(reply(envelope(extra=',"reasoning":{"effort":"medium","summary":null}')))
    with make(transport, reasoning_effort="low") as provider:
        result = provider.create(request(UserMessage("Quanto custa o violão?")))
    assert transport.body()["reasoning"] == {"effort": "low"}
    assert result.reasoning_effort_reported == "medium"


@pytest.mark.parametrize(
    "extra",
    ["", ',"reasoning":null', ',"reasoning":{}', ',"reasoning":{"effort":null,"summary":null}'],
    ids=["absent", "null", "no-effort", "effort-null"],
)
@pytest.mark.parametrize("requested", [None, "low"])
def test_rm04_absent_effort_has_no_fallback(extra, requested):
    transport = Transport(reply(envelope(extra=extra)))
    with make(transport, reasoning_effort=requested) as provider:
        result = provider.create(request())
    assert result.reasoning_effort_reported is None


_BASELINE = ModelResponse(
    response_id="resp_1",
    model=SNAPSHOT,
    status="completed",
    incomplete_reason=None,
    output=(ProviderItem(kind="assistant_message", raw=MSG),),
    text="Olá! Como posso ajudar?",
    commentary_present=False,
    refusal=False,
    usage=Usage(
        input_tokens=120,
        output_tokens=30,
        total_tokens=150,
        cached_input_tokens=40,
        reasoning_tokens=10,
    ),
    usage_invalid=False,
    request_id=None,
)


@pytest.mark.parametrize(
    "reasoning_json",
    [
        '"low"',
        '["low"]',
        "1",
        "true",
        '{"effort":1}',
        '{"effort":true}',
        '{"effort":["low"]}',
        '{"effort":{"value":"low"}}',
        '{"effort":"LOW"}',
        '{"effort":"Low"}',
        '{"effort":" low"}',
        '{"effort":"low "}',
        '{"effort":""}',
        '{"effort":"SENTINEL-EFFORT x"}',
        '{"effort":"\\u001b[31m"}',
        '{"effort":"low\\nx"}',
    ],
)
def test_rm05_invalid_metadata_is_ignored_and_the_answer_is_kept(reasoning_json):
    result = answer(envelope(extra=',"reasoning":' + reasoning_json))
    assert result == _BASELINE
    assert result.reasoning_effort_reported is None
    assert "SENTINEL" not in repr(result)


def test_rm05_baseline_without_reasoning_equals_the_literal():
    assert answer(envelope()) == _BASELINE
