"""Slice 08-D: interactive session of `chat` (docs/specs/08-cli.md G5, G7, G8, G8a, G9, G10,
G11a, G11e; plan CL56-CL85).

The synthetic database and policy index are built in tmp_path by the real prepare_database
and prepare_policy_index. Below the CLI everything is real: connect_readonly,
customer_exists, Agent, ConversationSession, execute_tool, services and SQLite. The model is
a scripted ModelProvider defined here, or the real OpenAIResponsesProvider over
httpx2.MockTransport (CL80, CL81, CL84). Spies only delegate and count; failures are injected
at boundaries (stdin, stdout, provider, customer_exists, the database file). Every expected
line and value is a literal of the spec or of the fixtures. Scripted turns prove mechanisms
of the CLI; they say nothing about the quality of a real model.
"""

from __future__ import annotations

import hashlib
import importlib
import io
import json
import logging
import os
import re
import shutil
import socket
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest

from emporio_agent.data.policy_index import prepare_policy_index
from emporio_agent.data.preparation import prepare_database
from emporio_agent.provider import (
    FunctionCall,
    FunctionCallOutput,
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderItem,
    UserMessage,
)

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests" / "fixtures" / "data" / "valid"
PRIVATE = "pasta-privada-08"
FAKE_KEY = "test-key-not-a-secret"
MODEL = "gpt-5.4-mini"
SNAPSHOT = "gpt-5.4-mini-2026-03-17"
VALID_ENV = {"OPENAI_API_KEY": FAKE_KEY, "OPENAI_MODEL": MODEL}
PREPARED_AT = datetime(2026, 11, 4, 12, 30, 45, tzinfo=UTC)
CLOCK = datetime(2026, 11, 4, 15, 0, tzinfo=UTC)
SDK_LOGGERS = ("openai", "httpx2", "httpcore2", "httpx", "httpcore")

# --- Literal lines (spec G8a, G7, G9a; agent texts of spec 07 G11) ---------------------

BANNER = (
    "Empório da Música — assistente de atendimento (demonstração local, sem autenticação real).\n"
    "Modo: {mode}.\n"
    "Comandos: /sair encerra; /reiniciar inicia nova conversa anônima; "
    "/cliente <id> troca o cliente demonstrativo.\n"
    "Aviso: o texto digitado é enviado à API da OpenAI.\n"
)
ANONYMOUS_BANNER = BANNER.format(mode="sessão anônima")
LINKED_1_BANNER = BANNER.format(mode="cliente demonstrativo 1")
PROMPT = "Você: "
FAREWELL_EOF = "\nAtendimento encerrado. Até logo.\n"
FAREWELL_SAIR = "Atendimento encerrado. Até logo.\n"
LIMIT = "Limite de 30 mensagens por execução atingido. Atendimento encerrado.\n"
INVALID_COMMAND = "Comando desconhecido. Comandos: /sair, /reiniciar, /cliente <id>.\n"
RESTARTED = "Conversa reiniciada. Sessão anônima ativa.\n"
LINKED_2 = "Sessão vinculada ao cliente demonstrativo 2. Histórico apagado.\n"
LINKED_1 = "Sessão vinculada ao cliente demonstrativo 1. Histórico apagado.\n"
ARGUMENT_INVALID = (
    "Comando /cliente inválido. Use /cliente <id> com um número de cliente demonstrativo. "
    "Sessão anônima ativa; histórico apagado.\n"
)
NOT_FOUND = "Cliente demonstrativo não encontrado. Sessão anônima ativa; histórico apagado.\n"
LOOKUP_FAILED = (
    "Não foi possível verificar o cliente demonstrativo. Sessão anônima ativa; histórico apagado.\n"
)
INTERRUPTED = "interrompido\n"
READ_FAILED = "input_read_failed - Não foi possível ler a entrada do terminal.\n"
CLEANUP_FAILED = "cleanup_failed - Falha ao encerrar a conexão com o provedor.\n"

INPUT_EMPTY_TEXT = (
    "A sua mensagem está vazia. Escreva a sua pergunta sobre produtos, preços, pedidos ou "
    "políticas da loja."
)
INPUT_TOO_LONG_TEXT = (
    "A sua mensagem é longa demais. Envie uma pergunta mais curta, com até 4.000 caracteres."
)
INPUT_INVALID_TEXT = (
    "Não consegui ler a sua mensagem porque ela contém caracteres inválidos. "
    "Escreva a mensagem novamente."
)
TEMPORARY_TEXT = "O assistente está temporariamente indisponível. Tente novamente em instantes."
CONFIGURATION_TEXT = (
    "O assistente está indisponível por um problema de configuração. "
    "Avise o responsável pela aplicação."
)
LINKED_LINE = (
    "Session: the application linked a demonstration customer to this session. "
    "Order lookups apply only to that customer."
)
ANONYMOUS_LINE = (
    "Session: no customer is linked to this session. Order lookups are unavailable; "
    "do not ask the customer to identify themselves by message."
)
NOT_ACCESSIBLE = (
    '{"tool":"get_order","status":"unavailable","data":null,'
    '"error":{"code":"order_not_accessible","field":null}}'
)
SEARCH_ARGS = (
    '{"query":"Teste","category":null,"max_price_brl":null,"available_only":false,"offset":0}'
)
QUOTE_PIX_ARGS = '{"product_id":10,"payment_method":"pix"}'
DIAG_RECORD = re.compile(r"diag: [a-z]+( [a-z_0-9]+=[A-Za-z0-9_.:@,;-]+)+\n")


def answer_line(text: str) -> str:
    return "Assistente: " + text + "\n"


# --- Offline guard and resources ---------------------------------------------------------


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
def cli():
    return importlib.import_module("emporio_agent.cli")


@pytest.fixture
def prepared(tmp_path, policy_kit):
    source = tmp_path / "src"
    shutil.copytree(FIXTURE, source)
    folder = tmp_path / PRIVATE
    folder.mkdir()
    database = folder / "emporio.sqlite3"
    prepare_database(source, database, clock=lambda: PREPARED_AT)
    index = folder / "policy-index.json"
    prepare_policy_index(policy_kit.write(tmp_path), index)
    return {"database": database, "index": index}


# --- Scripted model (test-only) ------------------------------------------------------------


def message(text: str, item_id: str) -> ProviderItem:
    body = {
        "type": "message",
        "id": item_id,
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }
    return ProviderItem("assistant_message", json.dumps(body, ensure_ascii=False))


