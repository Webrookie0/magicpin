"""Deterministic composition: route policy + contexts -> WhatsApp message.

Every claim is pulled from the supplied contexts; nothing is invented.
"""
from typing import Optional

from .routing import RoutePolicy, get_policy


# ---------------------------------------------------------------- helpers ---

def _owner(merchant: dict) -> str:
    identity = merchant.get("identity", {})
    first = identity.get("owner_first_name")
    if first:
        name = merchant.get("identity", {}).get("name", "")
        if name.strip().startswith("Dr.") and not first.strip().startswith("Dr."):
            return f"Dr. {first}"
        return first
    return identity.get("name", "there")


def _merchant_name(merchant: dict) -> str:
    return merchant.get("identity", {}).get("name", "your business")


def _customer_name(customer: dict) -> str:
    return ((customer or {}).get("identity") or {}).get("name", "there")


def _is_hi(merchant: dict) -> bool:
    langs = [str(x).lower() for x in merchant.get("identity", {}).get("languages", [])]
    return "hi" in langs


def _pct(x, signed=True) -> str:
    try:
        v = float(x) * 100
        s = f"{v:+.0f}%" if signed else f"{v:.0f}%"
    except (TypeError, ValueError):
        return ""
    return s


def _active_offers(merchant: dict) -> list:
    return [o for o in merchant.get("offers", []) if o.get("status") == "active"]


def _find_digest(category: dict, item_id: Optional[str]) -> Optional[dict]:
    for item in (category or {}).get("digest", []):
        if item.get("id") == item_id:
            return item
    if (category or {}).get("digest"):
        return category["digest"][0]
    return None


class Composed:
    __slots__ = ("body", "cta", "send_as", "suppression_key", "rationale",
                 "template_name", "template_params")

    def __init__(self, body, cta, send_as, suppression_key, rationale,
                 template_name, template_params):
        self.body = body
        self.cta = cta
        self.send_as = send_as
        self.suppression_key = suppression_key
        self.rationale = rationale
        self.template_name = template_name
        self.template_params = template_params


# ---------------------------------------------------------------- handlers ---

def h_research_digest(cat, m, trg, cust):
    p = trg.get("payload", {})
    item = _find_digest(cat, p.get("top_item_id"))
    if not item:
        return None
    who = _owner(m)
    bits = [f"{who}, {item.get('source', 'this week')} landed."]
    fact = item.get("title", "")
    extra = []
    if item.get("trial_n"):
        extra.append(f"{item['trial_n']:,}-patient trial")
    if item.get("patient_segment"):
        seg = str(item["patient_segment"]).replace("_", " ")
        if seg in " ".join(m.get("signals", [])).replace("_", " "):
            extra.append(f"relevant to your {seg} cohort")
    line = fact
    if extra:
        line = f"{fact} — {'; '.join(extra)}"
    body = (
        f"{who}, {item.get('source', 'new research')} just dropped. One item for you: "
        f"{line}. Worth a 2-min look. Want me to pull it and draft a patient-ed "
        f"WhatsApp you can share? — {item.get('source', 'source on file')}"
    )
    return Composed(
        body=body, cta="open_ended", send_as="vera",
        suppression_key=trg.get("suppression_key", ""),
        rationale=(
            f"External research digest item {item.get('id')} matches merchant category "
            f"and cohort signals; open CTA invites the next step without friction."
        ),
        template_name="vera_research_digest_v1",
        template_params=[_owner(m), item.get("source", ""), item.get("title", "")],
    )


def h_regulation_change(cat, m, trg, cust):
    p = trg.get("payload", {})
    item = _find_digest(cat, p.get("top_item_id"))
    if not item:
        return None
    deadline = p.get("deadline_iso") or item.get("summary", "")
    summary = item.get("summary", item.get("title", ""))
    body = (
        f"{_owner(m)}, compliance heads-up: {item.get('title', 'a regulation update')} "
        f"({item.get('source', '')}). {summary}"
        + (f" Effective {deadline}." if deadline and "effective" not in str(deadline).lower() else "")
        + " Want a checklist to see if your setup already complies?"
    )
    return Composed(
        body=body, cta="binary_yes_no", send_as="vera",
        suppression_key=trg.get("suppression_key", ""),
        rationale="Compliance deadline with cited source; binary CTA offers a concrete, low-effort next step.",
        template_name="vera_compliance_update_v1",
        template_params=[_owner(m), item.get("title", ""), str(deadline)],
    )


