"""The judge clock is authoritative. Never silently substitute wall time."""
from datetime import datetime, timezone


def parse_time(value: str, *, allow_date=False) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("an ISO timestamp is required")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        if allow_date and len(value) == 10:
            return parsed.replace(tzinfo=timezone.utc)
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)
