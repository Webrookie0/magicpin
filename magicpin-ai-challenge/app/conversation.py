"""Reply handling: deterministic classification + conversation state machine."""
import re
import time
from typing import Optional

from . import composer
from .stores import (
    ACTION_PENDING, ENDED, ENGAGED, INITIATED, WAITING, WAITING_FOR_REPLY,
    Conversation, ConversationStore, OutreachState,
)

# ---------------------------------------------------------------- patterns --

OPT_OUT_RE = re.compile(
    r"\b(stop|stop messaging|unsubscribe|not interested|no more|do not message|"
    r"dont message|don't message|band karo|band kijiye|spam|useless|harassment|"
    r"bothering me|ghatia)\b", re.I)
HOSTILE_RE = re.compile(r"\b(idiot|stupid|nonsense|bakwas|bkwass|fool)\b", re.I)

AUTO_REPLY_RES = [
    re.compile(r"thank you for contacting", re.I),
    re.compile(r"team will (respond|reply|get back)", re.I),
    re.compile(r"(we|our team) will (get back|respond|reply)", re.I),
    re.compile(r"office hours", re.I),
    re.compile(r"automated (assistant|reply|message)", re.I),
    re.compile(r"whatsapp business", re.I),
    re.compile(r"away from (my|our) (desk|phone)", re.I),
    re.compile(r"currently (unavailable|busy|out of office)", re.I),
]

INTENT_RE = re.compile(
    r"\b(yes|yeah|yep|haan|ha\.|ok|okay|sure|go ahead|lets do it|let's do it|"
    r"send it|send me|please send|i want to join|join karna hai|kar do|kar dijiye|"
    r"book|confirm|proceed| sounds good|do it|please do|count me in|reserve)\b", re.I)

REJECT_RE = re.compile(r"\b(no thanks|not now|maybe later|no need)\b", re.I)

QUESTION_RE = re.compile(r"\?|\b(what|when|how|why|can you|kya|kab|kaise|kitna)\b", re.I)

OFF_TOPIC_RE = re.compile(r"\b(gst|income tax|itr|loan|insurance|visa|passport)\b", re.I)


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def classify(message: str) -> str:
    """Return one of: opt_out, auto_reply, intent, rejection, question, text."""
    if OPT_OUT_RE.search(message) or HOSTILE_RE.search(message):
        return "opt_out"
    if any(r.search(message) for r in AUTO_REPLY_RES):
        return "auto_reply"
    if INTENT_RE.search(message):
        return "intent"
    if REJECT_RE.search(message):
        return "rejection"
    if QUESTION_RE.search(message):
        return "question"
    return "text"


# ---------------------------------------------------------------- policies --

def _is_auto_reply(message: str, conv: Conversation, state: OutreachState) -> bool:
    if any(r.search(message) for r in AUTO_REPLY_RES):
        return True
    fp = normalize(message)
    if fp and conv.fingerprint_counts.get(fp, 0) >= 2:
        return True  # same text 3+ times = auto-reply
    return False


