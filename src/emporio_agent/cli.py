"""Command-line interface (docs/specs/08-cli.md): arguments and configuration (slice 08-A),
the preparation commands (slice 08-B), the chat bootstrap (slice 08-C) and the interactive
session (slice 08-D).

Importing this module opens no file, reads no environment variable, imports no SDK and
opens no socket. The chat startup checks the prepared resources read-only, builds one
provider, agent and session, prints the banner and then reads one line per message. The
session loop only classifies lines, calls the agent and prints; context, tools and
authorization belong to the agent.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sqlite3
import sys
import time
import unicodedata
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final, NoReturn

from emporio_agent.agent import Agent, AgentResources, ToolRunRecord, TurnResult
from emporio_agent.data.errors import DataPreparationError, IssueCode
from emporio_agent.data.parsing import SQLITE_MAX_INTEGER
from emporio_agent.data.policy_index import (
    PolicyIndex,
    PolicyIndexUnavailableError,
    PolicyPreparationError,
    load_policy_index,
    prepare_policy_index,
)
from emporio_agent.data.preparation import prepare_database
from emporio_agent.data.readonly import (
    DatabaseUnavailableError,
    PreparationInfo,
    connect_readonly,
    read_preparation_info,
)
from emporio_agent.services.orders import CustomerContext, customer_exists
from emporio_agent.session import ConversationSession

PROG: Final = "emporio-agent"
DEFAULT_DATABASE: Final = Path("data/local/emporio.sqlite3")
DEFAULT_INDEX: Final = Path("data/local/policy-index.json")

EXIT_OK: Final = 0
EXIT_INTERNAL: Final = 1
EXIT_USAGE: Final = 2
EXIT_RESOURCES: Final = 3
EXIT_PREPARATION: Final = 4
EXIT_INTERRUPTED: Final = 130

KEY_ENV: Final = "OPENAI_API_KEY"
MODEL_ENV: Final = "OPENAI_MODEL"
SDK_LOG_ENV: Final = "OPENAI_LOG"

# Normal-use model configuration of the chat (spec G4, D-01). The adapter defaults
# (reasoning_effort=None, max_output_tokens=8000) are not changed.
CHAT_REASONING_EFFORT: Final = "low"
CHAT_MAX_OUTPUT_TOKENS: Final = 4096

_CUSTOMER_ID: Final = re.compile(r"[1-9][0-9]{0,18}")
_MODEL_NAME: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")

# Spec G10a: fixed PT-BR sentence per stable code. Never contains a value or a path.
_SENTENCES: Final[Mapping[str, str]] = {
    "arguments_invalid": "Argumentos inválidos. Use -h para ver os comandos e as opções.",
    "customer_invalid": (
        "Cliente demonstrativo inválido. "
        "Use um número inteiro positivo, sem sinal, espaços ou zeros à esquerda."
    ),
    "sdk_logging_enabled": (
        "Erro de configuração: remova a variável OPENAI_LOG do ambiente antes de iniciar o chat."
    ),
    "key_missing": (
        "Erro de configuração: defina a variável OPENAI_API_KEY no ambiente deste terminal."
    ),
    "key_invalid": (
        "Erro de configuração: o valor de OPENAI_API_KEY tem espaços ou quebras de linha "
        "nas extremidades."
    ),
    "model_missing": (
        "Erro de configuração: defina a variável OPENAI_MODEL no ambiente deste terminal."
    ),
    "model_invalid": "Erro de configuração: o valor de OPENAI_MODEL tem formato inválido.",
}


class UsageRejected(Exception):
    """Rejection with a stable code; the message never carries input."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _HelpRequested(Exception):
    def __init__(self, text: str) -> None:
        super().__init__("help")
        self.text = text


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        # The argparse message can repeat the given values (a key typed by mistake).
        raise UsageRejected("arguments_invalid")

    def print_help(self, file=None) -> None:
        raise _HelpRequested(self.format_help())

    def exit(self, status: int = 0, message: str | None = None) -> NoReturn:
        raise UsageRejected("arguments_invalid")


@dataclass(frozen=True, slots=True)
class ChatConfig:
    model: str
    api_key: str = field(repr=False)
    database: Path = DEFAULT_DATABASE
    index: Path = DEFAULT_INDEX
    customer: CustomerContext | None = None
    diagnostics: bool = False


