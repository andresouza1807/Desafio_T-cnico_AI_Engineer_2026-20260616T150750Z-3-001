"""Preparation, persistence and loading of the store policy index.

Slice 04, part A (docs/specs/04-policies.md, decisions P-01 to P-06, P-12 to P-15):
read one PDF given explicitly, extract its text with pypdf in layout mode, clean the
lines (rules N1-N5), split them into numbered sections located through the PDF outline,
and write one JSON index atomically. load_policy_index reads that index back with strict
validation and never opens the PDF.

The PDF and the index are untrusted data. Text is stored unchanged and never evaluated.
Errors carry a code and a location (base name, physical page, outline entry); they never
carry a directory, extracted text, an outline title or an original exception message.
"""

import hashlib
import io
import json
import os
import re
import tempfile
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal, NoReturn

import pypdf

MAX_SOURCE_BYTES: Final = 20 * 1024 * 1024
MAX_PAGES: Final = 100
MAX_INDEX_BYTES: Final = 5 * 1024 * 1024
INDEX_FORMAT: Final = "emporio-policy-index"
INDEX_FORMAT_VERSION: Final = 1
EXTRACTION_RULES: Final = "layout-v1"
FRONT_MATTER_ID: Final = "front_matter"

type SectionId = str
type PreparationErrorCode = Literal[
    "source_missing",
    "source_unreadable",
    "source_too_large",
    "not_pdf",
    "encrypted",
    "page_count_invalid",
    "extraction_failed",
    "page_text_missing",
    "outline_missing",
    "outline_invalid",
    "outline_mismatch",
    "no_retrievable_section",
    "target_directory_missing",
    "target_is_source",
    "write_failed",
]
type IndexErrorCode = Literal[
    "index_missing", "index_unreadable", "index_invalid", "index_version_unsupported"
]

_PREPARATION_CODES: Final = frozenset(PreparationErrorCode.__value__.__args__)
_INDEX_CODES: Final = frozenset(IndexErrorCode.__value__.__args__)

_FOOTER = re.compile(r"Página (\d+)")
_OUTLINE_TITLE = re.compile(r"(\d+(?:\.\d+)*)\.?\s+(\S.*)")
_NUMBERED_ID = re.compile(r"\d+(\.\d+)*")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_TOP_KEYS: Final = (
    "format",
    "format_version",
    "extraction_rules",
    "pypdf_version",
    "source",
    "sections",
)
_SOURCE_KEYS: Final = ("file_name", "sha256", "size_bytes", "page_count")
_SECTION_KEYS: Final = ("section_id", "level", "title", "parent_id", "heading_page", "lines")


# --- Types ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicySource:
    file_name: str
    sha256: str
    size_bytes: int
    page_count: int
    format_version: int
    extraction_rules: str
    pypdf_version: str


@dataclass(frozen=True)
class IndexedSection:
    section_id: SectionId
    level: int
    title: str | None
    parent_id: SectionId | None
    heading_page: int | None
    lines: tuple[tuple[int, str], ...]

    @property
    def pages(self) -> tuple[int, ...]:
        """Sorted unique physical pages of the heading and of every line."""
        pages = {page for page, _ in self.lines}
        if self.heading_page is not None:
            pages.add(self.heading_page)
        return tuple(sorted(pages))


@dataclass(frozen=True)
class PolicyIndex:
    source: PolicySource
    sections: tuple[IndexedSection, ...]  # document order


@dataclass(frozen=True)
class PolicyPreparationResult:
    index_path: Path  # outside the sanitization guarantee
    source: PolicySource
    section_count: int  # numbered sections plus front matter
    retrievable_count: int
    index_sha256: str


# --- Errors --------------------------------------------------------------------------


