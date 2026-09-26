"""In-memory stores: versioned contexts and conversations.

A repository interface is kept so Redis/SQLite can be substituted later.
"""
import hashlib
import json
import time
from copy import deepcopy
from datetime import datetime
from threading import RLock
from dataclasses import dataclass, field
from typing import Any, Optional


def canonical_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


@dataclass
class ContextRecord:
    scope: str
    context_id: str
    version: int
    payload: dict
    stored_at: float
    hash: str


class ContextStore:
    """Versioned store keyed by (scope, context_id).

    - Higher version replaces the prior version atomically (whole payload).
    - Equal or lower version is rejected as stale (judge examples expect 409).
    """

    def __init__(self) -> None:
        self._data: dict[tuple[str, str], ContextRecord] = {}
        self._lock = RLock()

    def put(self, scope: str, context_id: str, version: int, payload: dict):
        with self._lock:
            return self._put(scope, context_id, version, payload)

    def _put(self, scope, context_id, version, payload):
        key = (scope, context_id)
        current = self._data.get(key)
        if current is not None and version <= current.version:
            return deepcopy(current), False  # stale / duplicate
        record = ContextRecord(
            scope=scope,
            context_id=context_id,
            version=version,
            payload=deepcopy(payload),
            stored_at=time.time(),
            hash=canonical_hash(payload),
        )
        self._data[key] = record
        return deepcopy(record), True

    def get(self, scope: str, context_id: str) -> Optional[ContextRecord]:
        with self._lock:
            return deepcopy(self._data.get((scope, context_id)))

    def counts(self) -> dict[str, int]:
        with self._lock:
            counts = {"category": 0, "merchant": 0, "customer": 0, "trigger": 0}
            for scope, _ in self._data:
                counts[scope] = counts.get(scope, 0) + 1
            return counts

    def wipe(self) -> None:
        with self._lock:
            self._data.clear()


# Conversation states
NEW = "NEW"
INITIATED = "INITIATED"
WAITING_FOR_REPLY = "WAITING_FOR_REPLY"
ENGAGED = "ENGAGED"
ACTION_PENDING = "ACTION_PENDING"
WAITING = "WAITING"
ENDED = "ENDED"


@dataclass
class Conversation:
    conversation_id: str
    merchant_id: str
    customer_id: Optional[str]
    trigger_id: Optional[str]
    scope: str  # "merchant" | "customer"
    route: str  # trigger kind / mission
    state: str = INITIATED
    turns: list = field(default_factory=list)          # [{from, body, ts}]
    sent_bodies: list = field(default_factory=list)    # exact outbound bodies
    fingerprint_counts: dict = field(default_factory=dict)
    intent_detected: bool = False
    suppressed: bool = False
    pending_action: Optional[dict] = None              # mission facts for follow-ups
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    clock: Optional[datetime] = None
    auto_reply_count: int = 0
    reply_cache: dict = field(default_factory=dict)


class ConversationStore:
    def __init__(self) -> None:
        self._convs: dict[str, Conversation] = {}

    def create(self, **kwargs) -> Conversation:
        if kwargs["conversation_id"] in self._convs:
            raise ValueError("conversation_id already exists")
        conv = Conversation(**kwargs)
        self._convs[conv.conversation_id] = conv
        return conv

    def get(self, conversation_id: str) -> Optional[Conversation]:
        return self._convs.get(conversation_id)

    def open_for_merchant(self, merchant_id: str, customer_id: Optional[str] = None):
        """Most recent non-ended conversation for this merchant/customer pair."""
        best = None
        for conv in self._convs.values():
            if conv.merchant_id != merchant_id or conv.state == ENDED:
                continue
            if conv.customer_id != customer_id:
                continue
            if best is None or conv.updated_at > best.updated_at:
                best = conv
        return best

    def wipe(self) -> None:
        self._convs.clear()


class OutreachState:
    """Cross-conversation bookkeeping: dedup keys, opt-outs, auto-reply history."""

    def __init__(self) -> None:
        self.sent_suppression_keys: set = set()
        self.suppressed_recipients: dict[tuple, str] = {}
        self.cooldowns: dict[tuple, datetime] = {}
        self.last_inbound: dict[tuple, datetime] = {}
        self.last_outbound: dict[tuple, datetime] = {}
        self.unanswered: dict[tuple, int] = {}
        self.audit: list[dict] = []

    def wipe(self) -> None:
        self.__init__()