def _subparser(subparsers, name: str, help_text: str) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        name,
        help=help_text,
        description=help_text,
        allow_abbrev=False,
        add_help=False,
        color=False,
    )
    parser.add_argument("-h", "--help", action="help", help="mostra esta ajuda e sai")
    return parser


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog=PROG,
        description="Assistente de atendimento do Empório da Música (demonstração local).",
        allow_abbrev=False,
        add_help=False,
        color=False,
    )
    parser.add_argument("-h", "--help", action="help", help="mostra esta ajuda e sai")
    subparsers = parser.add_subparsers(
        dest="command", required=True, metavar="COMANDO", title="comandos"
    )

    data = _subparser(
        subparsers, "prepare-data", "Prepara o banco SQLite a partir dos seis arquivos CSV."
    )
    data.add_argument(
        "--source-dir", required=True, type=Path, help="pasta com os seis arquivos CSV"
    )
    data.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE,
        help=f"banco preparado (padrão: {DEFAULT_DATABASE.as_posix()})",
    )

    policies = _subparser(
        subparsers, "prepare-policies", "Prepara o índice de políticas a partir do PDF."
    )
    policies.add_argument("--source-pdf", required=True, type=Path, help="PDF de políticas")
    policies.add_argument(
        "--index",
        type=Path,
        default=DEFAULT_INDEX,
        help=f"índice preparado (padrão: {DEFAULT_INDEX.as_posix()})",
    )

    chat = _subparser(
        subparsers,
        "chat",
        "Inicia uma sessão de atendimento em PT-BR; o texto digitado é enviado à API da OpenAI.",
    )
    chat.add_argument(
        "--customer",
        help="ID do cliente demonstrativo (sem o argumento: sessão anônima)",
    )
    chat.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE,
        help=f"banco preparado (padrão: {DEFAULT_DATABASE.as_posix()})",
    )
    chat.add_argument(
        "--index",
        type=Path,
        default=DEFAULT_INDEX,
        help=f"índice preparado (padrão: {DEFAULT_INDEX.as_posix()})",
    )
    chat.add_argument(
        "--diagnostics", action="store_true", help="mostra diagnóstico seguro no stderr"
    )
    return parser


def _customer(value: str | None) -> CustomerContext | None:
    if value is None:
        return None
    if type(value) is not str or _CUSTOMER_ID.fullmatch(value) is None:
        raise UsageRejected("customer_invalid")
    number = int(value)
    if number > SQLITE_MAX_INTEGER:
        raise UsageRejected("customer_invalid")
    return CustomerContext(number)


def resolve_chat_config(args: argparse.Namespace, environ: Mapping[str, str]) -> ChatConfig:
    """Arguments, then OPENAI_LOG, then key, then model (spec G3). No file access."""
    customer = _customer(args.customer)
    if SDK_LOG_ENV in environ:
        raise UsageRejected("sdk_logging_enabled")
    key = environ.get(KEY_ENV)
    if key is None or key == "":
        raise UsageRejected("key_missing")
    if key != key.strip():
        raise UsageRejected("key_invalid")
    model = environ.get(MODEL_ENV)
    if model is None or model == "":
        raise UsageRejected("model_missing")
    if _MODEL_NAME.fullmatch(model) is None:
        raise UsageRejected("model_invalid")
    return ChatConfig(
        model=model,
        api_key=key,
        database=args.database,
        index=args.index,
        customer=customer,
        diagnostics=args.diagnostics,
    )


# --- Field serializer of the preparation summaries (spec G11a guard, G11b) ------------

UNPRINTABLE: Final = "unprintable"
_GUARD: Final = re.compile(r"[A-Za-z0-9_.:@,;-]{1,4096}")
_BASENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_SHA256: Final = re.compile(r"[0-9a-f]{64}")


def _field_value(kind: str, value: object) -> str:
    if kind == "int":
        if type(value) is int and 0 <= value <= SQLITE_MAX_INTEGER:
            return str(value)
        return UNPRINTABLE
    if kind == "basename":
        name = value.name if isinstance(value, Path) else value
        if type(name) is str and _BASENAME.fullmatch(name) is not None:
            return name
        return UNPRINTABLE
    if kind == "sha256":
        if type(value) is str and _SHA256.fullmatch(value) is not None:
            return value
        return UNPRINTABLE
    raise ValueError("unknown field kind")


def format_field(kind: str, value: object) -> str:
    """Value printed for one key=value field: its rule, then the final guard."""
    printed = _field_value(kind, value)
    return printed if _GUARD.fullmatch(printed) is not None else UNPRINTABLE


# --- Diagnostic records (spec G11a-G11e) -------------------------------------------------