class PolicyPreparationError(Exception):
    """Preparation failed. str() is "<file_name>: page <n>: <code>", absent parts omitted.

    Outline problems are located by outline_entry (1-based, flattened depth-first) and
    page, never by title. args == (str(self),).
    """

    code: PreparationErrorCode
    file_name: str | None
    page: int | None
    outline_entry: int | None

    def __init__(
        self,
        code: PreparationErrorCode,
        *,
        file_name: str | None = None,
        page: int | None = None,
        outline_entry: int | None = None,
    ) -> None:
        if code not in _PREPARATION_CODES:
            raise ValueError("unknown preparation error code")
        self.code = code
        self.file_name = file_name
        self.page = page
        self.outline_entry = outline_entry
        parts = [file_name] if file_name else []
        if page is not None:
            parts.append(f"page {page}")
        parts.append(code)
        super().__init__(": ".join(parts))


class PolicyIndexUnavailableError(Exception):
    """The index cannot be used. str() is "<file_name>: <code>"; args == (str(self),)."""

    code: IndexErrorCode
    file_name: str | None

    def __init__(self, code: IndexErrorCode, *, file_name: str | None = None) -> None:
        if code not in _INDEX_CODES:
            raise ValueError("unknown index error code")
        self.code = code
        self.file_name = file_name
        super().__init__(f"{file_name}: {code}" if file_name else code)


# --- Cleaning (P-01) -------------------------------------------------------------------


def _compact(text: str) -> str:
    """N2 then N3: compact whitespace, then Unicode NFC."""
    return unicodedata.normalize("NFC", " ".join(text.split()))


def clean_page_lines(raw_pages: Sequence[str]) -> tuple[tuple[str, ...], ...]:
    """Apply N1-N5 in order to the layout text of each page (index 0 is page 1)."""
    pages = [
        [_compact(line) for line in raw.splitlines() if line.strip() != ""]  # N1, N2, N3
        for raw in raw_pages
    ]
    first_counts: dict[str, int] = {}
    for lines in pages:
        if lines:
            first_counts[lines[0]] = first_counts.get(lines[0], 0) + 1
    for lines in pages:  # N4
        if lines and first_counts[lines[0]] >= 2:
            del lines[0]
    for number, lines in enumerate(pages, start=1):  # N5
        if lines:
            match = _FOOTER.fullmatch(lines[-1])
            if match is not None and int(match.group(1)) == number:
                del lines[-1]
    return tuple(tuple(lines) for lines in pages)


# --- Extraction (P-02 to P-04) --------------------------------------------------------


@dataclass(frozen=True)
class _OutlineEntry:
    depth: int
    title: object
    page: int | None  # 1-based, None when the destination does not resolve
    parent: int | None  # position of the parent entry in the flattened list


def _read_source(source_pdf: Path) -> bytes:
    file_name = source_pdf.name
    try:
        with open(source_pdf, "rb") as handle:
            data = handle.read(MAX_SOURCE_BYTES + 1)
    except FileNotFoundError:
        raise PolicyPreparationError("source_missing", file_name=file_name) from None
    except OSError, ValueError:
        raise PolicyPreparationError("source_unreadable", file_name=file_name) from None
    if len(data) > MAX_SOURCE_BYTES:
        raise PolicyPreparationError("source_too_large", file_name=file_name) from None
    return data


def _flatten_outline(reader: pypdf.PdfReader, page_count: int) -> list[_OutlineEntry]:
    entries: list[_OutlineEntry] = []

    def page_of(item: Any) -> int | None:
        try:
            number = reader.get_destination_page_number(item)
        except Exception:
            return None
        if type(number) is int and 0 <= number < page_count:
            return number + 1
        return None

    def walk(items: Sequence[Any], depth: int, parent: int | None) -> None:
        previous: int | None = None
        for item in items:
            if isinstance(item, list):
                walk(item, depth + 1, previous)
                continue
            entries.append(
                _OutlineEntry(depth, getattr(item, "title", None), page_of(item), parent)
            )
            previous = len(entries) - 1

    walk(reader.outline, 1, None)
    return entries


