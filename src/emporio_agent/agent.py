"""Bounded agent loop: one user message, one TurnResult (spec 07 G5, G8-G11, G14, G15).

`Agent.respond(session, text)` validates the text, then runs at most `MAX_MODEL_ATTEMPTS`
provider attempts and at most `MAX_TOOL_EXECUTIONS` tool executions, with at most one
automatic retry. A logical request budget is checked before every attempt. Tools run only
through `execute_tool`; schemas come only from `provider_tool_definitions`. The turn chain is
local: the session is changed only by one atomic commit after a valid final answer.

No provider SDK import, no network, no logging, no persistence. Sizes are logical UTF-8
byte counts (spec G10b), not HTTP body sizes and not tokens.
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, Literal

from emporio_agent.instructions import session_instructions
from emporio_agent.provider import (
    ConversationItem,
    FunctionCall,
    FunctionCallOutput,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderErrorKind,
    Usage,
    UserMessage,
    item_size_bytes,
    static_size_bytes,
)
from emporio_agent.session import CompletedTurn, ConversationSession
from emporio_agent.tools import (
    ToolContext,
    ToolExecution,
    ToolStatus,
    execute_tool,
    provider_tool_definitions,
)

__all__ = [
    "MAX_FINAL_TEXT_CHARS",
    "MAX_MODEL_ATTEMPTS",
    "MAX_REQUEST_BYTES",
    "MAX_RETRIES_PER_MESSAGE",
    "MAX_RETRY_AFTER_SECONDS",
    "MAX_STATIC_BYTES",
    "MAX_TOOL_EXECUTIONS",
    "MAX_TURN_CHAIN_BYTES",
    "MAX_USER_MESSAGE_CHARS",
    "RETRY_DELAY_SECONDS",
    "Agent",
    "AgentResources",
    "AttemptRecord",
    "ToolRunRecord",
    "TurnDiagnostics",
    "TurnOutcome",
    "TurnResult",
]

MAX_MODEL_ATTEMPTS: Final = 8
MAX_TOOL_EXECUTIONS: Final = 6
MAX_RETRIES_PER_MESSAGE: Final = 1
RETRY_DELAY_SECONDS: Final = 1.0
MAX_RETRY_AFTER_SECONDS: Final = 5.0
MAX_USER_MESSAGE_CHARS: Final = 4_000  # code points
MAX_FINAL_TEXT_CHARS: Final = 16_000  # code points
MAX_STATIC_BYTES: Final = 16_384
MAX_TURN_CHAIN_BYTES: Final = 262_144
MAX_REQUEST_BYTES: Final = 671_744  # 16,384 + 393,216 + 262,144

type TurnOutcome = Literal["answered", "refused", "failed", "rejected_input"]
type BudgetFailure = Literal["static", "chain", "total"]

# --- Fixed PT-BR texts (spec G11): no provider text, code, request ID or claimed result ---

_REFUSED_TEXT: Final = (
    "Não posso ajudar com esse pedido. Posso ajudar com produtos, preços, pedidos e "
    "políticas da loja."
)
_TEMPORARY_TEXT: Final = (
    "O assistente está temporariamente indisponível. Tente novamente em instantes."
)
_CONFIGURATION_TEXT: Final = (
    "O assistente está indisponível por um problema de configuração. "
    "Avise o responsável pela aplicação."
)
_NOT_COMPLETED_TEXT: Final = (
    "Não consegui concluir a resposta. Tente novamente ou reformule a pergunta."
)
_TOO_MUCH_TEXT: Final = (
    "A pergunta exigiu consultas ou contexto demais. Faça uma pergunta mais específica ou "
    "inicie uma nova conversa."
)
_INPUT_TEXTS: Final[dict[str, str]] = {
    "input_invalid_text": (
        "Não consegui ler a sua mensagem porque ela contém caracteres inválidos. "
        "Escreva a mensagem novamente."
    ),
    "input_empty": (
        "A sua mensagem está vazia. Escreva a sua pergunta sobre produtos, preços, pedidos ou "
        "políticas da loja."
    ),
    "input_too_long": (
        "A sua mensagem é longa demais. Envie uma pergunta mais curta, com até 4.000 caracteres."
    ),
}
_FAILURE_TEXTS: Final[dict[str, str]] = {
    "provider_timeout": _TEMPORARY_TEXT,
    "provider_unavailable": _TEMPORARY_TEXT,
    "provider_rate_limited": _TEMPORARY_TEXT,
    "provider_quota": _CONFIGURATION_TEXT,
    "provider_authentication": _CONFIGURATION_TEXT,
    "provider_permission": _CONFIGURATION_TEXT,
    "provider_model_unavailable": _CONFIGURATION_TEXT,
    "provider_bad_request": _CONFIGURATION_TEXT,
    "response_invalid": _NOT_COMPLETED_TEXT,
    "protocol_error": _NOT_COMPLETED_TEXT,
    "response_incomplete": _NOT_COMPLETED_TEXT,
    "empty_response": _NOT_COMPLETED_TEXT,
    "no_final_answer": _NOT_COMPLETED_TEXT,
    "response_too_long": _NOT_COMPLETED_TEXT,
    "provider_failed": _NOT_COMPLETED_TEXT,
    "unexpected": _NOT_COMPLETED_TEXT,
    "tool_limit_reached": _TOO_MUCH_TEXT,
    "model_call_limit_reached": _TOO_MUCH_TEXT,
    "request_too_large": _TOO_MUCH_TEXT,
}
# Provider error kind -> turn failure code (spec G3, G11).
_PROVIDER_FAILURES: Final[dict[str, str]] = {
    "timeout": "provider_timeout",
    "connection": "provider_unavailable",
    "server_error": "provider_unavailable",
    "rate_limited": "provider_rate_limited",
    "quota": "provider_quota",
    "authentication": "provider_authentication",
    "permission": "provider_permission",
    "model_unavailable": "provider_model_unavailable",
    "bad_request": "provider_bad_request",
    "provider_failed": "provider_failed",
    "response_invalid": "response_invalid",
    "unexpected": "unexpected",
}


# --- Public data classes (spec G11, G14, G15) -----------------------------------------


@dataclass(frozen=True, slots=True)
class AgentResources:
    """Application-owned tool resources; the customer comes from the session."""

    database_path: Path
    policy_index_path: Path
    clock: Callable[[], datetime]

    def __post_init__(self) -> None:
        if not isinstance(self.database_path, Path):
            raise TypeError("database_path must be a Path")
        if not isinstance(self.policy_index_path, Path):
            raise TypeError("policy_index_path must be a Path")
        if not callable(self.clock):
            raise TypeError("clock must be callable")


@dataclass(frozen=True, slots=True)
class ToolRunRecord:
    index: int  # 1..6 within the turn
    tool: str | None  # registered name, None for unknown
    status: ToolStatus
    error_code: str | None
    duration_ms: int  # injected monotonic clock
    references: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    index: int  # 1..8
    retry: bool
    outcome: Literal["response", "error"]
    error_kind: ProviderErrorKind | None
    reported_model: str | None
    request_id: str | None
    duration_ms: int
    request_bytes: int  # static + history + chain (spec G10b)
    usage: Usage | None
    usage_invalid: bool


@dataclass(frozen=True, slots=True)
class TurnDiagnostics:
    configured_model: str
    attempts: tuple[AttemptRecord, ...]
    tool_runs: tuple[ToolRunRecord, ...]
    retries_used: int
    budget_failure: BudgetFailure | None
    usage_reported_attempts: int
    usage_missing_attempts: int
    usage_invalid_attempts: int
    input_tokens_reported: int | None  # sum over attempts with valid usage; None if none
    output_tokens_reported: int | None
    history_turns_after: int
    history_pruned_turns: int


@dataclass(frozen=True, slots=True)
class TurnResult:
    outcome: TurnOutcome
    text: str  # model answer for "answered"; fixed PT-BR message otherwise
    failure: str | None  # fixed code, None when answered or refused
    diagnostics: TurnDiagnostics


# --- Pure helpers ---------------------------------------------------------------------


def _input_failure(text: str) -> str | None:
    """Input checks in spec G10 order; the first failure wins."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return "input_invalid_text"
    if text.strip() == "":
        return "input_empty"
    if len(text) > MAX_USER_MESSAGE_CHARS:
        return "input_too_long"
    return None