ABSENT: Final = "-"
_REQUEST_ID: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_MAX_REFERENCE: Final = 256
_REFERENCE: Final = re.compile(
    r"product:[1-9][0-9]{0,18}(?:@pix|@regular)?"
    r"|policy:(?:front_matter|[0-9]+(?:\.[0-9]+)*)@p[1-9][0-9]{0,2}(?:,[1-9][0-9]{0,2})*"
    r"|order"
)
# Frozen copies (spec G11c); tests compare them with TurnOutcome, ToolStatus,
# BudgetFailure, tool_names() and the failure codes of agent.py.
DIAG_VOCABULARIES: Final[Mapping[str, frozenset[str]]] = {
    "mode": frozenset({"anonymous", "linked"}),
    "effort": frozenset({"low", "omitted"}),
    # Spec 08 Review 08-R7: copy of openai_provider.REPORTED_REASONING_EFFORTS (no SDK import).
    "effort_reported": frozenset({"none", "minimal", "low", "medium", "high", "xhigh", "max"}),
    "outcome": frozenset({"answered", "refused", "failed", "rejected_input"}),
    "failure": frozenset(
        {
            "provider_timeout",
            "provider_unavailable",
            "provider_rate_limited",
            "provider_quota",
            "provider_authentication",
            "provider_permission",
            "provider_model_unavailable",
            "provider_bad_request",
            "response_invalid",
            "protocol_error",
            "response_incomplete",
            "empty_response",
            "no_final_answer",
            "response_too_long",
            "response_withheld",
            "provider_failed",
            "unexpected",
            "tool_limit_reached",
            "model_call_limit_reached",
            "request_too_large",
            "input_invalid_text",
            "input_empty",
            "input_too_long",
        }
    ),
    "budget": frozenset({"static", "chain", "total"}),
    "tool": frozenset(
        {"search_products", "get_product", "quote_product", "search_policies", "get_order"}
    ),
    "tool_status": frozenset({"ok", "not_found", "invalid_input", "unavailable", "internal_error"}),
    "trigger": frozenset({"reiniciar", "cliente"}),
    "switch_result": frozenset({"ok", "argument_invalid", "not_found", "lookup_failed"}),
}


_SUMMARY_KINDS: Final = frozenset({"int", "basename", "sha256"})


def _matching(value: object, grammar: re.Pattern[str]) -> str:
    return value if type(value) is str and grammar.fullmatch(value) is not None else UNPRINTABLE


def _diag_value(kind: str, value: object) -> str:
    """Field rules of spec G11b added by slice 08-C (the 08-B kinds go to format_field)."""
    if kind == "model":
        return _matching(value, _MODEL_NAME)
    if kind == "request_id":
        return _matching(value, _REQUEST_ID)
    if kind == "reference":
        if type(value) is str and len(value) > _MAX_REFERENCE:
            return UNPRINTABLE
        return _matching(value, _REFERENCE)
    if kind == "datetime":
        if isinstance(value, datetime) and value.utcoffset() == timedelta(0):
            return value.strftime("%Y-%m-%dT%H:%M:%SZ")
        return UNPRINTABLE
    if kind == "tool_run":
        if not isinstance(value, ToolRunRecord):
            return UNPRINTABLE
        tool = "unknown" if value.tool is None else _diag_value("tool", value.tool)
        return tool + ":" + _diag_value("tool_status", value.status)
    if kind in DIAG_VOCABULARIES:
        return value if type(value) is str and value in DIAG_VOCABULARIES[kind] else UNPRINTABLE
    raise ValueError("unknown field kind")


def diag_field(kind: str, value: object) -> str:
    """One diagnostic value: absent (None) is '-'; otherwise its rule, then the guard."""
    if value is None:
        return ABSENT
    if kind in _SUMMARY_KINDS:
        return format_field(kind, value)  # the 08-B function, reused unchanged
    printed = _diag_value(kind, value)
    return printed if _GUARD.fullmatch(printed) is not None else UNPRINTABLE


def diag_list(kind: str, values: object) -> str:
    """Elements in source order joined by ';'; an empty sequence is '-' (spec G11d)."""
    if not isinstance(values, tuple | list):
        return UNPRINTABLE
    if not values:
        return ABSENT
    joined = ";".join(diag_field(kind, value) for value in values)
    return joined if _GUARD.fullmatch(joined) is not None else UNPRINTABLE


def _record(kind: str, fields: Sequence[tuple[str, str]]) -> str:
    return f"diag: {kind} " + " ".join(f"{key}={value}" for key, value in fields) + "\n"


def _mode(customer: object) -> str:
    return "anonymous" if customer is None else "linked"


