"""Deterministic, extractive composition with explicit evidence and action drafts.

No model credentials or external side effects are required. Unknown or incomplete
triggers produce no message. Category examples are never treated as active offers.
"""
from dataclasses import dataclass, field
from datetime import datetime
import math
import re

from .routing import consent_allows, get_policy, trigger_ids
from .timeutils import parse_time

VERSION = "grounded-composer-v2"


def text(value):
    return value.strip() if isinstance(value, str) else ""


def label(value):
    return text(value).replace("_", " ")


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _owner(merchant):
    identity = merchant.get("identity") or {}
    first = text(identity.get("owner_first_name"))
    name = first or text(identity.get("name"))
    if first and merchant.get("category_slug") == "dentists" and not first.startswith("Dr."):
        return f"Dr. {first}"
    return name


def _merchant_name(merchant):
    return text((merchant.get("identity") or {}).get("name"))


def language(merchant, customer=None):
    identity = (customer or merchant).get("identity") or {}
    if customer:
        pref = text(identity.get("language_pref")).lower()
    else:
        langs = identity.get("languages") or []
        pref = text(identity.get("language_pref")).lower() or ("hi" if "hi" in langs else "en")
    for code in ("hi", "ta", "te", "kn", "mr"):
        if pref.startswith(code):
            return code
    return "en"


def _is_hi(merchant):
    return language(merchant) == "hi"


def _active_offers(merchant):
    return [o for o in merchant.get("offers", []) if o.get("status") == "active" and text(o.get("title"))]


def _find_digest(category, item_id):
    # An explicit missing id must not silently resolve to an unrelated paper.
    return next((x for x in category.get("digest", []) if item_id and x.get("id") == item_id), None)


def digest_item(category, trigger):
    p = trigger.get("payload") or {}
    item_id = p.get("top_item_id") or p.get("digest_item_id") or p.get("alert_id")
    if item_id:
        return _find_digest(category, item_id)
    if isinstance(p.get("top_item"), dict):
        return p["top_item"]
    kind = trigger.get("kind")
    allowed = {"regulation_change": {"compliance"}, "cde_opportunity": {"cde"},
               "supply_alert": {"supply"}}.get(kind, {"research", "trend", "tech", "seasonal"})
    return next((x for x in reversed(category.get("digest", [])) if x.get("kind") in allowed), None)


def slot_labels(payload, now=None):
    slots = payload.get("available_slots", payload.get("next_session_options", []))
    if not isinstance(slots, list):
        return []
    result = []
    for slot in slots:
        if not isinstance(slot, dict):
            continue
        if slot.get("iso"):
            try:
                instant = parse_time(slot["iso"])
                if now and instant <= now:
                    continue
                # Supplied labels sometimes have incorrect weekdays. Use the ISO
                # value (and its local offset) as the schedule source of truth.
                local = datetime.fromisoformat(slot["iso"].replace("Z", "+00:00"))
                rendered = local.strftime("%a %d %b, %H:%M %z")
            except (ValueError, TypeError):
                continue
        else:
            rendered = text(slot.get("label"))
        if rendered:
            result.append(rendered)
    return result[:2]


@dataclass
class Plan:
    fact: str
    ask: str = "Want a draft to review?"
    next_step: str = ""
    cta: str = "binary_yes_no"
    ask_hi: str = "Review ke liye draft banaun?"


@dataclass
class Composed:
    body: str
    cta: str
    send_as: str
    suppression_key: str
    rationale: str
    template_name: str | None
    template_params: list[str]
    fact: str = ""
    next_step: str = ""
    evidence: list[str] = field(default_factory=list)

    def public(self):
        return {k: getattr(self, k) for k in ("body", "cta", "send_as", "suppression_key", "rationale")}


