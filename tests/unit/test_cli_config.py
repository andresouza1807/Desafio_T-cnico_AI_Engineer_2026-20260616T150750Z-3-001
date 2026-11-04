"""Slice 08-A: CLI arguments and configuration (docs/specs/08-cli.md G2, G3, G10a, G13a).

Offline only: no key, no network, no file access by the code under test. The fake key is a
literal. Expected lines are literals copied from spec G10a, never read from production code.
"""

from __future__ import annotations

import dataclasses
import importlib
import importlib.abc
import io
import json
import os
import socket
import subprocess
import sys
import textwrap
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest

import emporio_agent
from emporio_agent.services.orders import CustomerContext

REPO = Path(__file__).resolve().parents[2]
FAKE_KEY = "test-key-not-a-secret"
KEY_SENTINEL = "sk-SENTINEL-KEY"
MODEL = "gpt-5.4-mini"
VALID_ENV = {"OPENAI_API_KEY": FAKE_KEY, "OPENAI_MODEL": MODEL}

# Spec G10a, exact lines.
LINES = {
    "arguments_invalid": (
        "rejected: arguments_invalid - Argumentos inválidos. "
        "Use -h para ver os comandos e as opções.\n"
    ),
    "customer_invalid": (
        "rejected: customer_invalid - Cliente demonstrativo inválido. "
        "Use um número inteiro positivo, sem sinal, espaços ou zeros à esquerda.\n"
    ),
    "sdk_logging_enabled": (
        "rejected: sdk_logging_enabled - Erro de configuração: remova a variável "
        "OPENAI_LOG do ambiente antes de iniciar o chat.\n"
    ),
    "key_missing": (
        "rejected: key_missing - Erro de configuração: defina a variável "
        "OPENAI_API_KEY no ambiente deste terminal.\n"
    ),
    "key_invalid": (
        "rejected: key_invalid - Erro de configuração: o valor de OPENAI_API_KEY "
        "tem espaços ou quebras de linha nas extremidades.\n"
    ),
    "model_missing": (
        "rejected: model_missing - Erro de configuração: defina a variável "
        "OPENAI_MODEL no ambiente deste terminal.\n"
    ),
    "model_invalid": (
        "rejected: model_invalid - Erro de configuração: o valor de OPENAI_MODEL "
        "tem formato inválido.\n"
    ),
    "command_unavailable": (
        "rejected: command_unavailable - Comando ainda não disponível nesta etapa "
        "de desenvolvimento.\n"
    ),
}

_BLOCKED_PREFIXES = ("openai", "emporio_agent.openai_provider")
_AUDITED = {"open", "sqlite3.connect", "os.listdir", "os.scandir", "os.mkdir"}
_AUDIT: list[tuple[str, str]] | None = None


def _audit_hook(event: str, args: tuple) -> None:
    if _AUDIT is not None and event in _AUDITED:
        _AUDIT.append((event, str(args[0]) if args else ""))


sys.addaudithook(_audit_hook)


@pytest.fixture
def audit() -> Iterator[list[tuple[str, str]]]:
    """Record file-system audit events while the test body runs."""
    global _AUDIT
    _AUDIT = []
    try:
        yield _AUDIT
    finally:
        _AUDIT = None