def start_record(config: ChatConfig, info: PreparationInfo, policy: PolicyIndex) -> str:
    effort = "omitted" if CHAT_REASONING_EFFORT is None else CHAT_REASONING_EFFORT
    return _record(
        "start",
        [
            ("mode", diag_field("mode", _mode(config.customer))),
            ("database", diag_field("basename", config.database)),
            ("schema_version", diag_field("int", info.schema_version)),
            ("prepared_at", diag_field("datetime", info.prepared_at)),
            ("index", diag_field("basename", config.index)),
            ("index_format_version", diag_field("int", policy.source.format_version)),
            ("policy_source_sha256", diag_field("sha256", policy.source.sha256)),
            ("configured_model", diag_field("model", config.model)),
            ("reasoning_effort", diag_field("effort", effort)),
            ("max_output_tokens", diag_field("int", CHAT_MAX_OUTPUT_TOKENS)),
            ("max_messages", diag_field("int", MAX_MESSAGES_PER_RUN)),  # RI-4 (spec G9a, R6)
        ],
    )


def turn_record(n: object, result: TurnResult) -> str:
    """`diag: turn` record of spec G11a/G11e; `n` is the submission ordinal (RI-2)."""
    diagnostics = result.diagnostics
    attempts = diagnostics.attempts
    sizes = [attempt.request_bytes for attempt in attempts]
    if not sizes:
        max_bytes: object = None
    elif all(type(size) is int for size in sizes):
        max_bytes = max(sizes)
    else:
        max_bytes = UNPRINTABLE  # fails the int rule
    references = tuple(ref for run in diagnostics.tool_runs for ref in run.references)
    return _record(
        "turn",
        [
            ("n", diag_field("int", n)),
            ("outcome", diag_field("outcome", result.outcome)),
            ("failure", diag_field("failure", result.failure)),
            ("attempts", diag_field("int", len(attempts))),
            ("retries", diag_field("int", diagnostics.retries_used)),
            ("tools", diag_list("tool_run", diagnostics.tool_runs)),
            ("references", diag_list("reference", references)),
            ("configured_model", diag_field("model", diagnostics.configured_model)),
            ("reported_models", diag_list("model", [a.reported_model for a in attempts])),
            ("request_ids", diag_list("request_id", [a.request_id for a in attempts])),
            (
                "reasoning_efforts_reported",
                diag_list("effort_reported", [a.reasoning_effort_reported for a in attempts]),
            ),
            ("max_request_bytes", diag_field("int", max_bytes)),
            ("input_tokens", diag_field("int", diagnostics.input_tokens_reported)),
            ("output_tokens", diag_field("int", diagnostics.output_tokens_reported)),
            ("usage_missing", diag_field("int", diagnostics.usage_missing_attempts)),
            ("usage_invalid", diag_field("int", diagnostics.usage_invalid_attempts)),
            ("budget_failure", diag_field("budget", diagnostics.budget_failure)),
            ("history_turns", diag_field("int", diagnostics.history_turns_after)),
            ("pruned_turns", diag_field("int", diagnostics.history_pruned_turns)),
        ],
    )


def reset_record(trigger: object, result: object, mode: object) -> str:
    """`diag: reset` record of spec G11a."""
    return _record(
        "reset",
        [
            ("trigger", diag_field("trigger", trigger)),
            ("result", diag_field("switch_result", result)),
            ("mode", diag_field("mode", mode)),
        ],
    )


def _closed(value: object, vocabulary: frozenset[str]) -> str:
    if isinstance(value, IssueCode):  # StrEnum of the data library; print its plain value
        value = value.value
    return value if type(value) is str and value in vocabulary else UNPRINTABLE


# --- Preparation error lines (spec G12a) ------------------------------------------------

_GUARD_CODES: Final = frozenset({"target_not_file", "target_not_prepared"})
# Frozen copies of the library vocabularies; tests compare them with the sources.
_DATA_ERROR_CODES: Final = (
    frozenset(
        {
            "source_dir_missing",
            "file_missing",
            "file_unreadable",
            "encoding_invalid",
            "csv_malformed",
            "header_missing",
            "header_duplicate",
            "header_mismatch",
            "field_count",
            "value_required",
            "value_blank",
            "invalid_integer",
            "out_of_range",
            "invalid_money",
            "money_out_of_range",
            "invalid_percent",
            "invalid_date",
            "invalid_flag",
            "invalid_enum",
            "invalid_json",
            "duplicate_key",
            "broken_reference",
            "target_directory_missing",
            "target_write_failed",
            "database_build_failed",
            "integrity_check_failed",
            "target_is_source",
        }
    )
    | _GUARD_CODES
)
_POLICY_ERROR_CODES: Final = (
    frozenset(
        {
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
            "index_missing",
            "index_unreadable",
            "index_invalid",
            "index_version_unsupported",
        }
    )
    | _GUARD_CODES
)
_TABLES: Final = ("categories", "customers", "products", "promotions", "orders", "order_items")
_SOURCE_FILE_TABLES: Final[Mapping[str, str]] = {
    f"desafio_tecnico_ai_eng - {table}.csv": table for table in _TABLES
}
_SOURCE_COLUMNS: Final = frozenset(
    {
        "category_id",
        "name",
        "description",
        "customer_id",
        "phone",
        "email",
        "city",
        "product_id",
        "price_brl",
        "stock_quantity",
        "status",
        "specs",
        "created_at",
        "promotion_id",
        "discount_percent",
        "is_active",
        "order_id",
        "order_date",
        "total_brl",
        "payment_method",
        "tracking_code",
        "estimated_delivery",
        "notes",
        "quantity",
    }
)
_SOURCE_TARGET_MESSAGE: Final = "database_path must not be a source file"