def call(call_id: str, name: str, arguments: str) -> FunctionCall:
    body = {
        "type": "function_call",
        "id": "fc_" + call_id,
        "call_id": call_id,
        "name": name,
        "arguments": arguments,
        "status": "completed",
    }
    return FunctionCall(call_id, name, arguments, json.dumps(body, ensure_ascii=False))


def response(*output, text: str = "") -> ModelResponse:
    return ModelResponse(
        response_id="resp_x",
        model=SNAPSHOT,
        status="completed",
        incomplete_reason=None,
        output=tuple(output),
        text=text,
        commentary_present=False,
        refusal=False,
        usage=None,
        usage_invalid=False,
        request_id=None,
    )


def final(text: str, item_id: str = "msg_f") -> ModelResponse:
    return response(message(text, item_id), text=text)


def tool(call_id: str, name: str, arguments: str) -> ModelResponse:
    return response(call(call_id, name, arguments))


class ScriptedProvider:
    """Returns or raises scripted steps; records requests and close(). No network."""

    def __init__(self, *steps, close_error: BaseException | None = None) -> None:
        self.steps = list(steps)
        self.requests: list[ModelRequest] = []
        self.overflow = 0
        self.close_calls = 0
        self.close_error = close_error

    @property
    def model(self) -> str:
        return MODEL

    def create(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self.steps:
            self.overflow += 1
            raise AssertionError("unscripted provider call")
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step

    def close(self) -> None:
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


class Factory:
    def __init__(self, provider) -> None:
        self.provider = provider
        self.calls = 0

    def __call__(self, config):
        self.calls += 1
        return self.provider


class Lines:
    """stdin double: one readline per scripted line; '' after the script (EOF).

    `actions` maps a read number (1-based) to a callable run before that read returns;
    `errors` maps a read number to an exception raised by that read.
    """

    def __init__(self, lines, *, actions=None, errors=None) -> None:
        self.lines = list(lines)
        self.reads = 0
        self.actions = actions or {}
        self.errors = errors or {}

    def readline(self, *args) -> str:
        self.reads += 1
        if self.reads in self.actions:
            self.actions[self.reads]()
        if self.reads in self.errors:
            raise self.errors[self.reads]
        if self.lines:
            return self.lines.pop(0)
        return ""


def _stream(encoding: str = "utf-8") -> io.TextIOWrapper:
    return io.TextIOWrapper(io.BytesIO(), encoding=encoding, newline="\n")


def _text(stream: io.TextIOWrapper) -> str:
    stream.flush()
    return stream.buffer.getvalue().decode("utf-8")


def run(cli, prepared, lines, provider, *extra, stdin=None, stdout=None, factory=None):
    stdin = Lines(lines) if stdin is None else stdin
    stdout = _stream() if stdout is None else stdout
    stderr = _stream()
    factory = Factory(provider) if factory is None else factory
    sleeps: list[float] = []
    code = cli.main(
        [
            "chat",
            "--database",
            str(prepared["database"]),
            "--index",
            str(prepared["index"]),
            *extra,
        ],
        environ=dict(VALID_ENV),
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        provider_factory=factory,
        clock=lambda: CLOCK,
        sleep=sleeps.append,
    )
    assert type(code) is int
    out = _text(stdout) if isinstance(stdout, io.TextIOWrapper) else ""
    err = _text(stderr)
    assert "Traceback" not in out + err
    assert FAKE_KEY not in out + err
    assert str(prepared["database"].parent) not in out + err
    assert factory.calls == 1
    if isinstance(provider, ScriptedProvider):
        assert provider.overflow == 0
        assert provider.close_calls == 1
    return code, out, err


def user_texts(request: ModelRequest) -> list[str]:
    return [item.text for item in request.items if isinstance(item, UserMessage)]


def outputs(request: ModelRequest) -> list[str]:
    return [item.output for item in request.items if isinstance(item, FunctionCallOutput)]


@pytest.fixture
def tool_opens(monkeypatch):
    """Delegating counter on connect_readonly as used by the tools (not by the CLI)."""
    from contextlib import contextmanager

    from emporio_agent.data.readonly import connect_readonly as real

    tools = importlib.import_module("emporio_agent.tools")
    record = {"opens": 0}

    @contextmanager
    def counted(path):
        record["opens"] += 1
        with real(path) as connection:
            yield connection

    monkeypatch.setattr(tools, "connect_readonly", counted)
    return record


@pytest.fixture
def cli_spies(monkeypatch, cli):
    """Delegating counters on connect_readonly and customer_exists as used by the CLI."""
    record = {"connect_readonly": 0, "customer_exists": 0}
    for name in record:
        original = getattr(cli, name)

        def spy(*args, _original=original, _name=name, **kwargs):
            record[_name] += 1
            return _original(*args, **kwargs)

        monkeypatch.setattr(cli, name, spy)
    return record


# --- CL56: one message ------------------------------------------------------------------------


def test_cl56_one_message_one_answer(cli, prepared):
    provider = ScriptedProvider(final("Olá! Como posso ajudar?"))
    code, out, err = run(cli, prepared, ["Olá\n"], provider)
    assert (code, err) == (0, "")
    assert out == (
        ANONYMOUS_BANNER + PROMPT + answer_line("Olá! Como posso ajudar?") + PROMPT + FAREWELL_EOF
    )
    assert len(provider.requests) == 1
    assert provider.requests[0].items == (UserMessage("Olá"),)
    assert provider.requests[0].instructions.endswith("\n\n" + ANONYMOUS_LINE)


# --- CL57: follow-up with history and real tools ---------------------------------------------


def test_cl57_follow_up_pix_resends_history_and_uses_real_tools(cli, prepared, tool_opens):
    database = prepared["database"]
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    first_call = call("call_s1", "search_products", SEARCH_ARGS)
    first_answer = message("Temos o Violão Teste por R$ 539,91.", "msg_1")
    provider = ScriptedProvider(
        response(first_call),
        response(first_answer, text="Temos o Violão Teste por R$ 539,91."),
        tool("call_q1", "quote_product", QUOTE_PIX_ARGS),
        final("No PIX fica R$ 539,91; o desconto PIX não acumula com a promoção.", "msg_2"),
    )
    code, out, err = run(cli, prepared, ["Quais violões?\n", "e no PIX?\n"], provider)
    assert (code, err) == (0, "")
    assert answer_line("Temos o Violão Teste por R$ 539,91.") in out
    assert answer_line("No PIX fica R$ 539,91; o desconto PIX não acumula com a promoção.") in out
    assert len(provider.requests) == 4
    third = provider.requests[2]
    # Turn 1 chain in order, then the new user message.
    assert [type(item).__name__ for item in third.items] == [
        "UserMessage",
        "FunctionCall",
        "FunctionCallOutput",
        "ProviderItem",
        "UserMessage",
    ]
    assert third.items[0] == UserMessage("Quais violões?")
    assert third.items[1] == first_call and third.items[3] == first_answer
    assert third.items[4] == UserMessage("e no PIX?")
    search = json.loads(third.items[2].output)
    assert search["status"] == "ok"
    assert [p["product_id"] for p in search["data"]["products"]] == [11, 10, 13, 12]
    quote = json.loads(outputs(provider.requests[3])[-1])
    assert quote["tool"] == "quote_product" and quote["status"] == "ok"
    assert quote["data"]["payment_method"] == "pix"
    assert quote["data"]["list_price"] == "599.90"
    assert quote["data"]["final_price"] == "539.91"
    assert quote["data"]["discount_source"] == "promotion"
    assert quote["data"]["pix_not_combined"] is True
    assert tool_opens["opens"] == 2
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    for suffix in ("-journal", "-wal", "-shm"):
        assert not database.with_name(database.name + suffix).exists()


# --- CL58: /sair ---------------------------------------------------------------------------


@pytest.mark.parametrize("line", ["/sair\n", "  /sair  \n", "/sair\r\n", "\t/sair"])
def test_cl58_sair_exits_without_model_call(cli, prepared, line):
    provider = ScriptedProvider()
    stdin = Lines([line, "SENTINEL-USER nunca lido\n"])
    code, out, err = run(cli, prepared, None, provider, stdin=stdin)
    assert (code, out, err) == (0, ANONYMOUS_BANNER + PROMPT + FAREWELL_SAIR, "")
    assert provider.requests == []
    assert stdin.reads == 1


# --- CL59: /reiniciar ------------------------------------------------------------------------


@pytest.mark.parametrize("extra", [(), ("--customer", "1")])
def test_cl59_reiniciar_clears_history_and_identity(cli, prepared, extra):
    provider = ScriptedProvider(final("Oi!"), final("Certo."))
    code, out, err = run(
        cli, prepared, ["oi\n", "/reiniciar\n", "nova pergunta\n"], provider, *extra
    )
    assert (code, err) == (0, "")
    assert out.endswith(
        PROMPT
        + answer_line("Oi!")
        + PROMPT
        + RESTARTED
        + PROMPT
        + answer_line("Certo.")
        + PROMPT
        + FAREWELL_EOF
    )
    assert len(provider.requests) == 2
    assert provider.requests[1].items == (UserMessage("nova pergunta"),)
    assert provider.requests[1].instructions.endswith("\n\n" + ANONYMOUS_LINE)


# --- CL60: switch between customers -----------------------------------------------------------


@pytest.mark.parametrize("switch", ["/cliente 2\n", "  /cliente   2  \n", "/cliente\t2\n"])
def test_cl60_switch_changes_identity_and_access(cli, prepared, switch):
    provider = ScriptedProvider(
        tool("c1", "get_order", '{"order_id":1000}'),
        final("Seu pedido 1000 foi entregue.", "m1"),
        tool("c2", "get_order", '{"order_id":1000}'),
        final("Não consigo acessar esse pedido.", "m2"),
        tool("c3", "get_order", '{"order_id":1001}'),
        final("O pedido 1001 foi cancelado.", "m3"),
    )
    lines = ["pedido 1000?\n", switch, "e o 1000?\n", "e o 1001?\n"]
    code, out, err = run(cli, prepared, lines, provider, "--customer", "1")
    assert (code, err) == (0, "")
    assert out.startswith(LINKED_1_BANNER)
    assert PROMPT + LINKED_2 + PROMPT in out
    first = json.loads(outputs(provider.requests[1])[-1])
    assert (first["status"], first["data"]["order_id"]) == ("ok", 1000)
    after_switch = provider.requests[2]
    assert after_switch.items == (UserMessage("e o 1000?"),)
    assert after_switch.instructions.endswith("\n\n" + LINKED_LINE)
    assert outputs(provider.requests[3])[-1] == NOT_ACCESSIBLE
    third = json.loads(outputs(provider.requests[5])[-1])
    assert (third["status"], third["data"]["order_id"]) == ("ok", 1001)


def test_cl60_anonymous_session_links_customer_one(cli, prepared):
    provider = ScriptedProvider(
        tool("c1", "get_order", '{"order_id":1000}'), final("Pedido entregue.")
    )
    code, out, err = run(cli, prepared, ["/cliente 1\n", "pedido 1000?\n"], provider)
    assert (code, err) == (0, "")
    assert out.startswith(ANONYMOUS_BANNER + PROMPT + LINKED_1 + PROMPT)
    assert provider.requests[0].instructions.endswith("\n\n" + LINKED_LINE)
    order = json.loads(outputs(provider.requests[1])[-1])
    assert (order["status"], order["data"]["order_id"]) == ("ok", 1000)


# --- CL61: not_found, lookup_failed, interruption during the lookup ---------------------------


def _assert_anonymous_after(provider, request_index: int, text: str) -> None:
    request = provider.requests[request_index]
    assert request.items == (UserMessage(text),)
    assert request.instructions.endswith("\n\n" + ANONYMOUS_LINE)


def test_cl61_unknown_customer_leaves_anonymous_empty_session(cli, prepared, tool_opens):
    provider = ScriptedProvider(
        final("Oi!"),
        tool("c1", "get_order", '{"order_id":1000}'),
        final("Não consigo acessar pedidos.", "m2"),
    )
    lines = ["oi\n", "/cliente 3\n", "pedido 1000?\n"]
    code, out, err = run(cli, prepared, lines, provider, "--customer", "1")
    assert (code, err) == (0, "")
    assert PROMPT + NOT_FOUND + PROMPT in out
    _assert_anonymous_after(provider, 1, "pedido 1000?")
    assert outputs(provider.requests[2]) == [NOT_ACCESSIBLE]
    assert tool_opens["opens"] == 0


def test_cl61_lookup_failure_with_broken_database_does_not_restore(cli, prepared):
    database = prepared["database"]

    def break_database():
        database.write_text("isto não é um banco SQLite " * 20, encoding="utf-8")

    provider = ScriptedProvider(
        final("Oi!"),
        tool("c1", "get_order", '{"order_id":1000}'),
        final("Não consigo acessar pedidos.", "m2"),
    )
    stdin = Lines(["oi\n", "/cliente 2\n", "pedido 1000?\n"], actions={2: break_database})
    code, out, err = run(cli, prepared, None, provider, "--customer", "1", stdin=stdin)
    assert (code, err) == (0, "")
    assert PROMPT + LOOKUP_FAILED + PROMPT in out
    _assert_anonymous_after(provider, 1, "pedido 1000?")
    assert outputs(provider.requests[2]) == [NOT_ACCESSIBLE]


def test_cl61_lookup_sqlite_error_is_lookup_failed(cli, prepared, monkeypatch):
    def broken(connection, customer):
        raise sqlite3.OperationalError("SENTINEL-PAYLOAD")

    provider = ScriptedProvider(final("Oi!"), final("Olá de novo."))
    monkeypatch.setattr(cli, "customer_exists", broken)
    lines = ["oi\n", "/cliente 2\n", "ainda aí?\n"]
    code, out, err = run(cli, prepared, lines, provider)
    assert (code, err) == (0, "")
    assert PROMPT + LOOKUP_FAILED + PROMPT in out
    assert "SENTINEL-PAYLOAD" not in out
    _assert_anonymous_after(provider, 1, "ainda aí?")


def test_cl61_interrupt_during_lookup_exits_130(cli, prepared, monkeypatch):
    real = cli.customer_exists
    calls = []

    def interrupted(connection, customer):
        calls.append(customer)
        if len(calls) == 2:  # the first call is the startup check of --customer 1
            raise KeyboardInterrupt
        return real(connection, customer)

    monkeypatch.setattr(cli, "customer_exists", interrupted)
    provider = ScriptedProvider(final("Oi!"))
    stdin = Lines(["oi\n", "/cliente 2\n", "não deve ser lido\n"])
    code, out, err = run(cli, prepared, None, provider, "--customer", "1", stdin=stdin)
    assert (code, err) == (130, INTERRUPTED)
    assert out == LINKED_1_BANNER + PROMPT + answer_line("Oi!") + PROMPT
    assert len(provider.requests) == 1
    assert stdin.reads == 2


# --- CL62: invalid /cliente arguments ---------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "/cliente\n",
        "/cliente   \n",
        "/cliente abc\n",
        "/cliente 1 2\n",
        "/cliente 01\n",
        "/cliente -1\n",
        "/cliente 9223372036854775808\n",
        "/cliente sk-SENTINEL-KEY\n",
    ],
)
def test_cl62_invalid_argument_resets_without_lookup(cli, prepared, cli_spies, line):
    provider = ScriptedProvider(
        final("Oi!"),
        tool("c1", "get_order", '{"order_id":1000}'),
        final("Não consigo acessar pedidos.", "m2"),
    )
    lines = ["oi\n", line, "pedido 1000?\n"]
    code, out, err = run(cli, prepared, lines, provider, "--customer", "1")
    assert (code, err) == (0, "")
    assert PROMPT + ARGUMENT_INVALID + PROMPT in out
    _assert_anonymous_after(provider, 1, "pedido 1000?")
    assert outputs(provider.requests[2]) == [NOT_ACCESSIBLE]
    # Startup only: one database check and one customer check of --customer 1.
    assert cli_spies == {"connect_readonly": 2, "customer_exists": 1}
    for echoed in ("abc", "SENTINEL", "9223372036854775808", "01"):
        assert echoed not in out + err