class _BlockImports(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if any(name == p or name.startswith(p + ".") for p in _BLOCKED_PREFIXES):
            raise ImportError(f"blocked import: {name}")
        return None


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
    # Any import of the SDK or of the adapter fails while the test runs.
    for name in [
        n for n in sys.modules if any(n == p or n.startswith(p + ".") for p in _BLOCKED_PREFIXES)
    ]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setattr(sys, "meta_path", [_BlockImports(), *sys.meta_path])


@pytest.fixture
def cli():
    return importlib.import_module("emporio_agent.cli")


class SpyEnviron(Mapping[str, str]):
    """Plain mapping that records every key read; iteration is recorded as '*'."""

    def __init__(self, values: Mapping[str, str] | None = None) -> None:
        self._values = dict(values or {})
        self.reads: list[str] = []

    def __getitem__(self, key: str) -> str:
        self.reads.append(key)
        return self._values[key]

    def __contains__(self, key: object) -> bool:
        self.reads.append(str(key))
        return key in self._values

    def get(self, key, default=None):
        self.reads.append(str(key))
        return self._values.get(key, default)

    def __iter__(self):
        self.reads.append("*")
        return iter(self._values)

    def __len__(self) -> int:
        self.reads.append("*")
        return len(self._values)


def _stream(encoding: str = "utf-8") -> io.TextIOWrapper:
    return io.TextIOWrapper(io.BytesIO(), encoding=encoding, newline="\n")


def _text(stream: io.TextIOWrapper) -> str:
    stream.flush()
    return stream.buffer.getvalue().decode("utf-8")


def run(cli, argv, env=None):
    environ = env if isinstance(env, SpyEnviron) else SpyEnviron(env)
    stdin, stdout, stderr = _stream(), _stream(), _stream()
    code = cli.main(argv, environ=environ, stdin=stdin, stdout=stdout, stderr=stderr)
    return code, _text(stdout), _text(stderr), environ


def parse(cli, argv):
    return cli.build_parser().parse_args(argv)


# --- CL01: safe import ------------------------------------------------------------------

_CHILD = textwrap.dedent(
    """
    import io, json, os, runpy, socket, sys

    src, mode = sys.argv[1], sys.argv[2]
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
    code = None
    err = ""
    if mode == "import":
        import emporio_agent.cli
    else:
        saved = sys.stdout
        sys.stdout = io.StringIO()
        sys.argv = ["cli", "--help"]
        try:
            runpy.run_module("emporio_agent.cli", run_name="__main__")
        except SystemExit as stop:
            code = stop.code
        finally:
            sys.stdout = saved
    print("REPORT " + json.dumps({
        "openai": "openai" in sys.modules,
        "adapter": "emporio_agent.openai_provider" in sys.modules,
        "events": events,
        "code": code,
        "err": err,
        "isolated": sys.flags.isolated,
        "data_local": os.path.exists(os.path.join("data", "local")),
    }))
    """
)


def _venv_python() -> Path:
    relative = Path("Scripts", "python.exe") if os.name == "nt" else Path("bin", "python")
    return REPO / ".venv" / relative


def run_child(tmp_path: Path, mode: str) -> dict:
    child = tmp_path / "child.py"
    child.write_text(_CHILD, encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    src = Path(emporio_agent.__file__).resolve().parents[1]
    env = {key: value for key, value in os.environ.items() if not key.startswith("OPENAI_")}
    env["OPENAI_API_KEY"] = KEY_SENTINEL
    completed = subprocess.run(
        [str(_venv_python()), "-I", str(child), str(src), mode],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=work,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert KEY_SENTINEL not in completed.stdout + completed.stderr
    (line,) = [x for x in completed.stdout.splitlines() if x.startswith("REPORT ")]
    return json.loads(line.removeprefix("REPORT "))


def test_cl01_import_has_no_sdk_no_socket_no_environment_read(tmp_path):
    report = run_child(tmp_path, "import")
    assert report == {
        "openai": False,
        "adapter": False,
        "events": [],
        "code": None,
        "err": "",
        "isolated": 1,
        "data_local": False,
    }


def test_cl01_module_form_prints_help_and_exits_zero(tmp_path):
    report = run_child(tmp_path, "module")
    assert report["code"] == 0
    assert report["openai"] is False
    assert report["adapter"] is False
    assert report["events"] == []


# --- CL02: help -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "names"),
    [
        (["-h"], ["prepare-data", "prepare-policies", "chat"]),
        (["--help"], ["prepare-data", "prepare-policies", "chat"]),
        (["chat", "-h"], ["chat", "--customer", "--database", "--index", "--diagnostics"]),
        (["prepare-data", "-h"], ["prepare-data", "--source-dir", "--database"]),
        (["prepare-policies", "--help"], ["prepare-policies", "--source-pdf", "--index"]),
    ],
)
def test_cl02_help_on_stdout_exit_zero_without_environment(cli, argv, names, audit):
    env = SpyEnviron({**VALID_ENV, "OPENAI_LOG": "debug"})
    code, out, err, spy = run(cli, argv, env)
    assert code == 0
    assert type(code) is int
    assert err == ""
    assert out.startswith("usage: emporio-agent")
    for name in names:
        assert name in out
    for absent in ("--api-key", "--model", "--reasoning-effort", "--max-output-tokens"):
        assert absent not in out
    assert spy.reads == []
    assert not [path for _, path in audit if path.endswith(".env")]
    assert not [event for event, _ in audit if event == "sqlite3.connect"]


# --- CL03: usage rejection without echo -------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["talk"],
        ["cha"],
        ["-x"],
        ["prepare-data"],
        ["prepare-policies"],
        ["prepare-data", "--source", "x"],
        ["prepare-policies", "--source", "x.pdf"],
        ["chat", "--unknown"],
        ["chat", "--diag"],
        ["chat", "--cust", "1"],
        ["chat", "--customer"],
        ["chat", "--diagnostics=yes"],
        ["chat", "extra"],
        ["chat", "--api-key", KEY_SENTINEL],
        ["chat", f"--api-key={KEY_SENTINEL}"],
        [f"--api-key={KEY_SENTINEL}", "chat"],
        ["chat", "--model", "x"],
        ["chat", "--reasoning-effort", "low"],
        ["chat", "--max-output-tokens", "1"],
        ["prepare-data", "--source-dir", "x", "--api-key", KEY_SENTINEL],
    ],
)
def test_cl03_usage_rejected_with_fixed_line_and_no_echo(cli, argv, capsys):
    code, out, err, spy = run(cli, argv, VALID_ENV)
    assert code == 2
    assert out == ""
    assert err == LINES["arguments_invalid"]
    assert KEY_SENTINEL not in err
    assert spy.reads == []
    # Nothing may bypass the injected streams (argparse writes usage to sys.stderr).
    assert capsys.readouterr() == ("", "")