def merchant_plan(cat, m, trg):
    p = trg.get("payload") or {}
    kind = trg.get("kind")
    name = _merchant_name(m)
    offers = _active_offers(m)
    active = f"Active offer: {offers[0]['title']}." if offers else ""
    if kind in {"research_digest", "research_digest_release", "category_research_digest_release", "regulation_change", "cde_opportunity"}:
        item = digest_item(cat, trg)
        if not item or not text(item.get("title")) or not text(item.get("source")):
            return None
        fact = f"{item['title']} — {item['source']}."
        if text(item.get("summary")):
            fact += " " + item["summary"]
        if kind == "regulation_change" and text(p.get("deadline_iso")):
            fact += f" Deadline: {p['deadline_iso']}."
        if kind == "cde_opportunity":
            for key in ("date", "credits"):
                value = p.get(key, item.get(key))
                if isinstance(value, (str, int, float)):
                    fact += f" {key.title()}: {value}."
            if text(p.get("fee")):
                fact += f" Fee: {label(p['fee'])}."
            return Plan(fact, "Want the registration checklist?",
                        f"Registration checklist: check eligibility and fee with the organiser; request a place for {item['title']}. No seat has been reserved.",
                        ask_hi="Registration checklist bhejun?")
        next_step = f"Source summary: {item.get('summary') or item['title']} — {item['source']}."
        if kind == "regulation_change":
            next_step += "\nChecklist: compare your current process with the cited notice; record any gaps and the required changes."
        else:
            noun = "care" if cat.get("slug") in {"dentists", "pharmacies"} else "service"
            next_step += f'\nCustomer note draft: "For questions about your {noun}, the team at {name} can explain the options relevant to you."'
        return Plan(fact, "Want a source summary and a draft note for review?", next_step,
                    ask_hi="Source summary aur review ke liye note ka draft bhejun?")
    if kind in {"perf_dip", "perf_spike", "seasonal_perf_dip"}:
        metric = text(p.get("metric"))
        delta = p.get("delta_pct")
        if not metric or not number(delta):
            return None
        window = label(p.get("window"))
        fact = f"{name}: {metric} changed {delta * 100:+g}%" + (f" over {window}" if window else "") + "."
        perf = m.get("performance") or {}
        if number(perf.get(metric)) and number(perf.get("window_days")):
            fact += f" Latest {perf['window_days']}-day snapshot: {perf[metric]} {metric}."
        if kind == "seasonal_perf_dip" and p.get("is_expected_seasonal") is True:
            fact += f" Flagged as expected seasonality: {label(p.get('season_note'))}."
            count = (m.get("customer_aggregate") or {}).get("total_active_members")
            if number(count):
                fact += f" You have {count} active members."
            return Plan(fact, "Want an attendance-message draft for your members?",
                        f'Attendance draft: "{name} members, what would help you keep a regular routine this season? Share your preferred session time."',
                        ask_hi="Members ke liye attendance message ka draft banaun?")
        if (m.get("identity") or {}).get("verified") is False:
            fact += " Your Google profile is unverified."
        return Plan(fact, "Want a profile checklist?",
                    f"Profile checklist for {name}: check verification, opening hours and service details against your current operations. " + active,
                    ask_hi="Profile checklist bhejun?")
    if kind == "festival_upcoming":
        festival = text(p.get("festival"))
        if not festival:
            return None
        fact = f"{festival} is coming up" + (f" on {p['date']}" if text(p.get("date")) else "") + ". " + active
        draft = f'Campaign draft for review: "{name} — {festival}. {active}" Terms and availability need your approval before use.'
        return Plan(fact, f"Want a {festival} campaign draft?", draft, ask_hi=f"{festival} campaign ka draft banaun?")
    if kind == "ipl_match_today":
        match = text(p.get("match"))
        if not match:
            return None
        fact = f"Match update: {match}"
        if text(p.get("venue")):
            fact += f" at {p['venue']}"
        if text(p.get("match_time_iso")):
            fact += f", {p['match_time_iso']}"
        fact += "."
        if offers:
            fact += f" Your listed offer is {offers[0]['title']}; keep its day restrictions when preparing any match post."
        return Plan(fact, "Want a match-day post draft?",
                    f'Draft for review: "{match} — following the match? Contact {name} for current menu and ordering details."',
                    ask_hi="Match-day post ka draft banaun?")
    if kind == "supply_alert":
        molecule, batches = text(p.get("molecule")), p.get("affected_batches", [])
        if not molecule or not isinstance(batches, list) or not batches or not all(isinstance(x, str) for x in batches):
            return None
        fact = f"Supply alert: {molecule}, batches {', '.join(batches)}"
        if text(p.get("manufacturer")):
            fact += f", manufacturer {p['manufacturer']}"
        item = digest_item(cat, trg)
        if item and text(item.get("source")):
            fact += f" — {item['source']}"
        fact += ". Check these identifiers against your stock and dispensing records."
        return Plan(fact, "Want a batch-check workflow?",
                    f"Batch-check workflow: locate {', '.join(batches)} in stock and dispensing records; verify the supplier's notice; follow its replacement instructions. Customer exposure cannot be determined from aggregate counts.",
                    ask_hi="Batch-check workflow bhejun?")
    if kind == "category_seasonal":
        trends = p.get("trends")
        if not isinstance(trends, list) or not trends or not all(isinstance(t, str) for t in trends):
            return None
        fact = f"{label(p.get('season'))} category trends: {'; '.join(label(t) for t in trends[:3])}."
        return Plan(fact, "Want a stock-review checklist?", "Stock-review checklist: compare these demand signals with current stock, expiry dates and recent sales before changing orders.", ask_hi="Stock-review checklist bhejun?")
    if kind == "competitor_opened":
        competitor = text(p.get("competitor_name"))
        if not competitor:
            return None
        fact = f"Nearby opening: {competitor}"
        if number(p.get("distance_km")):
            fact += f", {p['distance_km']} km away"
        if text(p.get("their_offer")):
            fact += f". Their listed offer: {p['their_offer']}"
        return Plan(fact + ".", "Want a profile comparison checklist?", f"Comparison checklist: compare {competitor}'s published services and hours with your listing; highlight your actual services. " + active, ask_hi="Profile comparison checklist bhejun?")
    if kind == "gbp_unverified":
        if (m.get("identity") or {}).get("verified") is not False:
            return None
        path = label(p.get("verification_path"))
        fact = "Your Google profile is unverified." + (f" Recorded verification route: {path}." if path else "")
        return Plan(fact, "Want the verification steps?", f"Verification steps: open your Google Business Profile, select verification and follow the offered method{': ' + path if path else ''}. Approval must come from Google.", ask_hi="Verification steps bhejun?")
    if kind in {"renewal_due", "winback_eligible"}:
        sub = m.get("subscription") or {}
        days = p.get("days_remaining", sub.get("days_remaining")) if kind == "renewal_due" else p.get("days_since_expiry", sub.get("days_since_expiry"))
        if not number(days):
            return None
        plan_name = text(p.get("plan")) or text(sub.get("plan"))
        fact = f"Your {plan_name} plan " + (f"has {days} days remaining." if kind == "renewal_due" else f"expired {days} days ago.")
        if number(p.get("renewal_amount")):
            fact += f" Renewal amount: ₹{p['renewal_amount']:,}."
        return Plan(fact, "Want a renewal request draft?", f'Renewal request draft: "Please confirm renewal terms for {name}. {fact}" Payment and renewal require confirmation from the provider.', ask_hi="Renewal request ka draft banaun?")
    if kind == "milestone_reached":
        value, target = p.get("value_now"), p.get("milestone_value")
        metric = label(p.get("metric"))
        if not number(value) or not metric:
            return None
        fact = f"{name} is at {value} {metric}."
        if p.get("is_imminent") is True and number(target) and target > value:
            fact += f" {target - value:g} more to reach {target}."
        return Plan(fact, "Want a thank-you post draft?", f'Thank-you draft: "{name} is at {value} {metric}. Thank you for sharing your experience with us."', ask_hi="Thank-you post ka draft banaun?")
    if kind == "review_theme_emerged":
        count, theme = p.get("occurrences_30d"), label(p.get("theme"))
        if not number(count) or not theme:
            return None
        fact = f"{count} reviews in the last 30 days mention {theme}."
        if text(p.get("common_quote")):
            fact += f' Example: "{p["common_quote"]}".'
        return Plan(fact, "Want a response and process-check draft?", f'Review response draft: "Thank you for your feedback about {theme}. We would like to understand your experience." Process check: review the relevant recent service records before deciding a change.', ask_hi="Review response aur process-check draft banaun?")
    if kind == "dormant_with_vera":
        days = p.get("days_since_last_merchant_message")
        if not number(days):
            return None
        topic = label(p.get("last_topic"))
        return Plan(f"It has been {days} days since your last message." + (f" Last topic: {topic}." if topic else ""), "Want a brief account recap?", f"Account recap for {name}: " + (active or "No active offer is recorded in the current context."), ask_hi="Account ka short recap bhejun?")
    if kind in {"curious_ask_due", "scheduled_recurring"}:
        if p.get("ask_template") != "what_service_in_demand_this_week":
            return None
        return Plan(f"Weekly check-in for {name}.", "Which service did customers ask for most this week?", "", "open_ended", "Is hafte customers ne sabse zyada kaunsi service poochhi?")
    if kind == "active_planning_intent":
        topic = label(p.get("intent_topic"))
        if not topic:
            return None
        draft = (f"Draft for review — {topic} at {name}:\n"
                 "Service/package: [confirm scope]\nPrice: [confirm price]\nSchedule and capacity: [confirm availability].")
        if active:
            draft += f"\nReference only — {active} Package pricing is still to be decided."
        return Plan(draft, "", draft, "none", "")
    # Whitelisted factual headline only, never serialize arbitrary payloads or IDs.
    headline = text(p.get("headline")) or text(p.get("title"))
    if headline:
        return Plan(f"Update: {headline}.", "", f"Available update: {headline}.", "none", "")
    return None


