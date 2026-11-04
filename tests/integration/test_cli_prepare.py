"""Slice 08-B: preparation commands (docs/specs/08-cli.md G12, G12a, D-05; plan CL16-CL30).

Real prepare_database / prepare_policy_index / connect_readonly / load_policy_index over
synthetic data in tmp_path (tests/fixtures/data/valid and the policy_kit PDF builder).
Spies only delegate and count; failures are injected at file-system or build boundaries.
Expected lines are literals; independent sqlite3 and hashlib reads are the oracles.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.abc
import io
import json
import logging
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import textwrap
from collections.abc import Mapping
from pathlib import Path

import pytest

import emporio_agent
from emporio_agent.data import policy_index as policy_module
from emporio_agent.data import preparation as preparation_module
from emporio_agent.data.errors import DataPreparationError, IssueCode
from emporio_agent.data.policy_index import (
    PolicyIndexUnavailableError,
    PolicyPreparationError,
    load_policy_index,
)
from emporio_agent.data.readonly import connect_readonly

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests" / "fixtures" / "data" / "valid"
PREFIX = "desafio_tecnico_ai_eng - "
PRIVATE = "pasta-privada-08"
PII = "SENTINEL-PII"
TABLES = ("categories", "customers", "products", "promotions", "orders", "order_items")

DATA_SUMMARY = (
    "dados preparados: arquivo={name} schema=1 tabelas=6 categories=2 customers=2 "
    "products=4 promotions=3 orders=2 order_items=3 notas_qualidade=2\n"
)
INDEX_SUMMARY = (
    "índice preparado: arquivo={name} paginas=5 secoes=15 recuperaveis=13 sha256={sha}\n"
)
_BLOCKED = ("openai", "emporio_agent.openai_provider")


class _BlockImports(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if any(name == p or name.startswith(p + ".") for p in _BLOCKED):
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
    for name in [n for n in sys.modules if any(n == p or n.startswith(p + ".") for p in _BLOCKED)]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setattr(sys, "meta_path", [_BlockImports(), *sys.meta_path])


@pytest.fixture
def cli():
    return importlib.import_module("emporio_agent.cli")


class SpyEnviron(Mapping[str, str]):
    def __init__(self) -> None:
        self._values = {"OPENAI_API_KEY": "test-key-not-a-secret", "OPENAI_LOG": "debug"}
        self.reads: list[str] = []

    def __getitem__(self, key):
        self.reads.append(key)
        return self._values[key]

    def __contains__(self, key):
        self.reads.append(str(key))
        return key in self._values

    def get(self, key, default=None):
        self.reads.append(str(key))
        return self._values.get(key, default)

    def __iter__(self):
        self.reads.append("*")
        return iter(self._values)

    def __len__(self):
        self.reads.append("*")
        return len(self._values)


def _stream() -> io.TextIOWrapper:
    return io.TextIOWrapper(io.BytesIO(), encoding="utf-8", newline="\n")


def _text(stream: io.TextIOWrapper) -> str:
    stream.flush()
    return stream.buffer.getvalue().decode("utf-8")


def run(cli, argv):
    environ = SpyEnviron()
    stdout, stderr = _stream(), _stream()
    code = cli.main(argv, environ=environ, stdin=_stream(), stdout=stdout, stderr=stderr)
    assert environ.reads == []  # preparation never reads the environment (CL10, CL28)
    assert type(code) is int
    return code, _text(stdout), _text(stderr)


def one_error_line(err: str) -> None:
    assert err.count("\n") == 1 and err.endswith("\n")
    assert "\r" not in err and "Traceback" not in err


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_copy(tmp_path: Path, name: str = "src") -> Path:
    target = tmp_path / name
    shutil.copytree(FIXTURE, target)
    return target


def target_dir(tmp_path: Path, *parts: str) -> Path:
    directory = tmp_path.joinpath(PRIVATE, *parts)
    directory.mkdir(parents=True)
    return directory


def table_rows(database: Path) -> dict[str, list[tuple]]:
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        return {
            table: connection.execute(f"SELECT * FROM {table} ORDER BY 1, 2").fetchall()
            for table in (*TABLES, "data_quality_notes", "source_files")
        }
    finally:
        connection.close()


@pytest.fixture
def counters(monkeypatch, cli):
    """Delegating counters on the library entry points, as seen by the CLI."""
    counts = {"prepare_database": 0, "prepare_policy_index": 0}
    for name in counts:
        original = getattr(cli, name)

        def spy(*args, _original=original, _name=name, **kwargs):
            counts[_name] += 1
            return _original(*args, **kwargs)

        monkeypatch.setattr(cli, name, spy)
    return counts


def prepare_data(cli, source: Path, database: Path):
    return run(cli, ["prepare-data", "--source-dir", str(source), "--database", str(database)])


def prepare_policies(cli, pdf: Path, index: Path):
    return run(cli, ["prepare-policies", "--source-pdf", str(pdf), "--index", str(index)])


# --- prepare-data -------------------------------------------------------------------


def test_cl16_prepare_data_creates_database_and_prints_summary(cli, tmp_path):
    source = source_copy(tmp_path)
    database = target_dir(tmp_path, "db") / "emporio.sqlite3"
    code, out, err = prepare_data(cli, source, database)
    assert (code, out, err) == (0, DATA_SUMMARY.format(name="emporio.sqlite3"), "")
    assert PRIVATE not in out and str(tmp_path) not in out
    with connect_readonly(database) as connection:
        assert tuple(connection.execute("SELECT count(*) FROM customers").fetchone()) == (2,)


def test_cl17_repeated_preparation_keeps_tables_and_relations(cli, tmp_path, counters):
    source = source_copy(tmp_path)
    database = target_dir(tmp_path, "db") / "emporio.sqlite3"
    assert prepare_data(cli, source, database)[0] == 0
    first = table_rows(database)
    code, out, err = prepare_data(cli, source, database)
    assert (code, out, err) == (0, DATA_SUMMARY.format(name="emporio.sqlite3"), "")
    assert table_rows(database) == first
    assert counters["prepare_database"] == 2


def test_cl18_invalid_source_value_safe_line_no_partial_result(cli, tmp_path):
    source = source_copy(tmp_path)
    csv = source / f"{PREFIX}customers.csv"
    lines = csv.read_text(encoding="utf-8").splitlines(keepends=True)
    lines[1] = PII + lines[1][lines[1].index(",") :]
    csv.write_text("".join(lines), encoding="utf-8")
    database = target_dir(tmp_path, "db") / "emporio.sqlite3"

    expected = "erro de preparação: invalid_integer tabela=customers linha=2 coluna=customer_id\n"
    code, out, err = prepare_data(cli, source, database)
    assert (code, out, err) == (4, "", expected)
    assert PII not in err and "customers.csv" not in err and PRIVATE not in err
    assert not database.exists()
    assert list(database.parent.iterdir()) == []


def test_cl18_failed_rebuild_keeps_previous_database_bytes(cli, tmp_path):
    good = source_copy(tmp_path, "good")
    database = target_dir(tmp_path, "db") / "emporio.sqlite3"
    assert prepare_data(cli, good, database)[0] == 0
    before = sha(database)
    bad = source_copy(tmp_path, "bad")
    csv = bad / f"{PREFIX}customers.csv"
    lines = csv.read_text(encoding="utf-8").splitlines(keepends=True)
    lines[1] = PII + lines[1][lines[1].index(",") :]
    csv.write_text("".join(lines), encoding="utf-8")
    code, out, err = prepare_data(cli, bad, database)
    assert code == 4 and out == ""
    assert err.startswith("erro de preparação: invalid_integer tabela=customers")
    assert sha(database) == before
    assert sorted(p.name for p in database.parent.iterdir()) == ["emporio.sqlite3"]


def test_cl19_missing_source_directory_and_missing_source_file(cli, tmp_path):
    database = target_dir(tmp_path, "db") / "emporio.sqlite3"
    code, out, err = prepare_data(cli, tmp_path / "nao-existe", database)
    assert (code, out, err) == (4, "", "erro de preparação: source_dir_missing\n")
    source = source_copy(tmp_path)
    (source / f"{PREFIX}orders.csv").unlink()
    code, out, err = prepare_data(cli, source, database)
    assert (code, out, err) == (4, "", "erro de preparação: file_missing tabela=orders\n")
    assert not database.exists()


def test_cl20_existing_text_file_is_not_replaced(cli, tmp_path, counters):
    source = source_copy(tmp_path)
    database = target_dir(tmp_path, "db") / "README.md"
    database.write_text("documento do operador\n", encoding="utf-8")
    before = sha(database)
    code, out, err = prepare_data(cli, source, database)
    assert (code, out, err) == (4, "", "erro de preparação: target_not_prepared\n")
    assert sha(database) == before
    assert counters["prepare_database"] == 0


@pytest.mark.parametrize("command", ["prepare-data", "prepare-policies"])
def test_cl21_directory_target_is_rejected_unchanged(cli, tmp_path, counters, command, policy_kit):
    target = target_dir(tmp_path, "db", "alvo")
    (target / "dentro.txt").write_text("x", encoding="utf-8")
    if command == "prepare-data":
        result = prepare_data(cli, source_copy(tmp_path), target)
    else:
        result = prepare_policies(cli, policy_kit.write(tmp_path), target)
    assert result == (4, "", "erro de preparação: target_not_file\n")
    assert [p.name for p in target.iterdir()] == ["dentro.txt"]
    assert set(counters.values()) == {0}


def test_cl22_sqlite_without_preparation_metadata_is_not_replaced(cli, tmp_path, counters):
    database = target_dir(tmp_path, "db") / "outro.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE notas (texto TEXT)")
    connection.execute("INSERT INTO notas VALUES ('do operador')")
    connection.commit()
    connection.close()
    before = sha(database)
    code, out, err = prepare_data(cli, source_copy(tmp_path), database)
    assert (code, out, err) == (4, "", "erro de preparação: target_not_prepared\n")
    assert sha(database) == before
    assert counters["prepare_database"] == 0


def test_cl23_target_equal_to_source_file_keeps_source(cli, tmp_path, counters, policy_kit):
    source = source_copy(tmp_path)
    csv = source / f"{PREFIX}customers.csv"
    before = sha(csv)
    assert prepare_data(cli, source, csv) == (4, "", "erro de preparação: target_not_prepared\n")
    assert sha(csv) == before
    pdf = policy_kit.write(tmp_path)
    pdf_before = sha(pdf)
    expected = (4, "", "erro de preparação: target_not_prepared\n")
    assert prepare_policies(cli, pdf, pdf) == expected
    assert sha(pdf) == pdf_before
    assert set(counters.values()) == {0}


def test_cl23_library_source_target_value_error_maps_to_target_is_source(
    cli, tmp_path, monkeypatch
):
    # Defensive path behind the guard (plan CL23): inject the library error at its boundary.
    def raise_source(*args, **kwargs):
        raise ValueError("database_path must not be a source file")

    monkeypatch.setattr(cli, "prepare_database", raise_source)
    database = target_dir(tmp_path, "db") / "emporio.sqlite3"
    result = prepare_data(cli, source_copy(tmp_path), database)
    assert result == (4, "", "erro de preparação: target_is_source\n")


def test_cl23_other_value_error_is_internal_error(cli, tmp_path, monkeypatch):
    def raise_other(*args, **kwargs):
        raise ValueError(f"{PII} {tmp_path}")

    monkeypatch.setattr(cli, "prepare_database", raise_other)
    database = target_dir(tmp_path, "db") / "emporio.sqlite3"
    result = prepare_data(cli, source_copy(tmp_path), database)
    assert result == (1, "", "internal_error: ValueError\n")


# --- prepare-policies -----------------------------------------------------------------


def test_cl24_policies_guard_rejects_unrelated_file_and_replaces_index(
    cli, tmp_path, policy_kit, counters
):
    pdf = policy_kit.write(tmp_path)
    directory = target_dir(tmp_path, "idx")
    other = directory / "notas.json"
    other.write_text('{"do": "operador"}', encoding="utf-8")
    before = sha(other)
    assert prepare_policies(cli, pdf, other) == (
        4,
        "",
        "erro de preparação: target_not_prepared\n",
    )
    assert sha(other) == before
    assert counters["prepare_policy_index"] == 0
    index = directory / "policy-index.json"
    assert prepare_policies(cli, pdf, index)[0] == 0
    code, out, err = prepare_policies(cli, pdf, index)
    assert (code, out, err) == (
        0,
        INDEX_SUMMARY.format(name="policy-index.json", sha=sha(index)),
        "",
    )
    assert counters["prepare_policy_index"] == 2


def test_cl25_prepare_policies_creates_index_and_prints_summary(cli, tmp_path, policy_kit):
    pdf = policy_kit.write(tmp_path)
    index = target_dir(tmp_path, "idx") / "policy-index.json"
    code, out, err = prepare_policies(cli, pdf, index)
    assert (code, out, err) == (
        0,
        INDEX_SUMMARY.format(name="policy-index.json", sha=sha(index)),
        "",
    )
    assert load_policy_index(index).source.page_count == 5
    assert PRIVATE not in out


def test_cl26_not_pdf_and_missing_pdf_safe_lines(cli, tmp_path):
    pdf = tmp_path / "manual-sintetico.pdf"
    pdf.write_bytes(b"not a pdf")
    index = target_dir(tmp_path, "idx") / "policy-index.json"
    expected = "erro de preparação: not_pdf arquivo=manual-sintetico.pdf\n"
    assert prepare_policies(cli, pdf, index) == (4, "", expected)
    assert not index.exists()
    missing = tmp_path / "ausente.pdf"
    expected = "erro de preparação: source_missing arquivo=ausente.pdf\n"
    assert prepare_policies(cli, missing, index) == (4, "", expected)


@pytest.mark.parametrize("name", ["manual sintético.pdf", "manual ção.pdf", "índice.pdf"])
def test_cl26_hostile_real_pdf_name_is_unprintable(cli, tmp_path, name):
    pdf = tmp_path / name
    pdf.write_bytes(b"not a pdf")
    index = target_dir(tmp_path, "idx") / "policy-index.json"
    expected = "erro de preparação: not_pdf arquivo=unprintable\n"
    assert prepare_policies(cli, pdf, index) == (4, "", expected)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            PolicyPreparationError(
                "outline_mismatch", file_name="a\nb\x1b[31m.pdf", page=3, outline_entry=2
            ),
            "erro de preparação: outline_mismatch arquivo=unprintable pagina=3 entrada_sumario=2\n",
        ),
        (
            PolicyPreparationError("extraction_failed", file_name="ok.pdf", page=True),
            "erro de preparação: extraction_failed arquivo=ok.pdf pagina=unprintable\n",
        ),
        (
            PolicyPreparationError("write_failed", file_name="i\tj.json"),
            "erro de preparação: write_failed arquivo=unprintable\n",
        ),
        (
            PolicyIndexUnavailableError("index_invalid", file_name="policy-index.json"),
            "erro de preparação: index_invalid arquivo=policy-index.json\n",
        ),
        (PolicyIndexUnavailableError("index_invalid"), "erro de preparação: index_invalid\n"),
    ],
)
def test_cl26_hostile_policy_error_attributes_never_break_the_line(
    cli, tmp_path, monkeypatch, policy_kit, error, expected
):
    def raise_error(*args, **kwargs):
        raise error

    monkeypatch.setattr(cli, "prepare_policy_index", raise_error)
    index = target_dir(tmp_path, "idx") / "policy-index.json"
    code, out, err = prepare_policies(cli, policy_kit.write(tmp_path), index)
    assert (code, out, err) == (4, "", expected)
    one_error_line(err)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            DataPreparationError(
                IssueCode.INVALID_INTEGER, file_name="x\ny.csv", line=-1, column="nome\x1b"
            ),
            "erro de preparação: invalid_integer tabela=unprintable linha=unprintable "
            "coluna=unprintable\n",
        ),
        (
            DataPreparationError(
                IssueCode.HEADER_MISMATCH, file_name=f"{PREFIX}orders.csv", line=1
            ),
            "erro de preparação: header_mismatch tabela=orders linha=1\n",
        ),
        (
            DataPreparationError(IssueCode.FILE_MISSING, file_name="customers.csv"),
            "erro de preparação: file_missing tabela=unprintable\n",
        ),
        (
            DataPreparationError(IssueCode.DUPLICATE_KEY, line=3, column="product_id"),
            "erro de preparação: duplicate_key linha=3 coluna=product_id\n",
        ),
        (
            DataPreparationError(IssueCode.DATABASE_BUILD_FAILED),
            "erro de preparação: database_build_failed\n",
        ),
    ],
)
def test_cl18_hostile_data_error_attributes_never_break_the_line(
    cli, tmp_path, monkeypatch, error, expected
):
    def raise_error(*args, **kwargs):
        raise error

    monkeypatch.setattr(cli, "prepare_database", raise_error)
    database = target_dir(tmp_path, "db") / "emporio.sqlite3"
    code, out, err = prepare_data(cli, source_copy(tmp_path), database)
    assert (code, out, err) == (4, "", expected)
    one_error_line(err)


def test_g12a_code_vocabularies_equal_library_literals(cli):
    data = {code.value for code in IssueCode}
    policy = set(policy_module.PreparationErrorCode.__value__.__args__)
    index = set(policy_module.IndexErrorCode.__value__.__args__)
    assert len(data) == 26 and len(policy) == 15 and len(index) == 4
    assert cli._DATA_ERROR_CODES == data | {
        "target_not_file",
        "target_not_prepared",
        "target_is_source",
    }
    assert cli._POLICY_ERROR_CODES == policy | index | {"target_not_file", "target_not_prepared"}


def test_g12a_unknown_code_is_unprintable(cli, tmp_path, monkeypatch):
    error = DataPreparationError(IssueCode.CSV_MALFORMED)
    error.code = "codigo\nnovo"  # a value outside the frozen vocabulary

    def raise_error(*args, **kwargs):
        raise error

    monkeypatch.setattr(cli, "prepare_database", raise_error)
    database = target_dir(tmp_path, "db") / "emporio.sqlite3"
    result = prepare_data(cli, source_copy(tmp_path), database)
    assert result == (4, "", "erro de preparação: unprintable\n")


def test_cl27_missing_target_directory_is_not_created(cli, tmp_path, policy_kit):
    missing = tmp_path / PRIVATE / "nao-existe"
    expected_data = (4, "", "erro de preparação: target_directory_missing\n")
    assert prepare_data(cli, source_copy(tmp_path), missing / "emporio.sqlite3") == expected_data
    expected_index = (
        4,
        "",
        "erro de preparação: target_directory_missing arquivo=policy-index.json\n",
    )
    pdf = policy_kit.write(tmp_path)
    assert prepare_policies(cli, pdf, missing / "policy-index.json") == expected_index
    assert not (tmp_path / PRIVATE).exists()


# --- process, interruption, unexpected failure ---------------------------------------

_CHILD = textwrap.dedent(
    """
    import io, json, os, socket, sys

    src, source, database = sys.argv[1], sys.argv[2], sys.argv[3]
    sys.path.insert(0, src)
    events = []

    def network(*args, **kwargs):
        events.append("network")
        raise OSError("network blocked")

    for name in ("create_connection", "getaddrinfo", "gethostbyname", "gethostbyname_ex"):
        setattr(socket, name, network)
    socket.socket.connect = network
    socket.socket.connect_ex = network
    original_getitem = os._Environ.__getitem__

    def getitem(self, key):
        if str(key).startswith("OPENAI"):
            events.append("env:" + str(key))
        return original_getitem(self, key)

    os._Environ.__getitem__ = getitem
    from emporio_agent import cli
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(
        ["prepare-data", "--source-dir", source, "--database", database],
        stdin=io.StringIO(), stdout=out, stderr=err,
    )
    print("REPORT " + json.dumps({
        "code": code, "out": out.getvalue(), "err": err.getvalue(), "events": events,
        "openai": "openai" in sys.modules,
        "adapter": "emporio_agent.openai_provider" in sys.modules,
        "isolated": sys.flags.isolated,
    }))
    """
)


def test_cl28_isolated_process_prepares_without_sdk_network_or_environment(tmp_path):
    child = tmp_path / "child.py"
    child.write_text(_CHILD, encoding="utf-8")
    source = source_copy(tmp_path)
    database = target_dir(tmp_path, "db") / "emporio.sqlite3"
    src = Path(emporio_agent.__file__).resolve().parents[1]
    env = {k: v for k, v in os.environ.items() if not k.startswith("OPENAI_")}
    env["OPENAI_LOG"] = "debug"
    python = REPO / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    completed = subprocess.run(
        [str(python), "-I", str(child), str(src), str(source), str(database)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    (line,) = [x for x in completed.stdout.splitlines() if x.startswith("REPORT ")]
    assert json.loads(line.removeprefix("REPORT ")) == {
        "code": 0,
        "out": DATA_SUMMARY.format(name="emporio.sqlite3"),
        "err": "",
        "events": [],
        "openai": False,
        "adapter": False,
        "isolated": 1,
    }


@pytest.mark.parametrize("command", ["prepare-data", "prepare-policies"])
def test_cl28_unexpected_exception_prints_type_name_only(
    cli, tmp_path, monkeypatch, policy_kit, command
):
    def boom(*args, **kwargs):
        raise RuntimeError(f"SENTINEL-PAYLOAD {tmp_path}")

    if command == "prepare-data":
        monkeypatch.setattr(cli, "prepare_database", boom)
        result = prepare_data(cli, source_copy(tmp_path), target_dir(tmp_path, "d") / "e.sqlite3")
    else:
        monkeypatch.setattr(cli, "prepare_policy_index", boom)
        pdf = policy_kit.write(tmp_path)
        result = prepare_policies(cli, pdf, target_dir(tmp_path, "i") / "policy-index.json")
    assert result == (1, "", "internal_error: RuntimeError\n")


class _InterruptingConnection:
    """Real connection; the first INSERT into a store table raises KeyboardInterrupt."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def executemany(self, sql, rows):
        if sql.startswith("INSERT INTO categories"):
            raise KeyboardInterrupt
        return self._connection.executemany(sql, rows)

    def __getattr__(self, name):
        return getattr(self._connection, name)


