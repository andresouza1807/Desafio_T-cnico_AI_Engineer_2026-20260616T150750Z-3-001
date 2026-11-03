"""Schema tests for task T2 (docs/specs/02-03-data-schema.md).

Every test uses a real SQLite file under pytest tmp_path and synthetic values written
here. Expected values are copied by hand from the specification, not read from the
module under test. The test controls PRAGMA settings and transactions itself, as the
future preparation code will.
"""

import os
import sqlite3
import subprocess
import sys
from importlib import import_module
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parents[2] / "src"

DOMAIN_TABLES = ("categories", "customers", "products", "promotions", "orders", "order_items")
AUXILIARY_TABLES = ("preparation_metadata", "source_files", "data_quality_notes")
ALL_TABLES = DOMAIN_TABLES + AUXILIARY_TABLES

PRODUCT_STATUSES = frozenset({"active", "discontinued", "coming_soon"})
ORDER_STATUSES = frozenset({"pending", "confirmed", "shipped", "delivered", "cancelled"})
PAYMENT_METHODS = frozenset(
    {"pix", "debit", "boleto"} | {f"credit_{count}x" for count in range(1, 13)}
)
QUALITY_NOTE_CODES = frozenset({"status_stock_conflict", "catalog_text_conflict"})
KNOWN_CATALOG_TEXT_CONFLICTS = frozenset({135, 137, 139, 140, 142, 144})
MAX_INTEGER = 9223372036854775807
SHA256_ZERO = "0" * 64

# (name, declared type, not null, primary key position), in table order.
EXPECTED_COLUMNS = {
    "categories": [
        ("category_id", "INTEGER", 1, 1),
        ("name", "TEXT", 1, 0),
        ("description", "TEXT", 1, 0),
    ],
    "customers": [
        ("customer_id", "INTEGER", 1, 1),
        ("name", "TEXT", 1, 0),
        ("phone", "TEXT", 1, 0),
        ("email", "TEXT", 1, 0),
        ("city", "TEXT", 1, 0),
    ],
    "products": [
        ("product_id", "INTEGER", 1, 1),
        ("category_id", "INTEGER", 1, 0),
        ("name", "TEXT", 1, 0),
        ("description", "TEXT", 1, 0),
        ("price_cents", "INTEGER", 1, 0),
        ("stock_quantity", "INTEGER", 1, 0),
        ("status", "TEXT", 1, 0),
        ("specs_json", "TEXT", 1, 0),
        ("created_at", "TEXT", 1, 0),
    ],
    "promotions": [
        ("promotion_id", "INTEGER", 1, 1),
        ("product_id", "INTEGER", 1, 0),
        ("discount_basis_points", "INTEGER", 1, 0),
        ("description", "TEXT", 1, 0),
        ("is_active", "INTEGER", 1, 0),
    ],
    "orders": [
        ("order_id", "INTEGER", 1, 1),
        ("customer_id", "INTEGER", 1, 0),
        ("order_date", "TEXT", 1, 0),
        ("status", "TEXT", 1, 0),
        ("total_cents", "INTEGER", 1, 0),
        ("payment_method", "TEXT", 1, 0),
        ("tracking_code", "TEXT", 0, 0),
        ("estimated_delivery", "TEXT", 0, 0),
        ("notes", "TEXT", 0, 0),
    ],
    "order_items": [
        ("order_id", "INTEGER", 1, 1),
        ("product_id", "INTEGER", 1, 2),
        ("quantity", "INTEGER", 1, 0),
    ],
    "preparation_metadata": [
        ("key", "TEXT", 1, 1),
        ("value", "TEXT", 1, 0),
    ],
    "source_files": [
        ("table_name", "TEXT", 1, 1),
        ("file_name", "TEXT", 1, 0),
        ("sha256", "TEXT", 1, 0),
        ("size_bytes", "INTEGER", 1, 0),
        ("row_count", "INTEGER", 1, 0),
    ],
    "data_quality_notes": [
        ("entity", "TEXT", 1, 1),
        ("entity_id", "INTEGER", 1, 2),
        ("code", "TEXT", 1, 3),
    ],
}