# --- CL63: similar and unknown commands keep the session --------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "/SAIR\n",
        "/sair agora\n",
        "/xyz\n",
        "/\n",
        "//sair\n",
        "/clienteX 2\n",
        "/cliente_2\n",
        "/CLIENTE 2\n",
        "/Cliente 2\n",
        "/reiniciar agora\n",
        "/SENTINEL-USER\n",
    ],
)
def test_cl63_invalid_command_keeps_identity_and_history(cli, prepared, cli_spies, line):
    provider = ScriptedProvider(
        final("Oi!"),
        tool("c1", "get_order", '{"order_id":1000}'),
        final("Pedido entregue.", "m2"),
    )
    lines = ["oi\n", line, "pedido 1000?\n"]
    code, out, err = run(cli, prepared, lines, provider, "--customer", "1")
    assert (code, err) == (0, "")
    assert PROMPT + INVALID_COMMAND + PROMPT in out
    assert len(provider.requests) == 3
    kept = provider.requests[1]
    assert user_texts(kept) == ["oi", "pedido 1000?"]
    assert kept.instructions.endswith("\n\n" + LINKED_LINE)
    order = json.loads(outputs(provider.requests[2])[-1])
    assert (order["status"], order["data"]["order_id"]) == ("ok", 1000)
    assert cli_spies == {"connect_readonly": 2, "customer_exists": 1}
    assert "SENTINEL-USER" not in out + err
    for echoed in ("/xyz", "/clienteX", "/cliente_2", "/CLIENTE", "/Cliente", "//sair", "agora"):
        assert echoed not in out + err