def _parse_outline(
    entries: list[_OutlineEntry], file_name: str
) -> list[tuple[str, str, str, int, str | None]]:
    """Validate every entry; return (section_id, title, heading text, page, parent_id)."""
    parsed: list[tuple[str, str, str, int, str | None]] = []
    seen: set[str] = set()
    for position, entry in enumerate(entries):

        def invalid(entry: _OutlineEntry = entry, position: int = position) -> NoReturn:
            raise PolicyPreparationError(
                "outline_invalid",
                file_name=file_name,
                page=entry.page,
                outline_entry=position + 1,
            ) from None

        if not isinstance(entry.title, str):
            invalid()
        heading = _compact(str(entry.title))
        match = _OUTLINE_TITLE.fullmatch(heading)
        if match is None:
            invalid()
        section_id, title = match.group(1), match.group(2)
        if section_id in seen or entry.depth != section_id.count(".") + 1:
            invalid()
        parent_id: str | None = None
        if entry.depth > 1:
            if entry.parent is None:
                invalid()
            parent_id = parsed[entry.parent][0]
            if not section_id.startswith(f"{parent_id}."):
                invalid()
        if entry.page is None:
            invalid()
        seen.add(section_id)
        parsed.append((section_id, title, heading, entry.page, parent_id))
    return parsed


def _extract(data: bytes, file_name: str) -> PolicyIndex:
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        encrypted = reader.is_encrypted
    except Exception:
        raise PolicyPreparationError("not_pdf", file_name=file_name) from None
    if encrypted:
        raise PolicyPreparationError("encrypted", file_name=file_name) from None
    try:
        page_count = len(reader.pages)
    except Exception:
        raise PolicyPreparationError("not_pdf", file_name=file_name) from None
    if page_count == 0 or page_count > MAX_PAGES:
        raise PolicyPreparationError("page_count_invalid", file_name=file_name) from None

    raw_pages: list[str] = []
    for number in range(1, page_count + 1):
        try:
            raw_pages.append(reader.pages[number - 1].extract_text(extraction_mode="layout"))
        except Exception:
            raise PolicyPreparationError(
                "extraction_failed", file_name=file_name, page=number
            ) from None
    pages = clean_page_lines(raw_pages)
    for number, lines in enumerate(pages, start=1):
        if not lines:
            raise PolicyPreparationError(
                "page_text_missing", file_name=file_name, page=number
            ) from None

    try:
        entries = _flatten_outline(reader, page_count)
    except Exception:
        raise PolicyPreparationError("outline_invalid", file_name=file_name) from None
    if not entries:
        raise PolicyPreparationError("outline_missing", file_name=file_name) from None
    outline = _parse_outline(entries, file_name)

    stream = [(number, text) for number, lines in enumerate(pages, start=1) for text in lines]
    positions: list[int] = []
    cursor = 0
    for position, (_, _, heading, page, _) in enumerate(outline):
        found = next(
            (
                index
                for index in range(cursor, len(stream))
                if stream[index][0] == page and stream[index][1] == heading
            ),
            None,
        )
        if found is None:
            raise PolicyPreparationError(
                "outline_mismatch", file_name=file_name, page=page, outline_entry=position + 1
            ) from None
        positions.append(found)
        cursor = found + 1

    sections: list[IndexedSection] = []
    if positions[0] > 0:
        sections.append(
            IndexedSection(FRONT_MATTER_ID, 0, None, None, None, tuple(stream[: positions[0]]))
        )
    ends = [*positions[1:], len(stream)]
    for (section_id, title, _, page, parent_id), start, end in zip(
        outline, positions, ends, strict=True
    ):
        sections.append(
            IndexedSection(
                section_id,
                section_id.count(".") + 1,
                title,
                parent_id,
                page,
                tuple(stream[start + 1 : end]),
            )
        )
    if not any(section.lines for section in sections):
        raise PolicyPreparationError("no_retrievable_section", file_name=file_name) from None

    source = PolicySource(
        file_name=file_name,
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        page_count=page_count,
        format_version=INDEX_FORMAT_VERSION,
        extraction_rules=EXTRACTION_RULES,
        pypdf_version=pypdf.__version__,
    )
    return PolicyIndex(source=source, sections=tuple(sections))


def extract_policy_index(source_pdf: Path) -> PolicyIndex:
    """Read source_pdf once and build the index in memory. Writes nothing."""
    source_pdf = Path(source_pdf)
    return _extract(_read_source(source_pdf), source_pdf.name)


# --- Persistence (P-05, P-06) ---------------------------------------------------------