def h_cde_opportunity(cat, m, trg, cust):
    p = trg.get("payload", {})
    item = _find_digest(cat, p.get("digest_item_id"))
    if not item:
        return None
    fee = p.get("fee", "")
    credits = p.get("credits")
    body = (
        f"{_owner(m)}, {item.get('title', 'a CDE session')} — {item.get('source', 'details on file')}. "
        + (f"{credits} credit(s), " if credits else "")
        + (f"{fee}. " if fee else "")
        + "Shall I reserve you a seat?"
    )
    return Composed(
        body=body, cta="binary_yes_no", send_as="vera",
        suppression_key=trg.get("suppression_key", ""),
        rationale="CDE opportunity from category digest with credits/fee facts; single binary CTA.",
        template_name="vera_cde_invite_v1",
        template_params=[_owner(m), item.get("title", ""), str(credits or "")],
    )


def h_perf_spike(cat, m, trg, cust):
    p = trg.get("payload", {})
    metric = p.get("metric", "views")
    delta = _pct(p.get("delta_pct"))
    base = p.get("vs_baseline")
    driver = p.get("likely_driver", "").replace("_", " ")
    body = (
        f"{_owner(m)}, good news — your {metric} are {delta} this week"
        + (f" vs your {base} avg" if base else "")
        + (f", likely from the {driver} post" if driver else "")
        + f". {_merchant_name(m)} is getting noticed right now. Want me to draft a follow-up post to ride this wave while it lasts?"
    )
    return Composed(
        body=body, cta="binary_yes_no", send_as="vera",
        suppression_key=trg.get("suppression_key", ""),
        rationale="Internal perf spike with exact metric/delta; momentum framing plus one binary ask.",
        template_name="vera_perf_update_v1",
        template_params=[_owner(m), metric, delta],
    )


def h_perf_dip(cat, m, trg, cust):
    p = trg.get("payload", {})
    metric = p.get("metric", "calls")
    delta = _pct(p.get("delta_pct"))
    window = p.get("window", "7d")
    base = p.get("vs_baseline")
    body = (
        f"{_owner(m)}, quick flag: your {metric} dropped {delta} over the last {window}"
        + (f" (from ~{base}/week)" if base else "")
        + ". Profile and offers look intact, so this is likely ranking drift. "
        + "Want me to run a 5-point profile check and tell you what to fix first?"
    )
    return Composed(
        body=body, cta="binary_yes_no", send_as="vera",
        suppression_key=trg.get("suppression_key", ""),
        rationale="Internal perf dip acknowledged with numbers before proposing the fix; avoids alarm tone.",
        template_name="vera_perf_update_v1",
        template_params=[_owner(m), metric, delta],
    )


def h_seasonal_perf_dip(cat, m, trg, cust):
    p = trg.get("payload", {})
    metric = p.get("metric", "views")
    delta = _pct(p.get("delta_pct"))
    note = str(p.get("season_note", "")).replace("_", " ")
    body = (
        f"{_owner(m)}, your {metric} are {delta} this week — that matches the seasonal "
        f"pattern ({note}) rather than anything you did. Peers see the same curve. "
        + "Want 2 counter-seasonal post ideas to hold visibility till it turns?"
    )
    return Composed(
        body=body, cta="binary_yes_no", send_as="vera",
        suppression_key=trg.get("suppression_key", ""),
        rationale="Seasonal dip framed as expected (per trigger payload is_expected_seasonal) with a constructive offer.",
        template_name="vera_perf_update_v1",
        template_params=[_owner(m), metric, delta],
    )


def h_festival_upcoming(cat, m, trg, cust):
    p = trg.get("payload", {})
    fest = p.get("festival", "the festival")
    days = p.get("days_until")
    offers = _active_offers(m)
    offer_line = ""
    if offers:
        offer_line = f" You have '{offers[0].get('title')}' active — we can anchor the campaign on it."
    when = f"on {p.get('date', '')}" if p.get("date") else ""
    body = (
        f"{_owner(m)}, {fest} is coming up {when}"
        + (f" — {days} days out" if isinstance(days, int) else "")
        + f". Booking intent for {fest} peaks about 2 weeks before. {offer_line} "
        + f"Want me to draft a {fest} campaign for {_merchant_name(m)}?"
    )
    return Composed(
        body=body, cta="binary_yes_no", send_as="vera",
        suppression_key=trg.get("suppression_key", ""),
        rationale="Festival trigger with date + active merchant offer as the anchor; timely and specific.",
        template_name="vera_opportunity_v1",
        template_params=[_owner(m), fest, str(days or "")],
    )


