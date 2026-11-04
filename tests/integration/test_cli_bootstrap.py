"""Slice 08-C: chat bootstrap up to the first prompt (docs/specs/08-cli.md G4, G6, G7, G9,
G9a, G11a; plan CL31-CL55). CL105 (temporary G13a row) was removed by slice 08-D.

Real prepare_database / prepare_policy_index build the synthetic database and index in
tmp_path; the CLI then runs the real connect_readonly, read_preparation_info,
load_policy_index, customer_exists, AgentResources, Agent and ConversationSession. The
provider is a scripted test double that records close() and fails on any create(), or the
real OpenAIResponsesProvider over httpx2.MockTransport (CL40). Spies only delegate and
count; failures are injected at boundaries. Every expected line is a literal of spec G9a.
"""

from __future__ import annotations

import hashlib
import importlib
import inspect
import io
import json
import logging
import os
import shutil
import socket
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest

from emporio_agent.data.policy_index import prepare_policy_index
from emporio_agent.data.preparation import prepare_database
from emporio_agent.provider import ModelRequest, UserMessage
from emporio_agent.services.orders import CustomerContext

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests" / "fixtures" / "data" / "valid"
PRIVATE = "pasta-privada-08"
FAKE_KEY = "test-key-not-a-secret"
MODEL = "gpt-5.4-mini"
VALID_ENV = {"OPENAI_API_KEY": FAKE_KEY, "OPENAI_MODEL": MODEL}
PREPARED_AT = datetime(2026, 11, 4, 12, 30, 45, 123456, tzinfo=UTC)
SDK_LOGGERS = ("openai", "httpx2", "httpcore2", "httpx", "httpcore")
CUSTOMER_FIELDS = (
    "Cliente Teste",
    "90000-000",
    "example.invalid",
    "São Paulo",
    "Recife",
)

BANNER = (
    "Empório da Música — assistente de atendimento (demonstração local, sem autenticação real).\n"
    "Modo: {mode}.\n"
    "Comandos: /sair encerra; /reiniciar inicia nova conversa anônima; "
    "/cliente <id> troca o cliente demonstrativo.\n"
    "Aviso: o texto digitado é enviado à API da OpenAI.\n"
)
ANONYMOUS_BANNER = BANNER.format(mode="sessão anônima")
PROMPT = "Você: "
FAREWELL = "\nAtendimento encerrado. Até logo.\n"
DATABASE_LINE = (
    "database_unavailable: {reason} - Banco preparado indisponível. "
    "Prepare os dados com emporio-agent prepare-data antes de iniciar o chat.\n"
)
INDEX_LINE = (
    "policy_index_unavailable: {code} - Índice de políticas indisponível. "
    "Prepare o índice com emporio-agent prepare-policies antes de iniciar o chat.\n"
)
CUSTOMER_LINE = "customer_not_found - Cliente demonstrativo não encontrado no banco preparado.\n"
INTERRUPTED = "interrompido\n"
READ_FAILED = "input_read_failed - Não foi possível ler a entrada do terminal.\n"
CLEANUP_FAILED = "cleanup_failed - Falha ao encerrar a conexão com o provedor.\n"
KEY_MISSING = (
    "rejected: key_missing - Erro de configuração: defina a variável "
    "OPENAI_API_KEY no ambiente deste terminal.\n"
)


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
    # Each test starts without the adapter loaded, so its import is observable.
    monkeypatch.delitem(sys.modules, "emporio_agent.openai_provider", raising=False)


@pytest.fixture
def cli():
    return importlib.import_module("emporio_agent.cli")


@pytest.fixture
def prepared(tmp_path, policy_kit):
    """Synthetic database and index prepared by the real library functions."""
    source = tmp_path / "src"
    shutil.copytree(FIXTURE, source)
    folder = tmp_path / PRIVATE
    folder.mkdir()
    database = folder / "emporio.sqlite3"
    prepare_database(source, database, clock=lambda: PREPARED_AT)
    pdf = policy_kit.write(tmp_path)
    index = folder / "policy-index.json"
    prepare_policy_index(pdf, index)
    return {
        "database": database,
        "index": index,
        "pdf_sha": hashlib.sha256(pdf.read_bytes()).hexdigest(),
    }