# (from column, parent table, parent column, on update, on delete).
EXPECTED_FOREIGN_KEYS = {
    "categories": set(),
    "customers": set(),
    "products": {("category_id", "categories", "category_id", "NO ACTION", "NO ACTION")},
    "promotions": {("product_id", "products", "product_id", "NO ACTION", "NO ACTION")},
    "orders": {("customer_id", "customers", "customer_id", "NO ACTION", "NO ACTION")},
    "order_items": {
        ("order_id", "orders", "order_id", "NO ACTION", "NO ACTION"),
        ("product_id", "products", "product_id", "NO ACTION", "NO ACTION"),
    },
    "preparation_metadata": set(),
    "source_files": set(),
    "data_quality_notes": set(),
}

# (table, source name, column name, kind, allowlist), in header-definition order.
EXPECTED_METADATA = {
    "categories": [
        ("category_id", "category_id", "id", None),
        ("name", "name", "text", None),
        ("description", "description", "text", None),
    ],
    "customers": [
        ("customer_id", "customer_id", "id", None),
        ("name", "name", "text", None),
        ("phone", "phone", "text", None),
        ("email", "email", "text", None),
        ("city", "city", "text", None),
    ],
    "products": [
        ("product_id", "product_id", "id", None),
        ("category_id", "category_id", "id", None),
        ("name", "name", "text", None),
        ("description", "description", "text", None),
        ("price_brl", "price_cents", "money", None),
        ("stock_quantity", "stock_quantity", "count", None),
        ("status", "status", "enum", PRODUCT_STATUSES),
        ("specs", "specs_json", "json_object", None),
        ("created_at", "created_at", "date", None),
    ],
    "promotions": [
        ("promotion_id", "promotion_id", "id", None),
        ("product_id", "product_id", "id", None),
        ("discount_percent", "discount_basis_points", "percent", None),
        ("description", "description", "text", None),
        ("is_active", "is_active", "flag", None),
    ],
    "orders": [
        ("order_id", "order_id", "id", None),
        ("customer_id", "customer_id", "id", None),
        ("order_date", "order_date", "date", None),
        ("status", "status", "enum", ORDER_STATUSES),
        ("total_brl", "total_cents", "money", None),
        ("payment_method", "payment_method", "enum", PAYMENT_METHODS),
        ("tracking_code", "tracking_code", "optional_text", None),
        ("estimated_delivery", "estimated_delivery", "optional_date", None),
        ("notes", "notes", "optional_text", None),
    ],
    "order_items": [
        ("order_id", "order_id", "id", None),
        ("product_id", "product_id", "id", None),
        ("quantity", "quantity", "quantity", None),
    ],
}
EXPECTED_KEYS = {
    "categories": (("category_id",), ("name",)),
    "customers": (("customer_id",), ()),
    "products": (("product_id",), ()),
    "promotions": (("promotion_id",), ()),
    "orders": (("order_id",), ()),
    "order_items": (("order_id", "product_id"), ()),
}
EXPECTED_REFERENCES = {
    "categories": set(),
    "customers": set(),
    "products": {("category_id", "categories")},
    "promotions": {("product_id", "products")},
    "orders": {("customer_id", "customers")},
    "order_items": {("order_id", "orders"), ("product_id", "products")},
}

# Synthetic rows. Contacts use example.invalid and the (00) area code.
BASE_ROWS = {
    "categories": [
        {"category_id": 1, "name": "Instrumentos", "description": "Cordas, sopro e percussão"},
    ],
    "customers": [
        {
            "customer_id": 1,
            "name": "Cliente Teste Um",
            "phone": "(00) 90000-0001",
            "email": "compartilhado@example.invalid",
            "city": "São Paulo",
        },
    ],
    "products": [
        {
            "product_id": 10,
            "category_id": 1,
            "name": "Violão Teste",
            "description": "Tampo maciço, braço de cedro",
            "price_cents": 59990,
            "stock_quantity": 3,
            "status": "active",
            "specs_json": '{"cordas": "nylon", "tampo": "abeto"}',
            "created_at": "2026-01-15",
        },
    ],
    "promotions": [
        {
            "promotion_id": 20,
            "product_id": 10,
            "discount_basis_points": 1000,
            "description": "Promoção Teste",
            "is_active": 1,
        },
    ],
    "orders": [
        {
            "order_id": 1,
            "customer_id": 1,
            "order_date": "2026-03-01",
            "status": "delivered",
            "total_cents": 50000,
            "payment_method": "pix",
            "tracking_code": "TRK-0001",
            "estimated_delivery": "2026-03-10",
            "notes": None,
        },
        {
            "order_id": 2,
            "customer_id": 1,
            "order_date": "2026-03-05",
            "status": "cancelled",
            "total_cents": 1990,
            "payment_method": "credit_12x",
            "tracking_code": None,
            "estimated_delivery": None,
            "notes": "Cancelado a pedido",
        },
    ],
    "order_items": [
        {"order_id": 1, "product_id": 10, "quantity": 1},
    ],
}