@pytest.mark.parametrize("previous", [False, True])
def test_cl29_ctrl_c_during_database_build_leaves_no_temporary(
    cli, tmp_path, monkeypatch, previous
):
    source = source_copy(tmp_path)
    database = target_dir(tmp_path, "db") / "emporio.sqlite3"
    if previous:
        assert prepare_data(cli, source, database)[0] == 0
    before = sha(database) if previous else None
    real_connect = sqlite3.connect

    def connect(target, *args, **kwargs):
        connection = real_connect(target, *args, **kwargs)
        return _InterruptingConnection(connection) if str(target).endswith(".tmp") else connection

    monkeypatch.setattr(preparation_module.sqlite3, "connect", connect)
    assert prepare_data(cli, source, database) == (130, "", "interrompido\n")
    names = sorted(p.name for p in database.parent.iterdir())
    if previous:
        assert names == ["emporio.sqlite3"] and sha(database) == before
    else:
        assert names == []


def test_cl29_ctrl_c_during_index_write_leaves_no_temporary(cli, tmp_path, monkeypatch, policy_kit):
    pdf = policy_kit.write(tmp_path)
    index = target_dir(tmp_path, "idx") / "policy-index.json"

    def interrupt(fd):
        raise KeyboardInterrupt

    monkeypatch.setattr(policy_module.os, "fsync", interrupt)
    assert prepare_policies(cli, pdf, index) == (130, "", "interrompido\n")
    assert list(index.parent.iterdir()) == []