class ScriptedProvider:
    """ModelProvider double: no request is expected before slice 08-D."""

    def __init__(self, close_error: BaseException | None = None) -> None:
        self.create_calls = 0
        self.close_calls = 0
        self.close_error = close_error

    @property
    def model(self) -> str:
        return MODEL

    def create(self, request):
        self.create_calls += 1
        raise AssertionError("no provider request is allowed in slice 08-C")

    def close(self) -> None:
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


class Factory:
    def __init__(self, provider=None, error: BaseException | None = None, events=None) -> None:
        self.provider = provider if provider is not None else ScriptedProvider()
        self.error = error
        self.configs = []
        self.events = events

    def __call__(self, config):
        self.configs.append(config)
        if self.events is not None:
            self.events.append("factory")
        if self.error is not None:
            raise self.error
        return self.provider


class FailingStdin(io.StringIO):
    def __init__(self, error: BaseException) -> None:
        super().__init__()
        self.error = error

    def readline(self, *args):
        raise self.error


def _stream(text: str = "") -> io.TextIOWrapper:
    stream = io.TextIOWrapper(io.BytesIO(text.encode("utf-8")), encoding="utf-8", newline="\n")
    return stream


def _text(stream: io.TextIOWrapper) -> str:
    stream.flush()
    return stream.buffer.getvalue().decode("utf-8")


def chat_args(prepared, *extra: str) -> list[str]:
    return [
        "chat",
        "--database",
        str(prepared["database"]),
        "--index",
        str(prepared["index"]),
        *extra,
    ]


def run(cli, argv, *, stdin="", factory=None, env=None, **seams):
    stdin_stream = stdin if isinstance(stdin, io.IOBase) else _stream(stdin)
    stdout, stderr = _stream(), _stream()
    code = cli.main(
        argv,
        environ=dict(VALID_ENV if env is None else env),
        stdin=stdin_stream,
        stdout=stdout,
        stderr=stderr,
        provider_factory=factory,
        **seams,
    )
    assert type(code) is int
    out, err = _text(stdout), _text(stderr)
    assert "Traceback" not in out + err
    assert FAKE_KEY not in out + err
    return code, out, err


@pytest.fixture
def spies(monkeypatch, cli):
    """Delegating spies on the entry points as seen by the CLI; one shared event list."""
    events: list[str] = []
    names = (
        "connect_readonly",
        "read_preparation_info",
        "load_policy_index",
        "customer_exists",
        "prepare_database",
        "prepare_policy_index",
    )
    for name in names:
        original = getattr(cli, name)

        def spy(*args, _original=original, _name=name, **kwargs):
            events.append(_name)
            return _original(*args, **kwargs)

        monkeypatch.setattr(cli, name, spy)
    return events


def snapshot(prepared) -> dict:
    folder = prepared["database"].parent
    return {
        "listing": sorted(p.name for p in folder.iterdir()),
        "files": {
            name: (
                hashlib.sha256((folder / name).read_bytes()).hexdigest(),
                (folder / name).stat().st_mtime_ns,
            )
            for name in ("emporio.sqlite3", "policy-index.json")
        },
    }


# --- CL31, CL47, CL51: anonymous startup and EOF -----------------------------------------


def test_cl31_valid_startup_then_eof_exits_zero_and_closes_once(cli, prepared, spies):
    factory = Factory()
    code, out, err = run(cli, chat_args(prepared), factory=factory)
    assert (code, out, err) == (0, ANONYMOUS_BANNER + PROMPT + FAREWELL, "")
    assert len(factory.configs) == 1
    assert factory.provider.close_calls == 1
    assert factory.provider.create_calls == 0
    assert spies == ["connect_readonly", "read_preparation_info", "load_policy_index"]