# --- CL64: ordinary messages are not commands -------------------------------------------------


def test_cl64_ordinary_messages_reach_the_agent_unchanged(cli, prepared):
    texts = ["sair", "quero sair", "reiniciar", "1/2 violão", "cliente 2", "  espaços  ", "a /sair"]
    provider = ScriptedProvider(*(final(f"r{i}", f"m{i}") for i in range(len(texts))))
    code, out, err = run(cli, prepared, [text + "\n" for text in texts], provider)
    assert (code, err) == (0, "")
    assert [user_texts(request)[-1] for request in provider.requests] == texts
    assert INVALID_COMMAND not in out


def test_cl64_cliente_without_slash_keeps_identity(cli, prepared):
    provider = ScriptedProvider(
        final("Entendi."),
        tool("c1", "get_order", '{"order_id":1000}'),
        final("Pedido entregue.", "m2"),
    )
    lines = ["cliente 2\n", "pedido 1000?\n"]
    code, _, err = run(cli, prepared, lines, provider, "--customer", "1")
    assert (code, err) == (0, "")
    order = json.loads(outputs(provider.requests[2])[-1])
    assert (order["status"], order["data"]["order_id"]) == ("ok", 1000)


# --- CL65, CL78: rejected input -----------------------------------------------------------------


def test_cl65_blank_and_too_long_lines_are_rejected_by_the_agent(cli, prepared):
    provider = ScriptedProvider()
    lines = ["\n", "   \n", "x" * 4001 + "\n"]
    code, out, err = run(cli, prepared, lines, provider)
    assert (code, err) == (0, "")
    assert out == (
        ANONYMOUS_BANNER
        + PROMPT
        + answer_line(INPUT_EMPTY_TEXT)
        + PROMPT
        + answer_line(INPUT_EMPTY_TEXT)
        + PROMPT
        + answer_line(INPUT_TOO_LONG_TEXT)
        + PROMPT
        + FAREWELL_EOF
    )
    assert provider.requests == []


