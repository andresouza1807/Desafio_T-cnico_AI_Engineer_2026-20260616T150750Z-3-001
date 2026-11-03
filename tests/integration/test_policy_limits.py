"""Case P68: error surfaces under a directory with spaces and accents.

docs/plans/04-policies.md, slice C, matrix row P68 (spec P-12). A synthetic PDF built by
policy_kit and its index live under tmp_path / "Desafio Técnico AI Engineer". Missing
index, invalid index and invalid sources are run through the real preparation, load and
retrieval functions. Every error surface defined by P-12 (str, repr, args, documented
attributes, format_exception_only) may hold a code, a page or outline number, and a base
name. It must hold no directory text, no PDF or index content (sentinel) and no message of
the original OS exception. No mock of extraction, loading or search; patching is limited
to failure injection (os.replace).
"""

import traceback
from pathlib import Path
from typing import Any

import pytest

import emporio_agent.data.policy_index as pi
from emporio_agent.data.policy_index import (
    PolicyIndexUnavailableError,
    PolicyPreparationError,
    load_policy_index,
    prepare_policy_index,
)
from emporio_agent.services.policies import PolicyInputError, search_policies

DIRECTORY = "Desafio Técnico AI Engineer"
ALLOWED_FILE_NAMES = {"manual-sintetico.pdf", "index.json"}
ATTRIBUTES = ("code", "file_name", "page", "outline_entry", "field")


def _surfaces(error: BaseException) -> list[str]:
    surfaces = [str(error), repr(error), *map(str, error.args)]
    surfaces.extend("".join(traceback.format_exception_only(error)).splitlines())
    surfaces.extend(str(getattr(error, name)) for name in ATTRIBUTES if hasattr(error, name))
    return surfaces


def _leaks(error: BaseException, root: Path, sentinel: str) -> list[str]:
    forbidden = (
        str(root),
        str(root.parent),
        "desafio",
        "técnico",
        "tecnico",
        "engineer",
        sentinel,
        "os-message",
    )
    found = []
    for surface in _surfaces(error):
        folded = surface.casefold()
        found.extend(f"{word!r} in {surface!r}" for word in forbidden if word.casefold() in folded)
    return found


def _assert_sanitized(error: BaseException, root: Path, sentinel: str) -> None:
    assert _leaks(error, root, sentinel) == []
    assert error.__cause__ is None
    assert error.__suppress_context__ is True
    assert error.args == (str(error),)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    directory = tmp_path / DIRECTORY
    directory.mkdir()
    return directory


def test_p68_success_under_spaced_accented_directory_keeps_base_names(
    policy_kit: Any, root: Path
) -> None:
    source = policy_kit.write(root, edit="injection_title")
    target = root / "index.json"

    result = prepare_policy_index(source, target)
    index = load_policy_index(target)

    assert result.source.file_name == "manual-sintetico.pdf"
    assert index.source.file_name == "manual-sintetico.pdf"
    raw = target.read_text(encoding="utf-8")
    assert DIRECTORY not in raw
    assert str(root) not in raw
    assert "Técnico" not in raw
    assert sorted(path.name for path in root.iterdir()) == ["index.json", "manual-sintetico.pdf"]


def test_p68_missing_and_invalid_index(policy_kit: Any, root: Path) -> None:
    sentinel = policy_kit.sentinel
    errors: list[PolicyIndexUnavailableError] = []

    def load_error(path: Path) -> PolicyIndexUnavailableError:
        with pytest.raises(PolicyIndexUnavailableError) as caught:
            load_policy_index(path)
        return caught.value

    errors.append(load_error(root / "index.json"))
    (root / "sub").mkdir()
    errors.append(load_error(root / "sub" / "missing" / "index.json"))
    (root / "dir" / "index.json").mkdir(parents=True)
    errors.append(load_error(root / "dir" / "index.json"))
    (root / "bad").mkdir()
    (root / "bad" / "index.json").write_text(f'{{"format": "{sentinel}"}}', encoding="utf-8")
    errors.append(load_error(root / "bad" / "index.json"))
    (root / "json").mkdir()
    (root / "json" / "index.json").write_text(f"{sentinel} {root}", encoding="utf-8")
    errors.append(load_error(root / "json" / "index.json"))

    assert [error.code for error in errors] == [
        "index_missing",
        "index_missing",
        "index_unreadable",
        "index_invalid",
        "index_invalid",
    ]
    for error in errors:
        _assert_sanitized(error, root, sentinel)
        assert error.file_name == "index.json"
        assert str(error) == f"index.json: {error.code}"


def test_p68_invalid_sources_and_targets(
    policy_kit: Any, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = policy_kit.sentinel
    errors: list[PolicyPreparationError] = []

    def prepare_error(source: Path, target: Path) -> PolicyPreparationError:
        with pytest.raises(PolicyPreparationError) as caught:
            prepare_policy_index(source, target)
        return caught.value

    def folder(name: str) -> Path:
        path = root / name
        path.mkdir()
        return path

    missing = folder("missing")
    errors.append(prepare_error(missing / "manual-sintetico.pdf", missing / "index.json"))
    directory = folder("directory")
    (directory / "manual-sintetico.pdf").mkdir()
    errors.append(prepare_error(directory / "manual-sintetico.pdf", directory / "index.json"))
    not_pdf = folder("not pdf")
    (not_pdf / "manual-sintetico.pdf").write_bytes(f"not a pdf {sentinel} {root}".encode())
    errors.append(prepare_error(not_pdf / "manual-sintetico.pdf", not_pdf / "index.json"))
    truncated = folder("truncated")
    (truncated / "manual-sintetico.pdf").write_bytes(policy_kit.build("injection_title")[:400])
    errors.append(prepare_error(truncated / "manual-sintetico.pdf", truncated / "index.json"))
    mismatch = folder("mismatch")
    source = policy_kit.write(mismatch, edit=("injection_title", "title_text_changed"))
    errors.append(prepare_error(source, mismatch / "index.json"))
    good = folder("good")
    source = policy_kit.write(good, edit="injection_title")
    errors.append(prepare_error(source, good / "absent dir" / "index.json"))
    with monkeypatch.context() as patch:
        message = f"os-message {sentinel} {root}"
        patch.setattr(pi.os, "replace", lambda *_: (_ for _ in ()).throw(OSError(message)))
        errors.append(prepare_error(source, good / "index.json"))

    assert [error.code for error in errors] == [
        "source_missing",
        "source_unreadable",
        "not_pdf",
        "not_pdf",
        "outline_mismatch",
        "target_directory_missing",
        "write_failed",
    ]
    for error in errors:
        _assert_sanitized(error, root, sentinel)
        assert error.file_name in ALLOWED_FILE_NAMES
    assert errors[4].outline_entry == 5
    assert sorted(path.name for path in good.iterdir()) == ["manual-sintetico.pdf"]
    assert not (mismatch / "index.json").exists()


def test_p68_query_errors_hold_no_query_text(policy_kit: Any, root: Path) -> None:
    source = policy_kit.write(root)
    prepare_policy_index(source, root / "index.json")
    index = load_policy_index(root / "index.json")
    sentinel = policy_kit.sentinel
    errors: list[PolicyInputError] = []

    for query in (f"{sentinel} {root} " + "x" * 4000, b"bytes", " \t "):
        with pytest.raises(PolicyInputError) as caught:
            search_policies(index, query)
        errors.append(caught.value)

    assert [error.code for error in errors] == ["too_long", "invalid_type", "blank"]
    for error in errors:
        _assert_sanitized(error, root, sentinel)
        assert error.field == "query"