def test_cl47_banner_notices_and_no_path(cli, prepared):
    _, out, _ = run(cli, chat_args(prepared), factory=Factory())
    assert "sem autenticação real" in out and "demonstração local" in out
    assert "enviado à API da OpenAI" in out
    for command in ("/sair", "/reiniciar", "/cliente <id>"):
        assert command in out
    assert PRIVATE not in out and str(prepared["database"].parent) not in out


def test_cl51_no_customer_is_anonymous(cli, prepared):
    _, out, _ = run(cli, chat_args(prepared), factory=Factory())
    assert out.startswith(ANONYMOUS_BANNER)


# --- CL32-CL37: resources unavailable (exit 3, no provider) ------------------------------


def _assert_resource_failure(cli, argv, line, spies=None):
    factory = Factory()
    code, out, err = run(cli, argv, factory=factory)
    assert (code, out, err) == (3, "", line)
    assert factory.configs == []
    return factory


def test_cl32_missing_database(cli, prepared, spies):
    missing = prepared["database"].parent / "ausente.sqlite3"
    argv = ["chat", "--database", str(missing), "--index", str(prepared["index"])]
    _assert_resource_failure(cli, argv, DATABASE_LINE.format(reason="missing"))
    assert not missing.exists()
    assert "load_policy_index" not in spies


def test_cl32_missing_database_with_default_factory_never_imports_the_adapter(cli, tmp_path):
    missing = tmp_path / "ausente.sqlite3"
    code, out, err = run(cli, ["chat", "--database", str(missing)], factory=None)
    assert (code, out, err) == (3, "", DATABASE_LINE.format(reason="missing"))
    assert "emporio_agent.openai_provider" not in sys.modules


def test_cl33_sqlite_without_metadata(cli, prepared, tmp_path):
    import sqlite3

    plain = tmp_path / "plain.sqlite3"
    connection = sqlite3.connect(plain)
    connection.execute("CREATE TABLE t (x INTEGER)")
    connection.commit()
    connection.close()
    argv = ["chat", "--database", str(plain), "--index", str(prepared["index"])]
    _assert_resource_failure(cli, argv, DATABASE_LINE.format(reason="schema_mismatch"))


def test_cl34_text_file_as_database(cli, prepared, tmp_path):
    text = tmp_path / "texto.sqlite3"
    text.write_text("isto não é um banco SQLite " * 20, encoding="utf-8")
    argv = ["chat", "--database", str(text), "--index", str(prepared["index"])]
    _assert_resource_failure(cli, argv, DATABASE_LINE.format(reason="open_failed"))


def test_cl35_missing_index(cli, prepared):
    missing = prepared["index"].parent / "ausente.json"
    argv = ["chat", "--database", str(prepared["database"]), "--index", str(missing)]
    _assert_resource_failure(cli, argv, INDEX_LINE.format(code="index_missing"))
    assert not missing.exists()


def test_cl36_invalid_json_index(cli, prepared, tmp_path):
    broken = tmp_path / "quebrado.json"
    broken.write_text("{not json", encoding="utf-8")
    argv = ["chat", "--database", str(prepared["database"]), "--index", str(broken)]
    _assert_resource_failure(cli, argv, INDEX_LINE.format(code="index_invalid"))


def test_cl36_unsupported_index_version(cli, prepared, tmp_path):
    data = json.loads(prepared["index"].read_text(encoding="utf-8"))
    data["format_version"] = 2
    newer = tmp_path / "novo.json"
    newer.write_text(json.dumps(data), encoding="utf-8")
    argv = ["chat", "--database", str(prepared["database"]), "--index", str(newer)]
    _assert_resource_failure(cli, argv, INDEX_LINE.format(code="index_version_unsupported"))


def test_cl37_both_missing_reports_only_the_database(cli, tmp_path, spies):
    argv = ["chat", "--database", str(tmp_path / "a.sqlite3"), "--index", str(tmp_path / "i.json")]
    _assert_resource_failure(cli, argv, DATABASE_LINE.format(reason="missing"))
    assert spies == ["connect_readonly"]


