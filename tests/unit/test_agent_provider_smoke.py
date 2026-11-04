"""Offline tests of the opt-in real-provider evaluator (docs/plans/07-agent.md, slice E).

Contract: docs/validation/07-05-agent-provider-smoke.md. The evaluator is loaded from
`evals/agent_provider_smoke.py` by path. Nothing here uses a real key or the network:
every `OPENAI_*` variable is removed, sockets and DNS raise, and the OpenAI SDK runs over
`httpx2.MockTransport` with literal HTTP bodies written in this module. Below the adapter
everything is real: the evaluator, the agent loop, the session, `execute_tool`, SQL over
the database built by the real `prepare_database` from the committed synthetic fixture,
and the real `load_policy_index`. These tests prove the evaluator's mechanics only; they do
not validate EV01-EV08, which need the real provider (spec Q-07).
"""

import importlib.util
import itertools
import json
import logging
import os
import re
import socket
import subprocess
import tempfile
import textwrap
from collections.abc import Iterator, Mapping
from pathlib import Path

import httpx2
import pytest

import emporio_agent

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "evals" / "agent_provider_smoke.py"

KEY = "test-key-not-a-secret"
KEY_SENTINEL = "sk-SENTINEL-KEY-0123456789"
MODEL = "gpt-5.4-mini"
SNAPSHOT = "gpt-5.4-mini-2026-03-17"
UNKNOWN = "emporio-smoke-unknown-model-0000"
URL = "https://api.openai.com/v1/responses"
# report 10-10 (10-08: 12,295; 10-06: 11,853); same literal as tests/integration
STATIC_ANONYMOUS = 12_732
MODEL_UNAVAILABLE = "The configured model is not available."

# User texts of the contract (report 07-05, "Scenarios and order").
TURN_1 = "Quais violões vocês têm disponíveis? Quero saber o preço."
TURN_2 = "E quanto fica esse violão no PIX?"
EV07_TEXT = "Obrigado! Pode resumir em uma frase o que você encontrou?"
EV08_TEXT = "Olá"

# Literal provider items, compact, in provider key order.
ENCRYPTED_1 = "ENC-SENTINEL-ONE+/=="
ENCRYPTED_2 = "ENC-SENTINEL-TWO+/=="
REASONING_1 = (
    '{"type":"reasoning","id":"rs_1","summary":[],"encrypted_content":"' + ENCRYPTED_1 + '"}'
)
REASONING_2 = (
    '{"type":"reasoning","id":"rs_2","summary":[],"encrypted_content":"' + ENCRYPTED_2 + '"}'
)
REASONING_PLAIN = '{"type":"reasoning","id":"rs_p","summary":[]}'
SEARCH_ARGS = '{"query":"violão"}'
CALL_1 = (
    r'{"type":"function_call","id":"fc_1","call_id":"call_s1","name":"search_products",'
    r'"arguments":"{\"query\":\"violão\"}","status":"completed"}'
)
CALL_1B = (
    r'{"type":"function_call","id":"fc_1b","call_id":"call_s1b","name":"search_products",'
    r'"arguments":"{\"query\":\"piano\"}","status":"completed"}'
)
QUOTE_ARGS = '{"product_id":10,"payment_method":"pix"}'
CALL_2 = (
    r'{"type":"function_call","id":"fc_2","call_id":"call_s2","name":"quote_product",'
    r'"arguments":"{\"product_id\":10,\"payment_method\":\"pix\"}","status":"completed"}'
)
CALL_7 = (
    r'{"type":"function_call","id":"fc_7","call_id":"call_s7","name":"search_products",'
    r'"arguments":"{\"query\":\"violão\"}","status":"completed"}'
)
ANSWER_1 = "ANSWER-SENTINEL-1 Temos o Violão Teste."
ANSWER_2 = "ANSWER-SENTINEL-2 No PIX fica igual ao preço promocional."
ANSWER_7 = "ANSWER-SENTINEL-7 Resumo do catálogo."


def message(answer: str, mid: str, phase: str | None = "final_answer") -> str:
    phase_part = '"phase":"' + phase + '",' if phase is not None else ""
    return (
        '{"type":"message","id":"'
        + mid
        + '","role":"assistant","status":"completed",'
        + phase_part
        + '"content":[{"type":"output_text","text":"'
        + answer
        + '","annotations":[]}]}'
    )


MESSAGE_1 = message(ANSWER_1, "msg_1")
MESSAGE_2 = message(ANSWER_2, "msg_2")
MESSAGE_7 = message(ANSWER_7, "msg_7", phase=None)
USAGE = (
    '{"input_tokens":1000,"output_tokens":50,"total_tokens":1050,'
    '"input_tokens_details":{"cached_tokens":0},"output_tokens_details":{"reasoning_tokens":10}}'
)
PROVIDER_SENTINEL = "PROVIDER-SENTINEL message from the provider"
ERROR_404 = (
    '{"error":{"message":"' + PROVIDER_SENTINEL + '","type":"invalid_request_error",'
    '"param":null,"code":"model_not_found"}}'
)
ERROR_400 = (
    '{"error":{"message":"' + PROVIDER_SENTINEL + '","type":"invalid_request_error",'
    '"param":"tools","code":"invalid_function_parameters"}}'
)
ERROR_503 = '{"error":{"message":"' + PROVIDER_SENTINEL + '","type":"server_error"}}'

ALL_SENTINELS = (
    KEY,
    KEY_SENTINEL,
    TURN_1,
    TURN_2,
    EV07_TEXT,
    "violão",
    "Violão Teste",
    "ANSWER-SENTINEL",
    "ENC-SENTINEL",
    "call_s1",
    "call_s2",
    "product_id",
    "PROVIDER-SENTINEL",
    "invalid_function_parameters",
    "Authorization",
    "Bearer",
    "Traceback",
    "emporio-eval-",  # temporary directory prefix: no local path is printed
    str(REPO),
    "fixtures",
)

LINE = re.compile(
    r"(agent_provider_smoke|config:|data:|attempt:|turn:|EV0[1-8]|resources:|summary:)"
    r"( [a-z0-9_]+=[A-Za-z0-9._:/,-]+)+"
)


def body(*output: str, rid: str = "resp_1", usage: str | None = USAGE, model=SNAPSHOT) -> bytes:
    usage_part = ',"usage":' + usage if usage is not None else ""
    text = (
        '{"id":"' + rid + '","object":"response","status":"completed",'
        '"model":"' + model + '","output":[' + ",".join(output) + "]" + usage_part + "}"
    )
    return text.encode("utf-8")


def ok(*output: str, **kwargs) -> tuple[int, bytes]:
    return 200, body(*output, **kwargs)


HAPPY = (
    ok(REASONING_1, CALL_1, rid="resp_1"),
    ok(MESSAGE_1, rid="resp_2"),
    ok(REASONING_2, CALL_2, rid="resp_3"),
    ok(MESSAGE_2, rid="resp_4"),
    ok(MESSAGE_7, rid="resp_5"),
    (404, ERROR_404.encode("utf-8")),
)


# --- guards and helpers (test-only) ---------------------------------------------------


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    for name in [name for name in os.environ if name.startswith("OPENAI_")]:
        monkeypatch.delenv(name)

    def guard(*args, **kwargs):
        raise AssertionError("network access attempted")

    for name in ("create_connection", "getaddrinfo", "gethostbyname", "gethostbyname_ex"):
        monkeypatch.setattr(socket, name, guard)
    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket.socket, "connect_ex", guard)


@pytest.fixture
def scratch_root(tmp_path, monkeypatch) -> Path:
    """Redirect the system temporary directory, to see what the evaluator leaves behind."""
    root = tmp_path / "system-temp"
    root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    return root


