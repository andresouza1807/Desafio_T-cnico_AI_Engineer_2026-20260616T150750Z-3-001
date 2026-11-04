"""Slice 08-E: installed entry point and CLI integration in separate processes (CL86-CL95).

Contract: docs/specs/08-cli.md G13, G14; plan docs/plans/08-cli.md, slice 08-E.

Two kinds of child process, kept apart on purpose:

- Installed command (CL86-CL91): the `emporio-agent` launcher of the project `.venv`, called by
  absolute path. Network is blocked by a test-only `sitecustomize` placed on `PYTHONPATH`; it
  also writes a JSON report at exit (socket attempts, SDK import, package origin). These runs
  never send a message to the model, so no request is possible.
- Wrapper (CL92, CL93): a test script that calls `cli.main` with a provider factory building
  the real `OpenAIResponsesProvider` over `httpx2.MockTransport`. It proves the full turn in a
  separate process with real streams; it is not a run of the installed launcher.

Every child gets an environment without `OPENAI_*` and without `PYTHON*` variables; only the
fake key, the model and the guard settings are added. No real call, no real credential.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
import tomllib
from importlib import metadata
from pathlib import Path

import pytest

from emporio_agent.data.policy_index import prepare_policy_index
from emporio_agent.data.preparation import prepare_database

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests" / "fixtures" / "data" / "valid"
SCRIPTS = REPO / ".venv" / ("Scripts" if os.name == "nt" else "bin")
EXE = SCRIPTS / ("emporio-agent.exe" if os.name == "nt" else "emporio-agent")
PYTHON = SCRIPTS / ("python.exe" if os.name == "nt" else "python")
PACKAGE_INIT = REPO / "src" / "emporio_agent" / "__init__.py"
PRIVATE = "pasta-privada-08"
FAKE_KEY = "test-key-not-a-secret"
SENTINEL_KEY = "sk-SENTINEL-KEY"
MODEL = "gpt-5.4-mini"
SNAPSHOT = "gpt-5.4-mini-2026-03-17"
TIMEOUT = 120

BANNER = (
    "Empório da Música — assistente de atendimento (demonstração local, sem autenticação real).\n"
    "Modo: sessão anônima.\n"
    "Comandos: /sair encerra; /reiniciar inicia nova conversa anônima; "
    "/cliente <id> troca o cliente demonstrativo.\n"
    "Aviso: o texto digitado é enviado à API da OpenAI.\n"
)
PROMPT = "Você: "
FAREWELL_EOF = "\nAtendimento encerrado. Até logo.\n"
FAREWELL_SAIR = "Atendimento encerrado. Até logo.\n"
KEY_MISSING = (
    "rejected: key_missing - Erro de configuração: defina a variável OPENAI_API_KEY no "
    "ambiente deste terminal.\n"
)
ARGUMENTS_INVALID = (
    "rejected: arguments_invalid - Argumentos inválidos. Use -h para ver os comandos e as opções.\n"
)
DATABASE_MISSING = (
    "database_unavailable: missing - Banco preparado indisponível. Prepare os dados com "
    "emporio-agent prepare-data antes de iniciar o chat.\n"
)
DATA_SUMMARY = (
    "dados preparados: arquivo=emporio.sqlite3 schema=1 tabelas=6 categories=2 customers=2 "
    "products=4 promotions=3 orders=2 order_items=3 notas_qualidade=2\n"
)
INDEX_SUMMARY = (
    "índice preparado: arquivo=policy-index.json paginas=5 secoes=15 recuperaveis=13 sha256={sha}\n"
)
QUESTION = "Quais violões vocês têm?"
ANSWER = "Temos o Violão Teste, com tampo maciço — ç e 🎸."
SEARCH_ARGS = (
    '{"query":"Violão","category":null,"max_price_brl":null,"available_only":false,"offset":0}'
)

# Test-only network guard for the installed launcher; loaded through PYTHONPATH.
_GUARD = textwrap.dedent(
    """
    import atexit, json, os, socket, sys

    _events = []

    def _blocked(*args, **kwargs):
        _events.append("network")
        raise OSError("network blocked by the test guard")

    for _name in ("create_connection", "getaddrinfo", "gethostbyname", "gethostbyname_ex"):
        setattr(socket, _name, _blocked)
    socket.socket.connect = _blocked
    socket.socket.connect_ex = _blocked

    def _report():
        package = sys.modules.get("emporio_agent")
        data = {
            "guard": 1,
            "events": _events,
            "openai": "openai" in sys.modules,
            "adapter": "emporio_agent.openai_provider" in sys.modules,
            "package": getattr(package, "__file__", None),
            "prefix": sys.prefix,
        }
        with open(os.environ["EMPORIO_TEST_GUARD_REPORT"], "w", encoding="utf-8") as handle:
            json.dump(data, handle)

    atexit.register(_report)
    """
)

# Wrapper for CL92/CL93: real cli.main, real Agent, tools and SDK; HTTP answered by MockTransport.
_WRAPPER = textwrap.dedent(
    """
    import json, socket, sys

    report_path, database, index = sys.argv[1:4]
    answers = json.loads(sys.argv[4])
    events = []
    requests = []

    def blocked(*args, **kwargs):
        events.append("network")
        raise OSError("network blocked")

    for name in ("create_connection", "getaddrinfo", "gethostbyname", "gethostbyname_ex"):
        setattr(socket, name, blocked)
    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked

    import httpx2
    from emporio_agent import cli

    def handle(request):
        requests.append({"url": str(request.url), "body": request.content.decode("utf-8")})
        if not answers:
            raise AssertionError("unscripted HTTP request")
        status, body, request_id = answers.pop(0)
        return httpx2.Response(
            status, content=body.encode("utf-8"), headers={"x-request-id": request_id}
        )

    def factory(config):
        from emporio_agent.openai_provider import OpenAIResponsesProvider

        return OpenAIResponsesProvider(
            api_key=config.api_key,
            model=config.model,
            max_output_tokens=cli.CHAT_MAX_OUTPUT_TOKENS,
            reasoning_effort=cli.CHAT_REASONING_EFFORT,
            http_client=httpx2.Client(transport=httpx2.MockTransport(handle)),
        )

    code = None
    try:
        code = cli.main(
            ["chat", "--database", database, "--index", index], provider_factory=factory
        )
    finally:
        with open(report_path, "w", encoding="utf-8") as out:
            json.dump(
                {
                    "code": code,
                    "events": events,
                    "requests": requests,
                    "isolated": sys.flags.isolated,
                    "package": cli.__file__,
                },
                out,
            )
    raise SystemExit(code)
    """
)


# --- Helpers ----------------------------------------------------------------------------


def child_env(**extra: str) -> dict[str, str]:
    """Inherited environment without OPENAI_* and PYTHON*, plus the given values only."""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(("OPENAI", "PYTHON"))
    }
    env.update(extra)
    return env


def chat_env(**extra: str) -> dict[str, str]:
    return child_env(OPENAI_API_KEY=FAKE_KEY, OPENAI_MODEL=MODEL, **extra)


def text(raw: bytes) -> str:
    """Strict UTF-8 decode of a pipe; on Windows every newline must arrive as CRLF."""
    decoded = raw.decode("utf-8")
    if os.name == "nt":
        assert decoded.count("\r\n") == decoded.count("\n")
        decoded = decoded.replace("\r\n", "\n")
    assert "\r" not in decoded
    return decoded


class Installed:
    """Runs the installed launcher with the network guard; returns (code, out, err, report)."""

    def __init__(self, base: Path) -> None:
        self.guard_dir = base / "guard"
        self.guard_dir.mkdir()
        (self.guard_dir / "sitecustomize.py").write_text(_GUARD, encoding="utf-8")
        self.base = base
        self.runs = 0

    def __call__(self, args, env, stdin: bytes = b"", cwd: Path | None = None):
        assert EXE.is_file(), f"installed script missing: {EXE.name}"
        self.runs += 1
        report = self.base / f"report-{self.runs}.json"
        env = {**env, "PYTHONPATH": str(self.guard_dir), "EMPORIO_TEST_GUARD_REPORT": str(report)}
        completed = subprocess.run(
            [str(EXE), *map(str, args)],
            input=stdin,
            capture_output=True,
            env=env,
            cwd=cwd or self.base,
            timeout=TIMEOUT,
            check=False,
        )
        guard = json.loads(report.read_text(encoding="utf-8"))
        out, err = text(completed.stdout), text(completed.stderr)
        for stream in (out, err):
            assert "Traceback" not in stream
            assert FAKE_KEY not in stream and SENTINEL_KEY not in stream
            assert str(self.base) not in stream and PRIVATE not in stream
        assert guard["guard"] == 1 and guard["events"] == []
        assert Path(guard["package"]) == PACKAGE_INIT
        return completed.returncode, out, err, guard


@pytest.fixture
def installed(tmp_path):
    return Installed(tmp_path)


@pytest.fixture
def resources(tmp_path, policy_kit):
    """Synthetic database and index prepared in-process (the preparation is not under test)."""
    source = tmp_path / "src"
    shutil.copytree(FIXTURE, source)
    folder = tmp_path / PRIVATE
    folder.mkdir()
    database = folder / "emporio.sqlite3"
    index = folder / "policy-index.json"
    prepare_database(source, database)
    prepare_policy_index(policy_kit.write(tmp_path), index)
    return database, index


def sha(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- CL86-CL91: the installed command -----------------------------------------------------


def test_pyproject_declares_only_the_entry_point():
    project = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["scripts"] == {"emporio-agent": "emporio_agent.cli:main"}


def test_cl86_installed_metadata_points_to_cli_main():
    found = metadata.entry_points(group="console_scripts", name="emporio-agent")
    assert [entry.value for entry in found] == ["emporio_agent.cli:main"]


def test_cl86_installed_script_shows_help(installed):
    code, out, err, guard = installed(["--help"], child_env())
    assert (code, err) == (0, "")
    assert out.startswith("usage: emporio-agent ")
    for name in ("prepare-data", "prepare-policies", "chat"):
        assert name in out
    assert guard["openai"] is False and guard["adapter"] is False
    assert Path(guard["prefix"]).resolve() == (REPO / ".venv").resolve()


def test_cl87_chat_without_configuration_is_rejected(installed):
    code, out, err, guard = installed(["chat"], child_env())
    assert (code, out, err) == (2, "", KEY_MISSING)
    assert guard["openai"] is False


def test_cl88_key_argument_is_rejected_without_echo(installed):
    code, out, err, guard = installed(["chat", "--api-key", SENTINEL_KEY], chat_env())
    assert (code, out, err) == (2, "", ARGUMENTS_INVALID)
    assert guard["openai"] is False


def test_cl89_installed_command_prepares_and_serves_the_resources(installed, tmp_path, policy_kit):
    source = tmp_path / "src"
    shutil.copytree(FIXTURE, source)
    folder = tmp_path / PRIVATE
    folder.mkdir()
    database, index = folder / "emporio.sqlite3", folder / "policy-index.json"
    pdf = policy_kit.write(tmp_path)

    data = installed(["prepare-data", "--source-dir", source, "--database", database], child_env())
    assert data[:3] == (0, DATA_SUMMARY, "")
    policies = installed(["prepare-policies", "--source-pdf", pdf, "--index", index], child_env())
    assert policies[:3] == (0, INDEX_SUMMARY.format(sha=sha(index)), "")
    assert data[3]["openai"] is False and policies[3]["openai"] is False

    code, out, err, _ = installed(
        ["chat", "--database", database, "--index", index], chat_env(), stdin=b"/sair\n"
    )
    assert (code, out, err) == (0, BANNER + PROMPT + FAREWELL_SAIR, "")


@pytest.mark.parametrize(
    ("stdin", "expected"),
    [(b"/sair\n", BANNER + PROMPT + FAREWELL_SAIR), (b"", BANNER + PROMPT + FAREWELL_EOF)],
    ids=["sair", "eof"],
)
def test_cl90_chat_ends_without_any_request(installed, resources, stdin, expected):
    database, index = resources
    code, out, err, guard = installed(
        ["chat", "--database", database, "--index", index], chat_env(), stdin=stdin
    )
    assert (code, out, err) == (0, expected, "")
    # The real adapter was built (SDK imported) and no socket was attempted.
    assert guard["adapter"] is True and guard["events"] == []


def test_cl91_missing_database_exits_3_without_creating_it(installed, resources):
    _, index = resources
    missing = index.parent / "ausente.sqlite3"
    code, out, err, guard = installed(["chat", "--database", missing, "--index", index], chat_env())
    assert (code, out, err) == (3, "", DATABASE_MISSING)
    assert not missing.exists()
    assert guard["openai"] is False and guard["adapter"] is False


# --- CL92, CL93: full turn through the wrapper (not the installed launcher) ---------------


def http_body(*output: dict) -> str:
    return json.dumps(
        {
            "id": "resp_1",
            "object": "response",
            "status": "completed",
            "model": SNAPSHOT,
            "output": list(output),
            "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
        },
        ensure_ascii=False,
    )


ANSWERS = [
    [
        200,
        http_body(
            {
                "type": "function_call",
                "id": "fc_call_1",
                "call_id": "call_1",
                "name": "search_products",
                "arguments": SEARCH_ARGS,
                "status": "completed",
            }
        ),
        "req_e1",
    ],
    [
        200,
        http_body(
            {
                "type": "message",
                "id": "msg_e2",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": ANSWER, "annotations": []}],
            }
        ),
        "req_e2",
    ],
]


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def run_wrapper(tmp_path, resources, env, isolated: bool, stdin: bytes):
    database, index = resources
    wrapper = tmp_path / "wrapper.py"
    wrapper.write_text(_WRAPPER, encoding="utf-8")
    report = tmp_path / "wrapper-report.json"
    flags = ["-I"] if isolated else []
    completed = subprocess.run(
        [
            str(PYTHON),
            *flags,
            str(wrapper),
            str(report),
            str(database),
            str(index),
            json.dumps(ANSWERS),
        ],
        input=stdin,
        capture_output=True,
        env=env,
        cwd=tmp_path,
        timeout=TIMEOUT,
        check=False,
    )
    return completed, json.loads(report.read_text(encoding="utf-8"))


def assert_full_turn(completed, report, question: str) -> None:
    out, err = text(completed.stdout), text(completed.stderr)
    assert (completed.returncode, report["code"]) == (0, 0)
    assert err == ""
    assert out == BANNER + PROMPT + "Assistente: " + ANSWER + "\n" + PROMPT + FAREWELL_SAIR
    assert report["events"] == []
    assert Path(report["package"]).parent == PACKAGE_INIT.parent
    first, second = (json.loads(request["body"]) for request in report["requests"])
    assert [request["url"] for request in report["requests"]] == [
        "https://api.openai.com/v1/responses"
    ] * 2
    assert question in list(strings(first["input"]))
    outputs = [item for item in second["input"] if item.get("type") == "function_call_output"]
    assert [item["call_id"] for item in outputs] == ["call_1"]
    payload = json.loads(outputs[0]["output"])
    assert "Violão Teste" in list(strings(payload))
    for stream in (out, err):
        assert FAKE_KEY not in stream and PRIVATE not in stream and "Traceback" not in stream


def test_cl92_isolated_wrapper_runs_one_full_turn_over_mock_transport(tmp_path, resources):
    stdin = (QUESTION + "\n/sair\n").encode("utf-8")
    completed, report = run_wrapper(tmp_path, resources, chat_env(), True, stdin)
    assert report["isolated"] == 1
    assert len(report["requests"]) == 2
    assert_full_turn(completed, report, QUESTION)


def test_cl93_utf8_pipes_without_python_encoding_variables(tmp_path, resources):
    env = chat_env(PYTHONUTF8="0")
    assert "PYTHONIOENCODING" not in env
    stdin = "ção\n/sair\n".encode()
    completed, report = run_wrapper(tmp_path, resources, env, False, stdin)
    assert report["isolated"] == 0
    assert "ç".encode() in completed.stdout and "🎸".encode() in completed.stdout
    assert_full_turn(completed, report, "ção")


# --- CL94, CL95: import safety and module form ---------------------------------------------

_IMPORT_PROBE = textwrap.dedent(
    """
    import json, socket, sys
    events = []
    def blocked(*args, **kwargs):
        events.append("network")
        raise OSError("network blocked")
    for name in ("create_connection", "getaddrinfo", "gethostbyname", "gethostbyname_ex"):
        setattr(socket, name, blocked)
    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked
    import emporio_agent.cli
    print(json.dumps({
        "openai": "openai" in sys.modules,
        "adapter": "emporio_agent.openai_provider" in sys.modules,
        "events": events,
        "package": emporio_agent.cli.__file__,
        "isolated": sys.flags.isolated,
    }))
    """
)


def test_cl94_isolated_import_loads_no_sdk_and_opens_no_socket(tmp_path):
    completed = subprocess.run(
        [str(PYTHON), "-I", "-c", _IMPORT_PROBE],
        capture_output=True,
        env=chat_env(),
        cwd=tmp_path,
        timeout=TIMEOUT,
        check=False,
    )
    assert (completed.returncode, completed.stderr) == (0, b"")
    report = json.loads(completed.stdout)
    assert report == {
        "openai": False,
        "adapter": False,
        "events": [],
        "package": str(PACKAGE_INIT.parent / "cli.py"),
        "isolated": 1,
    }


def test_cl95_module_form_shows_the_same_help(installed, tmp_path):
    completed = subprocess.run(
        [str(PYTHON), "-I", "-m", "emporio_agent.cli", "--help"],
        capture_output=True,
        env=child_env(),
        cwd=tmp_path,
        timeout=TIMEOUT,
        check=False,
    )
    assert (completed.returncode, completed.stderr) == (0, b"")
    out = text(completed.stdout)
    assert out.startswith("usage: emporio-agent ")
    for name in ("prepare-data", "prepare-policies", "chat"):
        assert name in out
    if EXE.is_file():  # same text as the launcher once installed (CL86 covers its absence)
        assert installed(["--help"], child_env())[1] == out


def test_guard_blocks_network_in_children(installed):
    """Control: the PYTHONPATH guard is loaded by the venv interpreter and blocks a connection."""
    report = installed.base / "probe.json"
    env = {
        **child_env(),
        "PYTHONPATH": str(installed.guard_dir),
        "EMPORIO_TEST_GUARD_REPORT": str(report),
    }
    probe = (
        "import socket\n"
        "try:\n"
        "    socket.create_connection(('api.openai.com', 443), timeout=1)\n"
        "except OSError as error:\n"
        "    print(error)\n"
    )
    completed = subprocess.run(
        [str(PYTHON), "-c", probe],
        capture_output=True,
        env=env,
        cwd=installed.base,
        timeout=TIMEOUT,
        check=False,
    )
    assert (completed.returncode, text(completed.stdout)) == (
        0,
        "network blocked by the test guard\n",
    )
    assert json.loads(report.read_text(encoding="utf-8"))["events"] == ["network"]


def test_interpreter_under_test_is_the_project_venv():
    assert Path(sys.prefix).resolve() == (REPO / ".venv").resolve()