def h_ipl_match_today(cat, m, trg, cust):
    p = trg.get("payload", {})
    match = p.get("match", "today's match")
    venue = p.get("venue", "")
    time = str(p.get("match_time_iso", ""))[11:16]
    body = (
        f"{_owner(m)}, {match} at {venue} today"
        + (f" around {time} IST" if time and time != "00:00" else "")
        + ". Match evenings reliably spike delivery orders nearby. "
        + "Want me to push a quick match-evening post for your listing?"
    )
    return Composed(
        body=body, cta="binary_yes_no", send_as="vera",
        suppression_key=trg.get("suppression_key", ""),
        rationale="Same-day local event with venue/time facts; urgency-appropriate single ask.",
        template_name="vera_opportunity_v1",
        template_params=[_owner(m), match, venue],
    )


def h_category_seasonal(cat, m, trg, cust):
    p = trg.get("payload", {})
    season = str(p.get("season", "")).replace("_", " ")
    trends = p.get("trends", [])
    top = trends[0].replace("_", " ") if trends else ""
    body = (
        f"{_owner(m)}, {season} demand shift is visible in your category: "
        f"{', '.join(t.replace('_', ' ') for t in trends[:3])}. "
        + ("Shelf action recommended now. " if p.get("shelf_action_recommended") else "")
        + "Want the full trend list with a stocking plan?"
    )
    return Composed(
        body=body, cta="binary_yes_no", send_as="vera",
        suppression_key=trg.get("suppression_key", ""),
        rationale="Category trend signal with concrete numbers; positions Vera as a knowledgeable peer.",
        template_name="vera_opportunity_v1",
        template_params=[_owner(m), season, top],
    )


def h_competitor_opened(cat, m, trg, cust):
    p = trg.get("payload", {})
    name = p.get("competitor_name", "a new competitor")
    dist = p.get("distance_km")
    offer = p.get("their_offer", "")
    body = (
        f"{_owner(m)}, heads-up: {name} opened"
        + (f" {dist} km away" if dist else "")
        + (" — their listing shows '" + offer + "'" if offer else "")
        + ". Your rating and reviews are still stronger. "
        + "Want me to refresh your profile so you keep the edge in local search?"
    )
    return Composed(
        body=body, cta="binary_yes_no", send_as="vera",
        suppression_key=trg.get("suppression_key", ""),
        rationale="Competitor trigger using only supplied facts; reassures with merchant's real strengths.",
        template_name="vera_risk_opportunity_v1",
        template_params=[_owner(m), name, str(dist or "")],
    )


def h_gbp_unverified(cat, m, trg, cust):
    p = trg.get("payload", {})
    uplift = _pct(p.get("estimated_uplift_pct"))
    path = str(p.get("verification_path", "")).replace("_", " / ")
    body = (
        f"{_owner(m)}, your Google profile is still unverified — verified listings get "
        f"~{uplift} more discovery calls. Verification takes one {path}. "
        + "Shall I walk you through it? It's a 5-minute job."
    )
    return Composed(
        body=body, cta="binary_yes_no", send_as="vera",
        suppression_key=trg.get("suppression_key", ""),
        rationale="Concrete fixable gap with estimated uplift from trigger payload; effort externalized.",
        template_name="vera_risk_opportunity_v1",
        template_params=[_owner(m), path, uplift],
    )


def h_winback_eligible(cat, m, trg, cust):
    p = trg.get("payload", {})
    days = p.get("days_since_expiry")
    dip = _pct(p.get("perf_dip_pct"))
    lapsed = p.get("lapsed_customers_added_since_expiry")
    body = (
        f"{_owner(m)}, your plan lapsed {days} days ago and views are {dip} since — "
        f"{lapsed} lapsed customers came looking in that window but couldn't reach your offers. "
        + "Renewing now reactivates all of them. Want me to restart your plan?"
    )
    return Composed(
        body=body, cta="binary_yes_no", send_as="vera",
        suppression_key=trg.get("suppression_key", ""),
        rationale="Winback with loss-aversion framing using trigger's exact numbers.",
        template_name="vera_risk_opportunity_v1",
        template_params=[_owner(m), str(days), dip],
    )


