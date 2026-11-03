"""Cases P29-P35: loading and strict validation of the policy index.

docs/plans/04-policies.md, slice A; spec P-05 and P-12. Index files are prepared from
real synthetic PDFs, then changed with the standard json module. The PDF is deleted
before every load, so loading cannot read it.
"""

import copy
import hashlib
import json
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from emporio_agent.data import policy_index as pi
from emporio_agent.data.policy_index import (
    PolicyIndexUnavailableError,
    extract_policy_index,
    load_policy_index,
    prepare_policy_index,
)

type Document = dict[str, Any]


def _prepared(policy_kit: Any, directory: Path, edit: Any = None) -> tuple[Path, Document]:
    source = policy_kit.write(directory, edit=edit)
    target = directory / "index.json"
    prepare_policy_index(source, target)
    source.unlink()
    return target, json.loads(target.read_bytes().decode("utf-8"))


def _write(path: Path, document: object) -> Path:
    path.write_bytes(json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8"))
    return path


def _load_error(path: Path) -> PolicyIndexUnavailableError:
    with pytest.raises(PolicyIndexUnavailableError) as caught:
        load_policy_index(path)
    return caught.value


def _by_id(document: Document, section_id: str) -> Document:
    return next(s for s in document["sections"] if s["section_id"] == section_id)


# --- P29 ----------------------------------------------------------------------------


def test_p29_load_equals_extract_and_mini(policy_kit: Any, tmp_path: Path) -> None:
    source = policy_kit.write(tmp_path)
    data = source.read_bytes()
    extracted = extract_policy_index(source)
    target = tmp_path / "index.json"
    prepare_policy_index(source, target)
    source.unlink()

    loaded = load_policy_index(target)

    assert loaded == extracted
    rows = [
        (s.section_id, s.level, s.parent_id, s.title, s.heading_page, s.lines)
        for s in loaded.sections
    ]
    assert rows == list(policy_kit.mini)
    assert loaded.source.sha256 == hashlib.sha256(data).hexdigest()
    assert loaded.source.size_bytes == len(data)
    assert loaded.source.file_name == "manual-sintetico.pdf"
    assert loaded.source.page_count == 5
    assert type(loaded.sections) is tuple
    assert all(type(s.lines) is tuple for s in loaded.sections)
    assert all(type(line) is tuple for s in loaded.sections for line in s.lines)


# --- P30-P32 ------------------------------------------------------------------------


def test_p30_missing_and_directory(tmp_path: Path) -> None:
    missing = _load_error(tmp_path / "index.json")
    assert missing.code == "index_missing"
    assert missing.file_name == "index.json"
    folder = tmp_path / "pasta.json"
    folder.mkdir()
    unreadable = _load_error(folder)
    assert unreadable.code == "index_unreadable"
    assert unreadable.file_name == "pasta.json"


@pytest.mark.parametrize("raw", [b'{"format": ', b"\xff\xfe", b"[]", b"", b"\xef\xbb\xbf{}"])
def test_p31_invalid_bytes_json_or_shape(tmp_path: Path, raw: bytes) -> None:
    path = tmp_path / "index.json"
    path.write_bytes(raw)

    assert _load_error(path).code == "index_invalid"


@pytest.mark.parametrize(
    ("change", "code"),
    [
        (lambda d: d.update(format_version=2), "index_version_unsupported"),
        (lambda d: d.update(format_version="1"), "index_version_unsupported"),
        (lambda d: d.update(format_version=True), "index_version_unsupported"),
        (lambda d: d.update(format_version=1.0), "index_version_unsupported"),
        (lambda d: d.update(format="outro-indice"), "index_invalid"),
    ],
    ids=["two", "string", "bool", "float", "format"],
)
def test_p32_format_and_version(
    policy_kit: Any, tmp_path: Path, change: Callable[[Document], None], code: str
) -> None:
    path, document = _prepared(policy_kit, tmp_path)
    change(document)

    assert _load_error(_write(path, document)).code == code


# --- P33 ----------------------------------------------------------------------------


def _set_line_page(section_id: str, position: int, value: object) -> Callable[[Document], None]:
    def change(document: Document) -> None:
        _by_id(document, section_id)["lines"][position][0] = value

    return change


def _set_field(section_id: str, key: str, value: object) -> Callable[[Document], None]:
    def change(document: Document) -> None:
        _by_id(document, section_id)[key] = value

    return change


def _set_source(key: str, value: object) -> Callable[[Document], None]:
    def change(document: Document) -> None:
        document["source"][key] = value

    return change


def _decreasing(document: Document) -> None:
    section = _by_id(document, "4.1")
    section["lines"] = [[4, "a"], [3, "b"]]


def _swap_front(document: Document) -> None:
    sections = document["sections"]
    sections[0], sections[1] = sections[1], sections[0]


def _no_lines(document: Document) -> None:
    for section in document["sections"]:
        section["lines"] = []


P33_CHANGES: dict[str, Callable[[Document], None]] = {
    # The sixteen changes of the matrix row.
    "missing_key": lambda d: d.pop("pypdf_version"),
    "extra_key": lambda d: d.update(extra=1),
    "true_as_page": _set_line_page("1.1", 0, True),
    "page_zero": _set_line_page("1.1", 0, 0),
    "page_six": _set_line_page("8", 0, 6),
    "decreasing_pages": _decreasing,
    "duplicate_section_id": _set_field("2", "section_id", "1"),
    "unknown_parent": _set_field("1.1", "parent_id", "9"),
    "level_mismatch": _set_field("1.1", "level", 1),
    "uppercase_sha256": _set_source("sha256", "A" * 64),
    "file_name_slash": _set_source("file_name", "dir/manual.pdf"),
    "line_newline": lambda d: _by_id(d, "1")["lines"][0].__setitem__(1, "a\nb"),
    "line_empty": lambda d: _by_id(d, "1")["lines"][0].__setitem__(1, ""),
    "title_null": _set_field("3", "title", None),
    "front_matter_not_first": _swap_front,
    "no_section_with_lines": _no_lines,
    # Additional strictness checks taken from spec P-05 (see the validation report).
    "missing_source_key": lambda d: d["source"].pop("page_count"),
    "extra_source_key": lambda d: d["source"].update(extra=1),
    "missing_section_key": lambda d: _by_id(d, "2").pop("heading_page"),
    "extra_section_key": _set_field("2", "pages", [2]),
    "extraction_rules_empty": lambda d: d.update(extraction_rules=""),
    "pypdf_version_number": lambda d: d.update(pypdf_version=6),
    "file_name_empty": _set_source("file_name", ""),
    "file_name_backslash": _set_source("file_name", "dir\\manual.pdf"),
    "file_name_colon": _set_source("file_name", "C:manual.pdf"),
    "sha256_short": _set_source("sha256", "a" * 63),
    "size_bytes_true": _set_source("size_bytes", True),
    "size_bytes_zero": _set_source("size_bytes", 0),
    "page_count_true": _set_source("page_count", True),
    "page_count_float": _set_source("page_count", 5.0),
    "level_true": _set_field("1", "level", True),
    "heading_page_true": _set_field("1", "heading_page", True),
    "heading_page_out_of_range": _set_field("8", "heading_page", 6),
    "heading_page_null": _set_field("8", "heading_page", None),
    "bad_section_id": _set_field("8", "section_id", "8."),
    "level1_with_parent": _set_field("2", "parent_id", "1"),
    "parent_not_prefix": _set_field("3.1", "parent_id", "2"),
    "parent_later": lambda d: d["sections"].append(d["sections"].pop(4)),
    "front_matter_title": _set_field("front_matter", "title", "Capa"),
    "front_matter_level": _set_field("front_matter", "level", 1),
    "title_empty": _set_field("3", "title", ""),
    "line_three_items": lambda d: _by_id(d, "1")["lines"][0].append("x"),
    "line_cr": lambda d: _by_id(d, "1")["lines"][0].__setitem__(1, "a\rb"),
    "line_text_number": lambda d: _by_id(d, "1")["lines"][0].__setitem__(1, 7),
    "lines_not_list": _set_field("1", "lines", "texto"),
    "sections_not_list": lambda d: d.update(sections={}),
    "section_not_object": lambda d: d["sections"].append([]),
}


@pytest.mark.parametrize("name", list(P33_CHANGES))
def test_p33_structure_rejected(policy_kit: Any, tmp_path: Path, name: str) -> None:
    path, document = _prepared(policy_kit, tmp_path)
    assert load_policy_index(path) is not None
    changed = copy.deepcopy(document)
    P33_CHANGES[name](changed)
    assert json.dumps(changed) != json.dumps(document)  # True == 1 and 5.0 == 5 in Python

    error = _load_error(_write(path, changed))

    assert error.code == "index_invalid"
    assert error.file_name == "index.json"


def test_p33_duplicate_json_key_and_non_finite_rejected(policy_kit: Any, tmp_path: Path) -> None:
    path, _ = _prepared(policy_kit, tmp_path)
    raw = path.read_bytes()

    path.write_bytes(raw.replace(b'"format_version": 1,', b'"format_version": 1, "format": "x",'))
    assert _load_error(path).code == "index_invalid"
    path.write_bytes(raw.replace(b'"heading_page": 2,', b'"heading_page": NaN,', 1))
    assert _load_error(path).code == "index_invalid"


def test_p33_rewritten_valid_document_still_loads(policy_kit: Any, tmp_path: Path) -> None:
    # Control for P33: the json round trip used by the cases above is not itself invalid.
    path, document = _prepared(policy_kit, tmp_path)

    assert load_policy_index(_write(path, document)).sections[0].section_id == "front_matter"


# --- P34-P35 ------------------------------------------------------------------------


def _forbidden(error: BaseException, tmp_path: Path) -> list[str]:
    surfaces = [str(error), repr(error), *map(str, error.args)]
    surfaces.extend("".join(traceback.format_exception_only(error)).splitlines())
    surfaces.extend(str(getattr(error, name)) for name in ("code", "file_name"))
    found = []
    for surface in surfaces:
        folded = surface.casefold()
        for word in ("sentinela", "pasta", str(tmp_path).casefold(), "ignore"):
            if word in folded:
                found.append(f"{word!r} in {surface!r}")
    return found


def test_p34_errors_hold_no_content_or_directory(
    policy_kit: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "Pasta Técnica Sentinela"
    root.mkdir()
    errors: list[PolicyIndexUnavailableError] = []

    def folder(name: str) -> Path:
        path = root / name
        path.mkdir()
        return path

    errors.append(_load_error(folder("missing") / "index.json"))
    (folder("directory") / "index.json").mkdir()
    errors.append(_load_error(root / "directory" / "index.json"))
    for name, raw in (
        ("json", f'{{"format": "{policy_kit.sentinel}"'.encode()),
        ("bytes", b"\xff\xfe" + policy_kit.sentinel.encode()),
        ("array", f'["{policy_kit.sentinel}"]'.encode()),
    ):
        path = folder(name) / "index.json"
        path.write_bytes(raw)
        errors.append(_load_error(path))
    changes: dict[str, Callable[[Document], None]] = {
        "version": lambda d: d.update(format_version=f"{policy_kit.sentinel}"),
        "format": lambda d: d.update(format=policy_kit.sentinel),
        **P33_CHANGES,
    }
    for name, change in changes.items():
        path, document = _prepared(policy_kit, folder(f"c-{name}"), edit="injection_title")
        assert policy_kit.sentinel.encode() in path.read_bytes()
        change(document)
        errors.append(_load_error(_write(path, document)))
    path, _ = _prepared(policy_kit, folder("size"), edit="injection_title")
    with monkeypatch.context() as patch:
        patch.setattr(pi, "MAX_INDEX_BYTES", 10)
        errors.append(_load_error(path))

    assert {error.code for error in errors} == {
        "index_missing",
        "index_unreadable",
        "index_invalid",
        "index_version_unsupported",
    }
    for error in errors:
        assert _forbidden(error, tmp_path) == []
        assert error.__cause__ is None
        assert error.__suppress_context__ is True
        assert error.args == (str(error),)
        assert error.file_name == "index.json"


def test_p34_message_format(tmp_path: Path) -> None:
    assert str(_load_error(tmp_path / "index.json")) == "index.json: index_missing"


def test_p35_index_size_limit(
    policy_kit: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, _ = _prepared(policy_kit, tmp_path)
    size = len(path.read_bytes())
    assert pi.MAX_INDEX_BYTES == 5 * 1024 * 1024

    with monkeypatch.context() as patch:
        patch.setattr(pi, "MAX_INDEX_BYTES", size - 1)
        assert _load_error(path).code == "index_invalid"
    with monkeypatch.context() as patch:
        patch.setattr(pi, "MAX_INDEX_BYTES", size)
        assert load_policy_index(path).source.page_count == 5