def test_cl29_ctrl_c_during_guard_recognition(cli, tmp_path, monkeypatch, counters):
    source = source_copy(tmp_path)
    database = target_dir(tmp_path, "db") / "emporio.sqlite3"
    assert prepare_data(cli, source, database)[0] == 0
    before = sha(database)

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "read_preparation_info", interrupt)
    assert prepare_data(cli, source, database) == (130, "", "interrompido\n")
    assert sha(database) == before
    assert counters["prepare_database"] == 1


# --- names with spaces and Unicode ----------------------------------------------------


def test_cl30_directories_with_spaces_and_unicode_print_base_names_only(cli, tmp_path, policy_kit):
    source = source_copy(tmp_path, "fonte privada ção")
    directory = target_dir(tmp_path, "pasta privada ção")
    code, out, err = prepare_data(cli, source, directory / "emporio.sqlite3")
    assert (code, out, err) == (0, DATA_SUMMARY.format(name="emporio.sqlite3"), "")
    pdf = policy_kit.write(tmp_path / "fonte privada ção")
    index = directory / "policy-index.json"
    code, out, err = prepare_policies(cli, pdf, index)
    assert (code, out, err) == (
        0,
        INDEX_SUMMARY.format(name="policy-index.json", sha=sha(index)),
        "",
    )
    assert "ção" not in out and PRIVATE not in out


