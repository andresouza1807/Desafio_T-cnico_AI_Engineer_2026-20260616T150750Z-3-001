"""Slice 07-A provider contract cases (docs/plans/07-agent.md, spec 07 G2, G3, G10b).

Covers the provider-neutral part of AG01 (import isolation in a subprocess with a raising
socket guard), the contract-type checks listed under AG01/AG08, the ProviderError surface
of spec G3, AG107 (size functions against literal byte counts) and AG108 (item
constructors reject non-str and non-UTF-8-encodable text with a fixed message).
Every expected size is a literal written by hand; no production helper computes an oracle.
"""

import dataclasses
import json
import os
import subprocess
import sys
import textwrap
import typing

import pytest

from emporio_agent.provider import (
    FunctionCall,
    FunctionCallOutput,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderItem,
    Usage,
    UserMessage,
    item_size_bytes,
    static_size_bytes,
)

LONE = "\ud800"  # lone surrogate: a str that cannot be encoded as UTF-8

# --- AG01: import isolation, isolated subprocess -----------------------------------------

_CHILD = textwrap.dedent(
    """
    import json
    import socket
    import sys

    attempts = []

    def guard(*args, **kwargs):
        attempts.append(1)
        raise AssertionError("network access attempted")

    class GuardSocket(socket.socket):
        # A class, not a function: modules such as ssl subclass socket.socket on import.
        def __init__(self, *args, **kwargs):
            guard()

    socket.socket = GuardSocket
    socket.create_connection = guard
    socket.getaddrinfo = guard
    socket.socketpair = guard

    import emporio_agent.provider

    openai_after_provider = "openai" in sys.modules

    import openai

    constructed = []
    original_init = openai.OpenAI.__init__

    def spy(self, *args, **kwargs):
        constructed.append(1)
        original_init(self, *args, **kwargs)

    openai.OpenAI.__init__ = spy

    import emporio_agent.openai_provider as adapter

    module_clients = [
        name for name, value in vars(adapter).items() if isinstance(value, openai.OpenAI)
    ]
    print(json.dumps({
        "openai_after_provider": openai_after_provider,
        "constructed": len(constructed),
        "module_clients": module_clients,
        "attempts": len(attempts),
        "env": sorted(key for key in __import__("os").environ if key.startswith("OPENAI")),
    }))
    """
)