@pytest.fixture(scope="module")
def smoke():
    spec = importlib.util.spec_from_file_location("agent_provider_smoke_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Transport:
    """MockTransport handler: records each request, answers with the next scripted item."""

    def __init__(self, script) -> None:
        self.script = list(script)
        self.requests: list[httpx2.Request] = []
        self.clients: list[httpx2.Client] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if not self.script:
            raise AssertionError("unscripted HTTP request")
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        status, content = item
        number = len(self.requests)
        return httpx2.Response(
            status,
            content=content,
            headers={"x-request-id": f"req_{number}", "content-type": "application/json"},
        )

    def client(self) -> httpx2.Client:
        client = httpx2.Client(transport=httpx2.MockTransport(self))
        self.clients.append(client)
        return client

    def sent(self, index: int) -> dict:
        return json.loads(self.requests[index].content)


def clock() -> Iterator[float]:
    counter = itertools.count()
    return lambda: next(counter) * 0.25


def run_main(smoke, capsys, script, *, argv=None, environ=None, **kwargs):
    transport = Transport(script)
    sleeps: list[float] = []
    code = smoke.main(
        ["--allow-real-calls", "--model", MODEL] if argv is None else argv,
        environ={"OPENAI_API_KEY": KEY} if environ is None else environ,
        http_client_factory=transport.client,
        sleep=sleeps.append,
        monotonic=clock(),
        **kwargs,
    )
    captured = capsys.readouterr()
    return code, captured.out, captured.err, transport, sleeps


def lines(out: str, prefix: str) -> list[str]:
    return [line for line in out.splitlines() if line.startswith(prefix)]


def ev(out: str, case: str) -> str:
    (line,) = lines(out, case + " ")
    return line


def field(line: str, key: str) -> str:
    match = re.search(r"(?:^| )" + re.escape(key) + r"=(\S+)", line)
    assert match is not None, (key, line)
    return match.group(1)


def assert_private(out: str, err: str = "") -> None:
    for sentinel in ALL_SENTINELS:
        assert sentinel not in out, sentinel
        assert sentinel not in err, sentinel


def assert_grammar(out: str) -> None:
    for line in out.splitlines():
        assert LINE.fullmatch(line), line


class RecordingEnv(Mapping[str, str]):
    """Environment stand-in that records every key read."""

    def __init__(self, values: dict[str, str]) -> None:
        self.values = values
        self.reads: list[str] = []

    def __getitem__(self, key: str) -> str:
        self.reads.append(key)
        return self.values[key]

    def __contains__(self, key: object) -> bool:
        self.reads.append(str(key))
        return key in self.values

    def get(self, key, default=None):
        self.reads.append(key)
        return self.values.get(key, default)

    def __iter__(self):
        self.reads.append("<iter>")
        return iter(self.values)

    def __len__(self) -> int:
        return len(self.values)


# --- constants and import safety ------------------------------------------------------


def test_contract_constants(smoke):
    assert smoke.MAX_PROVIDER_CALLS == 12
    assert smoke.MAX_OUTPUT_TOKENS_PER_CALL == 1024
    assert smoke.TIMEOUT_SECONDS == 30.0
    assert smoke.UNKNOWN_MODEL == UNKNOWN
    assert smoke.KEY_ENV == "OPENAI_API_KEY"
    assert smoke.MIN_BYTES_PER_INPUT_TOKEN == 2.5
    assert smoke.MODEL_ITEMS_ALLOWANCE_BYTES == 262_144 - 16_000 - 6 * 32_768 == 49_536
    assert (
        smoke.EXIT_PASSED,
        smoke.EXIT_FAILED,
        smoke.EXIT_USAGE,
        smoke.EXIT_NOT_OBSERVED,
        smoke.EXIT_INTERRUPTED,
        smoke.EXIT_DATA,
        smoke.EXIT_INTERNAL,
    ) == (0, 1, 2, 3, 4, 5, 6)


_CHILD = textwrap.dedent(
    """
    import importlib.util, json, os, runpy, socket, sys, tempfile

    src, script, mode = sys.argv[1], sys.argv[2], sys.argv[3]
    sys.path.insert(0, src)
    events = []

    def network(*args, **kwargs):
        events.append("network")
        raise OSError("network blocked")

    for name in ("create_connection", "getaddrinfo", "gethostbyname", "gethostbyname_ex"):
        setattr(socket, name, network)
    socket.socket.connect = network
    socket.socket.connect_ex = network
    original_init = socket.socket.__init__

    def socket_init(self, *args, **kwargs):
        events.append("socket")
        original_init(self, *args, **kwargs)

    socket.socket.__init__ = socket_init
    original_getitem = os._Environ.__getitem__

    def getitem(self, key):
        if str(key).startswith("OPENAI"):
            events.append("env:" + str(key))
        return original_getitem(self, key)

    os._Environ.__getitem__ = getitem

    def no_temp(*args, **kwargs):
        events.append("tempfile")
        raise OSError("tempfile blocked")

    tempfile.mkdtemp = no_temp
    tempfile.mkstemp = no_temp
    code = None
    if mode == "import":
        spec = importlib.util.spec_from_file_location("smoke_child", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        callable_main = callable(module.main)
    else:
        callable_main = None
        sys.argv = [script, "--model", "gpt-5.4-mini"]
        try:
            runpy.run_path(script, run_name="__main__")
        except SystemExit as stop:
            code = stop.code
    print("REPORT " + json.dumps({
        "openai": "openai" in sys.modules,
        "adapter": "emporio_agent.openai_provider" in sys.modules,
        "events": events,
        "main": callable_main,
        "code": code,
        "isolated": sys.flags.isolated,
    }))
    """
)


def venv_python() -> Path:
    relative = Path("Scripts", "python.exe") if os.name == "nt" else Path("bin", "python")
    return REPO / ".venv" / relative


def run_child(tmp_path: Path, mode: str) -> tuple[dict, str, str]:
    child = tmp_path / "child.py"
    child.write_text(_CHILD, encoding="utf-8")
    src = Path(emporio_agent.__file__).resolve().parents[1]
    env = {key: value for key, value in os.environ.items() if not key.startswith("OPENAI_")}
    env["OPENAI_API_KEY"] = KEY_SENTINEL
    completed = subprocess.run(
        [str(venv_python()), "-I", str(child), str(src), str(SCRIPT), mode],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    (report_line,) = [line for line in completed.stdout.splitlines() if line.startswith("REPORT ")]
    other = "\n".join(line for line in completed.stdout.splitlines() if line != report_line)
    return json.loads(report_line.removeprefix("REPORT ")), other, completed.stderr


def test_import_reads_no_credential_creates_nothing_and_opens_no_socket(tmp_path):
    report, out, err = run_child(tmp_path, "import")

    assert report == {
        "openai": False,
        "adapter": False,
        "events": [],
        "main": True,
        "code": None,
        "isolated": 1,
    }
    assert out == ""
    assert KEY_SENTINEL not in err


def test_script_without_opt_in_stops_before_environment_client_and_network(tmp_path):
    report, out, err = run_child(tmp_path, "no_opt_in")

    assert report == {
        "openai": False,
        "adapter": False,
        "events": [],
        "main": None,
        "code": 2,
        "isolated": 1,
    }
    assert out == "rejected: opt_in_required"
    assert KEY_SENTINEL not in out + err


# --- configuration: rejected before resources, client and calls -----------------------


@pytest.mark.parametrize(
    ("argv", "code"),
    [
        ([], "opt_in_required"),
        (["--model", MODEL], "opt_in_required"),
        (["--allow-real-calls"], "model_required"),
        (["--allow-real-calls", "--model", ""], "model_invalid"),
        (["--allow-real-calls", "--model", " gpt-5.4-mini"], "model_invalid"),
        (["--allow-real-calls", "--model", "gpt-5.4-mini\n"], "model_invalid"),
        (["--allow-real-calls", "--model", "gpt 5"], "model_invalid"),
        (["--allow-real-calls", "--model", "a" * 129], "model_invalid"),
        (["--allow-real-calls", "--model", UNKNOWN], "model_reserved"),
        (["--allow-real-calls", "--model", MODEL, "--api-key", KEY_SENTINEL], "arguments_invalid"),
        (["--allow-real-calls", "--model", MODEL, KEY_SENTINEL], "arguments_invalid"),
        (["--allow-real-calls", f"--model={KEY_SENTINEL}!"], "model_invalid"),
        (["--allow", "--model", MODEL], "arguments_invalid"),
        (["--allow-real-calls", "--mod", MODEL], "arguments_invalid"),
    ],
)
def test_arguments_rejected_with_fixed_code(smoke, capsys, scratch_root, argv, code):
    environ = RecordingEnv({"OPENAI_API_KEY": KEY_SENTINEL})

    result, out, err, transport, _ = run_main(smoke, capsys, [], argv=argv, environ=environ)

    assert result == 2
    assert out == f"rejected: {code}\n"
    assert err == ""
    assert transport.clients == [] and transport.requests == []
    assert list(scratch_root.iterdir()) == []
    assert environ.reads == []  # the key is never read before model and opt-in pass
    assert_private(out, err)


@pytest.mark.parametrize(
    ("values", "code"),
    [
        ({}, "key_missing"),
        ({"OPENAI_API_KEY": ""}, "key_missing"),
        ({"OPENAI_API_KEY": " " + KEY_SENTINEL}, "key_invalid"),
        ({"OPENAI_API_KEY": KEY_SENTINEL + "\n"}, "key_invalid"),
        ({"OPENAI_API_KEY": KEY_SENTINEL, "OPENAI_LOG": "debug"}, "sdk_logging_enabled"),
        ({"OPENAI_API_KEY": KEY_SENTINEL, "OPENAI_LOG": ""}, "sdk_logging_enabled"),
    ],
)
def test_environment_rejected_before_client(smoke, capsys, scratch_root, values, code):
    result, out, err, transport, _ = run_main(smoke, capsys, [], environ=values)

    assert result == 2
    assert out == f"rejected: {code}\n"
    assert transport.clients == [] and transport.requests == []
    assert list(scratch_root.iterdir()) == []
    assert_private(out, err)


def test_config_repr_hides_key(smoke):
    config = smoke.SmokeConfig(model=MODEL, api_key=KEY_SENTINEL)

    assert KEY_SENTINEL not in repr(config)
    assert MODEL in repr(config)


# --- complete run over the mock transport ---------------------------------------------


def test_happy_path_records_every_ev_as_passed(smoke, capsys, scratch_root):
    code, out, err, transport, sleeps = run_main(smoke, capsys, HAPPY)

    assert code == 0, out
    assert err == ""
    assert sleeps == []
    assert len(transport.requests) == 6
    sent = [transport.sent(index) for index in range(6)]
    for request, wire in zip(transport.requests, sent, strict=True):
        assert str(request.url) == URL
        assert wire["store"] is False
        assert wire["parallel_tool_calls"] is False
        assert wire["max_output_tokens"] == 1024
        assert [tool["name"] for tool in wire["tools"]] == [
            "search_products",
            "get_product",
            "quote_product",
            "search_policies",
            "get_order",
        ]
        assert request.headers["x-stainless-retry-count"] == "0"
    assert [wire["model"] for wire in sent] == [MODEL] * 5 + [UNKNOWN]
    assert [wire["tool_choice"] for wire in sent] == ["auto"] * 4 + ["none", "auto"]
    assert sent[0]["input"] == [{"type": "message", "role": "user", "content": TURN_1}]

    # EV02/EV03: the reasoning item and the call are resent unchanged with the output.
    second = sent[1]["input"]
    assert second[1] == json.loads(REASONING_1)
    assert second[2] == json.loads(CALL_1)
    assert second[3]["type"] == "function_call_output"
    assert second[3]["call_id"] == "call_s1"
    search = json.loads(second[3]["output"])
    assert search["status"] == "ok"
    assert [p["product_id"] for p in search["data"]["products"]] == [10]

    # EV04: turn 2 starts with the whole turn 1 chain, items unchanged.
    third = sent[2]["input"]
    assert third[:5] == second[:4] + [json.loads(MESSAGE_1)]
    assert third[5] == {"type": "message", "role": "user", "content": TURN_2}
    quote = json.loads(sent[3]["input"][-1]["output"])
    assert sent[3]["input"][-1]["call_id"] == "call_s2"
    assert quote["status"] == "ok"
    assert quote["data"]["product"]["product_id"] == 10

    # EV07: retained history of both turns, then the new user message; tools still listed.
    seventh = sent[4]["input"]
    assert seventh[:-1] == sent[3]["input"] + [json.loads(MESSAGE_2)]
    assert seventh[-1] == {"type": "message", "role": "user", "content": EV07_TEXT}
    assert sent[5]["input"] == [{"type": "message", "role": "user", "content": EV08_TEXT}]

    for case in ("EV01", "EV02", "EV03", "EV04", "EV05", "EV06", "EV07", "EV08"):
        assert field(ev(out, case), "status") == "passed", ev(out, case)
        assert field(ev(out, case), "codes") == "-"
    assert lines(out, "summary:") == [
        "summary: passed=8 failed=0 not_observed=0 provider_calls=6/12 blocked_calls=0"
        " interrupted=no exit_code=0"
    ]
    assert lines(out, "resources:") == ["resources: temporary_removed=yes providers_closed=2"]
    assert all(client.is_closed for client in transport.clients)
    assert len(transport.clients) == 2
    assert list(scratch_root.iterdir()) == []
    assert_private(out, err)
    assert_grammar(out)


def test_happy_path_header_config_and_data_lines(smoke, capsys, scratch_root):
    _, out, _, _, _ = run_main(smoke, capsys, HAPPY)

    assert out.splitlines()[:3] == [
        "agent_provider_smoke format=1",
        f"config: configured_model={MODEL} unknown_model={UNKNOWN} max_provider_calls=12"
        " max_output_tokens=1024 reasoning_effort=omitted timeout_seconds=30.0 store=false"
        " parallel_tool_calls=false",
        "data: source=synthetic_fixture_valid categories=2 customers=2 order_items=3 orders=2"
        " products=4 promotions=3 policy_index=synthetic policy_sections=2",
    ]


def test_attempt_lines_carry_measured_bytes_usage_and_sizes(smoke, capsys, scratch_root):
    _, out, _, transport, _ = run_main(smoke, capsys, HAPPY)

    attempts = lines(out, "attempt:")
    assert len(attempts) == 6
    first_bytes = STATIC_ANONYMOUS + len(TURN_1.encode("utf-8"))
    assert attempts[0] == (
        "attempt: n=1 scenario=S1T1 path=agent outcome=response error_kind=- status_code=-"
        f" request_id=req_1 reported_model={SNAPSHOT} duration_ms=250"
        f" request_bytes={first_bytes} allow_tool_calls=yes tools=5 input_items=1"
        " resent_reasoning=0 resent_encrypted=0 resent_with_id=0 resent_with_status=0"
        " resent_with_phase=0 previous_calls_returned=- history_resent=- status=completed"
        " incomplete_reason=- calls=1 reasoning_items=1 encrypted_items=1"
        f" encrypted_bytes={len(ENCRYPTED_1)} reasoning_bytes={len(REASONING_1)}"
        " assistant_messages=0 assistant_bytes=0"
        f" function_call_bytes={len(CALL_1.encode('utf-8'))}"
        " phase_commentary=0 phase_final=0 phase_absent=0 text=no refusal=no usage=reported"
        " input_tokens=1000 output_tokens=50 total_tokens=1050 cached_input_tokens=0"
        f" reasoning_tokens=10 bytes_per_input_token={first_bytes / 1000:.2f}"
    )
    # Request 2 bytes: request 1 + reasoning + call + call_id + the real tool payload (wire).
    payload = transport.sent(1)["input"][3]["output"]
    second_bytes = (
        first_bytes
        + len(REASONING_1.encode("utf-8"))
        + len(CALL_1.encode("utf-8"))
        + len("call_s1")
        + len(payload.encode("utf-8"))
    )
    assert field(attempts[1], "request_bytes") == str(second_bytes)
    assert field(attempts[1], "previous_calls_returned") == "yes"
    assert field(attempts[1], "resent_reasoning") == "1"
    assert field(attempts[1], "resent_encrypted") == "1"
    assert field(attempts[1], "assistant_messages") == "1"
    assert field(attempts[1], "phase_final") == "1"
    assert field(attempts[1], "text") == "yes"
    # Turn 2, first attempt: history of turn 1 resent (reasoning, call, message carry id;
    # call and message carry status; message carries phase).
    assert field(attempts[2], "scenario") == "S1T2"
    assert field(attempts[2], "history_resent") == "yes"
    assert field(attempts[2], "input_items") == "6"
    assert field(attempts[2], "resent_with_id") == "3"
    assert field(attempts[2], "resent_with_status") == "2"
    assert field(attempts[2], "resent_with_phase") == "1"
    assert field(attempts[4], "scenario") == "EV07"
    assert field(attempts[4], "path") == "adapter_direct"
    assert field(attempts[4], "allow_tool_calls") == "no"
    assert field(attempts[4], "phase_absent") == "1"
    assert attempts[5] == (
        "attempt: n=6 scenario=EV08 path=adapter_direct outcome=error"
        " error_kind=model_unavailable status_code=404 request_id=req_6 reported_model=-"
        f" duration_ms=250 request_bytes={STATIC_ANONYMOUS + len(EV08_TEXT.encode('utf-8'))}"
        " allow_tool_calls=yes tools=5 input_items=1 resent_reasoning=0 resent_encrypted=0"
        " resent_with_id=0 resent_with_status=0 resent_with_phase=0"
        " previous_calls_returned=- history_resent=- status=- incomplete_reason=- calls=-"
        " reasoning_items=- encrypted_items=- encrypted_bytes=- reasoning_bytes=-"
        " assistant_messages=- assistant_bytes=- function_call_bytes=- phase_commentary=-"
        " phase_final=- phase_absent=- text=- refusal=- usage=- input_tokens=-"
        " output_tokens=- total_tokens=- cached_input_tokens=- reasoning_tokens=-"
        " bytes_per_input_token=-"
    )


def test_turn_lines_and_ev_details(smoke, capsys, scratch_root):
    _, out, _, _, _ = run_main(smoke, capsys, HAPPY)

    assert lines(out, "turn:") == [
        "turn: scenario=S1T1 outcome=answered failure=- agent_attempts=2 retries=0"
        " tool_runs=1 tools=search_products:ok budget_failure=- history_turns_after=1",
        "turn: scenario=S1T2 outcome=answered failure=- agent_attempts=2 retries=0"
        " tool_runs=1 tools=quote_product:ok budget_failure=- history_turns_after=2",
    ]
    ev01 = ev(out, "EV01")
    assert field(ev01, "configured_model") == MODEL
    assert field(ev01, "reported_model") == SNAPSHOT
    assert field(ev01, "tools") == "5"
    assert field(ev01, "accepted_attempt") == "1"
    ev02 = ev(out, "EV02")
    assert field(ev02, "function_calls") == "1"
    assert field(ev02, "tool_runs") == "1"
    assert field(ev02, "outputs_same_call_id") == "yes"
    assert field(ev02, "final_answer") == "yes"
    ev03 = ev(out, "EV03")
    assert field(ev03, "reasoning_items") == "2"
    assert field(ev03, "encrypted_items") == "2"
    assert field(ev03, "resent_requests") == "4"  # attempts 2, 3, 4 and EV07
    assert field(ev03, "resent_accepted") == "4"
    ev04 = ev(out, "EV04")
    assert field(ev04, "history_items") == "5"
    assert field(ev04, "fields_observed") == "id,status,phase"
    assert field(ev04, "fields_not_observed") == "-"
    ev05 = ev(out, "EV05")
    assert field(ev05, "responses") == "5"
    assert field(ev05, "usage_reported") == "5"
    assert field(ev05, "usage_missing") == "0"
    assert field(ev05, "usage_invalid") == "0"
    assert field(ev05, "max_encrypted_bytes") == str(len(ENCRYPTED_1))
    ev06 = ev(out, "EV06")
    assert (field(ev06, "calls_0"), field(ev06, "calls_1"), field(ev06, "calls_2_or_more")) == (
        "3",
        "2",
        "0",
    )
    ev07 = ev(out, "EV07")
    assert field(ev07, "path") == "adapter_direct"
    assert field(ev07, "loop_exhaustion") == "not_exercised"
    assert field(ev07, "prior_calls") == "2"
    ev08 = ev(out, "EV08")
    assert field(ev08, "unknown_model") == UNKNOWN
    assert field(ev08, "error_kind") == "model_unavailable"
    assert field(ev08, "message_fixed") == "yes"
    assert field(ev08, "attempts") == "1"


# --- honest absence -------------------------------------------------------------------


def test_missing_reasoning_and_usage_are_not_observed_never_zero(smoke, capsys, scratch_root):
    script = (
        ok(CALL_1, usage=None),
        ok(MESSAGE_1, usage=None),
        ok(CALL_2, usage=None),
        ok(MESSAGE_2, usage=None),
        ok(MESSAGE_7, usage=None),
        (404, ERROR_404.encode("utf-8")),
    )

    code, out, err, _, _ = run_main(smoke, capsys, script)

    assert code == 3
    assert ev(out, "EV03").startswith("EV03 status=not_observed codes=no_reasoning_items ")
    assert ev(out, "EV05").startswith("EV05 status=not_observed codes=usage_absent ")
    for attempt in lines(out, "attempt:")[:5]:
        assert field(attempt, "usage") == "missing"
        assert field(attempt, "input_tokens") == "-"
        assert field(attempt, "bytes_per_input_token") == "-"
    assert "input_tokens=0" not in out
    for case in ("EV01", "EV02", "EV04", "EV06", "EV07", "EV08"):
        assert field(ev(out, case), "status") == "passed"
    assert lines(out, "summary:")[0].startswith("summary: passed=6 failed=0 not_observed=2 ")
    assert_private(out, err)


def test_reasoning_without_encrypted_content_is_not_observed(smoke, capsys, scratch_root):
    script = (ok(REASONING_PLAIN, CALL_1), HAPPY[1], ok(CALL_2)) + HAPPY[3:]

    code, out, _, _, _ = run_main(smoke, capsys, script)

    assert code == 3
    ev03 = ev(out, "EV03")
    assert ev03.startswith("EV03 status=not_observed codes=no_encrypted_content ")
    assert (field(ev03, "reasoning_items"), field(ev03, "encrypted_items")) == ("1", "0")


def test_answer_without_tool_call_is_not_observed_for_ev02(smoke, capsys, scratch_root):
    script = (ok(MESSAGE_1), ok(MESSAGE_2), (404, ERROR_404.encode("utf-8")))

    code, out, _, transport, _ = run_main(smoke, capsys, script)

    assert code == 3
    assert ev(out, "EV02").startswith("EV02 status=not_observed codes=no_function_call ")
    assert ev(out, "EV07").startswith("EV07 status=not_observed codes=no_prior_call_in_history ")
    assert field(ev(out, "EV04"), "status") == "passed"
    assert len(transport.requests) == 3  # S1T1, S1T2, EV08: EV07 needs an earlier call


def test_partial_and_invalid_usage_are_counted_separately(smoke, capsys, scratch_root):
    script = (
        ok(REASONING_1, CALL_1),
        ok(MESSAGE_1, usage=None),
        ok(REASONING_2, CALL_2, usage='{"input_tokens":true,"output_tokens":1,"total_tokens":1}'),
    ) + HAPPY[3:]

    code, out, _, _, _ = run_main(smoke, capsys, script)

    assert code == 0
    attempts = lines(out, "attempt:")
    assert field(attempts[1], "usage") == "missing"
    assert field(attempts[2], "usage") == "invalid"
    assert field(attempts[2], "input_tokens") == "-"
    ev05 = ev(out, "EV05")
    assert field(ev05, "status") == "passed"
    assert (field(ev05, "usage_reported"), field(ev05, "usage_missing")) == ("3", "1")
    assert field(ev05, "usage_invalid") == "1"


# --- findings and failures ------------------------------------------------------------


def test_low_bytes_per_token_is_a_finding(smoke, capsys, scratch_root):
    heavy = '{"input_tokens":100000,"output_tokens":5,"total_tokens":100005}'
    script = (ok(REASONING_1, CALL_1, usage=heavy),) + HAPPY[1:]

    code, out, _, _, _ = run_main(smoke, capsys, script)

    assert code == 1
    first = lines(out, "attempt:")[0]
    expected = (STATIC_ANONYMOUS + len(TURN_1.encode("utf-8"))) / 100_000
    assert field(first, "bytes_per_input_token") == f"{expected:.2f}"
    ev05 = ev(out, "EV05")
    assert ev05.startswith("EV05 status=failed codes=bytes_per_token_below_2_5 ")
    assert field(ev05, "min_bytes_per_input_token") == f"{expected:.2f}"


def test_model_items_above_allowance_is_a_finding(smoke, capsys, scratch_root):
    big = '{"type":"reasoning","id":"rs_big","summary":[],"encrypted_content":"' + "E" * 49_600
    big += '"}'
    script = (ok(big, CALL_1),) + HAPPY[1:]

    code, out, _, _, _ = run_main(smoke, capsys, script)

    assert code == 1
    ev05 = ev(out, "EV05")
    assert ev05.startswith("EV05 status=failed codes=model_items_above_allowance ")
    model_items = len(big) + len(CALL_1.encode("utf-8")) + len(MESSAGE_1.encode("utf-8"))
    assert field(ev05, "max_step_model_item_bytes") == str(model_items)
    assert "E" * 100 not in out


def test_two_calls_in_one_response_fail_ev06(smoke, capsys, scratch_root):
    script = (ok(REASONING_1, CALL_1, CALL_1B),) + HAPPY[1:]

    code, out, _, _, _ = run_main(smoke, capsys, script)

    assert code == 1
    ev06 = ev(out, "EV06")
    assert ev06.startswith("EV06 status=failed codes=multiple_calls_in_response ")
    assert field(ev06, "calls_2_or_more") == "1"
    assert lines(out, "turn:")[0].startswith(
        "turn: scenario=S1T1 outcome=answered failure=- agent_attempts=2 retries=0 tool_runs=2"
        " tools=search_products:ok,search_products:ok "
    )


def test_call_under_tool_choice_none_fails_ev07(smoke, capsys, scratch_root):
    script = HAPPY[:4] + (ok(CALL_7),) + HAPPY[5:]

    code, out, _, transport, _ = run_main(smoke, capsys, script)

    assert code == 1
    assert ev(out, "EV07").startswith("EV07 status=failed codes=call_with_tool_choice_none ")
    assert transport.sent(4)["tool_choice"] == "none"
    assert len(transport.requests) == 6  # the call is never executed or answered


def test_rejected_resend_of_reasoning_fails_ev03(smoke, capsys, scratch_root):
    script = (
        ok(REASONING_1, CALL_1),
        (400, ERROR_400.encode("utf-8")),
        (404, ERROR_404.encode("utf-8")),
    )

    code, out, err, transport, _ = run_main(smoke, capsys, script)

    assert code == 1
    assert len(transport.requests) == 3  # S1T1 twice, then EV08 only
    assert field(ev(out, "EV01"), "status") == "passed"
    assert ev(out, "EV02").startswith("EV02 status=failed codes=provider_bad_request ")
    assert ev(out, "EV03").startswith("EV03 status=failed codes=provider_bad_request ")
    assert ev(out, "EV04").startswith("EV04 status=not_observed codes=turn_1_not_answered ")
    assert ev(out, "EV07").startswith("EV07 status=not_observed codes=no_prior_call_in_history ")
    assert field(ev(out, "EV08"), "status") == "passed"
    assert_private(out, err)


def test_ev01_rejection_stops_the_run(smoke, capsys, scratch_root):
    code, out, err, transport, _ = run_main(smoke, capsys, ((400, ERROR_400.encode("utf-8")),))

    assert code == 1
    assert len(transport.requests) == 1
    assert ev(out, "EV01").startswith("EV01 status=failed codes=provider_bad_request ")
    for case in ("EV04", "EV07", "EV08"):
        assert ev(out, case).startswith(
            f"{case} status=not_observed codes=not_run_blocking_failure "
        )
    assert lines(out, "summary:")[0].endswith(" interrupted=blocking_failure exit_code=1")
    assert all(client.is_closed for client in transport.clients)
    assert list(scratch_root.iterdir()) == []
    assert_private(out, err)


@pytest.mark.parametrize(
    ("answer", "status", "codes", "kind"),
    [
        ((404, ERROR_404.encode("utf-8")), "passed", "-", "model_unavailable"),
        (
            (400, ERROR_404.encode("utf-8")),  # 400 with code model_not_found
            "passed",
            "-",
            "model_unavailable",
        ),
        (ok(MESSAGE_7, model=UNKNOWN), "failed", "unknown_model_accepted", "-"),
        ((401, ERROR_400.encode("utf-8")), "failed", "provider_authentication", "authentication"),
        ((503, ERROR_503.encode("utf-8")), "not_observed", "provider_server_error", "server_error"),
    ],
)
def test_ev08_single_attempt_classification(
    smoke, capsys, scratch_root, answer, status, codes, kind
):
    code, out, err, transport, sleeps = run_main(smoke, capsys, HAPPY[:5] + (answer,))

    assert len(transport.requests) == 6  # one EV08 attempt, never repeated
    assert sleeps == []
    assert transport.sent(5)["model"] == UNKNOWN
    line = ev(out, "EV08")
    assert line.startswith(f"EV08 status={status} codes={codes} ")
    assert field(line, "error_kind") == kind
    assert code == {"passed": 0, "failed": 1, "not_observed": 3}[status]
    assert_private(out, err)


# --- global call limit ----------------------------------------------------------------


def test_global_limit_counts_retries_and_interrupts_without_extra_call(smoke, capsys, scratch_root):
    script = ((503, ERROR_503.encode("utf-8")), ok(CALL_1))

    code, out, err, transport, sleeps = run_main(smoke, capsys, script, max_provider_calls=2)

    assert code == 4
    assert len(transport.requests) == 2  # the third attempt is never sent
    assert sleeps == [1.0]  # the agent retry is one of the two counted calls
    attempts = lines(out, "attempt:")
    assert [field(a, "outcome") for a in attempts] == ["error", "response"]
    assert field(attempts[0], "error_kind") == "server_error"
    assert lines(out, "turn:")[0].startswith(
        "turn: scenario=S1T1 outcome=failed failure=unexpected agent_attempts=3 retries=1 "
    )
    assert field(ev(out, "EV01"), "status") == "passed"
    assert ev(out, "EV02").startswith("EV02 status=not_observed codes=interrupted_call_limit ")
    for case in ("EV04", "EV07", "EV08"):
        assert ev(out, case).startswith(f"{case} status=not_observed codes=not_run_call_limit ")
    assert lines(out, "summary:") == [
        "summary: passed=3 failed=0 not_observed=5 provider_calls=2/2 blocked_calls=1"
        " interrupted=call_limit exit_code=4"
    ]
    assert all(client.is_closed for client in transport.clients)
    assert list(scratch_root.iterdir()) == []
    assert_private(out, err)


def test_global_limit_includes_direct_adapter_scenarios(smoke, capsys, scratch_root):
    code, out, _, transport, _ = run_main(smoke, capsys, HAPPY, max_provider_calls=5)

    assert code == 4
    assert len(transport.requests) == 5  # EV07 counted; EV08 blocked before the request
    assert ev(out, "EV07").startswith("EV07 status=passed ")
    assert ev(out, "EV08").startswith("EV08 status=not_observed codes=interrupted_call_limit ")
    assert lines(out, "summary:")[0].endswith(
        "provider_calls=5/5 blocked_calls=1 interrupted=call_limit exit_code=4"
    )


def test_exact_limit_is_not_an_interruption(smoke, capsys, scratch_root):
    code, out, _, transport, _ = run_main(smoke, capsys, HAPPY, max_provider_calls=6)

    assert code == 0
    assert len(transport.requests) == 6
    assert lines(out, "summary:")[0].endswith(
        "provider_calls=6/6 blocked_calls=0 interrupted=no exit_code=0"
    )


# --- errors, cleanup and logging ------------------------------------------------------


def test_unexpected_error_prints_type_only_and_cleans_up(smoke, capsys, scratch_root):
    transport = Transport(HAPPY)
    calls = 0

    def factory():
        nonlocal calls
        calls += 1
        if calls == 2:  # the EV08 adapter, after five real calls
            raise RuntimeError("EXC-SENTINEL " + KEY)
        return transport.client()

    code = smoke.main(
        ["--allow-real-calls", "--model", MODEL],
        environ={"OPENAI_API_KEY": KEY},
        http_client_factory=factory,
        sleep=lambda seconds: None,
        monotonic=clock(),
    )
    captured = capsys.readouterr()

    assert code == 6
    assert captured.out.splitlines()[-1] == "internal_error: RuntimeError"
    assert "EXC-SENTINEL" not in captured.out + captured.err
    assert len(transport.requests) == 5
    assert all(client.is_closed for client in transport.clients)
    assert list(scratch_root.iterdir()) == []
    assert_private(captured.out, captured.err)


def test_keyboard_interrupt_closes_resources(smoke, capsys, scratch_root):
    script = (ok(REASONING_1, CALL_1), KeyboardInterrupt())

    code, out, err, transport, _ = run_main(smoke, capsys, script)

    assert code == 4
    assert out.splitlines()[-1] == "interrupted: keyboard"
    assert all(client.is_closed for client in transport.clients)
    assert list(scratch_root.iterdir()) == []
    assert_private(out, err)


def test_data_preparation_failure_stops_before_any_client(
    smoke, capsys, scratch_root, monkeypatch, tmp_path
):
    monkeypatch.setattr(smoke, "FIXTURE_DIR", tmp_path / "missing")

    code, out, err, transport, _ = run_main(smoke, capsys, HAPPY)

    assert code == 5
    assert out.splitlines()[-1] == "data_error: database_preparation_failed"
    assert transport.clients == [] and transport.requests == []
    assert list(scratch_root.iterdir()) == []
    assert_private(out, err)


def test_sdk_logging_is_silenced_and_restored(smoke, capsys, scratch_root, caplog):
    caplog.set_level(logging.DEBUG)
    before = {name: logging.getLogger(name).level for name in ("openai", "httpx2", "httpcore2")}

    code, _, _, _, _ = run_main(smoke, capsys, HAPPY)

    assert code == 0
    assert [
        record.name
        for record in caplog.records
        if record.name.split(".")[0] in ("openai", "httpx2", "httpcore2", "httpx", "httpcore")
    ] == []
    after = {name: logging.getLogger(name).level for name in ("openai", "httpx2", "httpcore2")}
    assert after == before


# --- review 07-05-R1: cleanup failure wins over data error and interruption ------------
# Contract precedence 6 > 5 > 1 > 4 > 3 > 0: a failed removal of the temporary directory
# or a failed provider close gives exit 6 on every path.


class FailingCloseClient(httpx2.Client):
    """httpx2.Client whose close() really closes, then raises (a failed provider close)."""

    close_attempts = 0

    def close(self) -> None:
        self.close_attempts += 1
        super().close()
        raise RuntimeError("CLOSE-SENTINEL " + KEY)


class CleanupTransport(Transport):
    def __init__(self, script, failing_clients=()) -> None:
        super().__init__(script)
        self.failing_clients = set(failing_clients)

    def client(self) -> httpx2.Client:
        index = len(self.clients)
        kind = FailingCloseClient if index in self.failing_clients else httpx2.Client
        client = kind(transport=httpx2.MockTransport(self))
        self.clients.append(client)
        return client


@pytest.fixture
def failing_removal(smoke, monkeypatch):
    """Removal of the temporary directory fails (filesystem boundary); the directory stays."""
    attempts: list[Path] = []

    def rmtree(path, *args, **kwargs):
        attempts.append(Path(path))
        raise PermissionError("REMOVE-SENTINEL")

    monkeypatch.setattr(smoke.shutil, "rmtree", rmtree)
    return attempts


def run_cleanup(smoke, capsys, script, failing_clients=(), **kwargs):
    transport = CleanupTransport(script, failing_clients)
    code = smoke.main(
        ["--allow-real-calls", "--model", MODEL],
        environ={"OPENAI_API_KEY": KEY},
        http_client_factory=transport.client,
        sleep=lambda seconds: None,
        monotonic=clock(),
        **kwargs,
    )
    captured = capsys.readouterr()
    return code, captured.out, captured.err, transport


def assert_cleanup_private(out: str, err: str) -> None:
    assert_private(out, err)
    for sentinel in ("CLOSE-SENTINEL", "REMOVE-SENTINEL", "PermissionError", "RuntimeError"):
        assert sentinel not in out + err


def test_r1_data_error_with_failed_removal_exits_6(
    smoke, capsys, scratch_root, failing_removal, monkeypatch, tmp_path
):
    monkeypatch.setattr(smoke, "FIXTURE_DIR", tmp_path / "missing")

    code, out, err, transport = run_cleanup(smoke, capsys, HAPPY)

    assert code == 6
    assert out.splitlines()[-1] == (
        "cleanup_error: after=data_error temporary_removed=no providers_closed=0/0"
    )
    assert "data_error: database_preparation_failed" not in out
    assert len(failing_removal) == 1  # removal was attempted
    assert len(list(scratch_root.iterdir())) == 1  # and really failed
    # No provider exists on this path: data preparation runs before any client.
    assert transport.clients == [] and transport.requests == []
    assert_cleanup_private(out, err)


def test_r1_keyboard_interrupt_with_failed_removal_exits_6(
    smoke, capsys, scratch_root, failing_removal
):
    code, out, err, transport = run_cleanup(
        smoke, capsys, (ok(REASONING_1, CALL_1), KeyboardInterrupt())
    )

    assert code == 6
    assert out.splitlines()[-1] == (
        "cleanup_error: after=keyboard_interrupt temporary_removed=no providers_closed=1/1"
    )
    assert "interrupted: keyboard" not in out
    assert len(failing_removal) == 1
    assert all(client.is_closed for client in transport.clients)
    assert_cleanup_private(out, err)


def test_r1_keyboard_interrupt_with_failed_close_exits_6(smoke, capsys, scratch_root):
    code, out, err, transport = run_cleanup(
        smoke, capsys, (ok(REASONING_1, CALL_1), KeyboardInterrupt()), failing_clients={0}
    )

    assert code == 6
    assert out.splitlines()[-1] == (
        "cleanup_error: after=keyboard_interrupt temporary_removed=yes providers_closed=0/1"
    )
    assert transport.clients[0].close_attempts == 1
    assert list(scratch_root.iterdir()) == []  # removal still done after the failed close
    assert_cleanup_private(out, err)


def test_r1_failed_close_does_not_stop_other_closes_or_removal(smoke, capsys, scratch_root):
    # Interrupt during the EV08 request: both providers are open; the first close fails.
    code, out, err, transport = run_cleanup(
        smoke, capsys, HAPPY[:5] + (KeyboardInterrupt(),), failing_clients={0}
    )

    assert code == 6
    assert len(transport.clients) == 2
    assert transport.clients[0].close_attempts == 1
    assert transport.clients[1].is_closed
    assert out.splitlines()[-1] == (
        "cleanup_error: after=keyboard_interrupt temporary_removed=yes providers_closed=1/2"
    )
    assert list(scratch_root.iterdir()) == []
    assert_cleanup_private(out, err)


def test_r1_failed_removal_and_close_together_exit_6(smoke, capsys, scratch_root, failing_removal):
    code, out, err, transport = run_cleanup(
        smoke, capsys, (ok(REASONING_1, CALL_1), KeyboardInterrupt()), failing_clients={0}
    )

    assert code == 6
    assert out.splitlines()[-1] == (
        "cleanup_error: after=keyboard_interrupt temporary_removed=no providers_closed=0/1"
    )
    assert transport.clients[0].close_attempts == 1
    assert len(failing_removal) == 1
    assert_cleanup_private(out, err)


def test_r1_control_completed_run_with_failed_removal_exits_6(
    smoke, capsys, scratch_root, failing_removal
):
    code, out, err, _ = run_cleanup(smoke, capsys, HAPPY)

    assert code == 6
    assert lines(out, "resources:") == ["resources: temporary_removed=no providers_closed=2"]
    assert lines(out, "summary:")[0].endswith(" exit_code=6")
    assert_cleanup_private(out, err)


def test_r1_control_completed_run_with_failed_close_exits_6(smoke, capsys, scratch_root):
    code, out, err, transport = run_cleanup(smoke, capsys, HAPPY, failing_clients={0})

    assert code == 6
    assert transport.clients[1].is_closed
    assert lines(out, "resources:") == ["resources: temporary_removed=yes providers_closed=1"]
    assert lines(out, "summary:")[0].endswith(" exit_code=6")
    assert list(scratch_root.iterdir()) == []
    assert_cleanup_private(out, err)


# --- successor contract 07-08: optional reasoning effort and hardened EV03 -------------
# Spec Review 07-07-R1; report docs/validation/07-08-ev03-offline.md. These tests prove the
# evaluator's mechanics only. A scripted reasoning item is never evidence for EV03.

EFFORTS = ("low", "medium", "high", "xhigh")
CASES = ("EV01", "EV02", "EV03", "EV04", "EV05", "EV06", "EV07", "EV08")
INCOMPLETE_DETAILS = '{"reason":"max_output_tokens"}'


def incomplete(*output: str, rid: str = "resp_i", usage: str | None = USAGE) -> tuple[int, bytes]:
    usage_part = ',"usage":' + usage if usage is not None else ""
    text = (
        '{"id":"' + rid + '","object":"response","status":"incomplete",'
        '"incomplete_details":' + INCOMPLETE_DETAILS + ',"model":"' + SNAPSHOT + '",'
        '"output":[' + ",".join(output) + "]" + usage_part + "}"
    )
    return 200, text.encode("utf-8")


def with_effort(effort: str) -> list[str]:
    return ["--allow-real-calls", "--model", MODEL, "--reasoning-effort", effort]


def expected_exit(out: str) -> int:
    """Exit code from the printed EV statuses by contract precedence 1 > 4 > 3 > 0."""
    statuses = [field(ev(out, case), "status") for case in CASES]
    summary = lines(out, "summary:")[0]
    if "failed" in statuses:
        return 1
    if " interrupted=no " not in summary:
        return 4
    if "not_observed" in statuses:
        return 3
    return 0


def test_successor_constants(smoke):
    import emporio_agent.openai_provider as adapter

    assert smoke.MAX_OUTPUT_TOKENS_PER_CALL == 1024
    assert smoke.MAX_OUTPUT_TOKENS_WITH_REASONING == 4096
    assert smoke.REASONING_EFFORTS == EFFORTS
    assert smoke.REASONING_EFFORTS == adapter.REASONING_EFFORTS


def test_without_flag_body_has_no_reasoning_and_cap_1024(smoke, capsys, scratch_root):
    code, out, _, transport, _ = run_main(smoke, capsys, HAPPY)

    assert code == 0
    for index in range(6):
        wire = transport.sent(index)
        assert "reasoning" not in wire and "include" not in wire
        assert wire["max_output_tokens"] == 1024
    assert field(lines(out, "config:")[0], "reasoning_effort") == "omitted"
    assert field(lines(out, "config:")[0], "max_output_tokens") == "1024"
    assert field(ev(out, "EV03"), "reasoning_effort") == "omitted"


@pytest.mark.parametrize("effort", EFFORTS)
def test_valid_flag_sends_effort_and_cap_4096_to_every_provider(
    smoke, capsys, scratch_root, effort
):
    code, out, err, transport, sleeps = run_main(smoke, capsys, HAPPY, argv=with_effort(effort))

    assert code == 0, out
    assert sleeps == []
    assert len(transport.requests) == 6
    sent = [transport.sent(index) for index in range(6)]
    assert [wire["model"] for wire in sent] == [MODEL] * 5 + [UNKNOWN]
    for wire in sent:
        assert wire["reasoning"] == {"effort": effort}
        assert wire["max_output_tokens"] == 4096
        assert "include" not in wire
        assert wire["store"] is False and wire["parallel_tool_calls"] is False
    assert lines(out, "config:") == [
        f"config: configured_model={MODEL} unknown_model={UNKNOWN} max_provider_calls=12"
        f" max_output_tokens=4096 reasoning_effort={effort} timeout_seconds=30.0 store=false"
        " parallel_tool_calls=false"
    ]
    ev03 = ev(out, "EV03")
    assert ev03.startswith("EV03 status=passed codes=- ")
    assert field(ev03, "reasoning_effort") == effort
    assert field(ev03, "resent_completed") == "4"
    assert lines(out, "resources:") == ["resources: temporary_removed=yes providers_closed=2"]
    assert all(client.is_closed for client in transport.clients)
    assert list(scratch_root.iterdir()) == []
    assert_private(out, err)
    assert_grammar(out)


@pytest.mark.parametrize(
    "argv",
    [
        with_effort("none"),
        with_effort("minimal"),
        with_effort("max"),
        with_effort(""),
        with_effort("LOW"),
        with_effort(" low"),
        with_effort("low "),
        with_effort(KEY_SENTINEL),
        ["--allow-real-calls", "--model", MODEL, f"--reasoning-effort={KEY_SENTINEL}"],
    ],
    ids=repr,
)
def test_invalid_flag_value_rejected_before_environment(smoke, capsys, scratch_root, argv):
    environ = RecordingEnv({"OPENAI_API_KEY": KEY_SENTINEL})

    result, out, err, transport, _ = run_main(smoke, capsys, [], argv=argv, environ=environ)

    assert result == 2
    assert out == "rejected: reasoning_effort_invalid\n"
    assert err == ""
    assert environ.reads == []
    assert transport.clients == [] and transport.requests == []
    assert list(scratch_root.iterdir()) == []
    assert_private(out, err)


@pytest.mark.parametrize(
    ("argv", "code"),
    [
        (["--allow-real-calls", "--model", MODEL, "--reasoning-effort"], "arguments_invalid"),
        (["--allow-real-calls", "--model", MODEL, "--reasoning", "low"], "arguments_invalid"),
        (["--model", MODEL, "--reasoning-effort", "none"], "opt_in_required"),
        (["--allow-real-calls", "--reasoning-effort", "none"], "model_required"),
        (["--allow-real-calls", "--model", "", "--reasoning-effort", "none"], "model_invalid"),
    ],
    ids=repr,
)
def test_flag_parsing_and_check_order(smoke, capsys, scratch_root, argv, code):
    environ = RecordingEnv({"OPENAI_API_KEY": KEY_SENTINEL})

    result, out, err, transport, _ = run_main(smoke, capsys, [], argv=argv, environ=environ)

    assert result == 2
    assert out == f"rejected: {code}\n"
    assert environ.reads == []
    assert transport.clients == [] and transport.requests == []
    assert list(scratch_root.iterdir()) == []
    assert_private(out, err)


def test_valid_flag_still_rejects_bad_environment_after_arguments(smoke, capsys, scratch_root):
    result, out, _, transport, _ = run_main(
        smoke, capsys, [], argv=with_effort("low"), environ={"OPENAI_API_KEY": ""}
    )

    assert result == 2
    assert out == "rejected: key_missing\n"
    assert transport.clients == []


def test_config_holds_effort_and_hides_key(smoke):
    config = smoke.SmokeConfig(model=MODEL, api_key=KEY_SENTINEL, reasoning_effort="low")

    assert config.reasoning_effort == "low"
    assert smoke.SmokeConfig(model=MODEL, api_key=KEY).reasoning_effort is None
    assert KEY_SENTINEL not in repr(config)


# EV03 classification, first match wins: non-transient rejection > completed resend >
# answered but none completed > only transient errors > absence codes.


@pytest.mark.parametrize("argv", [None, with_effort("low")], ids=["no_flag", "low"])
def test_ev03_only_incomplete_resend_is_not_a_pass(smoke, capsys, scratch_root, argv):
    script = (
        ok(REASONING_1, CALL_1),
        incomplete(REASONING_2),  # the resend was accepted (HTTP 200) but cut by the budget
        (404, ERROR_404.encode("utf-8")),
    )

    code, out, err, transport, _ = run_main(smoke, capsys, script, argv=argv)

    assert len(transport.requests) == 3  # S1T1 twice, EV08; S1T2 and EV07 not run
    assert transport.sent(1)["input"][1] == json.loads(REASONING_1)
    ev03 = ev(out, "EV03")
    assert ev03.startswith("EV03 status=not_observed codes=resend_incomplete ")
    assert (field(ev03, "resent_requests"), field(ev03, "resent_accepted")) == ("1", "1")
    assert field(ev03, "resent_completed") == "0"
    assert field(lines(out, "attempt:")[1], "incomplete_reason") == "max_output_tokens"
    assert lines(out, "turn:")[0].startswith(
        "turn: scenario=S1T1 outcome=failed failure=response_incomplete "
    )
    assert field(ev(out, "EV02"), "status") != "passed"
    assert code == expected_exit(out)
    assert code != 0
    assert_private(out, err)


def test_ev03_completed_resend_among_incomplete_ones_passes(smoke, capsys, scratch_root):
    script = (
        ok(REASONING_1, CALL_1),
        ok(MESSAGE_1),  # resend 1: completed
        incomplete(),  # S1T2: history with the reasoning item resent, cut
        incomplete(),  # EV07: same history resent, cut
        (404, ERROR_404.encode("utf-8")),
    )

    code, out, _, transport, _ = run_main(smoke, capsys, script)

    assert len(transport.requests) == 5
    ev03 = ev(out, "EV03")
    assert ev03.startswith("EV03 status=passed codes=- ")
    assert (field(ev03, "resent_requests"), field(ev03, "resent_completed")) == ("3", "1")
    assert code == expected_exit(out)


def test_ev03_non_transient_rejection_wins_over_a_completed_resend(smoke, capsys, scratch_root):
    script = (
        HAPPY[0],
        HAPPY[1],  # resend completed
        (400, ERROR_400.encode("utf-8")),  # S1T2 resend rejected
        ok(MESSAGE_7),  # EV07 resend completed
        (404, ERROR_404.encode("utf-8")),
    )

    code, out, err, _, _ = run_main(smoke, capsys, script)

    assert ev(out, "EV03").startswith("EV03 status=failed codes=provider_bad_request ")
    assert code == 1
    assert_private(out, err)


def test_ev03_only_transient_errors_on_resend(smoke, capsys, scratch_root):
    script = (
        ok(REASONING_1, CALL_1),
        (503, ERROR_503.encode("utf-8")),
        (503, ERROR_503.encode("utf-8")),  # the one agent retry, also transient
        (404, ERROR_404.encode("utf-8")),
    )

    code, out, err, transport, sleeps = run_main(smoke, capsys, script)

    assert len(transport.requests) == 4
    assert sleeps == [1.0]
    ev03 = ev(out, "EV03")
    assert ev03.startswith("EV03 status=not_observed codes=resend_transient_error ")
    assert (field(ev03, "resent_requests"), field(ev03, "resent_accepted")) == ("2", "0")
    assert code == expected_exit(out)
    assert_private(out, err)


def test_ev03_transient_error_then_completed_retry_passes(smoke, capsys, scratch_root):
    script = (ok(REASONING_1, CALL_1), (503, ERROR_503.encode("utf-8"))) + HAPPY[1:]

    code, out, _, _, sleeps = run_main(smoke, capsys, script)

    assert sleeps == [1.0]
    ev03 = ev(out, "EV03")
    assert ev03.startswith("EV03 status=passed codes=- ")
    assert code == expected_exit(out)


@pytest.mark.parametrize("argv", [None, with_effort("low")], ids=["no_flag", "low"])
def test_ev03_initial_incomplete_runs_no_tool_and_resends_nothing(
    smoke, capsys, scratch_root, argv
):
    script = (incomplete(REASONING_1, CALL_1), (404, ERROR_404.encode("utf-8")))

    code, out, err, transport, _ = run_main(smoke, capsys, script, argv=argv)

    assert len(transport.requests) == 2  # no tool output sent back; EV08 only
    assert lines(out, "turn:")[0].startswith(
        "turn: scenario=S1T1 outcome=failed failure=response_incomplete agent_attempts=1"
        " retries=0 tool_runs=0 tools=- "
    )
    ev03 = ev(out, "EV03")
    assert ev03.startswith("EV03 status=not_observed codes=reasoning_not_resent ")
    assert field(ev03, "resent_requests") == "0"
    assert field(ev(out, "EV02"), "status") != "passed"
    assert code == expected_exit(out)
    assert_private(out, err)


@pytest.mark.parametrize("effort", EFFORTS)
def test_ev03_absence_with_flag_stays_not_observed(smoke, capsys, scratch_root, effort):
    plain = (ok(CALL_1), ok(MESSAGE_1), ok(CALL_2), ok(MESSAGE_2), ok(MESSAGE_7))
    no_encrypted = (ok(REASONING_PLAIN, CALL_1), HAPPY[1], ok(CALL_2)) + HAPPY[3:5]
    for script, code_name in (
        (plain, "no_reasoning_items"),
        (no_encrypted, "no_encrypted_content"),
    ):
        code, out, _, _, _ = run_main(smoke, capsys, script + (HAPPY[5],), argv=with_effort(effort))
        assert ev(out, "EV03").startswith(f"EV03 status=not_observed codes={code_name} ")
        assert code == 3


def test_flag_keeps_global_limit_and_retry_count(smoke, capsys, scratch_root):
    script = ((503, ERROR_503.encode("utf-8")), ok(CALL_1))

    code, out, err, transport, sleeps = run_main(
        smoke, capsys, script, argv=with_effort("low"), max_provider_calls=2
    )

    assert code == 4
    assert len(transport.requests) == 2
    assert sleeps == [1.0]
    assert all(transport.sent(i)["reasoning"] == {"effort": "low"} for i in range(2))
    assert lines(out, "summary:")[0].endswith(
        "provider_calls=2/2 blocked_calls=1 interrupted=call_limit exit_code=4"
    )
    assert all(client.is_closed for client in transport.clients)
    assert list(scratch_root.iterdir()) == []
    assert_private(out, err)


def test_flag_keeps_cleanup_failure_precedence(smoke, capsys, scratch_root, failing_removal):
    transport = CleanupTransport(HAPPY)
    code = smoke.main(
        with_effort("low"),
        environ={"OPENAI_API_KEY": KEY},
        http_client_factory=transport.client,
        sleep=lambda seconds: None,
        monotonic=clock(),
    )
    out = capsys.readouterr().out

    assert code == 6
    assert lines(out, "resources:") == ["resources: temporary_removed=no providers_closed=2"]
    assert_cleanup_private(out, "")


def test_flag_keeps_sdk_logging_silenced(smoke, capsys, scratch_root, caplog):
    caplog.set_level(logging.DEBUG)

    code, _, _, _, _ = run_main(smoke, capsys, HAPPY, argv=with_effort("low"))

    assert code == 0
    assert [
        record.name
        for record in caplog.records
        if record.name.split(".")[0] in ("openai", "httpx2", "httpcore2", "httpx", "httpcore")
    ] == []


# --- Review 10-07: synthetic policy disclosure of the smoke (SM01-SM05) -----------------
# docs/validation/10-07-confidentiality-follow-up.md; spec 04 Review 04-17-CONF-FU item 2,
# spec 07 Review 07-16-CONF-FU item 4. Offline: MockTransport, no key, no network. The
# scripted call proves the evaluator reaches the real search_policies path; it does not
# prove what a real model would do.

SMOKE_PUBLIC_LINE = "Texto sintético de teste; não descreve nenhuma regra real da loja."
SMOKE_INTERNAL_LINE = "Documento sintético de avaliação; não é o manual da loja."
CALL_P = (
    r'{"type":"function_call","id":"fc_p","call_id":"call_p1","name":"search_policies",'
    r'"arguments":"{\"query\":\"texto sintético de teste\"}","status":"completed"}'
)
CALL_P_INTERNAL = (
    r'{"type":"function_call","id":"fc_q","call_id":"call_p2","name":"search_policies",'
    r'"arguments":"{\"query\":\"documento sintético de avaliação manual\"}",'
    r'"status":"completed"}'
)


def policy_output(smoke, capsys, call):
    script = (ok(REASONING_1, call, rid="resp_1"), ok(MESSAGE_1, rid="resp_2"), *HAPPY[2:])
    _, _, _, transport, _ = run_main(smoke, capsys, script)
    item = transport.sent(1)["input"][3]
    assert item["type"] == "function_call_output"
    return item["output"]


def smoke_index(smoke, tmp_path, edit=None):
    from emporio_agent.data.policy_index import load_policy_index

    document = json.loads(smoke._synthetic_policy_index())
    if edit is not None:
        edit(document)
    path = tmp_path / "smoke-policies.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return load_policy_index(path)


def test_sm01_smoke_policy_call_runs_the_real_reviewed_path(smoke, capsys, scratch_root):
    output = policy_output(smoke, capsys, CALL_P)

    body = json.loads(output)
    assert body["status"] == "ok"
    assert body["data"]["sections"] == [
        {
            "title": "Seção sintética de teste",
            "heading_path": ["Seção sintética de teste"],
            "text": SMOKE_PUBLIC_LINE,
        }
    ]
    assert SMOKE_INTERNAL_LINE not in output
    assert '"section_id"' not in output and smoke.SYNTHETIC_POLICY_SHA256 not in output


def test_sm01_internal_synthetic_line_stays_withheld(smoke, capsys, scratch_root):
    output = policy_output(smoke, capsys, CALL_P_INTERNAL)
    assert SMOKE_INTERNAL_LINE not in output
    assert "Documento sintético" not in output


def test_sm02_registry_is_restored_after_the_run(
    smoke, capsys, scratch_root, monkeypatch, tmp_path
):
    from emporio_agent.services import policy_disclosure

    production = policy_disclosure.REVIEWED_DISCLOSURES
    run_main(smoke, capsys, HAPPY)
    assert policy_disclosure.REVIEWED_DISCLOSURES is production

    monkeypatch.setattr(smoke, "FIXTURE_DIR", tmp_path / "missing")
    code, _, _, _, _ = run_main(smoke, capsys, HAPPY)
    assert code == 5
    assert policy_disclosure.REVIEWED_DISCLOSURES is production
    assert smoke.SYNTHETIC_POLICY_DISCLOSURE not in production


def test_sm03_literal_review_matches_the_synthetic_index(smoke, tmp_path):
    # Verification only: the literal values describe the synthetic content.
    import hashlib

    index = smoke_index(smoke, tmp_path)
    review = smoke.SYNTHETIC_POLICY_DISCLOSURE
    assert review.source_sha256 == smoke.SYNTHETIC_POLICY_SHA256 == index.source.sha256
    for entry, section in zip(review.sections, index.sections, strict=True):
        texts = [text for _, text in section.lines]
        payload = json.dumps([section.title, texts], ensure_ascii=False, separators=(",", ":"))
        assert entry.section_id == section.section_id
        assert entry.digest == hashlib.sha256(payload.encode("utf-8")).hexdigest()
    assert [(e.section_id, e.disposition) for e in review.sections] == [
        ("front_matter", "internal"),
        ("1", "public"),
    ]


def test_sm04_changed_section_or_source_is_not_released(smoke, tmp_path):
    from emporio_agent.services import policy_disclosure

    review = (smoke.SYNTHETIC_POLICY_DISCLOSURE,)

    def change_line(document):
        document["sections"][1]["lines"][0][1] = "Frete grátis para qualquer valor."

    changed = smoke_index(smoke, tmp_path, change_line)
    public = policy_disclosure.public_policy_index(changed, review)
    assert all(section.lines == () for section in public.sections)

    def change_source(document):
        document["source"]["sha256"] = "c" * 64

    other = smoke_index(smoke, tmp_path, change_source)
    with pytest.raises(policy_disclosure.PolicyContentUnreviewedError):
        policy_disclosure.public_policy_index(other, review)


def test_sm05_production_code_never_reaches_the_synthetic_review():
    for path in sorted((REPO / "src").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert "agent_provider_smoke" not in text, path.name
        assert "SYNTHETIC_POLICY" not in text, path.name