class PreparationRejected(Exception):
    """Overwrite guard failure (D-05); carries a fixed code only."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _error_line(code: str, fields: Sequence[tuple[str, str | None]] = ()) -> str:
    parts = [f"erro de preparação: {code}"]
    parts.extend(f"{key}={value}" for key, value in fields if value is not None)
    return " ".join(parts) + "\n"


def _optional(kind: str, value: object) -> str | None:
    return None if value is None else format_field(kind, value)


def _data_error_line(error: DataPreparationError) -> str:
    table = None
    if error.file_name is not None:
        name = error.file_name
        table = _SOURCE_FILE_TABLES.get(name, UNPRINTABLE) if type(name) is str else UNPRINTABLE
    column = None if error.column is None else _closed(error.column, _SOURCE_COLUMNS)
    fields = (("tabela", table), ("linha", _optional("int", error.line)), ("coluna", column))
    return _error_line(_closed(error.code, _DATA_ERROR_CODES), fields)


def _policy_error_line(error: PolicyPreparationError | PolicyIndexUnavailableError) -> str:
    fields: list[tuple[str, str | None]] = [("arquivo", _optional("basename", error.file_name))]
    if isinstance(error, PolicyPreparationError):
        fields.append(("pagina", _optional("int", error.page)))
        fields.append(("entrada_sumario", _optional("int", error.outline_entry)))
    return _error_line(_closed(error.code, _POLICY_ERROR_CODES), fields)


# --- Preparation commands (spec G12, D-05) ---------------------------------------------


def _existing_target(path: Path) -> bool:
    """True for an existing regular file; PreparationRejected for any other existing entry."""
    if not (path.exists() or path.is_symlink()):
        return False
    if not path.is_file():
        raise PreparationRejected("target_not_file")
    return True


def _guard_database(path: Path) -> None:
    if not _existing_target(path):
        return
    try:
        with connect_readonly(path) as connection:
            read_preparation_info(connection)
    except DatabaseUnavailableError, sqlite3.Error, OSError:
        raise PreparationRejected("target_not_prepared") from None


def _guard_index(path: Path) -> None:
    if not _existing_target(path):
        return
    try:
        load_policy_index(path)
    except PolicyIndexUnavailableError:
        raise PreparationRejected("target_not_prepared") from None


def _summary(head: str, fields: Sequence[tuple[str, str]]) -> str:
    return head + " " + " ".join(f"{key}={value}" for key, value in fields) + "\n"


def _run_prepare_data(args: argparse.Namespace, stdout, stderr) -> int:
    database: Path = args.database
    try:
        _guard_database(database)
        result = prepare_database(args.source_dir, database)
    except PreparationRejected as rejected:
        return _write(stderr, _error_line(rejected.code), EXIT_PREPARATION)
    except DataPreparationError as error:
        return _write(stderr, _data_error_line(error), EXIT_PREPARATION)
    except ValueError as error:
        if error.args != (_SOURCE_TARGET_MESSAGE,):
            raise
        return _write(stderr, _error_line("target_is_source"), EXIT_PREPARATION)
    counts = result.row_counts
    fields = [
        ("arquivo", format_field("basename", database)),
        ("schema", format_field("int", result.schema_version)),
        ("tabelas", format_field("int", len(counts))),
        *((table, format_field("int", counts.get(table))) for table in _TABLES),
        ("notas_qualidade", format_field("int", result.quality_note_count)),
    ]
    return _write(stdout, _summary("dados preparados:", fields), EXIT_OK)


_PDF_LOGGER: Final = "pypdf"


@contextmanager
def _quiet_pdf_logging() -> Iterator[None]:
    """Drop pypdf log records before any handler, last resort included (spec G12b).

    The `pypdf` logger and every logger already registered below it get `CRITICAL + 1`;
    loggers created later inherit it. Previous levels come back on every exit path.
    """
    prefix = _PDF_LOGGER + "."
    registered = list(logging.Logger.manager.loggerDict.items())
    loggers = [logging.getLogger(_PDF_LOGGER)] + [
        logger
        for name, logger in registered
        if name.startswith(prefix) and isinstance(logger, logging.Logger)
    ]
    saved = [(logger, logger.level) for logger in loggers]
    try:
        for logger in loggers:
            logger.setLevel(logging.CRITICAL + 1)
        yield
    finally:
        for logger, level in saved:
            logger.setLevel(level)


def _run_prepare_policies(args: argparse.Namespace, stdout, stderr) -> int:
    index: Path = args.index
    try:
        with _quiet_pdf_logging():
            _guard_index(index)
            result = prepare_policy_index(args.source_pdf, index)
    except PreparationRejected as rejected:
        return _write(stderr, _error_line(rejected.code), EXIT_PREPARATION)
    except (PolicyPreparationError, PolicyIndexUnavailableError) as error:
        return _write(stderr, _policy_error_line(error), EXIT_PREPARATION)
    fields = [
        ("arquivo", format_field("basename", index)),
        ("paginas", format_field("int", result.source.page_count)),
        ("secoes", format_field("int", result.section_count)),
        ("recuperaveis", format_field("int", result.retrievable_count)),
        ("sha256", format_field("sha256", result.index_sha256)),
    ]
    return _write(stdout, _summary("índice preparado:", fields), EXIT_OK)


# --- Chat bootstrap (spec G6, G7, G9, G9a; slice 08-C) -----------------------------------

_DATABASE_REASONS: Final = frozenset({"missing", "open_failed", "schema_mismatch", "query_failed"})
_INDEX_CODES: Final = frozenset(
    {"index_missing", "index_unreadable", "index_invalid", "index_version_unsupported"}
)
_CUSTOMER_NOT_FOUND: Final = (
    "customer_not_found - Cliente demonstrativo não encontrado no banco preparado.\n"
)
_BANNER: Final = (
    "Empório da Música — assistente de atendimento "
    "(demonstração local, sem autenticação real).\n"
    "Modo: {mode}.\n"
    "Comandos: /sair encerra; /reiniciar inicia nova conversa anônima; "
    "/cliente <id> troca o cliente demonstrativo.\n"
    "Aviso: o texto digitado é enviado à API da OpenAI.\n"
)
PROMPT: Final = "Você: "
_FAREWELL: Final = "\nAtendimento encerrado. Até logo.\n"
_INTERRUPTED: Final = "interrompido\n"
_READ_FAILED: Final = "input_read_failed - Não foi possível ler a entrada do terminal.\n"
_CLEANUP_FAILED: Final = "cleanup_failed - Falha ao encerrar a conexão com o provedor.\n"
_SDK_LOGGERS: Final = ("openai", "httpx2", "httpcore2", "httpx", "httpcore")


class _ResourceUnavailable(Exception):
    """A prepared resource cannot be used; carries the fixed stderr line only."""

    def __init__(self, line: str) -> None:
        super().__init__("resource_unavailable")
        self.line = line


def _database_line(reason: object) -> str:
    return (
        f"database_unavailable: {_closed(reason, _DATABASE_REASONS)} - Banco preparado "
        "indisponível. Prepare os dados com emporio-agent prepare-data antes de iniciar o chat.\n"
    )


def _index_line(code: object) -> str:
    return (
        f"policy_index_unavailable: {_closed(code, _INDEX_CODES)} - Índice de políticas "
        "indisponível. Prepare o índice com emporio-agent prepare-policies antes de iniciar "
        "o chat.\n"
    )


def _read_database(path: Path, read: Callable[[sqlite3.Connection], object]):
    """Run one read inside connect_readonly; the connection closes when it returns."""
    try:
        with connect_readonly(path) as connection:
            return read(connection)
    except DatabaseUnavailableError as error:
        raise _ResourceUnavailable(_database_line(error.reason)) from None
    except sqlite3.Error:
        raise _ResourceUnavailable(_database_line("query_failed")) from None


def _check_resources(config: ChatConfig) -> tuple[PreparationInfo, PolicyIndex]:
    """Database, then index, then customer (spec G9a). Nothing is created or written."""
    info = _read_database(config.database, read_preparation_info)
    try:
        policy = load_policy_index(config.index)
    except PolicyIndexUnavailableError as error:
        raise _ResourceUnavailable(_index_line(error.code)) from None
    customer = config.customer
    if customer is not None and not _read_database(
        config.database, lambda connection: customer_exists(connection, customer)
    ):
        raise _ResourceUnavailable(_CUSTOMER_NOT_FOUND)
    return info, policy


def default_provider_factory(config: ChatConfig):
    """Real adapter with the CLI constants (spec G4, G14). Imports the SDK lazily (A11)."""
    from emporio_agent.openai_provider import OpenAIResponsesProvider

    return OpenAIResponsesProvider(
        api_key=config.api_key,
        model=config.model,
        max_output_tokens=CHAT_MAX_OUTPUT_TOKENS,
        reasoning_effort=CHAT_REASONING_EFFORT,
    )


@contextmanager
def _quiet_sdk_logging() -> Iterator[None]:
    loggers = [logging.getLogger(name) for name in _SDK_LOGGERS]
    saved = [(logger, logger.level) for logger in loggers]
    try:
        for logger in loggers:
            logger.setLevel(logging.CRITICAL + 1)
        yield
    finally:
        for logger, level in saved:
            logger.setLevel(level)


def _banner(customer: CustomerContext | None) -> str:
    mode = "sessão anônima" if customer is None else f"cliente demonstrativo {customer.customer_id}"
    return _BANNER.format(mode=mode)


# --- Interactive session (spec G5, G7, G8, G8a, G10; slice 08-D) ------------------------

MAX_MESSAGES_PER_RUN: Final = 30
_FAREWELL_COMMAND: Final = "Atendimento encerrado. Até logo.\n"
_LIMIT_REACHED: Final = "Limite de 30 mensagens por execução atingido. Atendimento encerrado.\n"
_INVALID_COMMAND: Final = "Comando desconhecido. Comandos: /sair, /reiniciar, /cliente <id>.\n"
_RESTARTED: Final = "Conversa reiniciada. Sessão anônima ativa.\n"
_SWITCH_LINES: Final[Mapping[str, str]] = {
    "ok": "Sessão vinculada ao cliente demonstrativo {id}. Histórico apagado.\n",
    "argument_invalid": (
        "Comando /cliente inválido. Use /cliente <id> com um número de cliente demonstrativo. "
        "Sessão anônima ativa; histórico apagado.\n"
    ),
    "not_found": (
        "Cliente demonstrativo não encontrado. Sessão anônima ativa; histórico apagado.\n"
    ),
    "lookup_failed": (
        "Não foi possível verificar o cliente demonstrativo. Sessão anônima ativa; "
        "histórico apagado.\n"
    ),
}
_KEPT_CONTROLS: Final = frozenset("\n\t")
_REPLACEMENT: Final = "\ufffd"


def _displayed(text: str) -> str:
    """Answer text for the terminal (D-10): Cc code points except LF and TAB -> U+FFFD.

    Presentation only; the session history keeps the original text.
    """
    return "".join(
        _REPLACEMENT if unicodedata.category(ch) == "Cc" and ch not in _KEPT_CONTROLS else ch
        for ch in text
    )


def _without_terminator(line: str) -> str:
    """Remove only the line terminator; every other character is kept (spec G8)."""
    if line.endswith("\r\n"):
        return line[:-2]
    if line.endswith("\n"):
        return line[:-1]
    return line


def _switch_customer(session: ConversationSession, tokens: Sequence[str], database: Path) -> str:
    """One /cliente attempt (spec G7, D-07): reset first; link only on `ok`."""
    session.reset()
    if len(tokens) != 2:
        return "argument_invalid"
    try:
        customer = _customer(tokens[1])
    except UsageRejected:
        return "argument_invalid"
    try:
        with connect_readonly(database) as connection:
            exists = customer_exists(connection, customer)
    except DatabaseUnavailableError, sqlite3.Error:
        return "lookup_failed"
    if not exists:
        return "not_found"
    session.reset(customer=customer)
    return "ok"


def _converse(agent: Agent, session: ConversationSession, config: ChatConfig, streams) -> int:
    """Read one line per message until /sair, EOF or the message limit (spec G8, G5)."""
    stdin, stdout, stderr = streams
    submitted = 0  # `n` of diag: turn, including rejected_input (RI-2)
    accepted = 0  # message limit count, without rejected_input (D-04)
    while True:
        if accepted >= MAX_MESSAGES_PER_RUN:
            return _write(stdout, _LIMIT_REACHED, EXIT_OK)
        stdout.write(PROMPT)
        stdout.flush()
        try:
            line = stdin.readline()
        except OSError:
            return _write(stderr, _READ_FAILED, EXIT_INTERNAL)
        if line == "":
            return _write(stdout, _FAREWELL, EXIT_OK)
        text = _without_terminator(line)
        stripped = text.strip()
        tokens = stripped.split()
        if tokens and tokens[0] == "/cliente":
            result = _switch_customer(session, tokens, config.database)
            customer = session.customer
            identifier = "" if customer is None else str(customer.customer_id)
            _write(stdout, _SWITCH_LINES[result].format(id=identifier), EXIT_OK)
            if config.diagnostics:
                _write(stderr, reset_record("cliente", result, _mode(customer)), EXIT_OK)
            continue
        if stripped == "/sair":
            return _write(stdout, _FAREWELL_COMMAND, EXIT_OK)
        if stripped == "/reiniciar":
            session.reset()
            _write(stdout, _RESTARTED, EXIT_OK)
            if config.diagnostics:
                _write(stderr, reset_record("reiniciar", "ok", "anonymous"), EXIT_OK)
            continue
        if stripped.startswith("/"):
            _write(stdout, _INVALID_COMMAND, EXIT_OK)
            continue
        submitted += 1
        turn = agent.respond(session, text)
        if turn.outcome != "rejected_input":
            accepted += 1
        _write(stdout, "Assistente: " + _displayed(turn.text) + "\n", EXIT_OK)
        if config.diagnostics:
            _write(stderr, turn_record(submitted, turn), EXIT_OK)


def _serve(provider, config: ChatConfig, info, policy, streams, seams) -> int:
    """Everything after the provider exists. Ctrl+C and failures map to fixed lines."""
    stdin, stdout, stderr = streams
    clock, sleep, monotonic = seams
    try:
        resources = AgentResources(
            database_path=config.database, policy_index_path=config.index, clock=clock
        )
        agent = Agent(provider, resources, sleep=sleep, monotonic=monotonic)
        session = ConversationSession(customer=config.customer)
        _write(stdout, _banner(config.customer), EXIT_OK)
        if config.diagnostics:
            _write(stderr, start_record(config, info, policy), EXIT_OK)
        return _converse(agent, session, config, streams)
    except KeyboardInterrupt:
        return _write(stderr, _INTERRUPTED, EXIT_INTERRUPTED)
    except Exception as error:  # type name only, never the message or a traceback
        return _write(stderr, _internal_line(error), EXIT_INTERNAL)


def _run_chat(config: ChatConfig, streams, factory, seams) -> int:
    stderr = streams[2]
    with _quiet_sdk_logging():
        try:
            info, policy = _check_resources(config)
        except _ResourceUnavailable as unavailable:
            return _write(stderr, unavailable.line, EXIT_RESOURCES)
        provider = factory(config)  # the only long-lived resource (spec G9 ownership)
        closed = False
        try:
            code = _serve(provider, config, info, policy, streams, seams)
        finally:
            try:
                provider.close()
                closed = True
            except Exception:
                closed = False
        if not closed:
            return _write(stderr, _CLEANUP_FAILED, EXIT_INTERNAL)  # wins over every code
        return code


def _internal_line(error: BaseException) -> str:
    return f"internal_error: {type(error).__name__}\n"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _write(stream, line: str, code: int) -> int:
    stream.write(line)
    stream.flush()
    return code


def _configure_streams(stdin, stdout, stderr) -> None:
    for stream, errors in ((stdout, "replace"), (stderr, "replace"), (stdin, "surrogateescape")):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors=errors)


def _reject(stderr, code: str) -> int:
    return _write(stderr, f"rejected: {code} - {_SENTENCES[code]}\n", EXIT_USAGE)


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    stdin=None,
    stdout=None,
    stderr=None,
    provider_factory: Callable[[ChatConfig], object] | None = None,
    clock: Callable[[], datetime] | None = None,
    sleep: Callable[[float], None] | None = None,
    monotonic: Callable[[], float] | None = None,
) -> int:
    """Command entry point. Keyword arguments are test seams; the command line has none."""
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    _configure_streams(stdin, stdout, stderr)
    config: ChatConfig | None = None
    try:
        args = build_parser().parse_args(sys.argv[1:] if argv is None else list(argv))
        if args.command == "chat":
            config = resolve_chat_config(args, os.environ if environ is None else environ)
    except _HelpRequested as shown:
        stdout.write(shown.text)
        stdout.flush()
        return EXIT_OK
    except UsageRejected as rejected:
        return _reject(stderr, rejected.code)
    try:
        if config is not None:
            return _run_chat(
                config,
                (stdin, stdout, stderr),
                default_provider_factory if provider_factory is None else provider_factory,
                (
                    _utc_now if clock is None else clock,
                    time.sleep if sleep is None else sleep,
                    time.monotonic if monotonic is None else monotonic,
                ),
            )
        handler = _run_prepare_data if args.command == "prepare-data" else _run_prepare_policies
        return handler(args, stdout, stderr)
    except KeyboardInterrupt:
        return _write(stderr, _INTERRUPTED, EXIT_INTERRUPTED)
    except Exception as error:  # type name only, never the message or a traceback
        return _write(stderr, _internal_line(error), EXIT_INTERNAL)


if __name__ == "__main__":
    raise SystemExit(main())