# One valid new row per table; its keys do not collide with BASE_ROWS.
NEW_ROWS = {
    "categories": {"category_id": 2, "name": "Acessórios", "description": "Cabos"},
    "customers": {
        "customer_id": 2,
        "name": "Cliente Teste Dois",
        "phone": "(00) 90000-0002",
        "email": "cliente02@example.invalid",
        "city": "Recife",
    },
    "products": {
        "product_id": 11,
        "category_id": 1,
        "name": "Cabo Teste",
        "description": "Cabo P10, 3 m",
        "price_cents": 1990,
        "stock_quantity": 0,
        "status": "active",
        "specs_json": '{"comprimento": "3 m"}',
        "created_at": "2026-02-28",
    },
    "promotions": {
        "promotion_id": 21,
        "product_id": 10,
        "discount_basis_points": 4000,
        "description": "Campanha Teste",
        "is_active": 0,
    },
    "orders": {
        "order_id": 3,
        "customer_id": 1,
        "order_date": "2026-04-01",
        "status": "pending",
        "total_cents": 0,
        "payment_method": "boleto",
        "tracking_code": None,
        "estimated_delivery": None,
        "notes": None,
    },
    "order_items": {"order_id": 2, "product_id": 10, "quantity": 2},
}

AUXILIARY_ROWS = {
    "preparation_metadata": [
        {"key": "schema_version", "value": "1"},
        {"key": "prepared_at", "value": "2026-11-02T12:00:00+00:00"},
    ],
    "source_files": [
        {
            "table_name": "categories",
            "file_name": "desafio_tecnico_ai_eng - categories.csv",
            "sha256": "0123456789abcdef" * 4,
            "size_bytes": 120,
            "row_count": 2,
        },
    ],
    "data_quality_notes": [
        {"entity": "product", "entity_id": 10, "code": "status_stock_conflict"},
        {"entity": "product", "entity_id": 10, "code": "catalog_text_conflict"},
    ],
}


def schema():
    return import_module("emporio_agent.data.schema")


def connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, autocommit=True)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def insert(connection: sqlite3.Connection, table: str, row: dict[str, object]) -> None:
    columns = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    connection.execute(f"INSERT INTO {table} ({columns}) VALUES ({marks})", tuple(row.values()))


def select_all(connection: sqlite3.Connection, table: str) -> list[dict[str, object]]:
    cursor = connection.execute(f"SELECT * FROM {table} ORDER BY rowid")
    names = [description[0] for description in cursor.description]
    return [dict(zip(names, values, strict=True)) for values in cursor.fetchall()]