def test_ag01_imports_create_no_client_and_open_no_socket(tmp_path):
    script = tmp_path / "filho.py"
    script.write_text(_CHILD, encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if not key.startswith("OPENAI")}

    completed = subprocess.run(
        [sys.executable, "-I", str(script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=120,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report == {
        "openai_after_provider": False,
        "constructed": 0,
        "module_clients": [],
        "attempts": 0,
        "env": [],
    }


# --- AG01/AG08: contract types --------------------------------------------------------------


def _usage() -> Usage:
    return Usage(
        input_tokens=1,
        output_tokens=2,
        total_tokens=3,
        cached_input_tokens=None,
        reasoning_tokens=None,
    )


def _response() -> ModelResponse:
    return ModelResponse(
        response_id="resp_1",
        model="m",
        status="completed",
        incomplete_reason=None,
        output=(),
        text="",
        commentary_present=False,
        refusal=False,
        usage=None,
        usage_invalid=False,
        request_id=None,
    )


@pytest.mark.parametrize(
    ("instance", "field"),
    [
        (UserMessage("oi"), "text"),
        (ProviderItem(kind="reasoning", raw="{}"), "raw"),
        (FunctionCall(call_id="c", name="n", arguments="{}", raw="{}"), "arguments"),
        (FunctionCallOutput(call_id="c", output="{}"), "output"),
        (ModelRequest(instructions="i", items=(), tools=(), allow_tool_calls=True), "items"),
        (_usage(), "input_tokens"),
        (_response(), "text"),
    ],
)
def test_contract_types_are_frozen_slotted_dataclasses(instance, field):
    assert dataclasses.is_dataclass(instance)
    assert not hasattr(instance, "__dict__")
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(instance, field, None)


def test_model_provider_is_a_protocol_satisfied_by_a_scripted_provider():
    class Scripted:
        def __init__(self) -> None:
            self.requests: list[ModelRequest] = []

        @property
        def model(self) -> str:
            return "scripted"

        def create(self, request: ModelRequest) -> ModelResponse:
            self.requests.append(request)
            return _response()

    def call(provider: ModelProvider, request: ModelRequest) -> ModelResponse:
        return provider.create(request)

    assert typing.is_protocol(ModelProvider)
    scripted = Scripted()
    assert isinstance(scripted, ModelProvider)
    request = ModelRequest(
        instructions="i", items=(UserMessage("oi"),), tools=(), allow_tool_calls=False
    )
    assert call(scripted, request) == _response()
    assert scripted.requests == [request]
    assert not isinstance(object(), ModelProvider)


def test_provider_item_kind_is_restricted():
    assert ProviderItem(kind="assistant_message", raw="{}").kind == "assistant_message"
    with pytest.raises(ValueError) as info:
        ProviderItem(kind="function_call", raw="{}")  # type: ignore[arg-type]
    assert "function_call" not in str(info.value)


# --- G3: ProviderError surface ----------------------------------------------------------------

KINDS = {
    "timeout": True,
    "connection": True,
    "rate_limited": True,
    "server_error": True,
    "quota": False,
    "authentication": False,
    "permission": False,
    "bad_request": False,
    "model_unavailable": False,
    "response_invalid": False,
    "unexpected": False,
}


@pytest.mark.parametrize(("kind", "transient"), sorted(KINDS.items()))
def test_provider_error_kind_fixes_transient_and_message(kind, transient):
    error = ProviderError(kind)
    assert error.kind == kind
    assert error.transient is transient
    assert error.status_code is None
    assert error.request_id is None
    assert error.retry_after_seconds is None
    assert error.args == (str(error),)
    assert str(error) and str(error).isascii()
    assert str(error) == str(ProviderError(kind, status_code=500, request_id="req_x"))


def test_provider_error_messages_are_distinct_per_kind():
    messages = {str(ProviderError(kind)) for kind in KINDS}
    messages.add(str(ProviderError("provider_failed", transient=True)))
    assert len(messages) == 12


def test_provider_failed_transient_is_explicit():
    assert ProviderError("provider_failed").transient is False
    assert ProviderError("provider_failed", transient=False).transient is False
    assert ProviderError("provider_failed", transient=True).transient is True


def test_provider_error_rejects_unknown_kind_and_contradictory_transient():
    with pytest.raises(ValueError) as info:
        ProviderError("SENTINEL-KIND")  # type: ignore[arg-type]
    assert "SENTINEL-KIND" not in str(info.value)
    with pytest.raises(ValueError):
        ProviderError("quota", transient=True)
    with pytest.raises(ValueError):
        ProviderError("timeout", transient=False)


def test_provider_error_keeps_metadata():
    error = ProviderError(
        "rate_limited", status_code=429, request_id="req_1", retry_after_seconds=1.5
    )
    assert (error.status_code, error.request_id, error.retry_after_seconds) == (429, "req_1", 1.5)


# --- AG107: size functions against literal byte counts --------------------------------------


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        (UserMessage("abc"), 3),
        (UserMessage("ç"), 2),
        (UserMessage("🎸"), 4),
        (UserMessage("ação 🎸"), 11),
        (UserMessage(""), 0),
        (ProviderItem(kind="reasoning", raw="x" * 200_000), 200_000),
        (ProviderItem(kind="assistant_message", raw="ç" * 10), 20),
        # FunctionCall counts raw only; call_id, name and arguments are inside raw.
        (FunctionCall(call_id="call_1", name="get_product", arguments="{}", raw="🎸" * 3), 12),
        # call_id (6 bytes) + output '{"ok":"ç"}' (11 bytes).
        (FunctionCallOutput(call_id="call_1", output='{"ok":"ç"}'), 17),
        (FunctionCallOutput(call_id="ç", output=""), 2),
    ],
)
def test_ag107_item_size_bytes_literal(item, expected):
    assert item_size_bytes(item) == expected


def test_ag107_item_size_bytes_rejects_other_objects():
    with pytest.raises(TypeError):
        item_size_bytes("texto")  # type: ignore[arg-type]


def test_ag107_static_size_bytes_literal():
    tool = {"type": "function", "name": "x"}
    # "Instruções": 10 code points, 12 bytes. '[{"type":"function","name":"x"}]': 32 bytes.
    assert static_size_bytes("Instruções", (tool,)) == 44
    assert static_size_bytes("Instruções", (tool,)) == len(
        "Instruções".encode() + b'[{"type":"function","name":"x"}]'
    )
    # ensure_ascii=False: '[{"d":"ã"}]' is 12 bytes (an ASCII escape would be 16).
    assert static_size_bytes("", ({"d": "ã"},)) == 12
    assert static_size_bytes("abc", ()) == 5  # "abc" + "[]"


# --- AG108: constructors reject non-str and non-encodable text ------------------------------

_BUILDERS = {
    "UserMessage.text": lambda v: UserMessage(text=v),
    "ProviderItem.raw": lambda v: ProviderItem(kind="reasoning", raw=v),
    "FunctionCall.call_id": lambda v: FunctionCall(call_id=v, name="n", arguments="{}", raw="{}"),
    "FunctionCall.name": lambda v: FunctionCall(call_id="c", name=v, arguments="{}", raw="{}"),
    "FunctionCall.arguments": lambda v: FunctionCall(call_id="c", name="n", arguments=v, raw="{}"),
    "FunctionCall.raw": lambda v: FunctionCall(call_id="c", name="n", arguments="{}", raw=v),
    "FunctionCallOutput.call_id": lambda v: FunctionCallOutput(call_id=v, output="{}"),
    "FunctionCallOutput.output": lambda v: FunctionCallOutput(call_id="c", output=v),
}
_BAD_VALUES = [
    "SENTINEL-TEXT" + LONE,
    LONE + "SENTINEL-TEXT",
    "SENTINEL-TEXT\udfff",
    None,
    b"SENTINEL-TEXT",
    ["SENTINEL-TEXT"],
    987654,
]


@pytest.mark.parametrize("field", sorted(_BUILDERS))
@pytest.mark.parametrize("value", _BAD_VALUES, ids=repr)
def test_ag108_constructors_reject_bad_text(field, value):
    with pytest.raises(ValueError) as info:
        _BUILDERS[field](value)
    message = str(info.value)
    assert "SENTINEL" not in message
    assert "987654" not in message
    assert LONE not in message
    assert message.isascii()
    assert info.value.__cause__ is None


@pytest.mark.parametrize("field", sorted(_BUILDERS))
def test_ag108_message_is_fixed_per_field(field):
    messages = set()
    for value in _BAD_VALUES:
        with pytest.raises(ValueError) as info:
            _BUILDERS[field](value)
        messages.add(str(info.value))
    assert len(messages) == 1


@pytest.mark.parametrize("field", sorted(_BUILDERS))
def test_ag108_valid_pair_and_empty_text_accepted(field):
    _BUILDERS[field]("🎸")
    _BUILDERS[field]("🎸".encode("utf-16", "surrogatepass").decode("utf-16"))
    _BUILDERS[field]("")


def test_rm06_model_response_reported_effort_is_last_and_defaults_to_none():
    # Spec 07 Review 07-10-D5: a defaulted last field keeps older constructions valid.
    names = [field.name for field in dataclasses.fields(ModelResponse)]
    assert names[-1] == "reasoning_effort_reported"
    assert names[:-1] == [
        "response_id",
        "model",
        "status",
        "incomplete_reason",
        "output",
        "text",
        "commentary_present",
        "refusal",
        "usage",
        "usage_invalid",
        "request_id",
    ]
    assert _response().reasoning_effort_reported is None
    reported = dataclasses.replace(_response(), reasoning_effort_reported="low")
    assert reported.reasoning_effort_reported == "low"
