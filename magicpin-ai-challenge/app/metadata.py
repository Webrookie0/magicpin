"""Bot identity metadata."""
import os
from datetime import datetime, timezone


def get_metadata() -> dict:
    return {
        "team_name": os.environ.get("VERA_TEAM_NAME", "Team Vera"),
        "team_members": os.environ.get("VERA_TEAM_MEMBERS", "Vera Builder").split(","),
        "model": os.environ.get("VERA_MODEL", "deterministic-route-composer-v1"),
        "approach": (
            "event-driven 4-context composer: versioned context store, trigger-kind "
            "routing with consent/dedup policies, deterministic fact-grounded "
            "composition with guardrails, rule-first reply state machine"
        ),
        "contact_email": os.environ.get("VERA_CONTACT_EMAIL", "vera@example.com"),
        "version": "1.0.0",
        "submitted_at": datetime.now(timezone.utc).isoformat(),
    }