def _encode(index: PolicyIndex) -> bytes:
    document = {
        "format": INDEX_FORMAT,
        "format_version": index.source.format_version,
        "extraction_rules": index.source.extraction_rules,
        "pypdf_version": index.source.pypdf_version,
        "source": {
            "file_name": index.source.file_name,
            "sha256": index.source.sha256,
            "size_bytes": index.source.size_bytes,
            "page_count": index.source.page_count,
        },
        "sections": [
            {
                "section_id": section.section_id,
                "level": section.level,
                "title": section.title,
                "parent_id": section.parent_id,
                "heading_page": section.heading_page,
                "lines": [[page, text] for page, text in section.lines],
            }
            for section in index.sections
        ],
    }
    return (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _is_same_file(source_pdf: Path, index_path: Path) -> bool:
    try:
        if source_pdf.exists() and index_path.exists():
            return os.path.samefile(source_pdf, index_path)
        resolved = (os.path.normcase(str(path.resolve())) for path in (source_pdf, index_path))
        return next(resolved) == next(resolved)
    except OSError, ValueError:
        return False


def _remove_quietly(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _write_atomic(index_path: Path, payload: bytes) -> None:
    file_name = index_path.name
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{file_name}.", suffix=".tmp", dir=index_path.parent
        )
    except OSError:
        raise PolicyPreparationError("write_failed", file_name=file_name) from None
    try:
        try:
            handle = os.fdopen(descriptor, "wb")
        except BaseException:
            os.close(descriptor)
            raise
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, index_path)
    except OSError:
        _remove_quietly(temporary)
        raise PolicyPreparationError("write_failed", file_name=file_name) from None
    except BaseException:
        _remove_quietly(temporary)
        raise


def prepare_policy_index(source_pdf: Path, index_path: Path) -> PolicyPreparationResult:
    """Extract source_pdf and publish the JSON index at index_path atomically.

    Order: target directory, target not the source, source read once, extraction and
    validation in memory (including the load validation of the produced bytes and the
    MAX_INDEX_BYTES limit; a failure raises PolicyIndexUnavailableError("index_invalid")
    before any temporary file exists), then a temporary file in the target directory,
    flush, fsync, close and os.replace. On a handled failure the previous target is
    unchanged, or absent on a first build.
    Durability after an OS crash or power loss is not guaranteed.
    """
    source_pdf = Path(source_pdf)
    index_path = Path(index_path)
    if not index_path.parent.is_dir():
        raise PolicyPreparationError(
            "target_directory_missing", file_name=index_path.name
        ) from None
    if _is_same_file(source_pdf, index_path):
        raise PolicyPreparationError("target_is_source", file_name=index_path.name) from None
    index = _extract(_read_source(source_pdf), source_pdf.name)
    payload = _encode(index)
    _decode(payload, index_path.name)  # size limit and load validation, before any write
    _write_atomic(index_path, payload)
    return PolicyPreparationResult(
        index_path=index_path,
        source=index.source,
        section_count=len(index.sections),
        retrievable_count=sum(1 for section in index.sections if section.lines),
        index_sha256=hashlib.sha256(payload).hexdigest(),
    )


# --- Loading and validation (P-05) ----------------------------------------------------


class _Invalid(Exception):
    pass


class _Unsupported(Exception):
    pass


def _require(condition: bool) -> None:
    if not condition:
        raise _Invalid


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject_constant(_: str) -> NoReturn:
    raise ValueError("non-finite number")


def _is_int(value: object) -> bool:
    return type(value) is int


def _is_text(value: object) -> bool:
    return type(value) is str and value != ""


def _exact_keys(value: object, keys: tuple[str, ...]) -> dict[str, Any]:
    _require(type(value) is dict and set(value) == set(keys))
    assert isinstance(value, dict)
    return value


