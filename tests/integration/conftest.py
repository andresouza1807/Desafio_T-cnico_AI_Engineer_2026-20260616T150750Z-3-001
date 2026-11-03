"""Fixtures for the database preparation tests of task T5.

docs/specs/02-06-database-preparation.md. Every fixture name is specific to T5 so no
earlier test module is affected. Source files are small synthetic CSV files written
into pytest tmp_path. The sqlite3.connect and tempfile.mkstemp helpers either record
and forbid calls, or call the real function and install a real SQLite authorizer.
Nothing here simulates a transaction, a relation or persistence.
"""

import copy
import os
import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

PREFIX = "desafio_tecnico_ai_eng - "
TABLE_NAMES = ("categories", "customers", "products", "promotions", "orders", "order_items")

HEADERS = {
    "categories": ["category_id", "name", "description"],
    "customers": ["customer_id", "name", "phone", "email", "city"],
    "products": [
        "product_id",
        "category_id",
        "name",
        "description",
        "price_brl",
        "stock_quantity",
        "status",
        "specs",
        "created_at",
    ],
    "promotions": ["promotion_id", "product_id", "discount_percent", "description", "is_active"],
    "orders": [
        "order_id",
        "customer_id",
        "order_date",
        "status",
        "total_brl",
        "payment_method",
        "tracking_code",
        "estimated_delivery",
        "notes",
    ],
    # Same column order as the supplied source file; header order is free.
    "order_items": ["order_id", "quantity", "product_id"],
}

# Plan fixture "valid/". All values invented. P12 (discontinued) and P13 (coming_soon)
# have positive stock. Order 1000 total (650.00) differs on purpose from current
# price x quantity (599.90 + 2 x 19.90 = 639.70).
ROWS = {
    "categories": [
        ["1", "Cordas", "Violões, guitarras e baixos"],
        ["2", "Teclas", "Pianos digitais"],
    ],
    "customers": [
        ["1", "Cliente Teste Um", "(00) 90000-0001", "cliente01@example.invalid", "São Paulo"],
        ["2", "Cliente Teste Dois", "(00) 90000-0002", "cliente02@example.invalid", "Recife"],
    ],
    "products": [
        [
            "10",
            "1",
            "Violão Teste",
            "Tampo maciço, cordas de aço",
            "599.9",
            "3",
            "active",
            '{"cor": "natural"}',
            "2026-01-15",
        ],
        ["11", "1", "Encordoamento Teste", "Aço", "19.90", "0", "active", "{}", "2026-01-16"],
        [
            "12",
            "2",
            "Piano Teste",
            "88 teclas",
            "1200",
            "2",
            "discontinued",
            '{"teclas": "88"}',
            "2025-12-01",
        ],
        ["13", "2", "Teclado Teste", "61 teclas", "850.5", "1", "coming_soon", "{}", "2026-03-01"],
    ],
    "promotions": [
        ["100", "10", "10", "Promo Teste", "1"],
        ["101", "10", "40", "Black Friday Teste", "0"],
        ["102", "11", "12.5", "Campanha Teste", "1"],
    ],
    "orders": [
        ["1000", "1", "2026-01-10", "delivered", "650.00", "pix", "TRK0001", "2026-01-15", ""],
        [
            "1001",
            "2",
            "2026-02-01",
            "cancelled",
            "1200.00",
            "credit_3x",
            "",
            "",
            "Cancelado pelo cliente",
        ],
    ],
    "order_items": [
        ["1000", "1", "10"],
        ["1000", "2", "11"],
        ["1001", "1", "12"],
    ],
}


def file_name(table: str) -> str:
    return f"{PREFIX}{table}.csv"


def _csv_field(text: str) -> str:
    if any(char in text for char in ',"\r\n'):
        return '"' + text.replace('"', '""') + '"'
    return text


def csv_bytes(header: list[str], rows: list[list[str]]) -> bytes:
    lines = [",".join(_csv_field(cell) for cell in row) for row in [header, *rows]]
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


def valid_rows() -> dict[str, list[list[str]]]:
    return copy.deepcopy(ROWS)


