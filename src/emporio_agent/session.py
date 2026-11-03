"""Conversation session: customer identity and bounded retained history (spec 07 G10, G12).

State lives in memory, per instance; there is no module-level mutable state, no persistence
and no network. The customer is set only by application code through the constructor or
`reset`. Nothing in user text, model output or tool payloads is read to choose identity.
Provider items are kept as given: the session never parses or rewrites them.
"""

from collections import deque
from dataclasses import dataclass
from typing import Final

from emporio_agent.provider import (
    ConversationItem,
    FunctionCall,
    FunctionCallOutput,
    ProviderItem,
    UserMessage,
    item_size_bytes,
)
from emporio_agent.services.orders import CustomerContext

__all__ = [
    "MAX_HISTORY_BYTES",
    "MAX_HISTORY_TURNS",
    "CompletedTurn",
    "ConversationSession",
]

MAX_HISTORY_TURNS: Final = 20
MAX_HISTORY_BYTES: Final = 393_216  # sum of item_size_bytes over retained items (G10b)

_ITEM_TYPES = (UserMessage, ProviderItem, FunctionCall, FunctionCallOutput)


@dataclass(frozen=True, slots=True)
class CompletedTurn:
    """One answered turn: starts with UserMessage; every call has its output (checked at commit)."""

    items: tuple[ConversationItem, ...]

    def __post_init__(self) -> None:
        if type(self.items) is not tuple:
            raise TypeError("CompletedTurn.items must be a tuple")
        for item in self.items:
            if not isinstance(item, _ITEM_TYPES):
                raise TypeError("CompletedTurn.items must hold conversation items")


def _check_customer(customer: object) -> None:
    if customer is not None and not isinstance(customer, CustomerContext):
        raise TypeError("customer must be a CustomerContext or None")


def _validate_turn(turn: CompletedTurn, retained_call_ids: set[str]) -> None:
    """Raise ValueError unless the turn is complete. Reads only; changes no state."""
    items = turn.items
    if not items:
        raise ValueError("a completed turn must not be empty")
    if not isinstance(items[0], UserMessage):
        raise ValueError("a completed turn must start with a UserMessage")
    open_calls: set[str] = set()
    seen_calls: set[str] = set()
    for item in items:
        if isinstance(item, FunctionCall):
            if item.call_id in seen_calls or item.call_id in retained_call_ids:
                raise ValueError("function call ids must be unique")
            seen_calls.add(item.call_id)
            open_calls.add(item.call_id)
        elif isinstance(item, FunctionCallOutput):
            if item.call_id not in open_calls:
                raise ValueError("every function call output needs one earlier call")
            open_calls.remove(item.call_id)
    if open_calls:
        raise ValueError("every function call needs one later output")


def _turn_call_ids(turn: CompletedTurn) -> set[str]:
    return {item.call_id for item in turn.items if isinstance(item, FunctionCall)}


def _turn_size(turn: CompletedTurn) -> int:
    return sum(item_size_bytes(item) for item in turn.items)


class ConversationSession:
    """Per-instance conversation state (spec G12). `commit` is used by the agent only."""

    __slots__ = ("_customer", "_turns", "_sizes", "_bytes", "_pruned")

    def __init__(self, *, customer: CustomerContext | None = None) -> None:
        _check_customer(customer)
        self._customer = customer
        self._turns: deque[CompletedTurn] = deque()
        self._sizes: deque[int] = deque()
        self._bytes = 0
        self._pruned = 0

    @property
    def customer(self) -> CustomerContext | None:
        return self._customer

    @property
    def history(self) -> tuple[CompletedTurn, ...]:
        return tuple(self._turns)

    @property
    def history_bytes(self) -> int:
        return self._bytes

    @property
    def pruned_turns(self) -> int:
        return self._pruned

    def reset(self, *, customer: CustomerContext | None = None) -> None:
        """Clear history and pruning count, then link `customer` (None: anonymous)."""
        _check_customer(customer)
        self._turns = deque()
        self._sizes = deque()
        self._bytes = 0
        self._pruned = 0
        self._customer = customer

    def commit(self, turn: CompletedTurn) -> None:
        """Validate the whole turn, append it, then drop the oldest whole turns over budget.

        A violation raises before any state changes (TypeError for a non-CompletedTurn,
        ValueError for an incomplete turn).
        """
        if type(turn) is not CompletedTurn:
            raise TypeError("commit expects a CompletedTurn")
        retained_call_ids: set[str] = set()
        for retained in self._turns:
            retained_call_ids |= _turn_call_ids(retained)
        _validate_turn(turn, retained_call_ids)

        turns = deque(self._turns)
        sizes = deque(self._sizes)
        total = self._bytes
        pruned = self._pruned
        turns.append(turn)
        size = _turn_size(turn)
        sizes.append(size)
        total += size
        while len(turns) > MAX_HISTORY_TURNS or total > MAX_HISTORY_BYTES:
            turns.popleft()
            total -= sizes.popleft()
            pruned += 1

        self._turns = turns
        self._sizes = sizes
        self._bytes = total
        self._pruned = pruned