def test_cl32_sqlite_error_during_database_check_is_query_failed(cli, prepared, monkeypatch):
    import sqlite3

    def broken(connection):
        raise sqlite3.DatabaseError("SENTINEL-PAYLOAD")

    monkeypatch.setattr(cli, "read_preparation_info", broken)
    _assert_resource_failure(cli, chat_args(prepared), DATABASE_LINE.format(reason="query_failed"))


# --- CL38, CL39: startup is read-only and never prepares ---------------------------------


def test_cl38_startup_writes_nothing(cli, prepared):
    before = snapshot(prepared)
    code, _, _ = run(
        cli, chat_args(prepared, "--customer", "1", "--diagnostics"), factory=Factory()
    )
    assert code == 0
    assert snapshot(prepared) == before
    for suffix in ("-journal", "-wal", "-shm"):
        assert not prepared["database"].with_name("emporio.sqlite3" + suffix).exists()


@pytest.mark.parametrize(
    "extra", [(), ("--customer", "2"), ("--customer", "3"), ("--database", "ausente.sqlite3")]
)
def test_cl39_chat_never_prepares(cli, prepared, spies, extra, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run(cli, chat_args(prepared) + list(extra), factory=Factory())
    assert "prepare_database" not in spies and "prepare_policy_index" not in spies


# --- CL40, CL41: provider configuration (D-01) -------------------------------------------


class Transport:
    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        body = (
            '{"id":"resp_1","object":"response","status":"completed","model":"'
            + MODEL
            + '","output":[{"type":"message","id":"msg_1","role":"assistant",'
            '"status":"completed","content":[{"type":"output_text","text":"Olá!",'
            '"annotations":[]}]}],"usage":{"input_tokens":1,"output_tokens":1,'
            '"total_tokens":2}}'
        )
        return httpx2.Response(200, content=body.encode("utf-8"))


@pytest.fixture
def adapter_spy(monkeypatch):
    """Delegating subclass of the real adapter: records kwargs, injects a MockTransport."""
    adapter = importlib.import_module("emporio_agent.openai_provider")
    record = {"kwargs": [], "closes": 0, "transport": Transport()}

    class Spy(adapter.OpenAIResponsesProvider):
        def __init__(self, **kwargs):
            record["kwargs"].append(dict(kwargs))
            client = httpx2.Client(transport=httpx2.MockTransport(record["transport"]))
            super().__init__(**kwargs, http_client=client)

        def close(self):
            record["closes"] += 1
            super().close()

    monkeypatch.setattr(adapter, "OpenAIResponsesProvider", Spy)
    return record


def test_cl40_default_factory_passes_model_low_and_4096(cli, prepared, adapter_spy):
    env = {"OPENAI_API_KEY": FAKE_KEY, "OPENAI_MODEL": "modelo-configurado-1"}
    code, out, err = run(cli, chat_args(prepared), factory=None, env=env)
    assert (code, err) == (0, "")
    assert adapter_spy["kwargs"] == [
        {
            "api_key": FAKE_KEY,
            "model": "modelo-configurado-1",
            "max_output_tokens": 4096,
            "reasoning_effort": "low",
        }
    ]
    assert adapter_spy["closes"] == 1
    assert adapter_spy["transport"].requests == []


def test_cl40_request_body_from_the_default_factory(cli, adapter_spy, tmp_path):
    config = cli.ChatConfig(model="modelo-configurado-1", api_key=FAKE_KEY)
    provider = cli.default_provider_factory(config)
    try:
        provider.create(
            ModelRequest(
                instructions="x", items=(UserMessage("oi"),), tools=(), allow_tool_calls=False
            )
        )
    finally:
        provider.close()
    (request,) = adapter_spy["transport"].requests
    sent = json.loads(request.content)
    assert sent["model"] == "modelo-configurado-1"
    assert sent["max_output_tokens"] == 4096
    assert sent["reasoning"] == {"effort": "low"}
    assert sent["store"] is False and sent["parallel_tool_calls"] is False


def test_cl41_adapter_defaults_unchanged():
    adapter = importlib.import_module("emporio_agent.openai_provider")
    parameters = inspect.signature(adapter.OpenAIResponsesProvider).parameters
    assert parameters["reasoning_effort"].default is None
    assert parameters["max_output_tokens"].default == 8000
    assert parameters["timeout_seconds"].default == 30.0


def test_cl40_cli_constants(cli):
    assert (cli.CHAT_REASONING_EFFORT, cli.CHAT_MAX_OUTPUT_TOKENS) == ("low", 4096)


# --- Construction of AgentResources, Agent and ConversationSession -----------------------


def test_agent_resources_session_built_from_config_and_seams(cli, prepared, monkeypatch):
    built = {}
    real = {name: getattr(cli, name) for name in ("AgentResources", "Agent", "ConversationSession")}

    def recorder(name):
        def build(*args, **kwargs):
            built[name] = (args, kwargs)
            return real[name](*args, **kwargs)

        return build

    for name in real:
        monkeypatch.setattr(cli, name, recorder(name))

    def clock():
        return PREPARED_AT

    def sleep(seconds):
        raise AssertionError("no sleep expected")

    def monotonic():
        return 0.0

    factory = Factory()
    code, _, _ = run(
        cli,
        chat_args(prepared, "--customer", "2"),
        factory=factory,
        clock=clock,
        sleep=sleep,
        monotonic=monotonic,
    )
    assert code == 0
    assert built["AgentResources"] == (
        (),
        {
            "database_path": prepared["database"],
            "policy_index_path": prepared["index"],
            "clock": clock,
        },
    )
    args, kwargs = built["Agent"]
    assert args[0] is factory.provider and type(args[1]) is real["AgentResources"]
    assert kwargs == {"sleep": sleep, "monotonic": monotonic}
    assert built["ConversationSession"] == ((), {"customer": CustomerContext(2)})


# --- CL42-CL44: failures and cleanup -------------------------------------------------------


def test_cl42_factory_failure_is_internal_error_without_message(cli, prepared):
    factory = Factory(error=RuntimeError("SENTINEL-PAYLOAD"))
    code, out, err = run(cli, chat_args(prepared), factory=factory)
    assert (code, out, err) == (1, "", "internal_error: RuntimeError\n")
    assert factory.provider.close_calls == 0


def test_cl42_construction_failure_after_factory_closes_the_provider(cli, prepared):
    class NotAProvider:
        close_calls = 0

        def close(self):
            NotAProvider.close_calls += 1

    code, out, err = run(cli, chat_args(prepared), factory=Factory(provider=NotAProvider()))
    assert (code, out, err) == (1, "", "internal_error: TypeError\n")
    assert NotAProvider.close_calls == 1


ROWS = [
    ("eof", "", 0, ANONYMOUS_BANNER + PROMPT + FAREWELL, ""),
    ("ctrl_c", FailingStdin(KeyboardInterrupt()), 130, ANONYMOUS_BANNER + PROMPT, INTERRUPTED),
    (
        "oserror",
        FailingStdin(OSError("SENTINEL-PAYLOAD")),
        1,
        ANONYMOUS_BANNER + PROMPT,
        READ_FAILED,
    ),
]


@pytest.mark.parametrize(("label", "stdin", "code", "out", "err"), ROWS)
def test_cl43_first_prompt_paths_close_exactly_once(cli, prepared, label, stdin, code, out, err):
    factory = Factory()
    assert run(cli, chat_args(prepared), stdin=stdin, factory=factory) == (code, out, err)
    assert factory.provider.close_calls == 1
    assert factory.provider.create_calls == 0


@pytest.mark.parametrize(("label", "stdin", "code", "out", "err"), ROWS)
def test_cl44_cleanup_failure_wins_with_exit_one(cli, prepared, label, stdin, code, out, err):
    factory = Factory(provider=ScriptedProvider(close_error=OSError("SENTINEL-PAYLOAD")))
    result = run(cli, chat_args(prepared), stdin=stdin, factory=factory)
    assert result == (1, out, err + CLEANUP_FAILED)
    assert factory.provider.close_calls == 1


def test_cl44_unexpected_exception_after_construction_closes_once(cli, prepared):
    class BrokenStdout(io.StringIO):
        def write(self, text):
            raise RuntimeError("SENTINEL-PAYLOAD")

    factory = Factory()
    stderr = _stream()
    code = cli.main(
        chat_args(prepared),
        environ=dict(VALID_ENV),
        stdin=_stream(),
        stdout=BrokenStdout(),
        stderr=stderr,
        provider_factory=factory,
    )
    assert (code, _text(stderr)) == (1, "internal_error: RuntimeError\n")
    assert factory.provider.close_calls == 1


# --- CL45: SDK loggers ---------------------------------------------------------------------


@pytest.mark.parametrize("factory_error", [None, RuntimeError("x"), KeyboardInterrupt()])
def test_cl45_sdk_loggers_silenced_during_chat_and_restored(cli, prepared, factory_error):
    previous = {name: logging.getLogger(name).level for name in SDK_LOGGERS}
    logging.getLogger("openai").setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.DEBUG)
    try:
        before = {name: logging.getLogger(name).level for name in SDK_LOGGERS}
        seen = {}

        class Recording(Factory):
            def __call__(self, config):
                seen.update({n: logging.getLogger(n).level for n in SDK_LOGGERS})
                return super().__call__(config)

        run(cli, chat_args(prepared), factory=Recording(error=factory_error))
        assert seen == {name: logging.CRITICAL + 1 for name in SDK_LOGGERS}
        assert {name: logging.getLogger(name).level for name in SDK_LOGGERS} == before
    finally:
        for name, level in previous.items():
            logging.getLogger(name).setLevel(level)