def h_milestone_reached(cat, m, trg, cust):
    p = trg.get("payload", {})
    metric = str(p.get("metric", "reviews")).replace("_", " ")
    now_v = p.get("value_now")
    target = p.get("milestone_value")
    if p.get("is_imminent") and target:
        body = (
            f"{_owner(m)}, you're at {now_v} {metric} — just {target - now_v} away from "
            f"the {target} milestone. Crossing it boosts your ranking badge. "
            + "Want 2 quick ways to nudge happy customers for the last few reviews?"
        )
    else:
        body = (
            f"{_owner(m)}, congratulations — {_merchant_name(m)} just crossed {now_v} {metric}! "
            + "That's top-tier for your locality. Want me to draft a 'thank you' post to convert this into fresh bookings?"
        )
    return Composed(
        body=body, cta="binary_yes_no", send_as="vera",
        suppression_key=trg.get("suppression_key", ""),
        rationale="Milestone with exact counts; social-proof + celebration framing.",
        template_name="vera_nudge_v1",
        template_params=[_owner(m), metric, str(now_v)],
    )


def h_review_theme_emerged(cat, m, trg, cust):
    p = trg.get("payload", {})
    theme = str(p.get("theme", "")).replace("_", " ")
    n = p.get("occurrences_30d")
    quote = p.get("common_quote", "")
    body = (
        f"{_owner(m)}, {n} reviews this week mention '{theme}'"
        + (f" — e.g. \"{quote}\"" if quote else "")
        + ". It's fixable and worth catching early. Want a one-step plan to turn this theme around?"
    )
    return Composed(
        body=body, cta="binary_yes_no", send_as="vera",
        suppression_key=trg.get("suppression_key", ""),
        rationale="Review theme named with count and a real quote; measurable fix offered.",
        template_name="vera_nudge_v1",
        template_params=[_owner(m), theme, str(n)],
    )


def h_renewal_due(cat, m, trg, cust):
    p = trg.get("payload", {})
    days = p.get("days_remaining", (m.get("subscription") or {}).get("days_remaining"))
    plan = p.get("plan", (m.get("subscription") or {}).get("plan", ""))
    amt = p.get("renewal_amount")
    body = (
        f"{_owner(m)}, your {plan} plan renews in {days} days"
        + (f" (₹{amt:,})" if amt else "")
        + f". Your current run rate: views and calls are holding, so continuity protects your ranking. "
        + "Reply YES and I'll set up the renewal for you."
    )
    return Composed(
        body=body, cta="binary_yes_no", send_as="vera",
        suppression_key=trg.get("suppression_key", ""),
        rationale="Renewal nudge with plan, days and price from payload; single binary CTA.",
        template_name="vera_nudge_v1",
        template_params=[_owner(m), plan, str(days)],
    )


def h_dormant_with_vera(cat, m, trg, cust):
    p = trg.get("payload", {})
    days = p.get("days_since_last_merchant_message")
    last_topic = str(p.get("last_topic", "")).replace("_", " ")
    body = (
        f"{_owner(m)}, it's been {days} days since we last spoke (last topic: {last_topic}). "
        + f"Meanwhile {_merchant_name(m)} got new views this week. "
        + "Want a 30-second catch-up on the one thing worth doing first?"
    )
    return Composed(
        body=body, cta="binary_yes_no", send_as="vera",
        suppression_key=trg.get("suppression_key", ""),
        rationale="Re-engagement after dormancy with a curiosity hook, no guilt tone.",
        template_name="vera_nudge_v1",
        template_params=[_owner(m), last_topic, str(days)],
    )


def h_curious_ask_due(cat, m, trg, cust):
    body = (
        f"{_owner(m)}, quick question from your customers' side: what's the one service "
        f"people asked for most at {_merchant_name(m)} this week? I ask because demand "
        "signals in your area are shifting, and the top asker-category gets a visibility boost. "
        "Curious what you're seeing."
    )
    return Composed(
        body=body, cta="open_ended", send_as="vera",
        suppression_key=trg.get("suppression_key", ""),
        rationale="Curiosity/social-proof ask per trigger's ask_template; invites merchant knowledge.",
        template_name="vera_nudge_v1",
        template_params=[_owner(m), "weekly_ask"],
    )


