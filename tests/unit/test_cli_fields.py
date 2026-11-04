"""Slice 08-B, CL106: minimal field serializer of the preparation summaries.

docs/specs/08-cli.md G11 introduction (R2), G11a final guard, G11b types `int`,
`basename`, `sha256`. Inputs and expected outputs are literals.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

PRIVATE_DIR = "pasta-privada-08"
HEX64 = "0123456789abcdef" * 4


@pytest.fixture
def cli():
    return importlib.import_module("emporio_agent.cli")


@pytest.mark.parametrize(("value", "printed"), [(0, "0"), (6, "6"), (2**63 - 1, str(2**63 - 1))])
def test_cl106_int_printed_as_ascii_decimal(cli, value, printed):
    assert cli.format_field("int", value) == printed


@pytest.mark.parametrize("value", [True, False, -1, 2**63, "6", 6.0, None, b"6"])
def test_cl106_int_rejects_bool_negative_out_of_range_and_non_int(cli, value):
    assert cli.format_field("int", value) == "unprintable"


@pytest.mark.parametrize(
    "name", ["emporio.sqlite3", "policy-index.json", "a", "A_b-1.2", "x" * 128]
)
def test_cl106_basename_valid_from_path_and_from_str(cli, name):
    assert cli.format_field("basename", Path(PRIVATE_DIR) / name) == name
    assert cli.format_field("basename", name) == name


@pytest.mark.parametrize(
    "name",
    [
        "meu banco.sqlite3",
        "a\nb.sqlite3",
        "a\tb.sqlite3",
        "a\rb.sqlite3",
        "a\x1bb.sqlite3",
        "a\x00b",
        "bancoção.sqlite3",
        "índice.json",
        "١.json",
        ".oculto",
        "-x",
        "_x",
        "a=b",
        "a;b",
        "a:b",
        "a@b",
        "a,b",
        "x" * 129,
    ],
)
def test_cl106_basename_invalid_is_exact_unprintable_without_any_part(cli, name):
    # On Windows a joined name with ':' is drive-relative, so Path(dir) / 'a:b' has name 'b';
    # the str form covers that input exactly.
    values = (name,) if ":" in name else (Path(PRIVATE_DIR) / name, name)
    for value in values:
        printed = cli.format_field("basename", value)
        assert printed == "unprintable"
        assert PRIVATE_DIR not in printed


@pytest.mark.parametrize(
    "value", ["", f"{PRIVATE_DIR}/x.json", f"{PRIVATE_DIR}\\x.json", 5, None, b"x.json"]
)
def test_cl106_basename_rejects_empty_path_strings_and_non_text(cli, value):
    assert cli.format_field("basename", value) == "unprintable"


def test_cl106_basename_of_empty_path_is_unprintable(cli):
    assert cli.format_field("basename", Path("")) == "unprintable"


def test_cl106_sha256_lowercase_hex_only(cli):
    assert cli.format_field("sha256", HEX64) == HEX64
    for value in (HEX64.upper(), HEX64[:63], HEX64 + "0", "g" * 64, " " + HEX64[1:], None, 1):
        assert cli.format_field("sha256", value) == "unprintable"


def test_cl106_unknown_kind_is_a_programming_error(cli):
    with pytest.raises(ValueError):
        cli.format_field("datetime", "2026-01-01")


def test_cl106_final_guard_catches_a_weakened_field_rule(cli, monkeypatch):
    # Defence in depth (G11a): with the basename grammar forced open, the guard still
    # replaces a value with a space or a control character.
    monkeypatch.setattr(cli, "_BASENAME", re.compile(r".*", re.DOTALL))
    assert cli.format_field("basename", "meu banco.sqlite3") == "unprintable"
    assert cli.format_field("basename", "a\nb") == "unprintable"
    assert cli.format_field("basename", "ok.json") == "ok.json"


def test_cl106_every_output_matches_the_guard_grammar(cli):
    guard = re.compile(r"[A-Za-z0-9_.:@,;-]{1,4096}")
    samples = [
        ("int", 7),
        ("int", True),
        ("basename", "x y"),
        ("basename", "ok"),
        ("sha256", HEX64),
    ]
    for kind, value in samples:
        assert guard.fullmatch(cli.format_field(kind, value))