# --- CL46, CL48: factory only after every check; interruption ----------------------------


def test_cl46_factory_runs_after_every_check(cli, prepared, spies):
    factory = Factory(events=spies)
    run(cli, chat_args(prepared, "--customer", "1"), factory=factory)
    assert spies == [
        "connect_readonly",
        "read_preparation_info",
        "load_policy_index",
        "connect_readonly",
        "customer_exists",
        "factory",
    ]


def test_cl48_interrupt_during_index_load(cli, prepared, monkeypatch):
    def interrupted(path):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "load_policy_index", interrupted)
    factory = Factory()
    assert run(cli, chat_args(prepared), factory=factory) == (130, "", INTERRUPTED)
    assert factory.configs == []


def test_cl48_interrupt_in_factory_closes_nothing(cli, prepared):
    factory = Factory(error=KeyboardInterrupt())
    assert run(cli, chat_args(prepared), factory=factory) == (130, "", INTERRUPTED)
    assert factory.provider.close_calls == 0


# --- CL49, CL50: defaults and linked customer -----------------------------------------------


def test_cl49_relative_defaults(cli, prepared, tmp_path, monkeypatch):
    work = tmp_path / "work"
    (work / "data" / "local").mkdir(parents=True)
    shutil.copy(prepared["database"], work / "data" / "local" / "emporio.sqlite3")
    shutil.copy(prepared["index"], work / "data" / "local" / "policy-index.json")
    monkeypatch.chdir(work)
    factory = Factory()
    code, out, err = run(cli, ["chat"], factory=factory)
    assert (code, out, err) == (0, ANONYMOUS_BANNER + PROMPT + FAREWELL, "")
    assert factory.configs[0].database == Path("data/local/emporio.sqlite3")