def test_cl78_undecodable_stdin_bytes_are_rejected(cli, prepared):
    stdin = io.TextIOWrapper(io.BytesIO(b"oi \xff\n"), encoding="cp1252", newline="\n")
    provider = ScriptedProvider()
    code, out, err = run(cli, prepared, None, provider, stdin=stdin)
    assert (code, err) == (0, "")
    assert (
        out == ANONYMOUS_BANNER + PROMPT + answer_line(INPUT_INVALID_TEXT) + PROMPT + FAREWELL_EOF
    )
    assert provider.requests == []


# --- CL66, CL67: message limit (D-04) --------------------------------------------------------


def test_cl66_thirty_messages_then_end_before_the_next_read(cli, prepared):
    provider = ScriptedProvider(*(final(f"r{i}", f"m{i}") for i in range(1, 31)))
    stdin = Lines([f"pergunta {i}\n" for i in range(1, 32)] + ["SENTINEL-USER\n"])
    code, out, err = run(cli, prepared, None, provider, stdin=stdin)
    assert (code, err) == (0, "")
    assert stdin.reads == 30
    assert len(provider.requests) == 30
    assert out.endswith(PROMPT + answer_line("r30") + LIMIT)
    assert out.count(PROMPT) == 30
    assert "SENTINEL-USER" not in out


def test_cl66_twenty_nine_messages_then_eof_is_a_normal_farewell(cli, prepared):
    provider = ScriptedProvider(*(final(f"r{i}", f"m{i}") for i in range(1, 30)))
    stdin = Lines([f"pergunta {i}\n" for i in range(1, 30)])
    code, out, err = run(cli, prepared, None, provider, stdin=stdin)
    assert (code, err) == (0, "")
    assert stdin.reads == 30
    assert out.endswith(PROMPT + answer_line("r29") + PROMPT + FAREWELL_EOF)
    assert LIMIT not in out


def test_cl67_rejected_inputs_and_commands_do_not_count_and_reset_restores_nothing(cli, prepared):
    steps = [final(f"r{i}", f"m{i}") for i in range(1, 30)]
    steps.insert(14, ProviderError("authentication"))  # a failed turn counts
    provider = ScriptedProvider(*steps)
    lines = (
        [f"a{i}\n" for i in range(1, 11)]
        + ["\n", "/reiniciar\n", "x" * 4001 + "\n"]
        + [f"b{i}\n" for i in range(1, 11)]
        + ["/cliente 2\n", "/xyz\n", "/cliente abc\n"]
        + [f"c{i}\n" for i in range(1, 11)]
        + ["SENTINEL-USER\n"]
    )
    stdin = Lines(lines)
    code, out, err = run(cli, prepared, None, provider, stdin=stdin)
    assert (code, err) == (0, "")
    assert stdin.reads == 36
    assert len(provider.requests) == 30
    assert answer_line(CONFIGURATION_TEXT) in out
    assert out.endswith(answer_line("r29") + LIMIT)


# --- CL68: anonymous session never opens the database for orders ------------------------------


def test_cl68_anonymous_get_order_opens_no_database(cli, prepared, tool_opens):
    provider = ScriptedProvider(
        tool("c1", "get_order", '{"order_id":1000}'), final("Não consigo acessar pedidos.")
    )
    code, _, err = run(cli, prepared, ["pedido 1000?\n"], provider)
    assert (code, err) == (0, "")
    assert outputs(provider.requests[1]) == [NOT_ACCESSIBLE]
    assert tool_opens["opens"] == 0


# --- CL69-CL75: end of session, interruption and failures -------------------------------------


def test_cl69_eof_after_two_turns(cli, prepared):
    provider = ScriptedProvider(final("Um."), final("Dois.", "m2"))
    code, out, err = run(cli, prepared, ["a\n", "b\n"], provider)
    assert (code, err) == (0, "")
    assert out.endswith(answer_line("Dois.") + PROMPT + FAREWELL_EOF)


def test_cl69_last_line_without_terminator_is_a_message(cli, prepared):
    provider = ScriptedProvider(final("Ok."))
    code, out, err = run(cli, prepared, ["sem quebra"], provider)
    assert (code, err) == (0, "")
    assert provider.requests[0].items == (UserMessage("sem quebra"),)


def test_cl70_interrupt_at_second_prompt(cli, prepared):
    provider = ScriptedProvider(final("Oi!"))
    stdin = Lines(["oi\n"], errors={2: KeyboardInterrupt()})
    code, out, err = run(cli, prepared, None, provider, stdin=stdin)
    assert (code, out, err) == (
        130,
        ANONYMOUS_BANNER + PROMPT + answer_line("Oi!") + PROMPT,
        INTERRUPTED,
    )


@pytest.mark.parametrize("diagnostics", [(), ("--diagnostics",)])
def test_cl71_interrupt_during_turn_prints_no_answer_and_no_record(cli, prepared, diagnostics):
    provider = ScriptedProvider(final("Oi!"), KeyboardInterrupt())
    stdin = Lines(["oi\n", "SENTINEL-USER segunda\n", "nunca lido\n"])
    code, out, err = run(cli, prepared, None, provider, *diagnostics, stdin=stdin)
    assert code == 130
    assert out == ANONYMOUS_BANNER + PROMPT + answer_line("Oi!") + PROMPT
    assert err.endswith(INTERRUPTED)
    assert err.count("diag: turn") == (1 if diagnostics else 0)
    assert "n=2" not in err
    assert stdin.reads == 2


def test_cl72_read_failure_after_one_turn(cli, prepared):
    provider = ScriptedProvider(final("Oi!"))
    stdin = Lines(["oi\n"], errors={2: OSError("SENTINEL-PAYLOAD")})
    code, out, err = run(cli, prepared, None, provider, stdin=stdin)
    assert (code, err) == (1, READ_FAILED)
    assert out.endswith(answer_line("Oi!") + PROMPT)