def customer_plan(cat, m, trg, cust, now=None):
    p, kind = trg.get("payload") or {}, trg.get("kind")
    slots = slot_labels(p, now)
    if kind == "recall_due":
        service = label(p.get("service_due"))
        if not service:
            return None
        fact = f"Reminder for {service}" + (f", due {p['due_date']}" if text(p.get("due_date")) else "") + "."
        if text(p.get("last_service_date")):
            fact += f" Last recorded service: {p['last_service_date']}."
    elif kind == "appointment_tomorrow":
        appointment = text(p.get("appointment_at")) or text(p.get("appointment_time_iso")) or text(p.get("appointment_iso"))
        if not appointment:
            return None
        fact = f"Appointment reminder: {appointment}."
    elif kind == "trial_followup":
        if not text(p.get("trial_date")):
            return None
        fact = f"Following up on your trial on {p['trial_date']}."
    elif kind in {"wedding_package_followup", "bridal_followup"}:
        if not text(p.get("wedding_date")):
            return None
        fact = f"Following up on your wedding plans for {p['wedding_date']}."
        if text(p.get("next_step_window_open")):
            fact += f" Proposed next step: {label(p['next_step_window_open'])}."
    elif kind == "chronic_refill_due":
        medicines = p.get("molecule_list")
        if not isinstance(medicines, list) or not medicines or not all(isinstance(x, str) for x in medicines):
            return None
        fact = f"Refill reminder for {', '.join(medicines)}."
        if text(p.get("stock_runs_out_iso")):
            fact += f" Estimated supply end: {p['stock_runs_out_iso'][:10]}."
        return Plan(fact, "Would you like the pharmacy to check your refill request?", "Refill request noted in this conversation. The pharmacist must check the prescription, stock, price and delivery before confirming.", ask_hi="Pharmacy se refill request check karwana chahenge?")
    elif kind in {"customer_lapsed_soft", "customer_lapsed_hard", "winback_customer"}:
        days = p.get("days_since_last_visit")
        if not number(days):
            return None
        fact = f"It has been {days} days since your last visit. We'd be happy to welcome you back when it suits you."
    else:
        return None
    if slots:
        fact += " Listed options: " + " or ".join(slots) + "."
        ask, hi, cta = "Which listed time would you prefer?", "Inmein se kaunsa time aapko suit karega?", "open_ended"
    else:
        ask, hi, cta = "Would you like to request a suitable time?", "Apne liye suitable time request karna chahenge?", "binary_yes_no"
    return Plan(fact, ask, "Your request is noted in this conversation. Availability and booking still need confirmation from the business.", cta, hi)


