from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from app.rate_limits import GroqRateLimiter, Limits, MODEL_LIMITS, estimate_tokens, retry_delay

MODEL = "qwen/qwen3.8-27b"


def test_supplied_model_limits():
    assert MODEL_LIMITS[MODEL] == Limits(30, 1000, 8000, 200000)
    assert MODEL_LIMITS["allam-2-7b"] == Limits(30, 7000, 6000, 500000)
    assert MODEL_LIMITS["meta-llama/llama-prompt-guard-2-22m"].requests_day == 14400


def test_minute_request_window_and_recovery():
    now = [0]
    limiter = GroqRateLimiter(clock=lambda: now[0])
    for _ in range(30):
        assert limiter.reserve(MODEL, 1)[0] is not None
    assert limiter.reserve(MODEL, 1)[1] == "local_rpm_limit"
    now[0] = 60
    assert limiter.reserve(MODEL, 1)[0] is not None


def test_token_reservations_reconcile_with_usage():
    limiter = GroqRateLimiter()
    reservation, _ = limiter.reserve(MODEL, 7900)
    assert limiter.reserve(MODEL, 101)[1] == "local_tpm_limit"
    limiter.reconcile(reservation, {"usage": {"total_tokens": 100}})
    assert limiter.reserve(MODEL, 200)[0] is not None


@pytest.mark.parametrize("usage", [None, [], {}, {"usage": None}, {"usage": {"total_tokens": -1}}, {"usage": {"total_tokens": True}}])
def test_bad_usage_preserves_conservative_reservation(usage):
    limiter = GroqRateLimiter()
    reservation, _ = limiter.reserve(MODEL, 200)
    limiter.reconcile(reservation, usage)
    assert reservation.tokens == 200


@pytest.mark.parametrize("limits,reason", [
    (Limits(30, 2, 8000, 200000), "local_rpd_limit"),
    (Limits(30, 1000, 8000, 200), "local_tpd_limit"),
])
def test_daily_quotas_do_not_reset_with_minute_window(limits, reason):
    now = [0]
    limiter = GroqRateLimiter(clock=lambda: now[0], limits={MODEL: limits})
    assert limiter.reserve(MODEL, 100)[0]
    now[0] = 61
    assert limiter.reserve(MODEL, 100)[0]
    now[0] = 122
    assert limiter.reserve(MODEL, 100)[1] == reason
    now[0] = 86400
    assert limiter.reserve(MODEL, 100)[0]


def test_retry_after_and_model_isolation():
    now = [0]
    limiter = GroqRateLimiter(clock=lambda: now[0])
    limiter.backoff(MODEL, "120")
    assert limiter.reserve(MODEL, 100)[1] == "rate_limit_backoff"
    assert limiter.reserve("allam-2-7b", 100)[0]
    now[0] = 119
    assert limiter.reserve(MODEL, 100)[1] == "rate_limit_backoff"
    now[0] = 120
    assert limiter.reserve(MODEL, 100)[0]


@pytest.mark.parametrize("value,expected", [(None, 60), ("invalid", 60), ("nan", 60), ("inf", 60), ("0", 1), ("15", 15)])
def test_retry_after_fallback(value, expected):
    assert retry_delay(value) == expected


def test_http_date_retry_after():
    now = datetime(2026, 9, 26, 10, 0, tzinfo=timezone.utc)
    assert retry_delay("Sat, 26 Sep 2026 10:01:30 GMT", now) == 90


def test_concurrent_quota_reservation_is_atomic():
    limiter = GroqRateLimiter()
    with ThreadPoolExecutor(max_workers=8) as pool:
        reservations = list(pool.map(lambda _: limiter.reserve(MODEL, 1)[0], range(60)))
    assert sum(r is not None for r in reservations) == 30


def test_estimate_covers_unicode_and_output_budget():
    messages = [{"role": "user", "content": "नमस्ते ₹299"}]
    assert estimate_tokens(messages, 128) >= len(messages[0]["content"].encode()) + 128


def test_judge_honors_retry_after_without_repeating_provider_calls(monkeypatch):
    from urllib.error import HTTPError
    import judge_simulator
    calls = []
    def limited(request, **kwargs):
        calls.append(request)
        raise HTTPError(request.full_url, 429, "rate limited", {"retry-after": "120"}, None)
    monkeypatch.setattr(judge_simulator.urlrequest, "urlopen", limited)
    provider = judge_simulator.GroqProvider("test-placeholder", MODEL)
    with pytest.raises(HTTPError):
        provider.complete("Score a message")
    with pytest.raises(RuntimeError, match="Retry-After"):
        provider.complete("Score another message")
    assert len(calls) == 1
