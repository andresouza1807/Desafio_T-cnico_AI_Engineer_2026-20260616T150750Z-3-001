"""Slice 08-C: diagnostic serializer and records (docs/specs/08-cli.md G11a-G11e; plan
CL96-CL103).

Pure functions over literal values of the real data classes (TurnResult, TurnDiagnostics,
AttemptRecord, ToolRunRecord, PreparationInfo, PolicyIndex, ChatConfig), including values
the real adapter would never produce. Expected strings are literals written here.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import get_args

import pytest

import emporio_agent.agent as agent_module
from emporio_agent import cli
from emporio_agent.agent import (
    AttemptRecord,
    BudgetFailure,
    ToolRunRecord,
    TurnDiagnostics,
    TurnOutcome,
    TurnResult,
)
from emporio_agent.data.policy_index import PolicyIndex, PolicySource
from emporio_agent.data.readonly import PreparationInfo
from emporio_agent.provider import Usage
from emporio_agent.services.orders import CustomerContext
from emporio_agent.tools import ToolStatus, tool_names

RECORD = re.compile(r"diag: [a-z]+( [a-z_0-9]+=[A-Za-z0-9_.:@,;-]+)+\n")
PRIVATE = "pasta-privada-08"
SENTINELS = (PRIVATE, "sk-SENTINEL-KEY", "SENTINEL-USER", "SENTINEL-MODEL", "SENTINEL-PAYLOAD")
START_KEYS = (
    "mode database schema_version prepared_at index index_format_version "
    "policy_source_sha256 configured_model reasoning_effort max_output_tokens max_messages"
).split()
TURN_KEYS = (
    "n outcome failure attempts retries tools references configured_model reported_models "
    "request_ids reasoning_efforts_reported max_request_bytes input_tokens output_tokens "
    "usage_missing usage_invalid "
    "budget_failure history_turns pruned_turns"
).split()
RESET_KEYS = ["trigger", "result", "mode"]
SHA = "0123456789abcdef" * 4
PREPARED = datetime(2026, 11, 4, 12, 30, 45, 123456, tzinfo=UTC)


def keys_of(record: str) -> list[str]:
    return [part.split("=", 1)[0] for part in record[len("diag: ") :].split()[1:]]


def values_of(record: str) -> dict[str, str]:
    return dict(part.split("=", 1) for part in record.rstrip("\n").split(" ")[2:])


def attempt(
    index=1,
    *,
    model="gpt-5.4-mini-2026-03-17",
    request_id="req_1",
    request_bytes=100,
    effort=None,
) -> AttemptRecord:
    return AttemptRecord(
        index=index,
        retry=False,
        outcome="response",
        error_kind=None,
        reported_model=model,
        request_id=request_id,
        duration_ms=5,
        request_bytes=request_bytes,
        usage=Usage(10, 2, 12, None, None),
        usage_invalid=False,
        reasoning_effort_reported=effort,
    )


def tool_run(tool="search_products", status="ok", references=()) -> ToolRunRecord:
    return ToolRunRecord(
        index=1, tool=tool, status=status, error_code=None, duration_ms=1, references=references
    )


def turn(
    *,
    outcome="answered",
    failure=None,
    attempts=(),
    tool_runs=(),
    configured_model="gpt-5.4-mini",
    budget_failure=None,
    input_tokens=None,
    output_tokens=None,
    retries=0,
) -> TurnResult:
    diagnostics = TurnDiagnostics(
        configured_model=configured_model,
        attempts=tuple(attempts),
        tool_runs=tuple(tool_runs),
        retries_used=retries,
        budget_failure=budget_failure,
        usage_reported_attempts=0,
        usage_missing_attempts=0,
        usage_invalid_attempts=0,
        input_tokens_reported=input_tokens,
        output_tokens_reported=output_tokens,
        history_turns_after=1,
        history_pruned_turns=0,
    )
    return TurnResult(
        outcome=outcome, text="SENTINEL-USER", failure=failure, diagnostics=diagnostics
    )


def config(database="emporio.sqlite3", customer=None, model="gpt-5.4-mini") -> cli.ChatConfig:
    return cli.ChatConfig(
        model=model,
        api_key="sk-SENTINEL-KEY",
        database=Path(PRIVATE) / database,
        index=Path(PRIVATE) / "policy-index.json",
        customer=customer,
        diagnostics=True,
    )


def info(prepared_at=PREPARED, schema_version=1) -> PreparationInfo:
    return PreparationInfo(schema_version=schema_version, prepared_at=prepared_at, sources=())


def policy(sha=SHA, format_version=1) -> PolicyIndex:
    source = PolicySource(
        file_name="manual-sintetico.pdf",
        sha256=sha,
        size_bytes=1000,
        page_count=5,
        format_version=format_version,
        extraction_rules="layout-v1",
        pypdf_version="test",
    )
    return PolicyIndex(source=source, sections=())


def assert_safe(record: str) -> None:
    assert RECORD.fullmatch(record), record
    assert record.count("\n") == 1 and "\r" not in record
    for sentinel in SENTINELS:
        assert sentinel not in record


# --- CL96: basename (reuse control of the 08-B field function) ---------------------------


@pytest.mark.parametrize(
    "name",
    [
        "meu banco.sqlite3",
        "a\nb.sqlite3",
        "a\tb.sqlite3",
        "a\x1bb.sqlite3",
        "bancoção.sqlite3",
        "índice.json",
        ".oculto",
        "-x",
        "x" * 129,
    ],
)
def test_cl96_hostile_database_base_name_prints_unprintable_in_start(name):
    record = cli.start_record(config(database=name), info(), policy())
    assert values_of(record)["database"] == "unprintable"
    assert_safe(record)
    assert name not in record


def test_cl96_empty_base_name_and_valid_name():
    # Path(PRIVATE) / "" is Path(PRIVATE) itself, so the empty name is tested as text.
    assert cli.diag_field("basename", "") == "unprintable"
    assert cli.diag_field("basename", Path(PRIVATE) / "emporio.sqlite3") == "emporio.sqlite3"


# --- CL97: request_id ---------------------------------------------------------------------


@pytest.mark.parametrize("value", ["req_abc123", "r" + "x" * 127])
def test_cl97_valid_request_ids_printed(value):
    assert cli.diag_field("request_id", value) == value


@pytest.mark.parametrize(
    "value",
    [
        "req 1",
        "req\t1",
        "req\n1",
        " req_1",
        "req_1 ",
        "\x1b[31m",
        "req/1",
        "req;1",
        "req=1",
        "ção",
        "r" * 129,
        "",
        b"req_1",
        1,
    ],
)
def test_cl97_hostile_request_ids_unprintable_without_trim_or_truncation(value):
    assert cli.diag_field("request_id", value) == "unprintable"


def test_cl97_absent_request_id_is_dash():
    assert cli.diag_field("request_id", None) == "-"


# --- CL98: model --------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["gpt-5.4-mini", "gpt-5.4-mini-2026-03-17", "a:b_c.d-e"])
def test_cl98_valid_models_printed(value):
    assert cli.diag_field("model", value) == value


@pytest.mark.parametrize(
    "value", ["gpt 5", "gpt-5\n", "modelo-é", "\x1b[0m", "", "m" * 129, "SENTINEL-MODEL/x"]
)
def test_cl98_hostile_reported_model_does_not_affect_configured_model(value):
    record = cli.turn_record(1, turn(attempts=[attempt(model=value)]))
    values = values_of(record)
    assert values["reported_models"] == "unprintable"
    assert values["configured_model"] == "gpt-5.4-mini"
    assert_safe(record)


# --- CL99: references, tool runs, lists ---------------------------------------------------


def test_cl99_valid_references_joined_by_semicolon_in_execution_order():
    runs = [
        tool_run("get_product", references=("product:10",)),
        tool_run("search_policies", references=("policy:3.2@p4,5", "policy:front_matter@p1")),
        tool_run("get_order", references=("order",)),
    ]
    values = values_of(cli.turn_record(1, turn(tool_runs=runs)))
    assert values["references"] == "product:10;policy:3.2@p4,5;policy:front_matter@p1;order"
    assert values["tools"] == "get_product:ok;search_policies:ok;get_order:ok"


@pytest.mark.parametrize(
    "hostile",
    [
        "product:abc",
        "product:01",
        "order:1001",
        "policy:1@p0",
        "policy:x y@p1",
        "policy:1@p1;x",
        "policy:1=2@p1",
        "policy:1@p1000",
        "policy:" + "1." * 130 + "1@p1",
        "",
    ],
)
def test_cl99_hostile_reference_replaced_in_its_position_only(hostile):
    runs = [tool_run(references=("product:10", hostile, "order"))]
    record = cli.turn_record(1, turn(tool_runs=runs))
    assert values_of(record)["references"] == "product:10;unprintable;order"
    assert_safe(record)


# PX06 (spec 08 Review 08-R8): quote references carry an optional payment-method suffix.


def test_px06_quote_references_with_payment_method_suffix_are_printed():
    runs = [
        tool_run("quote_product", references=("product:13@pix",)),
        tool_run("quote_product", references=("product:10@regular",)),
        tool_run("quote_product", references=("product:12",)),
    ]
    record = cli.turn_record(1, turn(tool_runs=runs))
    values = values_of(record)
    assert values["references"] == "product:13@pix;product:10@regular;product:12"
    assert values["tools"] == "quote_product:ok;quote_product:ok;quote_product:ok"
    assert_safe(record)


@pytest.mark.parametrize(
    "hostile",
    [
        "product:10@",
        "product:10@PIX",
        "product:10@Pix",
        "product:10@credit",
        "product:10@pix@pix",
        "product:10@pix@regular",
        "product:10@pix ",
        "product:10@pix\n",
        "product:10@pix\x1b[31m",
        "product:10@pix;order",
        "product:10@pix=1",
        "product:10@pìx",
        "product:10@regularx",
        "product:01@pix",
        "product:@pix",
        "@pix",
        "order@pix",
        "policy:1@p1@pix",
    ],
)
def test_px06_unknown_or_hostile_suffix_is_unprintable(hostile):
    runs = [tool_run("quote_product", references=("product:10@pix", hostile, "order"))]
    record = cli.turn_record(1, turn(tool_runs=runs))
    assert values_of(record)["references"] == "product:10@pix;unprintable;order"
    assert_safe(record)


def test_cl99_empty_lists_are_dash():
    values = values_of(cli.turn_record(1, turn()))
    assert (values["tools"], values["references"]) == ("-", "-")


@pytest.mark.parametrize(
    ("run", "expected"),
    [
        (tool_run(tool=None, status="ok"), "unknown:ok"),
        (tool_run(tool="drop_table", status="ok"), "unprintable:ok"),
        (tool_run(tool="search_products", status="weird"), "search_products:unprintable"),
        (tool_run(tool="x y", status="o k"), "unprintable:unprintable"),
    ],
)
def test_cl99_tool_run_parts_serialized_independently(run, expected):
    assert values_of(cli.turn_record(1, turn(tool_runs=[run])))["tools"] == expected


def test_cl99_non_record_tool_run_is_unprintable():
    assert cli.diag_field("tool_run", ("search_products", "ok")) == "unprintable"


# --- CL100: vocabularies ------------------------------------------------------------------


def test_cl100_frozen_vocabularies_equal_the_source_literals():
    assert cli.DIAG_VOCABULARIES["outcome"] == frozenset(get_args(TurnOutcome.__value__))
    assert cli.DIAG_VOCABULARIES["budget"] == frozenset(get_args(BudgetFailure.__value__))
    assert cli.DIAG_VOCABULARIES["tool_status"] == frozenset(get_args(ToolStatus.__value__))
    assert cli.DIAG_VOCABULARIES["tool"] == frozenset(tool_names())
    assert cli.DIAG_VOCABULARIES["failure"] == frozenset(agent_module._FAILURE_TEXTS) | frozenset(
        agent_module._INPUT_TEXTS
    )
    assert len(cli.DIAG_VOCABULARIES["failure"]) == 23  # + response_withheld (Review 08-R9)
    assert cli.DIAG_VOCABULARIES["mode"] == {"anonymous", "linked"}
    assert cli.DIAG_VOCABULARIES["effort"] == {"low", "omitted"}
    assert cli.DIAG_VOCABULARIES["trigger"] == {"reiniciar", "cliente"}
    assert cli.DIAG_VOCABULARIES["switch_result"] == {
        "ok",
        "argument_invalid",
        "not_found",
        "lookup_failed",
    }


@pytest.mark.parametrize(
    "kind", ["outcome", "budget", "tool_status", "tool", "failure", "mode", "effort"]
)
def test_cl100_every_source_value_printed_unchanged(kind):
    for value in cli.DIAG_VOCABULARIES[kind]:
        assert cli.diag_field(kind, value) == value


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("outcome", "exploded"),
        ("failure", "provider_exploded"),
        ("budget", "tokens"),
        ("mode", "admin"),
        ("trigger", "sair"),
        ("switch_result", "maybe"),
        ("outcome", 1),
        ("mode", "Linked"),
    ],
)
def test_cl100_unknown_values_unprintable(kind, value):
    assert cli.diag_field(kind, value) == "unprintable"


def test_cl100_turn_with_unknown_outcome_failure_and_budget():
    record = cli.turn_record(
        1, turn(outcome="exploded", failure="SENTINEL-PAYLOAD", budget_failure="huge")
    )
    values = values_of(record)
    assert (values["outcome"], values["failure"], values["budget_failure"]) == (
        "unprintable",
        "unprintable",
        "unprintable",
    )
    assert_safe(record)


def test_cl100_reset_record_literals():
    assert cli.reset_record("reiniciar", "ok", "anonymous") == (
        "diag: reset trigger=reiniciar result=ok mode=anonymous\n"
    )
    assert cli.reset_record("cliente", "lookup_failed", "anonymous") == (
        "diag: reset trigger=cliente result=lookup_failed mode=anonymous\n"
    )
    assert cli.reset_record("cliente 2", "SENTINEL-USER", "linked") == (
        "diag: reset trigger=unprintable result=unprintable mode=linked\n"
    )


# --- CL101: absence, integers, datetime, sha256 -------------------------------------------


def test_cl101_absent_and_zero_tokens():
    assert values_of(cli.turn_record(1, turn(input_tokens=None)))["input_tokens"] == "-"
    assert values_of(cli.turn_record(1, turn(input_tokens=0)))["input_tokens"] == "0"


def test_cl101_zero_attempts():
    values = values_of(cli.turn_record(1, turn()))
    assert (
        values["attempts"],
        values["reported_models"],
        values["request_ids"],
        values["max_request_bytes"],
    ) == ("0", "-", "-", "-")


def test_cl101_attempts_without_request_ids():
    one = values_of(cli.turn_record(1, turn(attempts=[attempt(request_id=None)])))
    assert (one["attempts"], one["request_ids"]) == ("1", "-")
    two = values_of(
        cli.turn_record(
            1,
            turn(
                attempts=[
                    attempt(1, request_id=None, request_bytes=300),
                    attempt(2, request_id="req_2", request_bytes=700),
                ]
            ),
        )
    )
    assert (two["attempts"], two["request_ids"], two["max_request_bytes"]) == (
        "2",
        "-;req_2",
        "700",
    )


@pytest.mark.parametrize("value", [True, -1, 2**63, "6", 6.0])
def test_cl101_hostile_integers_unprintable(value):
    assert cli.diag_field("int", value) == "unprintable"
    values = values_of(cli.turn_record(value, turn(output_tokens=value)))
    assert (values["n"], values["output_tokens"]) == ("unprintable", "unprintable")


@pytest.mark.parametrize("value", [0, 6, 2**63 - 1])
def test_cl101_integers_reuse_08b_rule(value):
    assert cli.diag_field("int", value) == str(value)


@pytest.mark.parametrize(
    "value",
    [
        datetime(2026, 11, 4, 12, 30, 45),
        datetime(2026, 11, 4, 12, 30, 45, tzinfo=timezone(timedelta(hours=3))),
        "2026-11-04T12:30:45Z",
    ],
)
def test_cl101_non_utc_prepared_at_unprintable(value):
    assert values_of(cli.start_record(config(), info(prepared_at=value), policy()))[
        "prepared_at"
    ] == ("unprintable")


def test_cl101_utc_datetime_drops_microseconds():
    assert cli.diag_field("datetime", PREPARED) == "2026-11-04T12:30:45Z"
    offset_zero = datetime(2026, 11, 4, 1, 2, 3, tzinfo=timezone(timedelta(0)))
    assert cli.diag_field("datetime", offset_zero) == "2026-11-04T01:02:03Z"


@pytest.mark.parametrize("value", [SHA.upper(), SHA[:63], 1])
def test_cl101_hostile_sha256_unprintable(value):
    record = cli.start_record(config(), info(), policy(sha=value))
    assert values_of(record)["policy_source_sha256"] == "unprintable"


# --- CL102: record layout -----------------------------------------------------------------


def test_cl102_start_record_literal_and_key_order():
    record = cli.start_record(config(customer=CustomerContext(2)), info(), policy())
    assert record == (
        "diag: start mode=linked database=emporio.sqlite3 schema_version=1 "
        "prepared_at=2026-11-04T12:30:45Z index=policy-index.json index_format_version=1 "
        f"policy_source_sha256={SHA} configured_model=gpt-5.4-mini reasoning_effort=low "
        "max_output_tokens=4096 max_messages=30\n"
    )
    assert keys_of(record) == START_KEYS
    anonymous = cli.start_record(config(), info(), policy())
    assert values_of(anonymous)["mode"] == "anonymous"
    assert "2" not in values_of(record)["mode"]


def test_cl102_start_max_messages_follows_the_loop_constant(monkeypatch):
    # RI-4 (spec R6, G9a): the record reads MAX_MESSAGES_PER_RUN, not a separate literal.
    monkeypatch.setattr(cli, "MAX_MESSAGES_PER_RUN", 7)
    record = cli.start_record(config(), info(), policy())
    assert values_of(record)["max_messages"] == "7"
    assert keys_of(record) == START_KEYS
    assert RECORD.fullmatch(record)


def test_cl102_turn_record_has_every_key_once_in_order():
    record = cli.turn_record(
        3,
        turn(
            attempts=[attempt()],
            tool_runs=[tool_run(references=("product:10", "product:11"))],
            input_tokens=120,
            output_tokens=30,
        ),
    )
    assert keys_of(record) == TURN_KEYS
    assert record == (
        "diag: turn n=3 outcome=answered failure=- attempts=1 retries=0 "
        "tools=search_products:ok references=product:10;product:11 "
        "configured_model=gpt-5.4-mini reported_models=gpt-5.4-mini-2026-03-17 "
        "request_ids=req_1 reasoning_efforts_reported=- max_request_bytes=100 "
        "input_tokens=120 output_tokens=30 "
        "usage_missing=0 usage_invalid=0 budget_failure=- history_turns=1 pruned_turns=0\n"
    )


HOSTILE = ["a b", "a\nb", "a\rb", "\x1b[31m", "a=b", "x;y", "ção", "", PRIVATE + "/x"]


@pytest.mark.parametrize("value", HOSTILE)
def test_cl102_every_record_stays_one_line_for_hostile_values(value):
    records = [
        cli.start_record(
            config(database=value + ".sqlite3", model=value),
            info(prepared_at=value),
            policy(sha=value),
        ),
        cli.turn_record(
            value,
            turn(
                outcome=value,
                failure=value,
                attempts=[attempt(model=value, request_id=value, effort=value)],
                tool_runs=[tool_run(tool=value, status=value, references=(value,))],
                configured_model=value,
                budget_failure=value,
            ),
        ),
        cli.reset_record(value, value, value),
    ]
    for record, keys in zip(records, (START_KEYS, TURN_KEYS, RESET_KEYS), strict=True):
        assert_safe(record)
        assert keys_of(record) == keys


def test_cl102_final_guard_catches_a_value_forced_past_the_field_rules(monkeypatch):
    monkeypatch.setattr(cli, "_diag_value", lambda kind, value: str(value))
    record = cli.turn_record(1, turn(attempts=[attempt(request_id="req 1\nx")]))
    assert values_of(record)["request_ids"] == "unprintable"
    assert_safe(record)


def test_cl102_list_longer_than_guard_limit_is_unprintable():
    runs = [tool_run(references=("policy:1@p" + ",".join(["1"] * 120),) * 20)]
    assert values_of(cli.turn_record(1, turn(tool_runs=runs)))["references"] == "unprintable"


# --- CL103: fallback content ----------------------------------------------------------------


def test_cl103_fallback_is_the_exact_literal_without_any_part_of_the_input():
    for kind in ("model", "request_id", "reference", "basename", "sha256", "mode", "int"):
        for value in ("SENTINEL-PAYLOAD x", PRIVATE + "/sk-SENTINEL-KEY", "SENTINEL-USER\n"):
            assert cli.diag_field(kind, value) == "unprintable"
    record = cli.start_record(
        config(
            database="SENTINEL-USER db", customer=CustomerContext(1001), model="SENTINEL-MODEL x"
        ),
        info(),
        policy(),
    )
    assert_safe(record)
    assert "1001" not in record and "customer" not in record


# --- RM08-RM09: reported reasoning effort (spec 08 Review 08-R7) ---------------------------


def _error_attempt(index: int) -> AttemptRecord:
    return AttemptRecord(
        index=index,
        retry=False,
        outcome="error",
        error_kind="timeout",
        reported_model=None,
        request_id=None,
        duration_ms=5,
        request_bytes=100,
        usage=None,
        usage_invalid=False,
    )


def test_rm08_turn_literal_with_reported_efforts_and_absent_positions():
    attempts = [
        _error_attempt(1),
        attempt(2, request_id="req_2", effort="medium"),
        attempt(3, request_id="req_3"),
        attempt(4, request_id="req_4", effort="medium"),
    ]
    record = cli.turn_record(1, turn(attempts=attempts, retries=1))
    assert keys_of(record) == TURN_KEYS
    assert record == (
        "diag: turn n=1 outcome=answered failure=- attempts=4 retries=1 tools=- references=- "
        "configured_model=gpt-5.4-mini reported_models=-;gpt-5.4-mini-2026-03-17;"
        "gpt-5.4-mini-2026-03-17;gpt-5.4-mini-2026-03-17 request_ids=-;req_2;req_3;req_4 "
        "reasoning_efforts_reported=-;medium;-;medium max_request_bytes=100 input_tokens=- "
        "output_tokens=- usage_missing=0 usage_invalid=0 budget_failure=- history_turns=1 "
        "pruned_turns=0\n"
    )


def test_rm08_zero_attempts_and_single_absent_value_are_dashes():
    assert values_of(cli.turn_record(1, turn()))["reasoning_efforts_reported"] == "-"
    single = cli.turn_record(1, turn(attempts=[attempt()]))
    assert values_of(single)["reasoning_efforts_reported"] == "-"
    assert values_of(single)["attempts"] == "1"


def test_rm08_requested_low_is_not_printed_as_reported():
    # diag: start keeps the requested effort; diag: turn prints only reported values.
    start = cli.start_record(config(), info(), policy())
    assert values_of(start)["reasoning_effort"] == "low"
    assert "reasoning_efforts_reported" not in keys_of(start)
    record = cli.turn_record(1, turn(attempts=[attempt(), attempt(2)]))
    assert values_of(record)["reasoning_efforts_reported"] == "-;-"


def test_rm09_vocabulary_is_a_frozen_copy_of_the_adapter_constant():
    from emporio_agent.openai_provider import REPORTED_REASONING_EFFORTS

    assert cli.DIAG_VOCABULARIES["effort_reported"] == frozenset(REPORTED_REASONING_EFFORTS)
    assert cli.DIAG_VOCABULARIES["effort_reported"] == {
        "none",
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    }
    for value in cli.DIAG_VOCABULARIES["effort_reported"]:
        assert cli.diag_field("effort_reported", value) == value
    # The requested-effort vocabulary is unchanged and separate.
    assert cli.DIAG_VOCABULARIES["effort"] == {"low", "omitted"}


@pytest.mark.parametrize(
    "value",
    ["LOW", " low", "low ", "omitted", "", "SENTINEL-PAYLOAD x", "a;b", "a=b", "\x1b[31m", 1, True],
)
def test_rm09_hostile_reported_effort_is_unprintable(value):
    record = cli.turn_record(1, turn(attempts=[attempt(effort=value), attempt(2, effort="low")]))
    assert values_of(record)["reasoning_efforts_reported"] == "unprintable;low"
    assert keys_of(record) == TURN_KEYS
    assert_safe(record)
