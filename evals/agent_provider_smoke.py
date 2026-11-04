"""Opt-in smoke evaluation of the agent against the real OpenAI Responses API.

docs/plans/07-agent.md, slice E: EV01-EV08. Contract, matrix and run command:
docs/validation/07-05-agent-provider-smoke.md. Not part of pytest.

Usage (nothing runs on import; real calls only with the explicit flag):

    OPENAI_API_KEY set by the operator in the environment (never as an argument)
    uv run --frozen python evals/agent_provider_smoke.py --allow-real-calls --model <model>
        [--reasoning-effort low|medium|high|xhigh]

Successor contract (spec review 07-07-R1, docs/validation/07-08-ev03-offline.md): the
optional --reasoning-effort is sent as reasoning.effort by every provider built here and
raises the per-call output cap from 1,024 to 4,096 tokens (a cost cap, not a sufficiency
guarantee). Without it no `reasoning` key is sent. EV03 passes only when a request that
resent a reasoning item with encrypted_content is answered with status=completed.

Rules:

- Without --allow-real-calls, a valid --model, a valid --reasoning-effort (when given), an
  unset OPENAI_LOG and a usable OPENAI_API_KEY the script stops before it creates any
  resource or client. The flags and the model are checked before the environment is read.
  The adapter, and so `openai`, is imported only after these checks.
- At most MAX_PROVIDER_CALLS provider calls in the whole run, counting every agent
  attempt, the agent retry and the direct adapter calls (EV07, EV08). The next call is
  never sent; the run is reported as interrupted.
- Data: only the committed synthetic CSVs of tests/fixtures/data/valid, prepared by the
  real prepare_database into a private temporary directory, and a synthetic policy index
  validated by the real load_policy_index. No path argument exists. The directory is
  removed at the end, also on failure. The synthetic index has a literal disclosure review
  (report 10-07), registered only during the run, so search_policies runs its real path.
- Output: metadata only (case IDs, fixed codes, model names, counts, booleans, bytes,
  durations, tokens, request IDs). Never the key, other headers, provider messages,
  prompts, user or model text, tool arguments or payloads, reasoning or
  encrypted_content, call IDs, data rows or local paths. SDK loggers are silenced.
- Model behavior is observed, never forced: no scripted response is used here.

Exit codes: 0 every EV passed; 1 at least one EV failed; 2 usage or configuration
rejected; 3 no EV failed and at least one not observed; 4 interrupted (call limit or
keyboard); 5 synthetic data preparation failed; 6 internal error or cleanup failed.
"""

import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, NoReturn

from emporio_agent.agent import Agent, AgentResources, TurnResult
from emporio_agent.data.errors import DataPreparationError
from emporio_agent.data.policy_index import (
    EXTRACTION_RULES,
    FRONT_MATTER_ID,
    INDEX_FORMAT,
    INDEX_FORMAT_VERSION,
    PolicyIndexUnavailableError,
    load_policy_index,
)
from emporio_agent.data.preparation import prepare_database
from emporio_agent.instructions import session_instructions
from emporio_agent.provider import (
    ConversationItem,
    FunctionCall,
    FunctionCallOutput,
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderItem,
    Usage,
    UserMessage,
    item_size_bytes,
    static_size_bytes,
)
from emporio_agent.services import policy_disclosure
from emporio_agent.session import ConversationSession
from emporio_agent.tools import provider_tool_definitions

REPO_ROOT: Final = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "data" / "valid"

KEY_ENV: Final = "OPENAI_API_KEY"
SDK_LOG_ENV: Final = "OPENAI_LOG"
MAX_PROVIDER_CALLS: Final = 12
MAX_OUTPUT_TOKENS_PER_CALL: Final = 1024
MAX_OUTPUT_TOKENS_WITH_REASONING: Final = 4096
# Same values as emporio_agent.openai_provider.REASONING_EFFORTS (a test checks this); not
# imported, because the adapter imports openai, which must wait for the configuration checks.
REASONING_EFFORTS: Final = ("low", "medium", "high", "xhigh")
TIMEOUT_SECONDS: Final = 30.0
UNKNOWN_MODEL: Final = "emporio-smoke-unknown-model-0000"
MIN_BYTES_PER_INPUT_TOKEN: Final = 2.5
# Spec G10b design allowance for model items in one turn chain:
# MAX_TURN_CHAIN_BYTES - 4,000-code-point user message - six full tool envelopes.
MODEL_ITEMS_ALLOWANCE_BYTES: Final = 262_144 - 16_000 - 6 * 32_768
MODEL_UNAVAILABLE_MESSAGE: Final = "The configured model is not available."

CLOCK: Final = datetime(2026, 11, 3, 15, 0, tzinfo=UTC)
PREPARED_AT: Final = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
TURN_1_TEXT: Final = "Quais violões vocês têm disponíveis? Quero saber o preço."
TURN_2_TEXT: Final = "E quanto fica esse violão no PIX?"
EV07_TEXT: Final = "Obrigado! Pode resumir em uma frase o que você encontrou?"
EV08_TEXT: Final = "Olá"

EXIT_PASSED: Final = 0
EXIT_FAILED: Final = 1
EXIT_USAGE: Final = 2
EXIT_NOT_OBSERVED: Final = 3
EXIT_INTERRUPTED: Final = 4
EXIT_DATA: Final = 5
EXIT_INTERNAL: Final = 6

_MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_PRINTABLE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}")
_SDK_LOGGERS: Final = ("openai", "httpx2", "httpcore2", "httpx", "httpcore")
_TRANSIENT_TURN_FAILURES: Final = frozenset(
    {"provider_timeout", "provider_unavailable", "provider_rate_limited"}
)
_CASES: Final = ("EV01", "EV02", "EV03", "EV04", "EV05", "EV06", "EV07", "EV08")


# --- configuration --------------------------------------------------------------------


class UsageRejected(Exception):
    """Argument or configuration rejected. Holds a fixed code only."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class DataSetupError(Exception):
    """Synthetic data could not be prepared. Holds a fixed code only."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class SmokeConfig:
    model: str
    api_key: str = field(repr=False)
    reasoning_effort: str | None = None

    @property
    def max_output_tokens(self) -> int:
        if self.reasoning_effort is None:
            return MAX_OUTPUT_TOKENS_PER_CALL
        return MAX_OUTPUT_TOKENS_WITH_REASONING


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        # The argparse message can repeat the given values (a key typed by mistake).
        raise UsageRejected("arguments_invalid")


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="agent_provider_smoke.py",
        description="Opt-in smoke evaluation of the agent against the real OpenAI API.",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--allow-real-calls",
        action="store_true",
        help="required: allow real, billed calls to the OpenAI API",
    )
    parser.add_argument("--model", help="model sent unchanged (no default, no fallback)")
    parser.add_argument(
        "--reasoning-effort",
        help="optional reasoning.effort: low, medium, high or xhigh (absent: not sent)",
    )
    return parser


def resolve_config(args: argparse.Namespace, environ: Mapping[str, str]) -> SmokeConfig:
    """Opt-in, then model, then environment. The environment is read only at the end."""
    if not args.allow_real_calls:
        raise UsageRejected("opt_in_required")
    model = args.model
    if model is None:
        raise UsageRejected("model_required")
    if type(model) is not str or _MODEL_NAME.fullmatch(model) is None:
        raise UsageRejected("model_invalid")
    if model == UNKNOWN_MODEL:
        raise UsageRejected("model_reserved")
    effort = args.reasoning_effort
    if effort is not None and (type(effort) is not str or effort not in REASONING_EFFORTS):
        raise UsageRejected("reasoning_effort_invalid")
    if SDK_LOG_ENV in environ:
        raise UsageRejected("sdk_logging_enabled")
    key = environ.get(KEY_ENV)
    if key is None or key == "":
        raise UsageRejected("key_missing")
    if key != key.strip():
        raise UsageRejected("key_invalid")
    return SmokeConfig(model=model, api_key=key, reasoning_effort=effort)


@contextmanager
def _quiet_sdk_logging() -> Iterator[None]:
    """Raise the SDK and transport loggers above CRITICAL for the run, then restore."""
    saved = [(logging.getLogger(name), logging.getLogger(name).level) for name in _SDK_LOGGERS]
    try:
        for logger, _ in saved:
            logger.setLevel(logging.CRITICAL + 1)
        yield
    finally:
        for logger, level in saved:
            logger.setLevel(level)


# --- output ---------------------------------------------------------------------------


def _show(value: object) -> str:
    if value is None or value == ():
        return "-"
    if isinstance(value, tuple):
        return ",".join(_show(part) for part in value)
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.2f}"
    if isinstance(value, str) and _PRINTABLE.fullmatch(value):
        return value
    return "unprintable"


def _record(head: str, fields: Sequence[tuple[str, object]]) -> str:
    return " ".join([head, *(f"{key}={_show(value)}" for key, value in fields)])


# --- observation of every provider call -----------------------------------------------


def _raw_fields(raw: str) -> dict[str, Any]:
    value = json.loads(raw)
    return value if isinstance(value, dict) else {}


def _encrypted(fields: Mapping[str, Any]) -> str | None:
    value = fields.get("encrypted_content")
    return value if isinstance(value, str) and value != "" else None