def compose(category, merchant, trigger, customer=None, *, now=None):
    """Return a validated draft, or None when evidence/consent is insufficient."""
    from .guardrails import validate
    p = trigger.get("payload") or {}
    if not category or not merchant or not p or p.get("placeholder") is True:
        return None
    mid, cid = trigger_ids(trigger)
    if mid != merchant.get("merchant_id") or category.get("slug") != merchant.get("category_slug"):
        return None
    policy = get_policy(trigger.get("kind", ""))
    scope = trigger.get("scope")
    if scope not in {"merchant", "customer"} or scope != policy.audience:
        return None
    if now:
        try:
            if trigger.get("expires_at") and parse_time(trigger["expires_at"]) <= now:
                return None
            if trigger.get("not_before") and parse_time(trigger["not_before"]) > now:
                return None
        except (ValueError, TypeError):
            return None
    if scope == "customer":
        if not customer or customer.get("merchant_id") != mid or customer.get("customer_id") != cid or not consent_allows(policy, customer, now):
            return None
        plan = customer_plan(category, merchant, trigger, customer, now)
    else:
        customer = None
        plan = merchant_plan(category, merchant, trigger)
    if not plan or not plan.fact:
        return None
    lang = language(merchant, customer)
    ask = plan.ask_hi if lang == "hi" else plan.ask
    if customer:
        name = text((customer.get("identity") or {}).get("name"))
        channel = (customer.get("preferences") or {}).get("channel", "")
        if channel == "whatsapp_via_son":
            name = f"family of {name}"
        greeting = {"hi": "Namaste", "ta": "Vanakkam", "te": "Namaskaram", "kn": "Namaskara", "mr": "Namaskar"}.get(lang, "Hi")
        opening = f"{greeting} {name}, {_merchant_name(merchant)} here."
    else:
        opening = f"{_owner(merchant)}," + (" ek update:" if lang == "hi" else "")
    body = " ".join(x for x in (opening, plan.fact, ask) if x)
    result = Composed(body, plan.cta, "merchant_on_behalf" if customer else "vera",
                      text(trigger.get("suppression_key")),
                      f"{trigger.get('kind')}: uses supplied event facts and current recipient context; "
                      + ("purpose-specific consent checked." if customer else "offers a reviewable next step without claiming execution."),
                      policy.template_name, [body], plan.fact, plan.next_step,
                      [plan.fact])
    return None if validate(result, category, merchant, trigger, customer) else result