# --- CL04: --customer grammar -----------------------------------------------------------


@pytest.mark.parametrize("value", ["1", "2", "10", "9223372036854775807"])
def test_cl04_valid_customer_becomes_customer_context(cli, value):
    config = cli.resolve_chat_config(parse(cli, ["chat", "--customer", value]), VALID_ENV)
    assert config.customer == CustomerContext(int(value))


@pytest.mark.parametrize(
    "value",
    [
        "0",
        "-1",
        "+1",
        " 1",
        "1 ",
        "1\n",
        "1_0",
        "١",
        "１",
        "01",
        "00",
        "9223372036854775808",
        "99999999999999999999",
        "1.0",
        "abc",
        "",
        KEY_SENTINEL,
    ],
)
def test_cl04_invalid_customer_rejected_before_environment_without_echo(cli, value):
    env = SpyEnviron({**VALID_ENV, "OPENAI_LOG": "debug"})
    code, out, err, spy = run(cli, ["chat", "--customer", value], env)
    assert code == 2
    assert out == ""
    assert err == LINES["customer_invalid"]
    assert KEY_SENTINEL not in err
    assert spy.reads == []


def test_cl04_equals_form_is_accepted_and_checked(cli):
    config = cli.resolve_chat_config(parse(cli, ["chat", "--customer=2"]), VALID_ENV)
    assert config.customer == CustomerContext(2)
    code, _, err, _ = run(cli, ["chat", "--customer=-1"], VALID_ENV)
    assert (code, err) == (2, LINES["customer_invalid"])


# --- CL05: OPENAI_LOG -------------------------------------------------------------------


@pytest.mark.parametrize("value", ["debug", "info", ""])
def test_cl05_openai_log_present_is_rejected(cli, value):
    code, out, err, _ = run(cli, ["chat"], {**VALID_ENV, "OPENAI_LOG": value})
    assert (code, out, err) == (2, "", LINES["sdk_logging_enabled"])


# --- CL06: key --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "env", [{"OPENAI_MODEL": MODEL}, {"OPENAI_MODEL": MODEL, "OPENAI_API_KEY": ""}]
)
def test_cl06_key_absent_or_empty_is_missing(cli, env):
    code, out, err, _ = run(cli, ["chat"], env)
    assert (code, out, err) == (2, "", LINES["key_missing"])


@pytest.mark.parametrize(
    "key",
    [
        " " + KEY_SENTINEL,
        KEY_SENTINEL + " ",
        KEY_SENTINEL + "\n",
        "\t" + KEY_SENTINEL,
        " ",
        "\n",
    ],
)
def test_cl06_key_with_outer_whitespace_is_invalid_and_never_printed(cli, key):
    code, out, err, _ = run(cli, ["chat"], {"OPENAI_API_KEY": key, "OPENAI_MODEL": MODEL})
    assert (code, out, err) == (2, "", LINES["key_invalid"])
    assert "SENTINEL" not in out + err