class ReplyEngine:
    def __init__(self, contexts, convs: ConversationStore, outreach: OutreachState):
        self.contexts = contexts
        self.convs = convs
        self.outreach = outreach

    def _send(self, conv, body, cta, rationale):
        """Anti-repetition guard: never send the same body twice in one conversation."""
        if body in conv.sent_bodies:
            return {"action": "wait", "wait_seconds": 3600,
                    "rationale": ("Composed follow-up would repeat a prior message; "
                                  "backing off instead of repeating.")}
        conv.sent_bodies.append(body)
        return {"action": "send", "body": body, "cta": cta, "rationale": rationale}

    # ------------------------------------------------------------- helpers --

    def _merchant(self, merchant_id):
        rec = self.contexts.get("merchant", merchant_id or "")
        return rec.payload if rec else {}

    def _category(self, merchant):
        slug = (merchant or {}).get("category_slug", "")
        rec = self.contexts.get("category", slug)
        return rec.payload if rec else {}

    def _customer(self, customer_id):
        if not customer_id:
            return {}
        rec = self.contexts.get("customer", customer_id)
        return rec.payload if rec else {}

    # --------------------------------------------------------------- entry --

    def handle(self, req) -> dict:
        conv = self.convs.get(req.conversation_id)
        if conv is None:
            conv = self._open_orphan_conversation(req)
        conv.turns.append({
            "from": req.from_role, "body": req.message,
            "ts": req.received_at or "", "turn": req.turn_number,
        })
        conv.updated_at = time.time()

        message = req.message or ""

        # 1) Opt-out / hostile: end and suppress.
        if classify(message) == "opt_out":
            conv.state = ENDED
            conv.suppressed = True
            self.outreach.suppressed_merchants[req.merchant_id or conv.merchant_id] = "opt_out"
            return {"action": "end", "rationale":
                    "Merchant opted out / hostile; ending conversation and suppressing future outreach."}

        # 2) Auto-reply detection with graduated backoff.
        if _is_auto_reply(message, conv, self.outreach):
            key = (req.merchant_id or conv.merchant_id, normalize(message))
            self.outreach.auto_reply_counts[key] = self.outreach.auto_reply_counts.get(key, 0) + 1
            n = self.outreach.auto_reply_counts[key]
            conv.fingerprint_counts[normalize(message)] = conv.fingerprint_counts.get(normalize(message), 0) + 1
            if n == 1:
                conv.state = WAITING_FOR_REPLY
                return self._send(
                    conv,
                    "Looks like an auto-reply — when the owner sees this, just reply "
                    "'Yes' and I'll pick it up from there.",
                    "binary_yes_no",
                    "Detected canned auto-reply; one owner-directed nudge before backing off.",
                )
            if n == 2:
                conv.state = WAITING
                return {"action": "wait", "wait_seconds": 86400,
                        "rationale": "Same auto-reply twice in a row — owner not at phone; backing off 24h."}
            conv.state = ENDED
            return {"action": "end",
                    "rationale": "Auto-reply 3x in a row with no engagement signal; closing conversation."}

        # 3) Explicit intent: action mode immediately, no qualification.
        if classify(message) == "intent":
            conv.intent_detected = True
            conv.state = ACTION_PENDING
            body = self._intent_followup(conv)
            return self._send(conv, body["body"], body["cta"], body["rationale"])

        # 4) Soft rejection: one short acknowledgment then wait.
        if classify(message) == "rejection":
            conv.state = WAITING
            return {"action": "wait", "wait_seconds": 604800,
                    "rationale": "Merchant said not now; backing off a week instead of pushing."}

        # 5) Curveball / off-topic question: acknowledge briefly, return to mission.
        if OFF_TOPIC_RE.search(message):
            body = self._curveball_reply(conv)
            return self._send(conv, body, "open_ended",
                              "Out-of-scope ask politely declined; redirected to the active mission.")

        # 6) Genuine question or engaged text: contextual follow-up from mission.
        conv.state = ENGAGED
        body = self._mission_followup(conv, message)
        return self._send(conv, body["body"], body["cta"], body["rationale"])

    # ------------------------------------------------------------ composers --

    def _open_orphan_conversation(self, req) -> Conversation:
        merchant = self._merchant(req.merchant_id)
        # Inherit the active mission from this merchant's most recent conversation.
        prior = None
        for conv in self.convs._convs.values():
            if conv.merchant_id != (req.merchant_id or ""):
                continue
            if req.customer_id and conv.customer_id != req.customer_id:
                continue
            if prior is None or conv.updated_at > prior.updated_at:
                prior = conv
        conv = self.convs.create(
            conversation_id=req.conversation_id,
            merchant_id=req.merchant_id or "",
            customer_id=req.customer_id,
            trigger_id=prior.trigger_id if prior else None,
            scope="customer" if req.customer_id else "merchant",
            route=prior.route if prior else "unknown",
            state=WAITING_FOR_REPLY,
        )
        if prior:
            conv.pending_action = dict(prior.pending_action or {})
            conv.intent_detected = prior.intent_detected
        else:
            conv.pending_action = {"merchant_name": merchant.get("identity", {}).get("name", "")}
        return conv

    def _intent_followup(self, conv) -> dict:
        merchant = self._merchant(conv.merchant_id)
        route = conv.route
        name = composer._owner(merchant) if merchant else "there"
        offers = composer._active_offers(merchant) if merchant else []

        if route == "research_digest":
            body = ("Sending the abstract now (2 pages). I've also drafted a patient-ed WhatsApp "
                    "you can share — reply CONFIRM and I'll pre-fill it as a Google post for tomorrow 10am.")
            cta = "binary_confirm_cancel"
        elif route in ("recall_due", "trial_followup", "wedding_package_followup", "chronic_refill_due",
                       "customer_lapsed_hard"):
            body = ("Booking you in now. I'll send the confirmed slot details right here once "
                    "it's locked. Anything specific we should note before your visit?")
            cta = "open_ended"
        elif route == "renewal_due":
            body = ("Setting up your renewal now — I'll share the payment link and confirmation "
                    "here. Your listing stays live without interruption.")
            cta = "none"
        elif route == "active_planning_intent" or conv.intent_detected:
            offer_line = f" I'll anchor it on '{offers[0].get('title')}'." if offers else ""
            body = (f"On it. Finalizing the draft for you now.{offer_line} "
                    "You'll have it in a minute — reply CONFIRM to publish.")
            cta = "binary_confirm_cancel"
        else:
            body = (f"Great, {name} — starting on it right away. I'll report back here as soon as "
                    "the first step is done.")
            cta = "none"
        return {"body": body, "cta": cta,
                "rationale": "Explicit commitment detected; switched to action mode without further qualification."}

    def _curveball_reply(self, conv) -> str:
        merchant = self._merchant(conv.merchant_id)
        route = conv.route
        anchors = {
            "research_digest": "the JIDA fluoride recall piece — want me to send the abstract?",
            "renewal_due": "your upcoming renewal — want me to set it up?",
            "perf_dip": "this week's call dip — want the 5-point profile check?",
            "competitor_opened": "the new listing nearby — want your profile refreshed first?",
        }
        anchor = anchors.get(route)
        if not anchor:
            pa = conv.pending_action or {}
            topic = pa.get("topic", "what I flagged earlier")
            anchor = f"{topic} — shall we finish that first?"
        return ("That one's outside what I can help with directly — your CA will be faster there. "
                f"Meanwhile, back to {anchor}")

    def _mission_followup(self, conv, message) -> dict:
        merchant = self._merchant(conv.merchant_id)
        route = conv.route
        name = composer._owner(merchant) if merchant else "there"
        offers = composer._active_offers(merchant) if merchant else []
        if conv.sent_bodies:
            return {
                "body": (f"Point taken, {name}. The one thing I'd do first: "
                         + (f"refresh '{offers[0].get('title')}' on your listing." if offers
                            else "pick the single highest-impact item on your profile and fix it this week."))
                        + " Want me to handle it?",
                "cta": "binary_yes_no",
                "rationale": "Engaged reply; advanced the mission with one concrete next step.",
            }
        return {
            "body": f"Thanks {name} — shall I take that as a go-ahead on {route.replace('_', ' ')}?",
            "cta": "binary_yes_no",
            "rationale": "Ambiguous engaged reply; confirmed direction without a long detour.",
        }