def test_cl30_hostile_target_base_names_print_unprintable(cli, tmp_path, policy_kit):
    directory = target_dir(tmp_path, "db")
    code, out, err = prepare_data(cli, source_copy(tmp_path), directory / "meu banco.sqlite3")
    assert (code, out, err) == (0, DATA_SUMMARY.format(name="unprintable"), "")
    assert (directory / "meu banco.sqlite3").is_file()
    index = directory / "índice.json"
    code, out, err = prepare_policies(cli, policy_kit.write(tmp_path), index)
    assert (code, out, err) == (0, INDEX_SUMMARY.format(name="unprintable", sha=sha(index)), "")
    assert PRIVATE not in out and str(tmp_path) not in out


def test_cl30_default_targets_relative_to_working_directory(cli, tmp_path, monkeypatch, policy_kit):
    source = source_copy(tmp_path)
    pdf = policy_kit.write(tmp_path)
    work = tmp_path / "work"
    (work / "data" / "local").mkdir(parents=True)
    monkeypatch.chdir(work)
    code, out, _ = run(cli, ["prepare-data", "--source-dir", str(source)])
    assert (code, out) == (0, DATA_SUMMARY.format(name="emporio.sqlite3"))
    code, out, _ = run(cli, ["prepare-policies", "--source-pdf", str(pdf)])
    index = work / "data" / "local" / "policy-index.json"
    assert (code, out) == (0, INDEX_SUMMARY.format(name="policy-index.json", sha=sha(index)))
    assert (work / "data" / "local" / "emporio.sqlite3").is_file()


