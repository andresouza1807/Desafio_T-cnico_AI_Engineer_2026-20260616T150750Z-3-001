"""Slice 08-C: customer existence check (docs/specs/08-cli.md G7, D-03; plan CL53).

Real prepare_database over a copy of tests/fixtures/data/valid (customers 1 and 2), real
connect_readonly. The oracle is the fixture: customers 1 and 2 exist, nothing else does.
"""

import shutil
import sqlite3
from pathlib import Path

import pytest

from emporio_agent.data.preparation import prepare_database
from emporio_agent.data.readonly import connect_readonly
from emporio_agent.services.orders import CustomerContext, customer_exists

FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "data" / "valid"


@pytest.fixture
def database(tmp_path: Path) -> Path:
    source = tmp_path / "src"
    shutil.copytree(FIXTURE, source)
    target = tmp_path / "db"
    target.mkdir()
    path = target / "emporio.sqlite3"
    prepare_database(source, path)
    return path


@pytest.mark.parametrize(("customer_id", "expected"), [(1, True), (2, True), (3, False)])
def test_cl53_existence_follows_the_prepared_customers(database, customer_id, expected):
    with connect_readonly(database) as connection:
        assert customer_exists(connection, CustomerContext(customer_id)) is expected


def test_cl53_largest_identifier_is_not_found(database):
    with connect_readonly(database) as connection:
        assert customer_exists(connection, CustomerContext(2**63 - 1)) is False


@pytest.mark.parametrize("customer", [1, None, "1", True])
def test_cl53_rejects_anything_but_customer_context(database, customer):
    with connect_readonly(database) as connection:
        with pytest.raises(TypeError):
            customer_exists(connection, customer)


def test_cl53_check_leaves_the_connection_read_only_and_returns_a_plain_bool(database):
    before = database.read_bytes()
    with connect_readonly(database) as connection:
        result = customer_exists(connection, CustomerContext(1))
        assert type(result) is bool
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("DELETE FROM customers")
    assert database.read_bytes() == before


def test_cl53_statement_binds_the_identifier_and_selects_no_customer_field(database):
    statements: list[str] = []
    with connect_readonly(database) as connection:
        connection.set_trace_callback(statements.append)
        customer_exists(connection, CustomerContext(2))
        connection.set_trace_callback(None)
    (statement,) = statements
    assert statement.split("FROM")[0].strip() == "SELECT 1"
    for column in ("name", "phone", "email", "city"):
        assert column not in statement