# --- CL07: model ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "env", [{"OPENAI_API_KEY": FAKE_KEY}, {"OPENAI_API_KEY": FAKE_KEY, "OPENAI_MODEL": ""}]
)
def test_cl07_model_absent_or_empty_is_missing(cli, env):
    code, out, err, _ = run(cli, ["chat"], env)
    assert (code, out, err) == (2, "", LINES["model_missing"])


@pytest.mark.parametrize(
    "model",
    [
        "gpt 5",
        "a" * 129,
        "../x",
        "-gpt",
        ".gpt",
        "modelo-é",
        "SENTINEL-MODEL\n",
        " gpt-5.4-mini",
        "gpt/5",
        "gpt\x1b[31m",
    ],
)
def test_cl07_invalid_model_rejected_without_echo(cli, model):
    code, out, err, _ = run(cli, ["chat"], {"OPENAI_API_KEY": FAKE_KEY, "OPENAI_MODEL": model})
    assert (code, out, err) == (2, "", LINES["model_invalid"])
    assert "SENTINEL" not in err


@pytest.mark.parametrize(
    "model", ["gpt-5.4-mini", "gpt-5.4-mini-2026-03-17", "a", "ft:gpt_x.y:org-1", "A" * 128]
)
def test_cl07_valid_model_kept_unchanged(cli, model):
    env = {"OPENAI_API_KEY": FAKE_KEY, "OPENAI_MODEL": model}
    config = cli.resolve_chat_config(parse(cli, ["chat"]), env)
    assert config.model == model
    assert config.model is model


# --- CL08: precedence -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "env", "expected", "reads"),
    [
        (["chat", "--bogus"], {"OPENAI_LOG": "x"}, "arguments_invalid", []),
        (["chat", "--customer", "x"], {"OPENAI_LOG": "x"}, "customer_invalid", []),
        (["chat", "--customer", "0"], {}, "customer_invalid", []),
        (["chat"], {"OPENAI_LOG": "x"}, "sdk_logging_enabled", ["OPENAI_LOG"]),
        (
            ["chat"],
            {"OPENAI_LOG": "", "OPENAI_API_KEY": " k"},
            "sdk_logging_enabled",
            ["OPENAI_LOG"],
        ),
        (["chat"], {}, "key_missing", ["OPENAI_LOG", "OPENAI_API_KEY"]),
        (["chat"], {"OPENAI_MODEL": "gpt 5"}, "key_missing", ["OPENAI_LOG", "OPENAI_API_KEY"]),
        (["chat"], {"OPENAI_API_KEY": "k "}, "key_invalid", ["OPENAI_LOG", "OPENAI_API_KEY"]),
        (
            ["chat"],
            {"OPENAI_API_KEY": FAKE_KEY},
            "model_missing",
            ["OPENAI_LOG", "OPENAI_API_KEY", "OPENAI_MODEL"],
        ),
    ],
)
def test_cl08_first_failure_in_spec_order_wins(cli, argv, env, expected, reads):
    code, out, err, spy = run(cli, argv, env)
    assert (code, out, err) == (2, "", LINES[expected])
    assert "*" not in spy.reads
    assert list(dict.fromkeys(spy.reads)) == reads


def test_cl08_valid_chat_reads_only_the_three_variables_in_order(cli):
    spy = SpyEnviron({**VALID_ENV, "OPENAI_BASE_URL": "http://x", "HTTPS_PROXY": "http://p"})
    cli.resolve_chat_config(parse(cli, ["chat"]), spy)
    assert list(dict.fromkeys(spy.reads)) == ["OPENAI_LOG", "OPENAI_API_KEY", "OPENAI_MODEL"]


# --- CL09: no file access ---------------------------------------------------------------


def test_cl09_resolution_accepts_missing_paths_without_touching_them(cli, tmp_path, audit):
    database = tmp_path / "nao-existe" / "x.sqlite3"
    index = tmp_path / "nao-existe" / "i.json"
    args = parse(cli, ["chat", "--database", str(database), "--index", str(index)])
    audit.clear()
    config = cli.resolve_chat_config(args, VALID_ENV)
    assert audit == []
    assert config.database == database
    assert config.index == index
    assert not (tmp_path / "nao-existe").exists()