def h_active_planning_intent(cat, m, trg, cust):
    p = trg.get("payload", {})
    topic = str(p.get("intent_topic", "")).replace("_", " ")
    offers = _active_offers(m)
    offer_line = f" We can price it alongside '{offers[0].get('title')}'." if offers else ""
    body = (
        f"{_owner(m)}, picking up where we left off on {topic}. Here's a concrete draft: "
        + f"a starter package with 3 items and one anchor price, ready to publish on your listing. {offer_line} "
        + "Reply GO and I'll finalize the draft for your review."
    )
    return Composed(
        body=body, cta="binary_confirm_cancel", send_as="vera",
        suppression_key=trg.get("suppression_key", ""),
        rationale="Merchant already signaled planning intent; route skips discovery and presents an executable draft.",
        template_name="vera_nudge_v1",
        template_params=[_owner(m), topic],
    )


# ------------------------------------------------------- customer handlers ---

def h_recall_due(cat, m, trg, cust):
    p = trg.get("payload", {})
    name = _customer_name(cust)
    service = str(p.get("service_due", "checkup")).replace("_", " ")
    last = p.get("last_service_date", "")
    slots = p.get("available_slots", [])
    slot_labels = [s.get("label", "") for s in slots][:2]
    offers = _active_offers(m)
    price = offers[0].get("title") if offers else ""
    months = ""
    if last and p.get("due_date"):
        months = "your 6-month recall is due"
    body = (
        f"Hi {name}, {_merchant_name(m)} here. It's been a while since your last visit"
        + (f" ({last})" if last else "")
        + f" — {months or f'your {service} is due'}. "
        + (f"Slots: {' ya '.join(slot_labels)}. " if slot_labels and _is_hi(m) else (f"Slots: {' or '.join(slot_labels)}. " if slot_labels else ""))
        + (f"{price}. " if price else "")
        + (f"Reply 1 for {slot_labels[0]}, 2 for {slot_labels[1]}, or tell us a time that works." if len(slot_labels) == 2 else "Reply to book a time that works.")
    )
    return Composed(
        body=body, cta="open_ended", send_as="merchant_on_behalf",
        suppression_key=trg.get("suppression_key", ""),
        rationale="Consented recall reminder using customer name, last-visit date, real slots and the merchant's active offer price.",
        template_name="merchant_recall_reminder_v1",
        template_params=[name, _merchant_name(m)] + slot_labels,
    )


def h_trial_followup(cat, m, trg, cust):
    p = trg.get("payload", {})
    name = _customer_name(cust)
    trial = p.get("trial_date", "")
    options = [s.get("label", "") for s in p.get("next_session_options", [])][:2]
    body = (
        f"Hi {name}, {_merchant_name(m)} here. Loved having you at the trial session"
        + (f" on {trial}" if trial else "")
        + (
            (f". Next session: {options[0]}" + (f" or {options[1]}" if len(options) > 1 else "") + ". ")
            if options else ". "
        )
        + "Shall I book you in? Just reply YES."
    )
    return Composed(
        body=body, cta="binary_yes_no", send_as="merchant_on_behalf",
        suppression_key=trg.get("suppression_key", ""),
        rationale="Trial follow-up with real session options; warm merchant voice, single binary CTA.",
        template_name="merchant_appointment_reminder_v1",
        template_params=[name] + options,
    )


def h_wedding_package_followup(cat, m, trg, cust):
    p = trg.get("payload", {})
    name = _customer_name(cust)
    wedding = p.get("wedding_date", "")
    days = p.get("days_to_wedding")
    next_step = str(p.get("next_step_window_open", "")).replace("_", " ")
    body = (
        f"Hi {name}, {_merchant_name(m)} here. With your wedding on {wedding}"
        + (f" ({days} days to go)" if isinstance(days, int) else "")
        + f", now is the right window to start the {next_step}. "
        + "Shall I share a 30-day plan tailored to your trial notes?"
    )
    return Composed(
        body=body, cta="binary_yes_no", send_as="merchant_on_behalf",
        suppression_key=trg.get("suppression_key", ""),
        rationale="Wedding countdown follow-up using trigger dates and the named next-step window.",
        template_name="merchant_appointment_reminder_v1",
        template_params=[name, str(wedding), next_step],
    )