@dataclass
class _Attempt:
    """Metadata of one real provider call. call_ids stay in memory and are never printed."""

    number: int
    scenario: str
    path: str
    allow_tool_calls: bool
    tools: int
    request_bytes: int
    input_items: int
    resent_reasoning: int
    resent_encrypted: int
    resent_with_id: int
    resent_with_status: int
    resent_with_phase: int
    previous_calls_returned: bool | None
    history_resent: bool | None
    outcome: str = "error"
    duration_ms: int = 0
    error_kind: str | None = None
    transient: bool = False
    status_code: int | None = None
    message_fixed: bool | None = None
    request_id: str | None = None
    reported_model: str | None = None
    status: str | None = None
    incomplete_reason: str | None = None
    calls: int = 0
    reasoning_items: int = 0
    encrypted_items: int = 0
    encrypted_bytes: int = 0
    reasoning_bytes: int = 0
    assistant_messages: int = 0
    assistant_bytes: int = 0
    function_call_bytes: int = 0
    phase_commentary: int = 0
    phase_final: int = 0
    phase_absent: int = 0
    text_present: bool = False
    refusal: bool = False
    usage: Usage | None = None
    usage_invalid: bool = False
    call_ids: frozenset[str] = frozenset()

    @property
    def model_item_bytes(self) -> int:
        return self.reasoning_bytes + self.assistant_bytes + self.function_call_bytes

    @property
    def bytes_per_input_token(self) -> float | None:
        if self.usage is None or self.usage.input_tokens <= 0:
            return None
        return self.request_bytes / self.usage.input_tokens

    def line(self) -> str:
        response = self.outcome == "response"

        def r(value: object) -> object:  # response-only field
            return value if response else None

        usage = self.usage
        if not response:
            usage_state = None
        elif usage is not None:
            usage_state = "reported"
        else:
            usage_state = "invalid" if self.usage_invalid else "missing"
        return _record(
            "attempt:",
            [
                ("n", self.number),
                ("scenario", self.scenario),
                ("path", self.path),
                ("outcome", self.outcome),
                ("error_kind", self.error_kind),
                ("status_code", self.status_code),
                ("request_id", self.request_id),
                ("reported_model", self.reported_model),
                ("duration_ms", self.duration_ms),
                ("request_bytes", self.request_bytes),
                ("allow_tool_calls", self.allow_tool_calls),
                ("tools", self.tools),
                ("input_items", self.input_items),
                ("resent_reasoning", self.resent_reasoning),
                ("resent_encrypted", self.resent_encrypted),
                ("resent_with_id", self.resent_with_id),
                ("resent_with_status", self.resent_with_status),
                ("resent_with_phase", self.resent_with_phase),
                ("previous_calls_returned", self.previous_calls_returned),
                ("history_resent", self.history_resent),
                ("status", self.status),
                ("incomplete_reason", self.incomplete_reason),
                ("calls", r(self.calls)),
                ("reasoning_items", r(self.reasoning_items)),
                ("encrypted_items", r(self.encrypted_items)),
                ("encrypted_bytes", r(self.encrypted_bytes)),
                ("reasoning_bytes", r(self.reasoning_bytes)),
                ("assistant_messages", r(self.assistant_messages)),
                ("assistant_bytes", r(self.assistant_bytes)),
                ("function_call_bytes", r(self.function_call_bytes)),
                ("phase_commentary", r(self.phase_commentary)),
                ("phase_final", r(self.phase_final)),
                ("phase_absent", r(self.phase_absent)),
                ("text", r(self.text_present)),
                ("refusal", r(self.refusal)),
                ("usage", usage_state),
                ("input_tokens", usage.input_tokens if usage else None),
                ("output_tokens", usage.output_tokens if usage else None),
                ("total_tokens", usage.total_tokens if usage else None),
                ("cached_input_tokens", usage.cached_input_tokens if usage else None),
                ("reasoning_tokens", usage.reasoning_tokens if usage else None),
                ("bytes_per_input_token", self.bytes_per_input_token),
            ],
        )


class _Ledger:
    """Global call counter and observer shared by every provider of the run."""

    def __init__(self, limit: int, monotonic: Callable[[], float], emit: Callable[[str], None]):
        self.limit = limit
        self.used = 0
        self.blocked = 0
        self.blocked_in: set[str] = set()
        self.attempts: list[_Attempt] = []
        self._monotonic = monotonic
        self._emit = emit
        self._scenario = "-"
        self._path = "-"
        self._prefix: tuple[ConversationItem, ...] | None = None
        self._pending: frozenset[str] = frozenset()

    def begin(
        self, scenario: str, path: str, prefix: tuple[ConversationItem, ...] | None = None
    ) -> None:
        self._scenario, self._path, self._prefix = scenario, path, prefix
        self._pending = frozenset()

    def of(self, scenario: str) -> list[_Attempt]:
        return [attempt for attempt in self.attempts if attempt.scenario == scenario]

    def call(self, inner: Any, request: ModelRequest) -> ModelResponse:
        if self.used >= self.limit:
            self.blocked += 1
            self.blocked_in.add(self._scenario)
            raise ProviderError("unexpected")  # the call is never sent
        self.used += 1
        attempt = self._describe(request)
        start = self._monotonic()
        try:
            response = inner.create(request)
        except ProviderError as error:
            attempt.duration_ms = self._elapsed(start)
            attempt.error_kind = error.kind
            attempt.transient = error.transient
            attempt.status_code = error.status_code
            attempt.request_id = error.request_id
            if error.kind == "model_unavailable":
                attempt.message_fixed = (
                    str(error) == MODEL_UNAVAILABLE_MESSAGE and error.__cause__ is None
                )
            self._finish(attempt)
            raise
        except Exception:
            attempt.duration_ms = self._elapsed(start)
            attempt.error_kind = "unexpected"
            self._finish(attempt)
            raise
        attempt.duration_ms = self._elapsed(start)
        self._observe(attempt, response)
        self._finish(attempt)
        self._pending = attempt.call_ids
        return response

    def _elapsed(self, start: float) -> int:
        return max(0, round((self._monotonic() - start) * 1000))

    def _finish(self, attempt: _Attempt) -> None:
        self.attempts.append(attempt)
        self._emit(attempt.line())

    def _describe(self, request: ModelRequest) -> _Attempt:
        items = request.items
        call_ids = {item.call_id for item in items if isinstance(item, FunctionCall)}
        output_ids = {item.call_id for item in items if isinstance(item, FunctionCallOutput)}
        counts = {"reasoning": 0, "encrypted": 0, "id": 0, "status": 0, "phase": 0}
        for item in items:
            if not isinstance(item, ProviderItem | FunctionCall):
                continue
            fields = _raw_fields(item.raw)
            if isinstance(item, ProviderItem) and item.kind == "reasoning":
                counts["reasoning"] += 1
                counts["encrypted"] += _encrypted(fields) is not None
            for key in ("id", "status", "phase"):
                counts[key] += fields.get(key) is not None
        previous = None
        if self._pending:
            previous = self._pending <= call_ids and self._pending <= output_ids
        history = None
        if self._prefix is not None:
            size = len(self._prefix)
            history = len(items) > size and tuple(items[:size]) == self._prefix
        return _Attempt(
            number=self.used,
            scenario=self._scenario,
            path=self._path,
            allow_tool_calls=request.allow_tool_calls,
            tools=len(request.tools),
            request_bytes=static_size_bytes(request.instructions, request.tools)
            + sum(item_size_bytes(item) for item in items),
            input_items=len(items),
            resent_reasoning=counts["reasoning"],
            resent_encrypted=counts["encrypted"],
            resent_with_id=counts["id"],
            resent_with_status=counts["status"],
            resent_with_phase=counts["phase"],
            previous_calls_returned=previous,
            history_resent=history,
        )

    @staticmethod
    def _observe(attempt: _Attempt, response: ModelResponse) -> None:
        attempt.outcome = "response"
        attempt.request_id = response.request_id
        attempt.reported_model = response.model
        attempt.status = response.status
        attempt.incomplete_reason = response.incomplete_reason
        attempt.text_present = response.text.strip() != ""
        attempt.refusal = response.refusal
        attempt.usage = response.usage
        attempt.usage_invalid = response.usage_invalid
        ids: set[str] = set()
        for item in response.output:
            size = item_size_bytes(item)
            if isinstance(item, FunctionCall):
                attempt.calls += 1
                attempt.function_call_bytes += size
                ids.add(item.call_id)
                continue
            fields = _raw_fields(item.raw)
            if item.kind == "reasoning":
                attempt.reasoning_items += 1
                attempt.reasoning_bytes += size
                encrypted = _encrypted(fields)
                if encrypted is not None:
                    attempt.encrypted_items += 1
                    attempt.encrypted_bytes += len(encrypted.encode("utf-8"))
            else:
                attempt.assistant_messages += 1
                attempt.assistant_bytes += size
                phase = fields.get("phase")
                if phase == "commentary":
                    attempt.phase_commentary += 1
                elif phase == "final_answer":
                    attempt.phase_final += 1
                else:
                    attempt.phase_absent += 1
        attempt.call_ids = frozenset(ids)