# --- CL10: preparation reads no environment ---------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["prepare-data"],
        ["prepare-policies"],
        ["prepare-data", "--source-dir", "src", "--bogus"],
    ],
)
def test_cl10_preparation_parse_errors_read_no_environment(cli, argv):
    spy = SpyEnviron({**VALID_ENV, "OPENAI_LOG": "debug"})
    code, out, err, _ = run(cli, argv, spy)
    assert (code, out, err) == (2, "", LINES["arguments_invalid"])
    assert spy.reads == []


# --- CL11: key outside repr -------------------------------------------------------------


def test_cl11_key_is_not_in_repr_or_str_and_config_is_frozen(cli):
    env = {"OPENAI_API_KEY": KEY_SENTINEL, "OPENAI_MODEL": MODEL}
    config = cli.resolve_chat_config(parse(cli, ["chat", "--customer", "1"]), env)
    assert KEY_SENTINEL not in repr(config)
    assert KEY_SENTINEL not in str(config)
    assert MODEL in repr(config)
    hidden = [f for f in dataclasses.fields(config) if not f.repr]
    assert len(hidden) == 1
    assert getattr(config, hidden[0].name) == KEY_SENTINEL
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(config, hidden[0].name, "other")


# --- CL12: defaults ---------------------------------------------------------------------


def test_cl12_defaults_are_relative_and_unresolved(cli):
    config = cli.resolve_chat_config(parse(cli, ["chat"]), VALID_ENV)
    assert config.database == Path("data/local/emporio.sqlite3")
    assert config.index == Path("data/local/policy-index.json")
    assert not config.database.is_absolute()
    assert not config.index.is_absolute()
    assert config.diagnostics is False
    assert config.customer is None


def test_cl12_explicit_values_are_kept_unresolved(cli):
    argv = ["chat", "--database", "rel/db.sqlite3", "--index", "rel/i.json", "--diagnostics"]
    config = cli.resolve_chat_config(parse(cli, argv), VALID_ENV)
    assert config.database == Path("rel/db.sqlite3")
    assert config.index == Path("rel/i.json")
    assert config.diagnostics is True


# --- CL13: .env never read --------------------------------------------------------------