# --- Correction 08-07: pypdf logging during prepare-policies (spec G12b) ---------------
# A real truncated PDF makes pypdf log "EOF marker not found" itself; the message is never
# simulated. Real process streams are observed in a child process, because the logging
# last-resort handler writes to the real sys.stderr, not to the stream given to main.

TRUNCATED_PDF = b"%PDF-1.4\n"
TRUNCATED_LINE = "erro de preparação: not_pdf arquivo=truncado.pdf\n"
RAW_WARNING = "EOF marker not found"
QUIET = logging.CRITICAL + 1

_LOG_CHILD = textwrap.dedent(
    """
    import logging
    import sys

    sys.path.insert(0, sys.argv[1])
    mode, pdf, index = sys.argv[2], sys.argv[3], sys.argv[4]
    if mode == "library":
        import io

        import pypdf

        with open(pdf, "rb") as handle:
            data = handle.read()
        try:
            pypdf.PdfReader(io.BytesIO(data))
        except Exception:
            pass
        raise SystemExit(0)
    if mode == "handler":
        logging.basicConfig(
            level=logging.DEBUG, stream=sys.stderr, format="LOG %(name)s %(message)s"
        )
    from emporio_agent.cli import main

    code = main(["prepare-policies", "--source-pdf", pdf, "--index", index])
    if "openai" in sys.modules:
        code = 99
    sys.stdout.flush()
    sys.stderr.flush()
    raise SystemExit(code)
    """
)