class _CountedProvider:
    """ModelProvider that sends every call through the ledger (count, limit, observe)."""

    __slots__ = ("_inner", "_ledger")

    def __init__(self, inner: Any, ledger: _Ledger) -> None:
        self._inner = inner
        self._ledger = ledger

    @property
    def model(self) -> str:
        return self._inner.model

    def create(self, request: ModelRequest) -> ModelResponse:
        return self._ledger.call(self._inner, request)


# --- synthetic resources --------------------------------------------------------------


def _synthetic_policy_index() -> bytes:
    """A policy index of the current format with invented text (no PDF exists)."""
    sections = [
        {
            "section_id": FRONT_MATTER_ID,
            "level": 0,
            "title": None,
            "parent_id": None,
            "heading_page": None,
            "lines": [[1, "Documento sintético de avaliação; não é o manual da loja."]],
        },
        {
            "section_id": "1",
            "level": 1,
            "title": "Seção sintética de teste",
            "parent_id": None,
            "heading_page": 1,
            "lines": [[1, "Texto sintético de teste; não descreve nenhuma regra real da loja."]],
        },
    ]
    described = json.dumps(sections, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    document = {
        "format": INDEX_FORMAT,
        "format_version": INDEX_FORMAT_VERSION,
        "extraction_rules": EXTRACTION_RULES,
        "pypdf_version": "not-extracted",
        "source": {
            "file_name": "synthetic-smoke-policies",
            "sha256": hashlib.sha256(described).hexdigest(),
            "size_bytes": len(described),
            "page_count": 1,
        },
        "sections": sections,
    }
    return (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


# Literal disclosure review of the synthetic index above (spec 04 Review 04-17-CONF-FU
# item 2; report 10-07). Written after reading the two invented sections: the front matter
# states the identity of the document (internal); section 1 is placeholder store text
# (public). The source SHA-256 and digests are literals, never computed from the index at
# run time; a changed section is withheld and a changed source is unavailable. Registered
# only while run() executes (_synthetic_disclosure); production code never sees it.
SYNTHETIC_POLICY_SHA256: Final = "3bad2c82107884311266bab82954293416a6d6003b9fcf316ae28a37f238c2c5"
SYNTHETIC_POLICY_DISCLOSURE: Final = policy_disclosure.DisclosureReview(
    source_sha256=SYNTHETIC_POLICY_SHA256,
    sections=(
        policy_disclosure.SectionReview(
            FRONT_MATTER_ID,
            "286bb887cb12a5dc0a4868c2302fa9b1750a64cd39dc7ff725b66c2d2fb8ae29",
            "internal",
        ),
        policy_disclosure.SectionReview(
            "1", "daba350a52d87d9e25b8560433d89983196c526783a6dc5d1f8b04312425ffc8", "public"
        ),
    ),
)


@contextmanager
def _synthetic_disclosure() -> Iterator[None]:
    """Add the synthetic review to the registry for the run; restore it on every exit."""
    previous = policy_disclosure.REVIEWED_DISCLOSURES
    policy_disclosure.REVIEWED_DISCLOSURES = (*previous, SYNTHETIC_POLICY_DISCLOSURE)
    try:
        yield
    finally:
        policy_disclosure.REVIEWED_DISCLOSURES = previous


def _prepare_resources(scratch: Path) -> tuple[AgentResources, Mapping[str, int], int]:
    database = scratch / "catalog.sqlite3"
    try:
        prepared = prepare_database(FIXTURE_DIR, database, clock=lambda: PREPARED_AT)
    except DataPreparationError, OSError, ValueError:
        raise DataSetupError("database_preparation_failed") from None
    index_path = scratch / "policies.json"
    try:
        index_path.write_bytes(_synthetic_policy_index())
        index = load_policy_index(index_path)
    except PolicyIndexUnavailableError, OSError:
        raise DataSetupError("policy_index_invalid") from None
    resources = AgentResources(
        database_path=database, policy_index_path=index_path, clock=lambda: CLOCK
    )
    return resources, prepared.row_counts, len(index.sections)


def _open_provider(
    config: SmokeConfig,
    model: str,
    http_client_factory: Callable[[], Any] | None,
    opened: list[Any],
) -> Any:
    # Imported here, after the configuration checks: importing openai reads OPENAI_LOG.
    from emporio_agent.openai_provider import OpenAIResponsesProvider

    client = None if http_client_factory is None else http_client_factory()
    provider = OpenAIResponsesProvider(
        api_key=config.api_key,
        model=model,
        timeout_seconds=TIMEOUT_SECONDS,
        max_output_tokens=config.max_output_tokens,
        http_client=client,
        reasoning_effort=config.reasoning_effort,
    )
    opened.append(provider)
    return provider


# --- scenarios ------------------------------------------------------------------------


@dataclass
class _Run:
    turn1: TurnResult | None = None
    turn2: TurnResult | None = None
    started: set[str] = field(default_factory=set)
    skipped: dict[str, str] = field(default_factory=dict)
    interrupted: str | None = None
    prior_calls: int = 0


def _history(session: ConversationSession) -> tuple[ConversationItem, ...]:
    return tuple(item for turn in session.history for item in turn.items)


def _turn_line(scenario: str, result: TurnResult) -> str:
    diagnostics = result.diagnostics
    tools = tuple(
        ":".join(
            _show(part)
            for part in (run.tool or "unknown", run.status, run.error_code)
            if part is not None
        )
        for run in diagnostics.tool_runs
    )
    return _record(
        "turn:",
        [
            ("scenario", scenario),
            ("outcome", result.outcome),
            ("failure", result.failure),
            ("agent_attempts", len(diagnostics.attempts)),
            ("retries", diagnostics.retries_used),
            ("tool_runs", len(diagnostics.tool_runs)),
            ("tools", tools),
            ("budget_failure", diagnostics.budget_failure),
            ("history_turns_after", diagnostics.history_turns_after),
        ],
    )


def _direct(provider: _CountedProvider, request: ModelRequest) -> None:
    try:
        provider.create(request)
    except ProviderError:
        pass  # recorded by the ledger


def _execute(
    config: SmokeConfig,
    scratch: Path,
    ledger: _Ledger,
    opened: list[Any],
    http_client_factory: Callable[[], Any] | None,
    sleep: Callable[[float], None],
    monotonic: Callable[[], float],
    emit: Callable[[str], None],
) -> _Run:
    resources, row_counts, policy_sections = _prepare_resources(scratch)
    emit(
        _record(
            "data:",
            [
                ("source", "synthetic_fixture_valid"),
                *((table, row_counts[table]) for table in sorted(row_counts)),
                ("policy_index", "synthetic"),
                ("policy_sections", policy_sections),
            ],
        )
    )
    state = _Run()
    provider = _CountedProvider(
        _open_provider(config, config.model, http_client_factory, opened), ledger
    )
    agent = Agent(provider, resources, sleep=sleep, monotonic=monotonic)
    session = ConversationSession()

    ledger.begin("S1T1", "agent")
    state.started.add("S1T1")
    state.turn1 = agent.respond(session, TURN_1_TEXT)
    emit(_turn_line("S1T1", state.turn1))
    if ledger.blocked:
        state.interrupted = "call_limit"
    elif _ev01(ledger, config).status == "failed":
        state.interrupted = "blocking_failure"

    if state.interrupted is None:
        if state.turn1.outcome == "answered":
            ledger.begin("S1T2", "agent", prefix=_history(session))
            state.started.add("S1T2")
            state.turn2 = agent.respond(session, TURN_2_TEXT)
            emit(_turn_line("S1T2", state.turn2))
            if ledger.blocked:
                state.interrupted = "call_limit"
        else:
            state.skipped["S1T2"] = "turn_1_not_answered"

    if state.interrupted is None:
        history = _history(session)
        state.prior_calls = sum(isinstance(item, FunctionCall) for item in history)
        if state.prior_calls:
            ledger.begin("EV07", "adapter_direct")
            state.started.add("EV07")
            _direct(
                provider,
                ModelRequest(
                    instructions=session_instructions(False),
                    items=history + (UserMessage(EV07_TEXT),),
                    tools=tuple(provider_tool_definitions()),
                    allow_tool_calls=False,
                ),
            )
            if ledger.blocked:
                state.interrupted = "call_limit"
        else:
            state.skipped["EV07"] = "no_prior_call_in_history"

    if state.interrupted is None:
        unknown = _CountedProvider(
            _open_provider(config, UNKNOWN_MODEL, http_client_factory, opened), ledger
        )
        ledger.begin("EV08", "adapter_direct")
        state.started.add("EV08")
        _direct(
            unknown,
            ModelRequest(
                instructions=session_instructions(False),
                items=(UserMessage(EV08_TEXT),),
                tools=tuple(provider_tool_definitions()),
                allow_tool_calls=True,
            ),
        )
        if ledger.blocked:
            state.interrupted = "call_limit"
    return state


# --- EV verdicts ----------------------------------------------------------------------


@dataclass(frozen=True)
class _Verdict:
    status: str  # passed | failed | not_observed
    codes: tuple[str, ...] = ()
    details: tuple[tuple[str, object], ...] = ()

    def line(self, case: str) -> str:
        return _record(case, [("status", self.status), ("codes", self.codes), *self.details])


def _passed(*details: tuple[str, object]) -> _Verdict:
    return _Verdict("passed", (), details)


def _failed(code: str, *details: tuple[str, object]) -> _Verdict:
    return _Verdict("failed", (code,), details)


def _absent(code: str, *details: tuple[str, object]) -> _Verdict:
    return _Verdict("not_observed", (code,), details)


def _provider_code(attempt: _Attempt) -> str:
    return f"provider_{attempt.error_kind}"


def _not_run(state: _Run, scenario: str, ledger: _Ledger) -> str | None:
    """Code for a scenario that was not run or was cut by the limit; None if it ran."""
    if scenario in ledger.blocked_in and not ledger.of(scenario):
        return "interrupted_call_limit"
    if scenario in state.started:
        return None
    if scenario in state.skipped:
        return state.skipped[scenario]
    return f"not_run_{state.interrupted}" if state.interrupted else "not_run"


def _ev01(ledger: _Ledger, config: SmokeConfig) -> _Verdict:
    details: list[tuple[str, object]] = [("configured_model", config.model)]
    for attempt in ledger.attempts:
        if attempt.outcome == "response":
            details += [
                ("reported_model", attempt.reported_model),
                ("tools", attempt.tools),
                ("accepted_attempt", attempt.number),
            ]
            if attempt.tools != 5:
                return _failed("tools_not_five", *details)
            return _passed(*details)
        if not attempt.transient:
            return _failed(_provider_code(attempt), *details, ("tools", attempt.tools))
    return _absent("no_accepted_request", *details)


def _ev02(state: _Run, ledger: _Ledger) -> _Verdict:
    turn = state.turn1
    assert turn is not None  # S1T1 always runs once the data is prepared
    attempts = ledger.of("S1T1")
    returned = [a for a in attempts if a.previous_calls_returned is not None]
    calls = sum(a.calls for a in attempts if a.outcome == "response")
    tool_runs = len(turn.diagnostics.tool_runs)
    answered = turn.outcome == "answered"
    same_call_id = all(a.previous_calls_returned for a in returned) if returned else None
    details = (
        ("function_calls", calls),
        ("tool_runs", tool_runs),
        ("outputs_same_call_id", same_call_id),
        ("final_answer", answered),
        ("turn_failure", turn.failure),
    )
    rejected = next((a for a in returned if a.outcome == "error" and not a.transient), None)
    if rejected is not None:
        return _failed(_provider_code(rejected), *details)
    if any(a.previous_calls_returned is False for a in returned):
        return _failed("call_id_not_returned", *details)
    if answered:
        if calls == 0:
            return _absent("no_function_call", *details)
        if tool_runs and any(a.outcome == "response" for a in returned):
            return _passed(*details)
        return _failed("round_trip_incomplete", *details)
    if "S1T1" in ledger.blocked_in:
        return _absent("interrupted_call_limit", *details)
    if turn.failure in _TRANSIENT_TURN_FAILURES:
        return _absent(turn.failure, *details)
    return _failed(turn.failure or "turn_not_answered", *details)


def _ev03(ledger: _Ledger, config: SmokeConfig) -> _Verdict:
    """Successor rule (spec review 07-07-R1), first match wins.

    Non-transient rejection of a resend > a resend answered with status=completed > resends
    answered but none completed (resend_incomplete) > resends ended only in transient errors
    (resend_transient_error) > no resend (absence codes of the 07-05 contract).
    """
    attempts = ledger.attempts
    reasoning = sum(a.reasoning_items for a in attempts)
    encrypted = sum(a.encrypted_items for a in attempts)
    resent = [a for a in attempts if a.resent_encrypted]
    accepted = sum(a.outcome == "response" for a in resent)
    completed = sum(a.outcome == "response" and a.status == "completed" for a in resent)
    details = (
        ("reasoning_items", reasoning),
        ("encrypted_items", encrypted),
        ("resent_requests", len(resent)),
        ("resent_accepted", accepted),
        ("resent_completed", completed),
        ("reasoning_effort", config.reasoning_effort or "omitted"),
        ("store", "false"),
    )
    rejected = next((a for a in resent if a.outcome == "error" and not a.transient), None)
    if rejected is not None:
        return _failed(_provider_code(rejected), *details)
    if completed:
        return _passed(*details)
    if accepted:
        return _absent("resend_incomplete", *details)
    if resent:
        return _absent("resend_transient_error", *details)
    if reasoning == 0:
        return _absent("no_reasoning_items", *details)
    if encrypted == 0:
        return _absent("no_encrypted_content", *details)
    return _absent("reasoning_not_resent", *details)


def _ev04(state: _Run, ledger: _Ledger) -> _Verdict:
    attempts = [a for a in ledger.of("S1T2") if a.history_resent is not None]
    first = attempts[0] if attempts else None
    counts: dict[str, int | None] = {
        "id": first.resent_with_id if first else None,
        "status": first.resent_with_status if first else None,
        "phase": first.resent_with_phase if first else None,
    }
    turn = state.turn2
    details = (
        ("history_items", first.input_items - 1 if first else None),
        ("resent_with_id", counts["id"]),
        ("resent_with_status", counts["status"]),
        ("resent_with_phase", counts["phase"]),
        ("fields_observed", tuple(key for key, count in counts.items() if count)),
        ("fields_not_observed", tuple(key for key, count in counts.items() if count == 0)),
        ("store", "false"),
        ("turn_outcome", turn.outcome if turn is not None else None),
        ("turn_failure", turn.failure if turn is not None else None),
    )
    missing = _not_run(state, "S1T2", ledger)
    if missing is not None or first is None:
        return _absent(missing or "interrupted_call_limit", *details)
    if not first.history_resent:
        return _failed("history_incomplete", *details)
    first_history = [a for a in attempts if a.input_items == first.input_items]
    rejected = next((a for a in first_history if a.outcome == "error" and not a.transient), None)
    if rejected is not None:
        return _failed(_provider_code(rejected), *details)
    if any(a.outcome == "response" for a in first_history):
        return _passed(*details)
    if "S1T2" in ledger.blocked_in:
        return _absent("interrupted_call_limit", *details)
    return _absent(_provider_code(first_history[-1]), *details)


def _ev05(ledger: _Ledger) -> _Verdict:
    responses = [a for a in ledger.attempts if a.outcome == "response"]
    reported = [a for a in responses if a.usage is not None]
    ratios = [a.bytes_per_input_token for a in reported if a.bytes_per_input_token is not None]
    steps: dict[str, int] = {}
    for attempt in responses:
        steps[attempt.scenario] = steps.get(attempt.scenario, 0) + attempt.model_item_bytes
    details = (
        ("attempts", len(ledger.attempts)),
        ("responses", len(responses)),
        ("usage_reported", len(reported)),
        ("usage_missing", sum(a.usage is None and not a.usage_invalid for a in responses)),
        ("usage_invalid", sum(a.usage_invalid for a in responses)),
        (
            "input_tokens_reported",
            sum(a.usage.input_tokens for a in reported) if reported else None,
        ),
        (
            "output_tokens_reported",
            sum(a.usage.output_tokens for a in reported) if reported else None,
        ),
        ("min_bytes_per_input_token", min(ratios) if ratios else None),
        ("max_request_bytes", max((a.request_bytes for a in ledger.attempts), default=None)),
        ("max_encrypted_bytes", max((a.encrypted_bytes for a in responses), default=None)),
        ("max_assistant_bytes", max((a.assistant_bytes for a in responses), default=None)),
        ("max_step_model_item_bytes", max(steps.values(), default=None)),
        ("max_duration_ms", max((a.duration_ms for a in ledger.attempts), default=None)),
    )
    if not responses:
        return _absent("no_response", *details)
    if not reported:
        return _absent("usage_absent", *details)
    findings = []
    if ratios and min(ratios) < MIN_BYTES_PER_INPUT_TOKEN:
        findings.append("bytes_per_token_below_2_5")
    if max(steps.values()) > MODEL_ITEMS_ALLOWANCE_BYTES:
        findings.append("model_items_above_allowance")
    if findings:
        return _Verdict("failed", tuple(findings), details)
    if not ratios:
        return _absent("input_tokens_zero", *details)
    return _passed(*details)


def _ev06(ledger: _Ledger) -> _Verdict:
    responses = [a for a in ledger.attempts if a.outcome == "response"]
    details = (
        ("responses", len(responses)),
        ("calls_0", sum(a.calls == 0 for a in responses)),
        ("calls_1", sum(a.calls == 1 for a in responses)),
        ("calls_2_or_more", sum(a.calls >= 2 for a in responses)),
        ("parallel_tool_calls", "false"),
    )
    if not responses:
        return _absent("no_response", *details)
    if any(a.calls >= 2 for a in responses):
        return _failed("multiple_calls_in_response", *details)
    return _passed(*details)


def _single(state: _Run, ledger: _Ledger, scenario: str) -> tuple[str | None, _Attempt | None]:
    missing = _not_run(state, scenario, ledger)
    if missing is not None:
        return missing, None
    attempts = ledger.of(scenario)
    return (None, attempts[0]) if attempts else ("interrupted_call_limit", None)


def _ev07(state: _Run, ledger: _Ledger) -> _Verdict:
    details: tuple[tuple[str, object], ...] = (
        ("path", "adapter_direct"),
        ("loop_exhaustion", "not_exercised"),
        ("allow_tool_calls", False),
        ("prior_calls", state.prior_calls),
    )
    missing, attempt = _single(state, ledger, "EV07")
    if attempt is None:
        return _absent(missing or "not_run", *details)
    details += (("calls", attempt.calls if attempt.outcome == "response" else None),)
    if attempt.outcome == "error":
        if attempt.transient:
            return _absent(_provider_code(attempt), *details)
        return _failed(_provider_code(attempt), *details)
    if attempt.calls:
        return _failed("call_with_tool_choice_none", *details)
    if attempt.status != "completed":
        return _failed("response_incomplete", *details)
    if attempt.refusal:
        return _failed("refusal", *details)
    if not attempt.text_present:
        return _failed("empty_text", *details)
    return _passed(*details)


def _ev08(state: _Run, ledger: _Ledger) -> _Verdict:
    missing, attempt = _single(state, ledger, "EV08")
    details: list[tuple[str, object]] = [("unknown_model", UNKNOWN_MODEL)]
    if attempt is None:
        return _absent(missing or "not_run", *details)
    details += [
        ("error_kind", attempt.error_kind),
        ("status_code", attempt.status_code),
        ("message_fixed", attempt.message_fixed),
        ("attempts", len(ledger.of("EV08"))),
        ("retries", 0),
    ]
    if attempt.outcome == "response":
        return _failed("unknown_model_accepted", *details)
    if attempt.error_kind == "model_unavailable":
        if attempt.message_fixed:
            return _passed(*details)
        return _failed("message_not_fixed", *details)
    if attempt.transient:
        return _absent(_provider_code(attempt), *details)
    return _failed(_provider_code(attempt), *details)


def _verdicts(state: _Run, ledger: _Ledger, config: SmokeConfig) -> dict[str, _Verdict]:
    return {
        "EV01": _ev01(ledger, config),
        "EV02": _ev02(state, ledger),
        "EV03": _ev03(ledger, config),
        "EV04": _ev04(state, ledger),
        "EV05": _ev05(ledger),
        "EV06": _ev06(ledger),
        "EV07": _ev07(state, ledger),
        "EV08": _ev08(state, ledger),
    }


# --- run ------------------------------------------------------------------------------


def _close_all(opened: list[Any]) -> int:
    closed = 0
    for provider in opened:
        try:
            provider.close()
        except Exception:
            continue
        closed += 1
    return closed


class CleanupFailed(Exception):
    """A provider close or the temporary directory removal failed after an aborted run."""

    def __init__(self, after: str, removed: bool, closed: int, opened: int) -> None:
        self.line = _record(
            "cleanup_error:",
            [
                ("after", after),
                ("temporary_removed", removed),
                ("providers_closed", f"{closed}/{opened}"),
            ],
        )
        super().__init__("cleanup_failed")


def _after(error: BaseException) -> str:
    if isinstance(error, KeyboardInterrupt):
        return "keyboard_interrupt"
    if isinstance(error, DataSetupError):
        return "data_error"
    return "internal_error"


def _remove(scratch: Path) -> bool:
    try:
        shutil.rmtree(scratch)
    except OSError:
        return False
    return not scratch.exists()


def run(
    config: SmokeConfig,
    *,
    http_client_factory: Callable[[], Any] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    max_provider_calls: int = MAX_PROVIDER_CALLS,
    emit: Callable[[str], None] = print,
) -> int:
    """Run the scenarios once.

    Raises DataSetupError, KeyboardInterrupt, CleanupFailed (exit 6 wins) or a defect.
    """
    emit("agent_provider_smoke format=1")
    emit(
        _record(
            "config:",
            [
                ("configured_model", config.model),
                ("unknown_model", UNKNOWN_MODEL),
                ("max_provider_calls", max_provider_calls),
                ("max_output_tokens", config.max_output_tokens),
                ("reasoning_effort", config.reasoning_effort or "omitted"),
                ("timeout_seconds", f"{TIMEOUT_SECONDS:.1f}"),
                ("store", "false"),
                ("parallel_tool_calls", "false"),
            ],
        )
    )
    ledger = _Ledger(max_provider_calls, monotonic, emit)
    opened: list[Any] = []
    scratch = Path(tempfile.mkdtemp(prefix="emporio-eval-"))
    try:
        with _synthetic_disclosure():
            state = _execute(
                config, scratch, ledger, opened, http_client_factory, sleep, monotonic, emit
            )
    except BaseException as error:
        closed = _close_all(opened)
        removed = _remove(scratch)
        if not removed or closed != len(opened):
            # Exit 6 wins over 5 and 4 (contract precedence); fixed fields only.
            raise CleanupFailed(_after(error), removed, closed, len(opened)) from None
        raise
    closed = _close_all(opened)
    removed = _remove(scratch)

    verdicts = _verdicts(state, ledger, config)
    for case in _CASES:
        emit(verdicts[case].line(case))
    emit(_record("resources:", [("temporary_removed", removed), ("providers_closed", closed)]))
    statuses = [verdict.status for verdict in verdicts.values()]
    if not removed or closed != len(opened):
        code = EXIT_INTERNAL
    elif "failed" in statuses:
        code = EXIT_FAILED
    elif state.interrupted is not None:
        code = EXIT_INTERRUPTED
    elif "not_observed" in statuses:
        code = EXIT_NOT_OBSERVED
    else:
        code = EXIT_PASSED
    emit(
        _record(
            "summary:",
            [
                ("passed", statuses.count("passed")),
                ("failed", statuses.count("failed")),
                ("not_observed", statuses.count("not_observed")),
                ("provider_calls", f"{ledger.used}/{max_provider_calls}"),
                ("blocked_calls", ledger.blocked),
                ("interrupted", state.interrupted or "no"),
                ("exit_code", code),
            ],
        )
    )
    return code


def _utf8_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    http_client_factory: Callable[[], Any] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    max_provider_calls: int = MAX_PROVIDER_CALLS,
) -> int:
    """Command entry point. Keyword arguments are test seams; the command line has none."""
    _utf8_streams()
    try:
        args = _parser().parse_args(argv)
        config = resolve_config(args, os.environ if environ is None else environ)
    except UsageRejected as error:
        print(f"rejected: {error.code}")
        return EXIT_USAGE
    try:
        with _quiet_sdk_logging():
            return run(
                config,
                http_client_factory=http_client_factory,
                sleep=sleep,
                monotonic=monotonic,
                max_provider_calls=max_provider_calls,
            )
    except CleanupFailed as error:
        print(error.line)
        return EXIT_INTERNAL
    except KeyboardInterrupt:
        print("interrupted: keyboard")
        return EXIT_INTERRUPTED
    except DataSetupError as error:
        print(f"data_error: {error.code}")
        return EXIT_DATA
    except Exception as error:  # type name only, never the message or a traceback
        print(f"internal_error: {type(error).__name__}")
        return EXIT_INTERNAL


if __name__ == "__main__":
    raise SystemExit(main())
