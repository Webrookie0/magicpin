"""Bot identity metadata."""
import os
from datetime import datetime, timezone
from . import llm

SUBMITTED_AT = os.environ.get("VERA_SUBMITTED_AT", datetime.now(timezone.utc).isoformat())


def get_metadata() -> dict:
    return {
        "team_name": os.environ.get("VERA_TEAM_NAME", "Team Vera"),
        "team_members": [name.strip() for name in os.environ.get("VERA_TEAM_MEMBERS", "Vera Builder").split(",") if name.strip()],
        "model": llm.model_name() if llm.enabled() else "deterministic-grounded-composer-v2",
        "approach": (
            "event-driven 4-context composer: versioned context store, trigger-kind "
            "routing with consent/dedup policies, deterministic fact-grounded "
            "composition with optional Groq selection among grounded variants, "
            "validated fallback, rule-first reply state machine"
        ),
        "contact_email": os.environ.get("VERA_CONTACT_EMAIL", "vera@example.com"),
        "version": "2.0.0",
        "submitted_at": SUBMITTED_AT,
    }