def user_tables(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_schema WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return sorted(row[0] for row in rows)


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    return tmp_path / "schema.sqlite3"


@pytest.fixture
def empty_db(database_path: Path):
    connection = connect(database_path)
    for statement in schema().DDL_STATEMENTS:
        connection.execute(statement)
    yield connection
    connection.close()


@pytest.fixture
def db(empty_db: sqlite3.Connection) -> sqlite3.Connection:
    for table in DOMAIN_TABLES:
        for row in BASE_ROWS[table]:
            insert(empty_db, table, row)
    return empty_db


# Constants and module contract.


def test_schema_public_constants():
    module = schema()
    assert module.SCHEMA_VERSION == 1
    assert module.SOURCE_FILE_PREFIX == "desafio_tecnico_ai_eng - "
    assert module.TABLE_ORDER == DOMAIN_TABLES
    assert module.AUXILIARY_TABLES == AUXILIARY_TABLES
    assert module.PRODUCT_STATUSES == PRODUCT_STATUSES
    assert module.ORDER_STATUSES == ORDER_STATUSES
    assert module.PAYMENT_METHODS == PAYMENT_METHODS
    assert len(module.PAYMENT_METHODS) == 15
    assert module.QUALITY_ENTITY_PRODUCT == "product"
    assert module.STATUS_STOCK_CONFLICT == "status_stock_conflict"
    assert module.CATALOG_TEXT_CONFLICT == "catalog_text_conflict"
    assert module.QUALITY_NOTE_CODES == QUALITY_NOTE_CODES
    assert module.KNOWN_CATALOG_TEXT_CONFLICTS == KNOWN_CATALOG_TEXT_CONFLICTS


def test_schema_collections_are_immutable():
    module = schema()
    for name in (
        "PRODUCT_STATUSES",
        "ORDER_STATUSES",
        "PAYMENT_METHODS",
        "QUALITY_NOTE_CODES",
        "KNOWN_CATALOG_TEXT_CONFLICTS",
    ):
        assert type(getattr(module, name)) is frozenset, name
    for name in ("TABLE_ORDER", "AUXILIARY_TABLES", "TABLES", "DDL_STATEMENTS"):
        assert type(getattr(module, name)) is tuple, name
    for table in module.TABLES:
        assert type(table.columns) is tuple
        with pytest.raises(AttributeError):
            table.name = "changed"
        with pytest.raises(AttributeError):
            table.columns[0].kind = "text"


def test_schema_reuses_sqlite_max_integer_from_parsing():
    assert not hasattr(schema(), "SQLITE_MAX_INTEGER")
    assert import_module("emporio_agent.data.parsing").SQLITE_MAX_INTEGER == MAX_INTEGER


def test_schema_import_has_no_side_effects(tmp_path: Path):
    probe = (
        "import sys\n"
        "before = set(sys.modules)\n"
        "import emporio_agent.data.schema\n"
        "print('sqlite3' in sys.modules)\n"
    )
    env = {**os.environ, "PYTHONPATH": str(SRC_DIR)}
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "False"
    assert list(tmp_path.iterdir()) == []


def test_schema_ddl_is_a_sequence_of_single_create_statements():
    statements = schema().DDL_STATEMENTS
    assert len(statements) == 9
    for statement in statements:
        assert type(statement) is str
        # complete_statement needs the terminating semicolon that execute() does not.
        assert sqlite3.complete_statement(statement + ";")
        normalized = " ".join(statement.split()).upper()
        assert normalized.startswith("CREATE TABLE ")
        assert normalized.count("CREATE ") == 1
        assert normalized.rstrip(";").endswith("STRICT")
        for forbidden in ("IF NOT EXISTS", "BEGIN", "COMMIT", "ROLLBACK", "PRAGMA", "DROP "):
            assert forbidden not in normalized


def test_schema_ddl_follows_import_order_then_auxiliary_tables(empty_db):
    created = [
        row[0]
        for row in empty_db.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'table' ORDER BY rowid"
        ).fetchall()
    ]
    assert created == list(ALL_TABLES)


def test_schema_ddl_cannot_run_twice(empty_db):
    with pytest.raises(sqlite3.OperationalError, match="already exists"):
        empty_db.execute(schema().DDL_STATEMENTS[0])


# Structure.