def test_cl73_transient_failure_then_history_intact(cli, prepared):
    provider = ScriptedProvider(
        final("Oi!"),
        ProviderError("timeout"),
        ProviderError("timeout"),
        final("Agora sim.", "m3"),
    )
    lines = ["oi\n", "falha\n", "de novo\n"]
    code, out, err = run(cli, prepared, lines, provider)
    assert (code, err) == (0, "")
    assert PROMPT + answer_line(TEMPORARY_TEXT) + PROMPT in out
    assert user_texts(provider.requests[3]) == ["oi", "de novo"]


def test_cl74_authentication_failure_does_not_end_the_session(cli, prepared):
    provider = ScriptedProvider(ProviderError("authentication"), final("Ok.", "m2"))
    code, out, err = run(cli, prepared, ["a\n", "b\n"], provider)
    assert (code, err) == (0, "")
    assert out == (
        ANONYMOUS_BANNER
        + PROMPT
        + answer_line(CONFIGURATION_TEXT)
        + PROMPT
        + answer_line("Ok.")
        + PROMPT
        + FAREWELL_EOF
    )


def test_cl75_answer_write_failure_is_internal_error(cli, prepared):
    class BrokenAnswer(io.StringIO):
        def write(self, text):
            if text.startswith("Assistente:"):
                raise RuntimeError("SENTINEL-PAYLOAD")
            return super().write(text)

    provider = ScriptedProvider(final("Oi!"))
    stdout = BrokenAnswer()
    code, _, err = run(cli, prepared, ["oi\n", "nunca\n"], provider, stdout=stdout)
    assert (code, err) == (1, "internal_error: RuntimeError\n")
    assert "SENTINEL-PAYLOAD" not in stdout.getvalue()


def test_unexpected_exception_from_respond_is_internal_error(cli, prepared, monkeypatch):
    def broken(self, session, text):
        raise LookupError("SENTINEL-PAYLOAD")

    monkeypatch.setattr(cli.Agent, "respond", broken)
    provider = ScriptedProvider()
    code, out, err = run(cli, prepared, ["oi\n"], provider, "--diagnostics")
    assert code == 1
    assert err.endswith("internal_error: LookupError\n") and "diag: turn" not in err
    assert "Assistente:" not in out


@pytest.mark.parametrize(
    ("lines", "errors", "first_code_err"),
    [
        (["/sair\n"], {}, ""),
        (["oi\n"], {2: KeyboardInterrupt()}, INTERRUPTED),
        ([f"m{i}\n" for i in range(30)], {}, ""),
    ],
)
def test_cleanup_failure_wins_after_session_paths(cli, prepared, lines, errors, first_code_err):
    steps = [final(f"r{i}", f"m{i}") for i in range(30)]
    provider = ScriptedProvider(*steps, close_error=OSError("SENTINEL-PAYLOAD"))
    stdin = Lines(lines, errors=errors)
    stdout, stderr = _stream(), _stream()
    code = cli.main(
        ["chat", "--database", str(prepared["database"]), "--index", str(prepared["index"])],
        environ=dict(VALID_ENV),
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        provider_factory=Factory(provider),
        clock=lambda: CLOCK,
    )
    assert (code, _text(stderr)) == (1, first_code_err + CLEANUP_FAILED)
    assert provider.close_calls == 1


@pytest.mark.parametrize(
    ("lines", "errors"), [(["/sair\n"], {}), (["oi\n"], {2: KeyboardInterrupt()})]
)
def test_sdk_loggers_restored_after_session_exits(cli, prepared, lines, errors):
    previous = {name: logging.getLogger(name).level for name in SDK_LOGGERS}
    logging.getLogger("openai").setLevel(logging.INFO)
    try:
        before = {name: logging.getLogger(name).level for name in SDK_LOGGERS}
        seen = {}

        class Observing(ScriptedProvider):
            def create(self, request):
                seen.update({n: logging.getLogger(n).level for n in SDK_LOGGERS})
                return super().create(request)

        provider = Observing(final("Oi!"))
        run(cli, prepared, None, provider, stdin=Lines(lines, errors=errors))
        if provider.requests:
            assert seen == {name: logging.CRITICAL + 1 for name in SDK_LOGGERS}
        assert {name: logging.getLogger(name).level for name in SDK_LOGGERS} == before
    finally:
        for name, level in previous.items():
            logging.getLogger(name).setLevel(level)


# --- CL76, CL77: terminal sanitization and encoding -------------------------------------------


def test_cl76_controls_replaced_on_display_only(cli, prepared):
    raw_text = "a\x1b[31mb\rc\x07d\x9be\x00f\x7fg\x85h\ti\nj"
    shown = "a�[31mb�c�d�e�f�g�h\ti\nj"
    original = message(raw_text, "m1")
    provider = ScriptedProvider(response(original, text=raw_text), final("Ok.", "m2"))
    code, out, err = run(cli, prepared, ["oi\n", "e?\n"], provider)
    assert (code, err) == (0, "")
    assert PROMPT + "Assistente: " + shown + "\n" + PROMPT in out
    assert "\x1b" not in out and "\r" not in out and "\x00" not in out
    assert provider.requests[1].items[1] == original
    assert raw_text in json.loads(provider.requests[1].items[1].raw)["content"][0]["text"]


def test_cl77_utf8_answer_on_a_cp1252_declared_stdout(cli, prepared):
    stdout = _stream(encoding="cp1252")
    provider = ScriptedProvider(final("Violão ç 🎸"))
    code, _, err = run(cli, prepared, ["oi\n"], provider, stdout=stdout)
    assert (code, err) == (0, "")
    assert "Assistente: Violão ç 🎸\n".encode() in stdout.buffer.getvalue()


# --- CL79-CL85: diagnostics and streams ---------------------------------------------------------

TURN_KEYS = (
    "n outcome failure attempts retries tools references configured_model reported_models "
    "request_ids reasoning_efforts_reported max_request_bytes input_tokens output_tokens "
    "usage_missing usage_invalid "
    "budget_failure history_turns pruned_turns"
).split()


def records(err: str, kind: str) -> list[dict[str, str]]:
    found = []
    for line in err.splitlines(keepends=True):
        if line.startswith(f"diag: {kind} "):
            assert DIAG_RECORD.fullmatch(line)
            found.append(dict(part.split("=", 1) for part in line[len(f"diag: {kind} ") :].split()))
    return found