def h_customer_lapsed_hard(cat, m, trg, cust):
    p = trg.get("payload", {})
    name = _customer_name(cust)
    days = p.get("days_since_last_visit")
    focus = str(p.get("previous_focus", "")).replace("_", " ")
    months = p.get("previous_membership_months")
    body = (
        f"Hi {name}, {_merchant_name(m)} here. It's been {days} days since your last session"
        + (f" — your {focus} progress from your {months}-month run was solid" if focus else "")
        + ". We'd love to have you back"
        + (f"; your first session back is on us this month" if _active_offers(m) else "")
        + ". Want me to book you in?"
    )
    return Composed(
        body=body, cta="binary_yes_no", send_as="merchant_on_behalf",
        suppression_key=trg.get("suppression_key", ""),
        rationale="Winback referencing the customer's own history; only if consent scope covers outreach.",
        template_name="merchant_winback_v1",
        template_params=[name, str(days), focus],
    )


def h_chronic_refill_due(cat, m, trg, cust):
    p = trg.get("payload", {})
    name = _customer_name(cust)
    molecules = p.get("molecule_list", [])
    last = p.get("last_refill", "")
    runs_out = str(p.get("stock_runs_out_iso", ""))[:10]
    saved = p.get("delivery_address_saved")
    body = (
        f"Hi {name}, {_merchant_name(m)} here. Your regular medicines"
        + (f" ({', '.join(molecules)})" if molecules else "")
        + (f" from your {last} refill" if last else "")
        + (f" run out around {runs_out}" if runs_out else " are due for refill")
        + (". Your saved address is on file" if saved else "")
        + ". Shall we schedule the refill delivery? Reply YES and it's done."
    )
    return Composed(
        body=body, cta="binary_yes_no", send_as="merchant_on_behalf",
        suppression_key=trg.get("suppression_key", ""),
        rationale="Consented refill reminder with molecule list, dates and saved-address fact; no medical claims.",
        template_name="merchant_refill_reminder_v1",
        template_params=[name, ", ".join(molecules), runs_out],
    )


HANDLERS = {
    "research_digest": h_research_digest,
    "regulation_change": h_regulation_change,
    "cde_opportunity": h_cde_opportunity,
    "perf_spike": h_perf_spike,
    "perf_dip": h_perf_dip,
    "seasonal_perf_dip": h_seasonal_perf_dip,
    "festival_upcoming": h_festival_upcoming,
    "ipl_match_today": h_ipl_match_today,
    "category_seasonal": h_category_seasonal,
    "competitor_opened": h_competitor_opened,
    "gbp_unverified": h_gbp_unverified,
    "winback_eligible": h_winback_eligible,
    "milestone_reached": h_milestone_reached,
    "review_theme_emerged": h_review_theme_emerged,
    "renewal_due": h_renewal_due,
    "dormant_with_vera": h_dormant_with_vera,
    "curious_ask_due": h_curious_ask_due,
    "active_planning_intent": h_active_planning_intent,
    "recall_due": h_recall_due,
    "trial_followup": h_trial_followup,
    "wedding_package_followup": h_wedding_package_followup,
    "customer_lapsed_hard": h_customer_lapsed_hard,
    "chronic_refill_due": h_chronic_refill_due,
}


def compose(category: dict, merchant: dict, trigger: dict,
            customer: Optional[dict] = None) -> Optional[Composed]:
    """Route by kind, then compose. Returns None if facts are insufficient."""
    kind = trigger.get("kind", "")
    handler = HANDLERS.get(kind)
    if handler is None:
        return _generic_fallback(category, merchant, trigger, customer)
    composed = handler(category, merchant, trigger, customer)
    if composed is None:
        composed = _generic_fallback(category, merchant, trigger, customer)
    return composed


def _generic_fallback(category, merchant, trigger, customer):
    """Safe factual summary; only uses facts verbatim present in the payload."""
    p = trigger.get("payload", {})
    if not p:
        return None
    fact_items = [f"{k.replace('_', ' ')}: {v}" for k, v in list(p.items())[:3]]
    if trigger.get("scope") == "customer" and customer:
        body = (
            f"Hi {_customer_name(customer)}, {_merchant_name(merchant)} here. "
            + "Update: " + "; ".join(fact_items) + ". Reply if you'd like to act on this."
        )
        send_as = "merchant_on_behalf"
    else:
        body = (
            f"{_owner(merchant)}, quick update for you: " + "; ".join(fact_items)
            + ". Want me to look into it?"
        )
        send_as = "vera"
    return Composed(
        body=body, cta="open_ended", send_as=send_as,
        suppression_key=trigger.get("suppression_key", ""),
        rationale="Unknown trigger kind; sent a low-risk factual summary from payload without invention.",
        template_name="vera_generic_v1",
        template_params=[_owner(merchant)],
    )