def set_cell(rows, table: str, index: int, column: str, text: str) -> None:
    rows[table][index][HEADERS[table].index(column)] = text


def write_sources(directory: Path, rows=None) -> dict[str, bytes]:
    directory.mkdir(parents=True, exist_ok=True)
    rows = valid_rows() if rows is None else rows
    written = {}
    for table in TABLE_NAMES:
        data = csv_bytes(HEADERS[table], rows[table])
        (directory / file_name(table)).write_bytes(data)
        written[table] = data
    return written


@pytest.fixture
def prep_kit():
    """Constants and helpers for synthetic preparation sources."""
    return SimpleNamespace(
        PREFIX=PREFIX,
        TABLE_NAMES=TABLE_NAMES,
        HEADERS=HEADERS,
        file_name=file_name,
        csv_bytes=csv_bytes,
        valid_rows=valid_rows,
        set_cell=set_cell,
        write_sources=write_sources,
    )


@dataclass
class ForbiddenCalls:
    connect: list[tuple[object, ...]] = field(default_factory=list)
    mkstemp: list[dict[str, object]] = field(default_factory=list)


@pytest.fixture
def prep_forbid_build(monkeypatch):
    """Record and forbid sqlite3.connect and tempfile.mkstemp."""
    calls = ForbiddenCalls()

    def connect(*args, **kwargs):
        calls.connect.append(args)
        raise AssertionError("sqlite3.connect called before validation ended")

    def mkstemp(*args, **kwargs):
        calls.mkstemp.append(kwargs)
        raise AssertionError("tempfile.mkstemp called before validation ended")

    monkeypatch.setattr(sqlite3, "connect", connect)
    monkeypatch.setattr(tempfile, "mkstemp", mkstemp)
    return calls


@dataclass
class BuildSpy:
    target_dir: Path
    deny_insert_into: str | None = None
    connects: list[tuple[str, bool]] = field(default_factory=list)
    connect_kwargs: list[dict[str, object]] = field(default_factory=list)
    actions: list[tuple[str, str | None, str | None]] = field(default_factory=list)
    denied: int = 0


_ACTION_NAMES = {
    sqlite3.SQLITE_PRAGMA: "pragma",
    sqlite3.SQLITE_TRANSACTION: "transaction",
    sqlite3.SQLITE_INSERT: "insert",
}


@pytest.fixture
def prep_build_spy(monkeypatch):
    """Factory: wrap the real sqlite3.connect for temporary files of one directory.

    The wrapper calls the real function with the same arguments and installs a real
    authorizer that records PRAGMA, transaction and INSERT actions and, when
    deny_insert_into is set, returns SQLITE_DENY for INSERT on that table.
    The returned undo() restores sqlite3.connect.
    """
    real_connect = sqlite3.connect

    def install(target_dir: Path, *, deny_insert_into: str | None = None) -> BuildSpy:
        spy = BuildSpy(target_dir=target_dir, deny_insert_into=deny_insert_into)
        directory = os.path.normcase(os.path.abspath(target_dir))

        def authorizer(action, arg1, arg2, _database, _source):
            name = _ACTION_NAMES.get(action)
            if name is not None:
                spy.actions.append((name, arg1, arg2))
            if action == sqlite3.SQLITE_INSERT and arg1 == spy.deny_insert_into:
                spy.denied += 1
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        def connect(database, *args, **kwargs):
            path = os.fsdecode(database)
            existed = os.path.exists(path)
            connection = real_connect(database, *args, **kwargs)
            in_target = os.path.normcase(os.path.dirname(os.path.abspath(path))) == directory
            if in_target and path.endswith(".tmp"):
                spy.connects.append((path, existed))
                spy.connect_kwargs.append(dict(kwargs))
                connection.set_authorizer(authorizer)
            return connection

        monkeypatch.setattr(sqlite3, "connect", connect)
        return spy

    def undo() -> None:
        monkeypatch.setattr(sqlite3, "connect", real_connect)

    return SimpleNamespace(install=install, undo=undo, real_connect=real_connect)