def test_cl79_no_diagnostics_without_flag(cli, prepared):
    provider = ScriptedProvider(final("Oi!"), ProviderError("authentication"))
    code, _, err = run(cli, prepared, ["oi\n", "\n", "b\n", "/reiniciar\n"], provider)
    assert (code, err) == (0, "")


def test_cl80_ri2_turn_numbering_includes_rejected_input(cli, prepared):
    provider = ScriptedProvider(final("Oi!"), final("Ok.", "m2"))
    lines = ["oi\n", "\n", "/xyz\n", "/reiniciar\n", "e?\n"]
    code, out, err = run(cli, prepared, lines, provider, "--diagnostics")
    assert code == 0
    turns = records(err, "turn")
    assert [(t["n"], t["outcome"]) for t in turns] == [
        ("1", "answered"),
        ("2", "rejected_input"),
        ("3", "answered"),
    ]
    assert (
        "diag: turn n=2 outcome=rejected_input failure=input_empty attempts=0 retries=0 "
        "tools=- references=- configured_model=gpt-5.4-mini reported_models=- request_ids=- "
        "reasoning_efforts_reported=- max_request_bytes=- input_tokens=- output_tokens=- "
        "usage_missing=0 usage_invalid=0 "
        "budget_failure=- history_turns=1 pruned_turns=0\n"
    ) in err
    assert list(turns[0]) == TURN_KEYS
    assert turns[2]["history_turns"] == "1"  # after /reiniciar
    kinds = [" ".join(line.split()[:3]) for line in err.splitlines()]
    assert kinds == [
        "diag: start mode=anonymous",
        "diag: turn n=1",
        "diag: turn n=2",
        "diag: reset trigger=reiniciar",
        "diag: turn n=3",
    ]


def test_cl80_turn_record_follows_its_answer_line(cli, prepared):
    class Shared:
        def __init__(self):
            self.events = []

        def stream(self, name):
            shared = self

            class Recorder(io.StringIO):
                def write(self, text):
                    shared.events.append((name, text))
                    return super().write(text)

            return Recorder()

    shared = Shared()
    provider = ScriptedProvider(final("Oi!"))
    code = cli.main(
        [
            "chat",
            "--database",
            str(prepared["database"]),
            "--index",
            str(prepared["index"]),
            "--diagnostics",
        ],
        environ=dict(VALID_ENV),
        stdin=Lines(["oi\n", "/reiniciar\n"]),
        stdout=shared.stream("out"),
        stderr=shared.stream("err"),
        provider_factory=Factory(provider),
        clock=lambda: CLOCK,
    )
    assert code == 0
    events = [(name, text) for name, text in shared.events if text]
    answer = events.index(("out", "Assistente: Oi!\n"))
    assert events[answer + 1][0] == "err" and events[answer + 1][1].startswith("diag: turn n=1 ")
    restarted = events.index(("out", RESTARTED))
    assert events[restarted + 1] == (
        "err",
        "diag: reset trigger=reiniciar result=ok mode=anonymous\n",
    )


class HttpTransport:
    """MockTransport handler: literal HTTP answers in order; records every request."""

    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if not self.answers:
            raise AssertionError("unscripted HTTP request")
        status, content, request_id = self.answers.pop(0)
        return httpx2.Response(status, content=content, headers={"x-request-id": request_id})


def http_body(*output: str) -> bytes:
    return (
        '{"id":"resp_1","object":"response","status":"completed","model":"'
        + SNAPSHOT
        + '","output":['
        + ",".join(output)
        + '],"usage":{"input_tokens":100,"output_tokens":20,"total_tokens":120}}'
    ).encode("utf-8")


def http_call(call_id: str, name: str, arguments: str) -> str:
    return json.dumps(
        {
            "type": "function_call",
            "id": "fc_" + call_id,
            "call_id": call_id,
            "name": name,
            "arguments": arguments,
            "status": "completed",
        }
    )


def http_message(text: str) -> str:
    return json.dumps(
        {
            "type": "message",
            "id": "msg_h",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": text, "annotations": []}],
        },
        ensure_ascii=False,
    )


def test_cl80_cl81_cl82_cl84_real_adapter_records(cli, prepared):
    from emporio_agent.openai_provider import OpenAIResponsesProvider

    transport = HttpTransport(
        (200, http_body(http_call("call_1", "search_products", SEARCH_ARGS)), "req_a1"),
        (200, http_body(http_message("SENTINEL-MODEL Temos quatro itens.")), "req_a2"),
        (200, http_body(http_call("call_2", "get_order", '{"order_id":1001}')), "req_b1"),
        (200, http_body(http_message("Pedido cancelado.")), "req 1"),
        (
            401,
            b'{"error":{"message":"SENTINEL-PAYLOAD","type":"invalid_request_error"}}',
            "req_c1",
        ),
    )
    client = httpx2.Client(transport=httpx2.MockTransport(transport))
    closes = []

    class Provider(OpenAIResponsesProvider):
        def close(self):
            closes.append(1)
            super().close()

    def factory(config):
        factory.calls += 1
        return Provider(
            api_key=config.api_key,
            model=config.model,
            max_output_tokens=4096,
            reasoning_effort="low",
            http_client=client,
        )

    factory.calls = 0
    lines = [
        "SENTINEL-USER violões?\n",
        "/cliente 2\n",
        "pedido 1001?\n",
        "SENTINEL-USER outra\n",
    ]
    code, out, err = run(cli, prepared, lines, None, "--diagnostics", factory=factory)
    assert code == 0 and closes == [1]
    assert len(transport.requests) == 5
    turns = records(err, "turn")
    assert len(turns) == 3
    first, second, third = turns
    assert {k: first[k] for k in TURN_KEYS if k != "max_request_bytes"} == {
        "n": "1",
        "outcome": "answered",
        "failure": "-",
        "attempts": "2",
        "retries": "0",
        "tools": "search_products:ok",
        "references": "product:11;product:10;product:13;product:12",
        "configured_model": "gpt-5.4-mini",
        "reported_models": "gpt-5.4-mini-2026-03-17;gpt-5.4-mini-2026-03-17",
        "request_ids": "req_a1;req_a2",
        "reasoning_efforts_reported": "-;-",  # low requested, nothing reported (RM10)
        "input_tokens": "200",
        "output_tokens": "40",
        "usage_missing": "0",
        "usage_invalid": "0",
        "budget_failure": "-",
        "history_turns": "1",
        "pruned_turns": "0",
    }
    assert first["max_request_bytes"].isdigit()
    assert (second["tools"], second["references"]) == ("get_order:ok", "order")
    assert second["request_ids"] == "req_b1;unprintable"
    assert (third["outcome"], third["failure"]) == ("failed", "provider_authentication")
    assert records(err, "reset") == [{"trigger": "cliente", "result": "ok", "mode": "linked"}]
    assert answer_line(CONFIGURATION_TEXT) in out
    diag_lines = [line for line in err.splitlines() if line.startswith("diag:")]
    assert diag_lines == err.splitlines()
    for sentinel in ("SENTINEL-USER", "SENTINEL-MODEL", "SENTINEL-PAYLOAD", PRIVATE):
        assert sentinel not in err
    for record in [*turns, *records(err, "reset")]:
        for key, value in record.items():
            if key != "max_request_bytes":  # a byte count may contain the digits by chance
                assert "1001" not in value
    assert "customer" not in err and "SENTINEL-PAYLOAD" not in out


