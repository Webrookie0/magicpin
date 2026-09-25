"""Trigger routing: map trigger kind -> route policy (data, not copy)."""
from dataclasses import dataclass, field


@dataclass
class RoutePolicy:
    kinds: set
    audience: str                     # "merchant" | "customer"
    template_name: str
    cta_types: set = field(default_factory=lambda: {"open_ended", "binary_yes_no", "binary_confirm_cancel", "none"})
    requires_consent: bool = False
    consent_scopes: tuple = ()        # consent.scope tokens that cover this outreach
    required_facts: tuple = ()
    prompt_variant: str = "merchant_insight_v1"


ROUTES = [
    RoutePolicy(
        kinds={"research_digest"},
        audience="merchant",
        template_name="vera_research_digest_v1",
        required_facts=("trigger.payload.top_item_id", "category.digest"),
        prompt_variant="merchant_insight_v1",
    ),
    RoutePolicy(
        kinds={"regulation_change"},
        audience="merchant",
        template_name="vera_compliance_update_v1",
        required_facts=("trigger.payload", "category.digest"),
    ),
    RoutePolicy(
        kinds={"cde_opportunity"},
        audience="merchant",
        template_name="vera_cde_invite_v1",
        required_facts=("trigger.payload", "category.digest"),
    ),
    RoutePolicy(
        kinds={"perf_spike", "perf_dip", "seasonal_perf_dip"},
        audience="merchant",
        template_name="vera_perf_update_v1",
        required_facts=("trigger.payload", "merchant.performance"),
    ),
    RoutePolicy(
        kinds={"festival_upcoming", "category_seasonal", "ipl_match_today"},
        audience="merchant",
        template_name="vera_opportunity_v1",
        required_facts=("trigger.payload",),
    ),
    RoutePolicy(
        kinds={"competitor_opened", "gbp_unverified", "winback_eligible"},
        audience="merchant",
        template_name="vera_risk_opportunity_v1",
        required_facts=("trigger.payload",),
    ),
    RoutePolicy(
        kinds={"milestone_reached", "review_theme_emerged", "renewal_due",
               "dormant_with_vera", "curious_ask_due", "active_planning_intent"},
        audience="merchant",
        template_name="vera_nudge_v1",
        required_facts=("trigger.payload", "merchant"),
    ),
    RoutePolicy(
        kinds={"recall_due"},
        audience="customer",
        template_name="merchant_recall_reminder_v1",
        requires_consent=True,
        consent_scopes=("recall",),
    ),
    RoutePolicy(
        kinds={"appointment_tomorrow", "trial_followup"},
        audience="customer",
        template_name="merchant_appointment_reminder_v1",
        requires_consent=True,
        consent_scopes=("appointment", "trial", "program", "session", "treatment"),
    ),
    RoutePolicy(
        kinds={"wedding_package_followup"},
        audience="customer",
        template_name="merchant_appointment_reminder_v1",
        requires_consent=True,
        consent_scopes=("appointment", "bridal", "wedding"),
    ),
    RoutePolicy(
        kinds={"chronic_refill_due"},
        audience="customer",
        template_name="merchant_refill_reminder_v1",
        requires_consent=True,
        consent_scopes=("refill", "delivery", "order"),
    ),
    RoutePolicy(
        kinds={"customer_lapsed_hard", "winback_customer"},
        audience="customer",
        template_name="merchant_winback_v1",
        requires_consent=True,
        consent_scopes=("winback", "promotional", "offer", "recall"),
    ),
]

FALLBACK_ROUTE = RoutePolicy(
    kinds=set(),
    audience="merchant",
    template_name="vera_generic_v1",
    prompt_variant="merchant_insight_v1",
)


def get_policy(kind: str) -> RoutePolicy:
    for route in ROUTES:
        if kind in route.kinds:
            return route
    return FALLBACK_ROUTE


def consent_allows(policy: RoutePolicy, customer: dict) -> bool:
    if not policy.requires_consent:
        return True
    consent = (customer or {}).get("consent") or {}
    scopes = [str(s).lower() for s in (consent.get("scope") or [])]
    if not scopes:
        return False
    for token in policy.consent_scopes:
        for scope in scopes:
            if token in scope or scope in token:
                return True
    return False