def truncated_pdf(tmp_path: Path) -> Path:
    pdf = tmp_path / "truncado.pdf"
    pdf.write_bytes(TRUNCATED_PDF)
    return pdf


def run_log_child(tmp_path: Path, mode: str, pdf: Path, index: Path):
    child = tmp_path / "log_child.py"
    child.write_text(_LOG_CHILD, encoding="utf-8")
    src = Path(emporio_agent.__file__).resolve().parents[1]
    env = {k: v for k, v in os.environ.items() if not k.startswith("OPENAI_")}
    python = REPO / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return subprocess.run(
        [str(python), "-I", str(child), str(src), mode, str(pdf), str(index)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=120,
        check=False,
    )


def is_pypdf(name: str) -> bool:
    return name == "pypdf" or name.startswith("pypdf.")


@pytest.fixture
def root_records():
    """Handler configured by the host on the root logger, at DEBUG, collecting every record."""
    records: list[tuple[str, str]] = []

    class Collect(logging.Handler):
        def emit(self, record):
            records.append((record.name, record.getMessage()))

    root = logging.getLogger()
    handler = Collect(level=logging.DEBUG)
    previous = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield records
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)


def test_cl31_control_truncated_pdf_makes_pypdf_log_raw_warning(tmp_path):
    completed = run_log_child(tmp_path, "library", truncated_pdf(tmp_path), tmp_path / "x.json")
    assert completed.returncode == 0
    assert RAW_WARNING in completed.stderr


