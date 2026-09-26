"""Local Groq quota reservations and non-blocking provider backoff.

Limits are the account values supplied by the user. Rolling windows are
conservative; usage in other processes is handled by Groq's 429/Retry-After.
Only token counts, model IDs and monotonic times are retained, never prompts.
"""
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import math
from threading import RLock
import time


@dataclass(frozen=True)
class Limits:
    requests_minute: int = 30
    requests_day: int = 1000
    tokens_minute: int = 8000
    tokens_day: int = 200000


MODEL_LIMITS = {
    "allam-2-7b": Limits(30, 7000, 6000, 500000),
    "meta-llama/llama-prompt-guard-2-22m": Limits(30, 14400, 15000, 500000),
    "meta-llama/llama-prompt-guard-2-86m": Limits(30, 14400, 15000, 500000),
    "openai/gpt-oss-120b": Limits(),
    "openai/gpt-oss-20b": Limits(),
    "openai/gpt-oss-safeguard-20b": Limits(),
    "qwen/qwen3.8-27b": Limits(),
}


@dataclass
class Reservation:
    at: float
    tokens: int


@dataclass
class Usage:
    requests: deque = field(default_factory=deque)
    blocked_until: float = 0


def estimate_tokens(messages, max_output_tokens):
    # UTF-8 bytes give a conservative bound without downloading a tokenizer.
    # Include chat framing and the full output allowance; reconcile with usage.
    return len(json.dumps(messages, ensure_ascii=False).encode("utf-8")) + 64 + max_output_tokens


def retry_delay(value, now=None):
    """Retry-After accepts seconds or an HTTP date; malformed values use 60s."""
    try:
        delay = float(value)
    except (TypeError, ValueError):
        try:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            delay = (parsed - (now or datetime.now(timezone.utc))).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return 60.0
    return max(1.0, delay) if math.isfinite(delay) else 60.0


class GroqRateLimiter:
    def __init__(self, *, clock=time.monotonic, limits=None):
        self.clock = clock
        self.limits = MODEL_LIMITS if limits is None else limits
        self._usage = {}
        self._lock = RLock()

    def reserve(self, model, tokens):
        with self._lock:
            now = self.clock()
            usage = self._usage.setdefault(model, Usage())
            while usage.requests and now - usage.requests[0].at >= 86400:
                usage.requests.popleft()
            if now < usage.blocked_until:
                return None, "rate_limit_backoff"
            limits = self.limits.get(model, Limits(tokens_minute=6000))
            minute = [r for r in usage.requests if now - r.at < 60]
            for exceeded, reason in (
                (len(minute) >= limits.requests_minute, "local_rpm_limit"),
                (len(usage.requests) >= limits.requests_day, "local_rpd_limit"),
                (sum(r.tokens for r in minute) + tokens > limits.tokens_minute, "local_tpm_limit"),
                (sum(r.tokens for r in usage.requests) + tokens > limits.tokens_day, "local_tpd_limit"),
            ):
                if exceeded:
                    return None, reason
            reservation = Reservation(now, tokens)
            usage.requests.append(reservation)
            return reservation, None

    def reconcile(self, reservation, payload):
        if not isinstance(payload, dict) or not isinstance(payload.get("usage"), dict):
            return
        actual = payload["usage"].get("total_tokens")
        if type(actual) is int and actual >= 0:
            with self._lock:
                reservation.tokens = actual

    def backoff(self, model, retry_after=None):
        with self._lock:
            usage = self._usage.setdefault(model, Usage())
            usage.blocked_until = max(usage.blocked_until, self.clock() + retry_delay(retry_after))


limiter = GroqRateLimiter()