def _duration_ms(start: float, end: float) -> int:
    return max(0, round((end - start) * 1000))


def _product_id(value: object) -> int | None:
    if not isinstance(value, dict):
        return None
    product_id = value.get("product_id")
    return product_id if type(product_id) is int else None


def _tool_references(execution: ToolExecution) -> tuple[str, ...]:
    """References from `ok` envelopes only, by the fixed paths of spec G14."""
    if execution.status != "ok" or execution.tool is None:
        return ()
    envelope = json.loads(execution.payload)
    data = envelope.get("data") if isinstance(envelope, dict) else None
    if not isinstance(data, dict):
        return ()
    references: list[str] = []
    if execution.tool == "search_products":
        products = data.get("products")
        for product in products if isinstance(products, list) else ():
            product_id = _product_id(product)
            if product_id is not None:
                references.append(f"product:{product_id}")
    elif execution.tool in ("get_product", "quote_product"):
        product_id = _product_id(data.get("product"))
        if product_id is not None:
            references.append(f"product:{product_id}")
    elif execution.tool == "search_policies":
        sections = data.get("sections")
        for section in sections if isinstance(sections, list) else ():
            if not isinstance(section, dict):
                continue
            section_id = section.get("section_id")
            pages = section.get("pages")
            if (
                type(section_id) is str
                and isinstance(pages, list)
                and pages
                and all(type(page) is int for page in pages)
            ):
                joined = ",".join(str(page) for page in pages)
                references.append(f"policy:{section_id}@p{joined}")
    elif execution.tool == "get_order":
        references.append("order")  # never the order ID
    return tuple(references)