@pytest.mark.parametrize("mode", ["plain", "handler"])
def test_cl31_real_process_stderr_has_only_the_safe_line(tmp_path, mode):
    index = target_dir(tmp_path, "idx") / "policy-index.json"
    completed = run_log_child(tmp_path, mode, truncated_pdf(tmp_path), index)
    assert (completed.returncode, completed.stdout, completed.stderr) == (4, "", TRUNCATED_LINE)
    assert list(index.parent.iterdir()) == []


def test_cl31_real_process_valid_pdf_still_prepares(tmp_path, policy_kit):
    pdf = policy_kit.write(tmp_path)
    index = target_dir(tmp_path, "idx") / "policy-index.json"
    completed = run_log_child(tmp_path, "plain", pdf, index)
    assert completed.returncode == 0, completed.stderr
    expected = INDEX_SUMMARY.format(name="policy-index.json", sha=sha(index))
    assert (completed.stdout, completed.stderr) == (expected, "")
    assert sorted(p.name for p in index.parent.iterdir()) == ["policy-index.json"]


@pytest.mark.parametrize("child_level", [None, logging.WARNING])
def test_cl31_injected_streams_and_host_handler_get_no_raw_message(
    cli, tmp_path, capfd, root_records, child_level
):
    reader = logging.getLogger("pypdf._reader")
    before = reader.level
    if child_level is not None:
        reader.setLevel(child_level)
    index = target_dir(tmp_path, "idx") / "policy-index.json"
    try:
        result = prepare_policies(cli, truncated_pdf(tmp_path), index)
    finally:
        reader.setLevel(before)
    assert result == (4, "", TRUNCATED_LINE)
    assert [r for r in root_records if is_pypdf(r[0]) or RAW_WARNING in r[1]] == []
    captured = capfd.readouterr()
    assert (captured.out, captured.err) == ("", "")
    assert list(index.parent.iterdir()) == []