def test_cl50_linked_customer_banner(cli, prepared):
    code, out, err = run(cli, chat_args(prepared, "--customer", "1"), factory=Factory())
    assert (code, err) == (0, "")
    assert out == BANNER.format(mode="cliente demonstrativo 1") + PROMPT + FAREWELL
    for field in CUSTOMER_FIELDS:
        assert field not in out


# --- CL52: unknown customer ---------------------------------------------------------------


def test_cl52_unknown_customer_exit_three_without_provider(cli, prepared, spies):
    factory = Factory()
    code, out, err = run(cli, chat_args(prepared, "--customer", "3"), factory=factory)
    assert (code, out, err) == (3, "", CUSTOMER_LINE)
    assert factory.configs == []
    assert "customer_exists" in spies
    assert "emporio_agent.openai_provider" not in sys.modules


def test_cl52_sqlite_error_during_customer_check(cli, prepared, monkeypatch):
    import sqlite3

    def broken(connection, customer):
        raise sqlite3.OperationalError("SENTINEL-PAYLOAD")

    monkeypatch.setattr(cli, "customer_exists", broken)
    argv = chat_args(prepared, "--customer", "1")
    _assert_resource_failure(cli, argv, DATABASE_LINE.format(reason="query_failed"))


# --- CL54: diag start ------------------------------------------------------------------------