def _call_ids(items: tuple[ConversationItem, ...] | list[ConversationItem]) -> set[str]:
    return {item.call_id for item in items if isinstance(item, FunctionCall)}


# --- Agent ----------------------------------------------------------------------------


class _Turn:
    """Mutable state of one `respond` call. Discarded unless the turn is committed."""

    def __init__(self, user: UserMessage) -> None:
        self.chain: list[ConversationItem] = [user]
        self.chain_bytes = item_size_bytes(user)
        self.attempts: list[AttemptRecord] = []
        self.tool_runs: list[ToolRunRecord] = []
        self.executed = 0
        self.retries = 0
        self.budget_failure: BudgetFailure | None = None


class Agent:
    """Runs one bounded turn per call. Holds no conversation state (spec G15)."""

    __slots__ = ("_provider", "_resources", "_sleep", "_monotonic")

    def __init__(
        self,
        provider: ModelProvider,
        resources: AgentResources,
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(provider, ModelProvider):
            raise TypeError("provider must implement ModelProvider")
        if not isinstance(resources, AgentResources):
            raise TypeError("resources must be AgentResources")
        if not callable(sleep):
            raise TypeError("sleep must be callable")
        if not callable(monotonic):
            raise TypeError("monotonic must be callable")
        self._provider = provider
        self._resources = resources
        self._sleep = sleep
        self._monotonic = monotonic

    def respond(self, session: ConversationSession, text: str) -> TurnResult:
        """Answer one user message. The session changes only on `answered`."""
        if not isinstance(session, ConversationSession):
            raise TypeError("session must be a ConversationSession")
        if not isinstance(text, str):
            raise TypeError("text must be a str")
        rejected = _input_failure(text)
        if rejected is not None:
            return self._result(session, None, "rejected_input", _INPUT_TEXTS[rejected], rejected)

        history: tuple[ConversationItem, ...] = tuple(
            item for completed in session.history for item in completed.items
        )
        history_bytes = session.history_bytes
        history_call_ids = _call_ids(history)
        instructions = session_instructions(session.customer is not None)
        tools = tuple(provider_tool_definitions())  # once per turn, unchanged
        static_bytes = static_size_bytes(instructions, tools)
        context = ToolContext(
            database_path=self._resources.database_path,
            policy_index_path=self._resources.policy_index_path,
            clock=self._resources.clock,
            customer=session.customer,
        )
        turn = _Turn(UserMessage(text))

        request: ModelRequest | None = None
        retrying = False
        for attempt_index in range(1, MAX_MODEL_ATTEMPTS + 1):
            budget = self._budget_failure(static_bytes, history_bytes, turn.chain_bytes)
            if budget is not None:
                turn.budget_failure = budget
                return self._fail(session, turn, "request_too_large")
            request_bytes = static_bytes + history_bytes + turn.chain_bytes
            if not retrying or request is None:
                allow = turn.executed < MAX_TOOL_EXECUTIONS and attempt_index < MAX_MODEL_ATTEMPTS
                request = ModelRequest(
                    instructions=instructions,
                    items=history + tuple(turn.chain),
                    tools=tools,
                    allow_tool_calls=allow,
                )

            start = self._monotonic()
            response: ModelResponse | None = None
            error: ProviderError | None = None
            try:
                outcome = self._provider.create(request)
            except ProviderError as provider_error:
                error = provider_error
            except Exception:  # fail closed: anything else from the provider (spec G15)
                error = ProviderError("unexpected")
            else:
                if type(outcome) is ModelResponse:
                    response = outcome
                else:
                    error = ProviderError("unexpected")
            end = self._monotonic()
            duration = _duration_ms(start, end)

            if response is None:
                assert error is not None
                turn.attempts.append(
                    AttemptRecord(
                        index=attempt_index,
                        retry=retrying,
                        outcome="error",
                        error_kind=error.kind,
                        reported_model=None,
                        request_id=error.request_id,
                        duration_ms=duration,
                        request_bytes=request_bytes,
                        usage=None,
                        usage_invalid=False,
                    )
                )
                if self._may_retry(error, turn, attempt_index):
                    retry_after = error.retry_after_seconds
                    self._sleep(max(RETRY_DELAY_SECONDS, retry_after or 0.0))
                    turn.retries += 1
                    retrying = True
                    continue
                return self._fail(session, turn, _PROVIDER_FAILURES[error.kind])

            turn.attempts.append(
                AttemptRecord(
                    index=attempt_index,
                    retry=retrying,
                    outcome="response",
                    error_kind=None,
                    reported_model=response.model,
                    request_id=response.request_id,
                    duration_ms=duration,
                    request_bytes=request_bytes,
                    usage=response.usage,
                    usage_invalid=response.usage_invalid,
                )
            )
            retrying = False
            calls = [item for item in response.output if isinstance(item, FunctionCall)]

            # Protocol validation, spec G5: the first failing rule ends the turn.
            if response.status != "completed":
                return self._fail(session, turn, "response_incomplete")  # V1
            batch_ids = [call.call_id for call in calls]
            known_ids = history_call_ids | _call_ids(turn.chain)
            if len(set(batch_ids)) != len(batch_ids) or known_ids.intersection(batch_ids):
                return self._fail(session, turn, "protocol_error")  # V2
            if response.refusal:
                return self._result(session, turn, "refused", _REFUSED_TEXT, None)  # V3
            if calls:
                if attempt_index >= MAX_MODEL_ATTEMPTS:
                    return self._fail(session, turn, "model_call_limit_reached")  # V4
                if turn.executed + len(calls) > MAX_TOOL_EXECUTIONS:
                    return self._fail(session, turn, "tool_limit_reached")  # V4b
                output_bytes = sum(item_size_bytes(item) for item in response.output)
                if turn.chain_bytes + output_bytes > MAX_TURN_CHAIN_BYTES:
                    turn.budget_failure = "chain"
                    return self._fail(session, turn, "request_too_large")  # V4c
                turn.chain.extend(response.output)
                turn.chain_bytes += output_bytes
                for call in calls:
                    self._run_tool(turn, call, context)
                continue

            if response.text.strip() == "":
                code = "no_final_answer" if response.commentary_present else "empty_response"
                return self._fail(session, turn, code)  # V5, V5b
            if len(response.text) > MAX_FINAL_TEXT_CHARS:
                return self._fail(session, turn, "response_too_long")  # V6
            output_bytes = sum(item_size_bytes(item) for item in response.output)
            if turn.chain_bytes + output_bytes > MAX_TURN_CHAIN_BYTES:
                turn.budget_failure = "chain"
                return self._fail(session, turn, "request_too_large")  # G10b, before commit
            turn.chain.extend(response.output)  # V7
            session.commit(CompletedTurn(tuple(turn.chain)))
            return self._result(session, turn, "answered", response.text, None)

        raise AssertionError("the attempt loop always returns")  # pragma: no cover

    # --- steps ---

    @staticmethod
    def _budget_failure(static: int, history: int, chain: int) -> BudgetFailure | None:
        if static > MAX_STATIC_BYTES:
            return "static"
        if chain > MAX_TURN_CHAIN_BYTES:
            return "chain"
        if static + history + chain > MAX_REQUEST_BYTES:
            return "total"
        return None

    @staticmethod
    def _may_retry(error: ProviderError, turn: _Turn, attempt_index: int) -> bool:
        retry_after = error.retry_after_seconds
        return (
            error.transient
            and turn.retries < MAX_RETRIES_PER_MESSAGE
            and attempt_index < MAX_MODEL_ATTEMPTS
            and (retry_after is None or retry_after <= MAX_RETRY_AFTER_SECONDS)
        )

    def _run_tool(self, turn: _Turn, call: FunctionCall, context: ToolContext) -> None:
        start = self._monotonic()
        execution = execute_tool(call.name, call.arguments, context)
        end = self._monotonic()
        turn.executed += 1
        output = FunctionCallOutput(call.call_id, execution.payload)
        turn.chain.append(output)
        turn.chain_bytes += item_size_bytes(output)
        turn.tool_runs.append(
            ToolRunRecord(
                index=turn.executed,
                tool=execution.tool,
                status=execution.status,
                error_code=execution.error_code,
                duration_ms=_duration_ms(start, end),
                references=_tool_references(execution),
            )
        )

    def _fail(self, session: ConversationSession, turn: _Turn, code: str) -> TurnResult:
        return self._result(session, turn, "failed", _FAILURE_TEXTS[code], code)

    def _result(
        self,
        session: ConversationSession,
        turn: _Turn | None,
        outcome: TurnOutcome,
        text: str,
        failure: str | None,
    ) -> TurnResult:
        attempts = tuple(turn.attempts) if turn is not None else ()
        responses = [attempt for attempt in attempts if attempt.outcome == "response"]
        reported = [attempt.usage for attempt in responses if attempt.usage is not None]
        diagnostics = TurnDiagnostics(
            configured_model=self._provider.model,
            attempts=attempts,
            tool_runs=tuple(turn.tool_runs) if turn is not None else (),
            retries_used=turn.retries if turn is not None else 0,
            budget_failure=turn.budget_failure if turn is not None else None,
            usage_reported_attempts=len(reported),
            usage_missing_attempts=sum(
                1 for a in responses if a.usage is None and not a.usage_invalid
            ),
            usage_invalid_attempts=sum(1 for a in responses if a.usage_invalid),
            input_tokens_reported=sum(u.input_tokens for u in reported) if reported else None,
            output_tokens_reported=sum(u.output_tokens for u in reported) if reported else None,
            history_turns_after=len(session.history),
            history_pruned_turns=session.pruned_turns,
        )
        return TurnResult(outcome=outcome, text=text, failure=failure, diagnostics=diagnostics)