def test_cl31_failed_rebuild_keeps_previous_index(cli, tmp_path, policy_kit, root_records):
    index = target_dir(tmp_path, "idx") / "policy-index.json"
    assert prepare_policies(cli, policy_kit.write(tmp_path), index)[0] == 0
    before = sha(index)
    assert prepare_policies(cli, truncated_pdf(tmp_path), index) == (4, "", TRUNCATED_LINE)
    assert sorted(p.name for p in index.parent.iterdir()) == ["policy-index.json"]
    assert sha(index) == before
    assert [r for r in root_records if is_pypdf(r[0])] == []


def test_cl31_other_namespaces_keep_logging(cli, tmp_path, monkeypatch, policy_kit, root_records):
    real = cli.prepare_policy_index
    stderr = sys.stderr
    seen = []

    def spy(*args, **kwargs):
        seen.append((logging.root.manager.disable, sys.stderr is stderr))
        for name in ("emporio_agent.controle_08_07", "pypdfx", "root"):
            logging.getLogger(name).warning("CONTROLE-08-07")
        return real(*args, **kwargs)

    monkeypatch.setattr(cli, "prepare_policy_index", spy)
    index = target_dir(tmp_path, "idx") / "policy-index.json"
    assert prepare_policies(cli, policy_kit.write(tmp_path), index)[0] == 0
    assert seen == [(logging.NOTSET, True)]
    controls = [r for r in root_records if r[1] == "CONTROLE-08-07"]
    assert sorted(name for name, _ in controls) == [
        "emporio_agent.controle_08_07",
        "pypdfx",
        "root",
    ]


@pytest.mark.parametrize("outcome", ["success", "error", "guard", "interrupt", "internal"])
def test_cl31_logging_levels_restored_on_every_exit(
    cli, tmp_path, monkeypatch, policy_kit, outcome
):
    parent = logging.getLogger("pypdf")
    child = logging.getLogger("pypdf._reader")
    other = logging.getLogger("pypdfx")
    saved = {logger: logger.level for logger in (parent, child, other)}
    real = cli.prepare_policy_index
    seen = []

    def spy(*args, **kwargs):
        late = logging.getLogger("pypdf._late_08_07").getEffectiveLevel()
        seen.append((parent.level, child.level, other.level, late))
        if outcome == "internal":
            raise RuntimeError("SENTINEL-PAYLOAD")
        return real(*args, **kwargs)

    def interrupt(fd):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "prepare_policy_index", spy)
    if outcome == "interrupt":
        monkeypatch.setattr(policy_module.os, "fsync", interrupt)
    pdf = truncated_pdf(tmp_path) if outcome == "error" else policy_kit.write(tmp_path)
    idx = target_dir(tmp_path, "idx")
    index = idx / "policy-index.json"
    if outcome == "guard":
        index.mkdir()
    parent.setLevel(logging.INFO)
    child.setLevel(logging.ERROR)
    other.setLevel(logging.DEBUG)
    try:
        code, out, err = prepare_policies(cli, pdf, index)
        after = (parent.level, child.level, other.level)
    finally:
        for logger, level in saved.items():
            logger.setLevel(level)
    expected = {
        "success": (0, None),
        "error": (4, TRUNCATED_LINE),
        "guard": (4, "erro de preparação: target_not_file\n"),
        "interrupt": (130, "interrompido\n"),
        "internal": (1, "internal_error: RuntimeError\n"),
    }[outcome]
    assert code == expected[0]
    if expected[1] is not None:
        assert (out, err) == ("", expected[1])
    assert after == (logging.INFO, logging.ERROR, logging.DEBUG)
    assert seen == ([] if outcome == "guard" else [(QUIET, QUIET, logging.DEBUG, QUIET)])
    if outcome != "success":
        leftovers = sorted(p.name for p in idx.iterdir())
        assert leftovers == (["policy-index.json"] if outcome == "guard" else [])