# --- RM10: reported reasoning effort through the real adapter (spec 08 Review 08-R7) -----------


def http_body_reasoning(reasoning: str, *output: str) -> bytes:
    """`http_body` plus a literal top-level `reasoning` object (raw JSON text)."""
    body = http_body(*output)
    assert body.endswith(b"}")
    return body[:-1] + b',"reasoning":' + reasoning.encode("utf-8") + b"}"


def _rm10_factory(client):
    from emporio_agent.openai_provider import OpenAIResponsesProvider

    def factory(config):
        factory.calls += 1
        return OpenAIResponsesProvider(
            api_key=config.api_key,
            model=config.model,
            max_output_tokens=4096,
            reasoning_effort="low",
            http_client=client,
        )

    factory.calls = 0
    return factory


def _rm10_transport():
    return HttpTransport(
        (503, b'{"error":{"message":"SENTINEL-PAYLOAD","type":"server_error"}}', "req_e1"),
        (
            200,
            http_body_reasoning(
                '{"effort":"medium","summary":"SENTINEL-MODEL"}',
                http_call("call_1", "search_products", SEARCH_ARGS),
            ),
            "req_r1",
        ),
        (200, http_body(http_message("Temos quatro itens.")), "req_r2"),
    )


def test_rm10_requested_and_reported_effort_through_the_cli(cli, prepared):
    transport = _rm10_transport()
    client = httpx2.Client(transport=httpx2.MockTransport(transport))
    factory = _rm10_factory(client)
    code, out, err = run(cli, prepared, ["violões?\n"], None, "--diagnostics", factory=factory)
    assert code == 0
    assert len(transport.requests) == 3
    for sent in transport.requests:
        assert json.loads(sent.content)["reasoning"] == {"effort": "low"}
    (start,) = records(err, "start")
    assert start["reasoning_effort"] == "low"  # requested
    (only,) = records(err, "turn")
    assert list(only) == TURN_KEYS
    assert (only["outcome"], only["attempts"], only["retries"]) == ("answered", "3", "1")
    assert only["request_ids"] == "req_e1;req_r1;req_r2"
    assert only["reasoning_efforts_reported"] == "-;medium;-"  # reported, absent kept
    assert answer_line("Temos quatro itens.") in out
    assert "SENTINEL" not in out + err


def test_rm10_no_record_without_the_flag(cli, prepared):
    transport = _rm10_transport()
    client = httpx2.Client(transport=httpx2.MockTransport(transport))
    code, out, err = run(cli, prepared, ["violões?\n"], None, factory=_rm10_factory(client))
    assert (code, err) == (0, "")
    assert len(transport.requests) == 3
    assert answer_line("Temos quatro itens.") in out
    for text in ("medium", "reasoning", "SENTINEL"):
        assert text not in out


# --- CL83: reset records -------------------------------------------------------------------------


def test_cl83_reset_records_for_every_result(cli, prepared, monkeypatch):
    provider = ScriptedProvider()
    real = cli.customer_exists
    calls = []

    def flaky(connection, customer):
        calls.append(1)
        if len(calls) == 3:  # the /cliente 1 attempt
            raise sqlite3.OperationalError("SENTINEL-PAYLOAD")
        return real(connection, customer)

    monkeypatch.setattr(cli, "customer_exists", flaky)
    lines = [
        "/reiniciar\n",
        "/cliente 2\n",
        "/cliente abc\n",
        "/cliente 3\n",
        "/cliente 1\n",
        "/clienteX 2\n",
        "/cliente\n",
    ]
    code, out, err = run(cli, prepared, lines, provider, "--diagnostics")
    assert code == 0
    assert err.splitlines()[1:] == [
        "diag: reset trigger=reiniciar result=ok mode=anonymous",
        "diag: reset trigger=cliente result=ok mode=linked",
        "diag: reset trigger=cliente result=argument_invalid mode=anonymous",
        "diag: reset trigger=cliente result=not_found mode=anonymous",
        "diag: reset trigger=cliente result=lookup_failed mode=anonymous",
        "diag: reset trigger=cliente result=argument_invalid mode=anonymous",
    ]
    assert out == (
        ANONYMOUS_BANNER
        + PROMPT
        + RESTARTED
        + PROMPT
        + LINKED_2
        + PROMPT
        + ARGUMENT_INVALID
        + PROMPT
        + NOT_FOUND
        + PROMPT
        + LOOKUP_FAILED
        + PROMPT
        + INVALID_COMMAND
        + PROMPT
        + ARGUMENT_INVALID
        + PROMPT
        + FAREWELL_EOF
    )
    assert provider.requests == []


# --- CL85: stream separation; no temporary rejection left -----------------------------------


def test_cl85_streams_are_separated(cli, prepared):
    provider = ScriptedProvider(final("Oi!"), ProviderError("authentication"))
    lines = ["oi\n", "\n", "b\n", "/cliente 2\n", "/xyz\n", "/sair\n"]
    code, out, err = run(cli, prepared, lines, provider, "--diagnostics")
    assert code == 0
    assert "diag:" not in out
    assert all(line.startswith("diag: ") for line in err.splitlines())
    assert "Assistente:" not in err and PROMPT not in err
    assert out.count("Assistente: ") == 3


def test_no_temporary_rejection_remains_in_cli(cli):
    source = Path(cli.__file__).read_text(encoding="utf-8")
    assert "command_unavailable" not in source