def test_cl54_diagnostics_start_record(cli, prepared):
    code, out, err = run(
        cli, chat_args(prepared, "--diagnostics", "--customer", "2"), factory=Factory()
    )
    assert code == 0
    assert out == BANNER.format(mode="cliente demonstrativo 2") + PROMPT + FAREWELL
    assert err == (
        "diag: start mode=linked database=emporio.sqlite3 schema_version=1 "
        "prepared_at=2026-11-04T12:30:45Z index=policy-index.json index_format_version=1 "
        f"policy_source_sha256={prepared['pdf_sha']} configured_model=gpt-5.4-mini "
        "reasoning_effort=low max_output_tokens=4096 max_messages=30\n"
    )
    assert "customer" not in err and PRIVATE not in err


def test_cl54_start_record_written_with_one_write_before_the_prompt(cli, prepared):
    class Recorder(io.StringIO):
        def __init__(self):
            super().__init__()
            self.writes = []

        def write(self, text):
            self.writes.append(text)
            return super().write(text)

    stderr = Recorder()
    code = cli.main(
        chat_args(prepared, "--diagnostics"),
        environ=dict(VALID_ENV),
        stdin=io.StringIO(""),
        stdout=io.StringIO(),
        stderr=stderr,
        provider_factory=Factory(),
    )
    assert code == 0
    (write,) = [w for w in stderr.writes if w]
    assert write.startswith("diag: start mode=anonymous ") and write.count("\n") == 1


def test_cl54_hostile_database_base_name(cli, prepared, tmp_path):
    folder = tmp_path / PRIVATE / "sub"
    folder.mkdir()
    hostile = folder / "meu banco.sqlite3"
    shutil.copy(prepared["database"], hostile)
    argv = ["chat", "--database", str(hostile), "--index", str(prepared["index"]), "--diagnostics"]
    code, _, err = run(cli, argv, factory=Factory())
    assert code == 0
    assert " database=unprintable " in err
    assert PRIVATE not in err and "meu banco" not in err


def test_cl54_without_flag_no_diagnostics(cli, prepared):
    assert run(cli, chat_args(prepared), factory=Factory())[2] == ""


# --- CL55: exit precedence ----------------------------------------------------------------


def test_cl55_configuration_error_wins_over_missing_resources(cli, tmp_path, spies):
    argv = ["chat", "--database", str(tmp_path / "x.sqlite3"), "--customer", "3"]
    code, out, err = run(cli, argv, factory=Factory(), env={"OPENAI_MODEL": MODEL})
    assert (code, out, err) == (2, "", KEY_MISSING)
    assert spies == []


def test_cl55_missing_database_wins_over_unknown_customer(cli, prepared, tmp_path):
    argv = [
        "chat",
        "--database",
        str(tmp_path / "x.sqlite3"),
        "--index",
        str(prepared["index"]),
        "--customer",
        "3",
    ]
    _assert_resource_failure(cli, argv, DATABASE_LINE.format(reason="missing"))