def _source(document: dict[str, Any]) -> PolicySource:
    source = _exact_keys(document["source"], _SOURCE_KEYS)
    file_name = source["file_name"]
    _require(_is_text(file_name) and not any(char in file_name for char in "/\\:"))
    _require(type(source["sha256"]) is str and _SHA256.fullmatch(source["sha256"]) is not None)
    for key in ("size_bytes", "page_count"):
        _require(_is_int(source[key]) and source[key] >= 1)
    _require(_is_text(document["extraction_rules"]) and _is_text(document["pypdf_version"]))
    return PolicySource(
        file_name=file_name,
        sha256=source["sha256"],
        size_bytes=source["size_bytes"],
        page_count=source["page_count"],
        format_version=document["format_version"],
        extraction_rules=document["extraction_rules"],
        pypdf_version=document["pypdf_version"],
    )


def _lines(value: object, page_count: int) -> tuple[tuple[int, str], ...]:
    _require(type(value) is list)
    assert isinstance(value, list)
    lines: list[tuple[int, str]] = []
    previous = 1
    for line in value:
        _require(type(line) is list and len(line) == 2)
        page, text = line
        _require(_is_int(page) and previous <= page <= page_count)
        _require(_is_text(text) and "\n" not in text and "\r" not in text)
        lines.append((page, text))
        previous = page
    return tuple(lines)


def _sections(value: object, page_count: int) -> tuple[IndexedSection, ...]:
    _require(type(value) is list)
    assert isinstance(value, list)
    levels: dict[str, int] = {}
    sections: list[IndexedSection] = []
    for position, item in enumerate(value):
        section = _exact_keys(item, _SECTION_KEYS)
        section_id, level = section["section_id"], section["level"]
        title, parent_id, heading_page = (
            section["title"],
            section["parent_id"],
            section["heading_page"],
        )
        _require(type(section_id) is str and section_id not in levels)
        if section_id == FRONT_MATTER_ID:
            _require(position == 0 and _is_int(level) and level == 0)
            _require(title is None and parent_id is None and heading_page is None)
        else:
            _require(_NUMBERED_ID.fullmatch(section_id) is not None)
            _require(_is_int(level) and level == section_id.count(".") + 1)
            _require(_is_text(title))
            _require(_is_int(heading_page) and 1 <= heading_page <= page_count)
            if level == 1:
                _require(parent_id is None)
            else:
                _require(type(parent_id) is str and levels.get(parent_id) == level - 1)
                _require(section_id.startswith(f"{parent_id}."))
        levels[section_id] = level
        sections.append(
            IndexedSection(
                section_id,
                level,
                title,
                parent_id,
                heading_page,
                _lines(section["lines"], page_count),
            )
        )
    _require(any(section.lines for section in sections))
    return tuple(sections)


def _validate(document: object) -> PolicyIndex:
    top = _exact_keys(document, _TOP_KEYS)
    _require(type(top["format"]) is str and top["format"] == INDEX_FORMAT)
    version = top["format_version"]
    if not (_is_int(version) and version == INDEX_FORMAT_VERSION):
        raise _Unsupported
    source = _source(top)
    return PolicyIndex(source=source, sections=_sections(top["sections"], source.page_count))


def _decode(data: bytes, file_name: str) -> PolicyIndex:
    """Load validation of P-05 on raw index bytes, shared by load and prepare.

    The byte limit comes first, so prepare never publishes a file the loader rejects.
    """
    if len(data) > MAX_INDEX_BYTES:
        raise PolicyIndexUnavailableError("index_invalid", file_name=file_name) from None
    try:
        document = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_unique_keys,
            parse_constant=_reject_constant,
        )
        return _validate(document)
    except _Unsupported:
        raise PolicyIndexUnavailableError(
            "index_version_unsupported", file_name=file_name
        ) from None
    except _Invalid, ValueError, RecursionError:
        raise PolicyIndexUnavailableError("index_invalid", file_name=file_name) from None


def load_policy_index(index_path: Path) -> PolicyIndex:
    """Read and strictly validate the JSON index. Never opens the PDF."""
    index_path = Path(index_path)
    file_name = index_path.name
    try:
        with open(index_path, "rb") as handle:
            data = handle.read(MAX_INDEX_BYTES + 1)
    except FileNotFoundError:
        raise PolicyIndexUnavailableError("index_missing", file_name=file_name) from None
    except OSError, ValueError:
        raise PolicyIndexUnavailableError("index_unreadable", file_name=file_name) from None
    return _decode(data, file_name)
