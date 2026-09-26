"""Explicit audience and purpose policies. Unknown customer purposes fail closed."""
from dataclasses import dataclass
from datetime import datetime

from .timeutils import parse_time


@dataclass(frozen=True)
class RoutePolicy:
    kinds: frozenset[str]
    audience: str
    template_name: str
    consent_scopes: frozenset[str] = frozenset()

    @property
    def requires_consent(self):
        return self.audience == "customer"


def route(kinds, audience="merchant", scopes=()):
    return RoutePolicy(frozenset(kinds.split()), audience,
                       f"{'merchant' if audience == 'customer' else 'vera'}_{kinds.split()[0]}_v2",
                       frozenset(scopes))


ROUTES = [
    route("research_digest research_digest_release category_research_digest_release"),
    route("regulation_change supply_alert cde_opportunity"),
    route("perf_spike perf_dip seasonal_perf_dip"),
    route("festival_upcoming category_seasonal ipl_match_today"),
    route("competitor_opened gbp_unverified winback_eligible"),
    route("milestone_reached review_theme_emerged renewal_due dormant_with_vera curious_ask_due scheduled_recurring active_planning_intent"),
    route("recall_due", "customer", ("recall_reminders",)),
    route("appointment_tomorrow", "customer", ("appointment_reminders",)),
    route("trial_followup", "customer", ("trial_followup", "trial_followups", "program_updates", "kids_program_updates")),
    route("wedding_package_followup bridal_followup", "customer", ("bridal_package_followup", "wedding_package_followup")),
    route("chronic_refill_due", "customer", ("refill_reminders",)),
    route("customer_lapsed_soft customer_lapsed_hard winback_customer", "customer", ("winback_offers", "promotional_offers")),
]
FALLBACK_ROUTE = route("unknown")


def get_policy(kind: str) -> RoutePolicy:
    return next((policy for policy in ROUTES if kind in policy.kinds), FALLBACK_ROUTE)


def consent_allows(policy: RoutePolicy, customer: dict, now: datetime | None = None) -> bool:
    if not policy.requires_consent or not customer:
        return False
    consent = customer.get("consent") or {}
    preferences = customer.get("preferences") or {}
    scopes = consent.get("scope", [])
    if not isinstance(scopes, list) or any(not isinstance(s, str) for s in scopes) or not policy.consent_scopes.intersection(scopes):
        return False
    if preferences.get("reminder_opt_in") is False:
        return False
    if preferences.get("channel", "whatsapp") not in {
        "whatsapp", "whatsapp_via_parent", "whatsapp_via_son", "whatsapp_via_guardian"
    }:
        return False
    if (consent.get("opted_out_at") or consent.get("revoked_at")
            or consent.get("active") is False or consent.get("opted_in") is False
            or consent.get("status", "active") not in {"active", "granted"}):
        return False
    try:
        opted_in = parse_time(consent.get("opted_in_at"), allow_date=True)
        expires = parse_time(consent["expires_at"], allow_date=True) if consent.get("expires_at") else None
    except (ValueError, TypeError):
        return False
    return now is None or (opted_in <= now and (expires is None or now < expires))


def trigger_ids(trigger: dict) -> tuple[str | None, str | None]:
    """Both layouts occur in the supplied briefs; reject conflicts at ingestion."""
    payload = trigger.get("payload") or {}
    return (trigger.get("merchant_id") or payload.get("merchant_id"),
            trigger.get("customer_id") or payload.get("customer_id"))