def test_cl13_dotenv_in_working_directory_is_never_read(cli, tmp_path, monkeypatch, audit):
    (tmp_path / ".env").write_text(
        f"OPENAI_API_KEY={KEY_SENTINEL}\nOPENAI_MODEL={MODEL}\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    audit.clear()
    code, out, err, _ = run(cli, ["chat"], {})
    assert (code, out, err) == (2, "", LINES["key_missing"])
    assert not [path for _, path in audit if path.endswith(".env")]


def test_cl13_default_environment_is_process_environment_without_dotenv(
    cli, tmp_path, monkeypatch, audit
):
    (tmp_path / ".env").write_text(f"OPENAI_API_KEY={KEY_SENTINEL}\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_MODEL", MODEL)
    stdout, stderr = _stream(), _stream()
    audit.clear()
    code = cli.main(["chat"], stdin=_stream(), stdout=stdout, stderr=stderr)
    assert (code, _text(stdout), _text(stderr)) == (2, "", LINES["key_missing"])
    assert not [path for _, path in audit if path.endswith(".env")]


# --- CL14: streams ----------------------------------------------------------------------


def test_cl14_streams_reconfigured_to_utf8_with_spec_error_policies(cli):
    stdin, stdout, stderr = _stream("cp1252"), _stream("cp1252"), _stream("cp1252")
    code = cli.main(
        ["chat", "--customer", "x"], environ={}, stdin=stdin, stdout=stdout, stderr=stderr
    )
    assert code == 2
    stderr.flush()
    assert stderr.buffer.getvalue() == LINES["customer_invalid"].encode("utf-8")
    assert (stdout.encoding, stdout.errors) == ("utf-8", "replace")
    assert (stderr.encoding, stderr.errors) == ("utf-8", "replace")
    assert (stdin.encoding, stdin.errors) == ("utf-8", "surrogateescape")


def test_cl14_help_also_written_as_utf8(cli):
    stdout, stderr = _stream("cp1252"), _stream("cp1252")
    code = cli.main(["chat", "-h"], environ={}, stdin=_stream(), stdout=stdout, stderr=stderr)
    assert code == 0
    stdout.flush()
    raw = stdout.buffer.getvalue()
    assert raw.decode("utf-8").startswith("usage: emporio-agent")
    assert "ã".encode() in raw
    assert stderr.buffer.getvalue() == b""


def test_cl14_streams_without_reconfigure_are_accepted(cli):
    stdout, stderr = io.StringIO(), io.StringIO()
    code = cli.main(["chat"], environ={}, stdin=io.StringIO(), stdout=stdout, stderr=stderr)
    assert (code, stdout.getvalue(), stderr.getvalue()) == (2, "", LINES["key_missing"])


# --- CL15: rejection layout -------------------------------------------------------------

_REJECTIONS = [
    (["chat", "--bogus"], {}, "arguments_invalid"),
    (["chat", "--customer", "01"], {}, "customer_invalid"),
    (["chat"], {**VALID_ENV, "OPENAI_LOG": "1"}, "sdk_logging_enabled"),
    (["chat"], {"OPENAI_MODEL": MODEL}, "key_missing"),
    (["chat"], {"OPENAI_API_KEY": " k", "OPENAI_MODEL": MODEL}, "key_invalid"),
    (["chat"], {"OPENAI_API_KEY": FAKE_KEY}, "model_missing"),
    (["chat"], {"OPENAI_API_KEY": FAKE_KEY, "OPENAI_MODEL": "x y"}, "model_invalid"),
]


@pytest.mark.parametrize(("argv", "env", "code_token"), _REJECTIONS)
def test_cl15_every_rejection_is_one_stderr_line_with_one_write(cli, argv, env, code_token):
    class Recorder(io.StringIO):
        def __init__(self):
            super().__init__()
            self.writes: list[str] = []

        def write(self, text):
            self.writes.append(text)
            return super().write(text)

    stdout, stderr = Recorder(), Recorder()
    code = cli.main(argv, environ=env, stdin=io.StringIO(), stdout=stdout, stderr=stderr)
    assert code == 2
    assert stdout.writes == [] or "".join(stdout.writes) == ""
    assert [w for w in stderr.writes if w] == [LINES[code_token]]
    line = stderr.getvalue()
    assert line.startswith(f"rejected: {code_token} - ")
    assert line.count("\n") == 1 and line.endswith("\n")
    assert "\r" not in line and not line.endswith(" \n")


def test_cl15_usage_configuration_and_temporary_sentences_are_distinct():
    sentence = {code: line.split(" - ", 1)[1] for code, line in LINES.items()}
    for code in (
        "sdk_logging_enabled",
        "key_missing",
        "key_invalid",
        "model_missing",
        "model_invalid",
    ):
        assert sentence[code].startswith("Erro de configuração:")
    for code in ("arguments_invalid", "customer_invalid", "command_unavailable"):
        assert not sentence[code].startswith("Erro de configuração:")
    assert "inválid" not in sentence["command_unavailable"]
    assert len(set(sentence.values())) == len(sentence)


# --- CL104: temporary command_unavailable (spec G13a). The prepare rows were removed by
# slice 08-B and the chat rows by slice 08-C (planned contract evolution,
# docs/plans/08-cli.md Reviews 08-R3 and 08-R4). The final usage and configuration
# rejections below stay.


@pytest.mark.parametrize(
    ("argv", "env", "expected"),
    [
        (["chat"], {**VALID_ENV, "OPENAI_LOG": "debug"}, "sdk_logging_enabled"),
        (["chat"], {"OPENAI_MODEL": MODEL}, "key_missing"),
        (["chat"], {"OPENAI_API_KEY": FAKE_KEY, "OPENAI_MODEL": "x y"}, "model_invalid"),
        (["chat", "--customer", "0"], VALID_ENV, "customer_invalid"),
        (["chat", "--model", MODEL], VALID_ENV, "arguments_invalid"),
        (["prepare-data"], VALID_ENV, "arguments_invalid"),
    ],
)
def test_cl104_invalid_configuration_wins_over_temporary_rejection(cli, argv, env, expected):
    code, out, err, _ = run(cli, argv, env)
    assert (code, out, err) == (2, "", LINES[expected])