def test_schema_creates_nine_strict_tables(empty_db):
    rows = empty_db.execute(
        "SELECT name, type, strict FROM pragma_table_list WHERE schema = 'main' "
        "AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    assert sorted(rows) == sorted((table, "table", 1) for table in ALL_TABLES)


@pytest.mark.parametrize("table", ALL_TABLES)
def test_schema_columns_types_nullability_and_keys(empty_db, table):
    rows = empty_db.execute(
        'SELECT name, type, "notnull", pk FROM pragma_table_info(?) ORDER BY cid', (table,)
    ).fetchall()
    assert rows == EXPECTED_COLUMNS[table]


@pytest.mark.parametrize("table", ALL_TABLES)
def test_schema_foreign_keys(empty_db, table):
    rows = empty_db.execute(
        'SELECT "from", "table", "to", on_update, on_delete FROM pragma_foreign_key_list(?)',
        (table,),
    ).fetchall()
    assert set(rows) == EXPECTED_FOREIGN_KEYS[table]


def test_schema_unique_index_only_on_category_name(empty_db):
    unique_columns = set()
    for table in ALL_TABLES:
        for index_name, origin in empty_db.execute(
            'SELECT name, origin FROM pragma_index_list(?) WHERE "unique" = 1', (table,)
        ).fetchall():
            if origin == "u":
                columns = empty_db.execute(
                    "SELECT name FROM pragma_index_info(?) ORDER BY seqno", (index_name,)
                ).fetchall()
                unique_columns.add((table, tuple(column[0] for column in columns)))
    assert unique_columns == {("categories", ("name",))}


@pytest.mark.parametrize("table", DOMAIN_TABLES)
def test_schema_column_metadata(table):
    specs = {spec.name: spec for spec in schema().TABLES}
    assert [spec.name for spec in schema().TABLES] == list(DOMAIN_TABLES)
    spec = specs[table]
    actual = [
        (column.source_name, column.column_name, str(column.kind), column.allowed)
        for column in spec.columns
    ]
    assert actual == EXPECTED_METADATA[table]
    assert (spec.primary_key, spec.unique) == EXPECTED_KEYS[table]
    assert {(ref.column_name, ref.parent_table) for ref in spec.references} == (
        EXPECTED_REFERENCES[table]
    )


@pytest.mark.parametrize("table", DOMAIN_TABLES)
def test_schema_metadata_matches_created_columns(empty_db, table):
    spec = {spec.name: spec for spec in schema().TABLES}[table]
    created = [
        row[0]
        for row in empty_db.execute(
            "SELECT name FROM pragma_table_info(?) ORDER BY cid", (table,)
        ).fetchall()
    ]
    assert [column.column_name for column in spec.columns] == created


def test_schema_field_kinds():
    assert {str(kind) for kind in schema().FieldKind} == {
        "id",
        "count",
        "quantity",
        "money",
        "percent",
        "date",
        "optional_date",
        "flag",
        "enum",
        "json_object",
        "text",
        "optional_text",
    }


# Valid data is stored and read back unchanged.


def test_schema_valid_rows_are_preserved(db):
    for table in DOMAIN_TABLES:
        insert(db, table, NEW_ROWS[table])
    for table in AUXILIARY_TABLES:
        for row in AUXILIARY_ROWS[table]:
            insert(db, table, row)
    for table in DOMAIN_TABLES:
        assert select_all(db, table) == BASE_ROWS[table] + [NEW_ROWS[table]]
    for table in AUXILIARY_TABLES:
        assert select_all(db, table) == AUXILIARY_ROWS[table]
    assert db.execute("SELECT count(*) FROM pragma_foreign_key_check").fetchone() == (0,)


def test_schema_maximum_money_is_preserved_exactly(db):
    insert(db, "products", NEW_ROWS["products"] | {"price_cents": MAX_INTEGER})
    insert(db, "orders", NEW_ROWS["orders"] | {"total_cents": MAX_INTEGER})
    assert db.execute(
        "SELECT price_cents, typeof(price_cents) FROM products WHERE product_id = 11"
    ).fetchone() == (MAX_INTEGER, "integer")
    assert db.execute(
        "SELECT total_cents, typeof(total_cents) FROM orders WHERE order_id = 3"
    ).fetchone() == (MAX_INTEGER, "integer")


def test_schema_money_above_sqlite_integer_cannot_be_bound(db):
    with pytest.raises(OverflowError):
        insert(db, "products", NEW_ROWS["products"] | {"price_cents": MAX_INTEGER + 1})
    assert db.execute("SELECT count(*) FROM products WHERE product_id = 11").fetchone() == (0,)


def test_schema_customers_may_share_an_email(db):
    insert(db, "customers", NEW_ROWS["customers"] | {"email": "compartilhado@example.invalid"})
    assert db.execute(
        "SELECT count(*) FROM customers WHERE email = 'compartilhado@example.invalid'"
    ).fetchone() == (2,)


@pytest.mark.parametrize(
    ("status", "stock"), [("discontinued", 2), ("coming_soon", 1), ("active", 0)]
)
def test_schema_status_and_stock_are_stored_independently(db, status, stock):
    insert(db, "products", NEW_ROWS["products"] | {"status": status, "stock_quantity": stock})
    assert db.execute(
        "SELECT status, stock_quantity FROM products WHERE product_id = 11"
    ).fetchone() == (status, stock)


def test_schema_historical_total_is_not_recalculated(db):
    # Order 1 holds one product at 59990 cents; its stored total is 50000 cents.
    row = db.execute(
        "SELECT o.total_cents, sum(p.price_cents * i.quantity) FROM orders o "
        "JOIN order_items i ON i.order_id = o.order_id "
        "JOIN products p ON p.product_id = i.product_id WHERE o.order_id = 1"
    ).fetchone()
    assert row == (50000, 59990)


def test_schema_optional_order_fields_accept_null(db):
    assert db.execute(
        "SELECT tracking_code, estimated_delivery, notes FROM orders WHERE order_id = 2"
    ).fetchone() == (None, None, "Cancelado a pedido")


@pytest.mark.parametrize(
    ("table", "column", "values"),
    [
        ("products", "status", sorted(PRODUCT_STATUSES)),
        ("orders", "status", sorted(ORDER_STATUSES)),
        ("orders", "payment_method", sorted(PAYMENT_METHODS)),
        ("promotions", "is_active", [0, 1]),
        ("promotions", "discount_basis_points", [0, 1250, 10000]),
        ("products", "price_cents", [0, 1]),
        ("products", "stock_quantity", [0, MAX_INTEGER]),
        ("order_items", "quantity", [1, MAX_INTEGER]),
    ],
)
def test_schema_boundary_and_allowlisted_values_accepted(db, table, column, values):
    key = schema_key(table)
    for value in values:
        insert(db, table, NEW_ROWS[table] | {column: value})
        assert db.execute(
            f"SELECT {column} FROM {table} WHERE {key}", key_args(table)
        ).fetchone() == (value,)
        db.execute(f"DELETE FROM {table} WHERE {key}", key_args(table))


def schema_key(table: str) -> str:
    if table == "order_items":
        return "order_id = ? AND product_id = ?"
    return f"{EXPECTED_COLUMNS[table][0][0]} = ?"


def key_args(table: str) -> tuple[object, ...]:
    row = NEW_ROWS[table]
    if table == "order_items":
        return (row["order_id"], row["product_id"])
    return (row[EXPECTED_COLUMNS[table][0][0]],)


# Keys and references.


@pytest.mark.parametrize("table", DOMAIN_TABLES)
def test_schema_duplicate_primary_key_rejected(db, table):
    row = NEW_ROWS[table] | {
        column: BASE_ROWS[table][0][column] for column in EXPECTED_KEYS[table][0]
    }
    if table == "categories":
        row["name"] = "Outro nome"
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
        insert(db, table, row)


def test_schema_repeated_order_item_pair_rejected(db):
    with pytest.raises(
        sqlite3.IntegrityError,
        match=r"UNIQUE constraint failed: order_items.order_id, order_items.product_id",
    ):
        insert(db, "order_items", {"order_id": 1, "product_id": 10, "quantity": 2})


def test_schema_duplicate_category_name_rejected(db):
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed: categories.name"):
        insert(db, "categories", NEW_ROWS["categories"] | {"name": "Instrumentos"})


@pytest.mark.parametrize("name", ["instrumentos", "Instrumentos ", "INSTRUMENTOS"])
def test_schema_category_name_compared_exactly(db, name):
    insert(db, "categories", NEW_ROWS["categories"] | {"name": name})
    assert db.execute("SELECT name FROM categories WHERE category_id = 2").fetchone() == (name,)


@pytest.mark.parametrize(
    ("table", "column"),
    [
        ("products", "category_id"),
        ("promotions", "product_id"),
        ("orders", "customer_id"),
        ("order_items", "order_id"),
        ("order_items", "product_id"),
    ],
)
def test_schema_broken_reference_rejected_with_foreign_keys_on(db, table, column):
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
        insert(db, table, NEW_ROWS[table] | {column: 99})


def test_schema_limit_foreign_keys_need_the_pragma(db):
    db.execute("PRAGMA foreign_keys = OFF")
    insert(db, "products", NEW_ROWS["products"] | {"category_id": 99})
    assert db.execute("SELECT count(*) FROM pragma_foreign_key_check").fetchone() == (1,)


# CHECK constraints.

INVALID_VALUES = [
    *[
        (table, EXPECTED_COLUMNS[table][0][0], value)
        for table in DOMAIN_TABLES[:5]
        for value in (0, -1)
    ],
    ("products", "price_cents", -1),
    ("orders", "total_cents", -1),
    ("products", "stock_quantity", -1),
    ("order_items", "quantity", 0),
    ("order_items", "quantity", -1),
    ("promotions", "discount_basis_points", -1),
    ("promotions", "discount_basis_points", 10001),
    ("promotions", "is_active", 2),
    ("promotions", "is_active", -1),
    ("products", "status", "Active"),
    ("products", "status", "unknown"),
    ("orders", "status", "Shipped"),
    ("orders", "status", "returned"),
    ("orders", "payment_method", "credit_13x"),
    ("orders", "payment_method", "credit_0x"),
    ("orders", "payment_method", "cash"),
    ("orders", "payment_method", "PIX"),
    ("products", "specs_json", "[]"),
    ("products", "specs_json", '"x"'),
    ("products", "specs_json", "{"),
    ("products", "specs_json", '{"a": NaN}'),
    ("products", "specs_json", "{'a': 1}"),
    ("products", "created_at", "2026-02-30"),
    ("products", "created_at", "2026-2-1"),
    ("products", "created_at", "20260228"),
    ("products", "created_at", "28/02/2026"),
    ("products", "created_at", "2026-02-28 "),
    ("products", "created_at", "2026-02-28T00:00:00"),
    ("orders", "order_date", "2026-13-01"),
    ("orders", "estimated_delivery", "2026-04-31"),
    ("orders", "estimated_delivery", ""),
    ("orders", "tracking_code", ""),
    ("orders", "tracking_code", "   "),
    ("orders", "notes", ""),
    ("categories", "name", ""),
    ("categories", "description", "  "),
    ("customers", "name", ""),
    ("customers", "phone", " "),
    ("customers", "email", ""),
    ("customers", "city", "   "),
    ("products", "name", ""),
    ("products", "description", " "),
    ("promotions", "description", ""),
]


@pytest.mark.parametrize(("table", "column", "value"), INVALID_VALUES)
def test_schema_check_constraint_rejects(db, table, column, value):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        insert(db, table, NEW_ROWS[table] | {column: value})


@pytest.mark.parametrize(
    ("table", "column"),
    [
        (table, column)
        for table in DOMAIN_TABLES
        for column, _, notnull, pk in EXPECTED_COLUMNS[table]
        if notnull and not (pk == 1 and table != "order_items")
    ],
)
def test_schema_required_column_rejects_null(db, table, column):
    with pytest.raises(sqlite3.IntegrityError, match="NOT NULL constraint failed"):
        insert(db, table, NEW_ROWS[table] | {column: None})


@pytest.mark.parametrize("table", DOMAIN_TABLES[:5])
def test_schema_limit_null_integer_primary_key_becomes_rowid(db, table):
    # Documented SQLite rowid-alias behavior; the importer never binds NULL here.
    key = EXPECTED_COLUMNS[table][0][0]
    insert(db, table, NEW_ROWS[table] | {key: None})
    assert db.execute(f"SELECT count(*) FROM {table}").fetchone() == (len(BASE_ROWS[table]) + 1,)
    assert db.execute(f"SELECT count(*) FROM {table} WHERE {key} IS NULL").fetchone() == (0,)


@pytest.mark.parametrize(
    ("table", "column", "value"),
    [
        ("products", "price_cents", "abc"),
        ("products", "price_cents", 1.5),
        ("order_items", "quantity", "1.5"),
        ("promotions", "is_active", "true"),
        ("products", "product_id", "x"),
    ],
)
def test_schema_strict_rejects_unconvertible_values(db, table, column, value):
    # SQLite reports "datatype mismatch" for a rowid key, "cannot store" otherwise.
    with pytest.raises(sqlite3.IntegrityError, match="datatype mismatch|cannot store"):
        insert(db, table, NEW_ROWS[table] | {column: value})


def test_schema_limit_strict_converts_lossless_values(db):
    insert(db, "products", NEW_ROWS["products"] | {"price_cents": "05", "stock_quantity": 2.0})
    assert db.execute(
        "SELECT price_cents, typeof(price_cents), stock_quantity, typeof(stock_quantity) "
        "FROM products WHERE product_id = 11"
    ).fetchone() == (5, "integer", 2, "integer")


@pytest.mark.parametrize(
    ("table", "column", "value"),
    [
        ("customers", "name", "\t"),
        ("customers", "city", " "),
        ("products", "specs_json", '{"a": 1, "a": 2}'),
        ("products", "created_at", "0000-01-01"),
    ],
)
def test_schema_limit_rules_left_to_parsers(db, table, column, value):
    # These values are rejected by the T1 parsers, not by SQL.
    insert(db, table, NEW_ROWS[table] | {column: value})


def test_schema_json_object_text_is_stored_byte_for_byte(db):
    text = '{ "b" : "2",\n "a": "1" }'
    insert(db, "products", NEW_ROWS["products"] | {"specs_json": text})
    assert db.execute("SELECT specs_json FROM products WHERE product_id = 11").fetchone() == (text,)


# Auxiliary tables.

INVALID_AUXILIARY = [
    ("source_files", {"table_name": "unknown"}, "CHECK"),
    ("source_files", {"table_name": "preparation_metadata"}, "CHECK"),
    ("source_files", {"file_name": ""}, "CHECK"),
    ("source_files", {"file_name": "dir/categories.csv"}, "CHECK"),
    ("source_files", {"file_name": "dir\\categories.csv"}, "CHECK"),
    ("source_files", {"sha256": "A" * 64}, "CHECK"),
    ("source_files", {"sha256": "0" * 63}, "CHECK"),
    ("source_files", {"sha256": "0" * 65}, "CHECK"),
    ("source_files", {"sha256": "g" * 64}, "CHECK"),
    ("source_files", {"size_bytes": -1}, "CHECK"),
    ("source_files", {"row_count": -1}, "CHECK"),
    ("source_files", {"sha256": None}, "NOT NULL"),
    ("source_files", {"table_name": None}, "NOT NULL"),
    ("preparation_metadata", {"value": None}, "NOT NULL"),
    ("preparation_metadata", {"key": None}, "NOT NULL"),
    ("data_quality_notes", {"entity": "order"}, "CHECK"),
    ("data_quality_notes", {"entity_id": 0}, "CHECK"),
    ("data_quality_notes", {"code": "other"}, "CHECK"),
    ("data_quality_notes", {"code": None}, "NOT NULL"),
]


@pytest.mark.parametrize(("table", "override", "message"), INVALID_AUXILIARY)
def test_schema_auxiliary_constraints_reject(empty_db, table, override, message):
    with pytest.raises(sqlite3.IntegrityError, match=f"{message} constraint failed"):
        insert(empty_db, table, AUXILIARY_ROWS[table][0] | override)


@pytest.mark.parametrize("table", AUXILIARY_TABLES)
def test_schema_auxiliary_duplicate_key_rejected(empty_db, table):
    row = AUXILIARY_ROWS[table][0]
    insert(empty_db, table, row)
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
        insert(empty_db, table, row)


def test_schema_source_files_accept_every_domain_table(empty_db):
    for table in DOMAIN_TABLES:
        insert(
            empty_db,
            "source_files",
            {
                "table_name": table,
                "file_name": f"desafio_tecnico_ai_eng - {table}.csv",
                "sha256": SHA256_ZERO,
                "size_bytes": 0,
                "row_count": 0,
            },
        )
    assert empty_db.execute("SELECT count(*) FROM source_files").fetchone() == (6,)


def test_schema_quality_note_needs_no_product_row(empty_db):
    # entity_id has no foreign key: entity names the target table.
    insert(
        empty_db,
        "data_quality_notes",
        {"entity": "product", "entity_id": 135, "code": "catalog_text_conflict"},
    )
    assert empty_db.execute("SELECT count(*) FROM data_quality_notes").fetchone() == (1,)


# Transactions controlled by the caller.


def test_schema_ddl_and_dml_roll_back_together(database_path):
    connection = connect(database_path)
    try:
        connection.execute("BEGIN")
        for statement in schema().DDL_STATEMENTS:
            connection.execute(statement)
        insert(connection, "categories", BASE_ROWS["categories"][0])
        assert connection.in_transaction
        connection.execute("ROLLBACK")
        assert user_tables(connection) == []
    finally:
        connection.close()
    check = sqlite3.connect(database_path)
    try:
        assert user_tables(check) == []
    finally:
        check.close()


def test_schema_failed_insert_keeps_transaction_until_caller_rolls_back(db, database_path):
    db.execute("BEGIN")
    insert(db, "categories", NEW_ROWS["categories"])
    with pytest.raises(sqlite3.IntegrityError):
        insert(db, "products", NEW_ROWS["products"] | {"category_id": 99})
    assert db.in_transaction
    assert db.execute("SELECT count(*) FROM categories").fetchone() == (2,)
    db.execute("ROLLBACK")
    check = sqlite3.connect(database_path)
    try:
        assert select_all(check, "categories") == BASE_ROWS["categories"]
        assert check.execute("SELECT count(*) FROM products").fetchone() == (1,)
    finally:
        check.close()


def test_schema_committed_rows_visible_to_independent_connection(db, database_path):
    db.execute("BEGIN")
    insert(db, "categories", NEW_ROWS["categories"])
    db.execute("COMMIT")
    check = sqlite3.connect(database_path)
    try:
        assert select_all(check, "categories") == BASE_ROWS["categories"] + [NEW_ROWS["categories"]]
    finally:
        check.close()
