"""Recipient-bound state machine. Prepare actions; never claim external execution."""
import re
import time
from datetime import timedelta

from fastapi import HTTPException

from . import composer
from .guardrails import validate_text
from .routing import consent_allows, get_policy, trigger_ids
from .stores import ACTION_PENDING, ENDED, ENGAGED, WAITING, WAITING_FOR_REPLY
from .timeutils import parse_time

OPT_OUT_RE = re.compile(r"\b(stop(?: messaging| sending)?|unsubscribe|not interested|do not (?:message|contact)|don't (?:message|contact)|dont (?:message|contact)|band karo|band kijiye|spam|useless|bothering me|idiot|bakwas)\b|बंद करो", re.I)
AUTO_REPLY_RE = re.compile(r"thank(?:s| you) for contacting|(?:our team|we|team) will (?:respond|reply|get back)|automated (?:assistant|reply|message)|away from (?:my|our) (?:desk|phone)|currently unavailable|team tak pahunch", re.I)
DEFER_RE = re.compile(r"\b(not now|maybe later|later|busy|give me (?:some )?time|tomorrow|baad mein|kal)\b", re.I)
NEGATION_RE = re.compile(r"\b(no|nope|nah|don't|dont|do not|not yet|cancel|nahi)\b|नहीं", re.I)
INTENT_RE = re.compile(r"\b(go ahead|let'?s do it|send it|send me|please send|i want to join|join karna hai|kar do|kar dijiye|proceed|do it|please do|count me in|book me|reserve me)\b", re.I)
ACCEPT_RE = re.compile(r"^\s*(yes|yeah|yep|haan|han|ha|okay|ok|sure|confirm|go|हां|हाँ)(?:\s|[,.!]|$)", re.I)
OFF_TOPIC_RE = re.compile(r"\b(gst|income tax|itr|loan|insurance|visa|passport)\b", re.I)
QUESTION_RE = re.compile(r"\?|\b(what|when|how|why|can you|kya|kab|kaise|kitna)\b", re.I)


def normalize(message):
    return re.sub(r"[\W_]+", "", message.casefold())


def classify(message):
    if OPT_OUT_RE.search(message):
        return "opt_out"
    if AUTO_REPLY_RE.search(message):
        return "auto_reply"
    if DEFER_RE.search(message) and not INTENT_RE.search(message):
        return "defer"
    if NEGATION_RE.search(message):
        return "rejection"
    if OFF_TOPIC_RE.search(message):
        return "off_topic"
    if INTENT_RE.search(message):
        return "intent"
    if QUESTION_RE.search(message):
        # "OK, let's do it. What's next?" is handled by explicit intent above.
        return "question"
    if ACCEPT_RE.search(message):
        return "intent"
    return "text"


class ReplyEngine:
    def __init__(self, contexts, convs, outreach):
        self.contexts, self.convs, self.outreach = contexts, convs, outreach

    def _wait(self, conv, now, seconds, reason):
        conv.state = WAITING
        self.outreach.cooldowns[(conv.merchant_id, conv.customer_id)] = now + timedelta(seconds=seconds)
        return {"action": "wait", "wait_seconds": seconds, "rationale": reason}

    def _send(self, conv, body, cta, rationale, category, snapshot):
        errors = validate_text(body, cta, category, conv.sent_bodies)
        if errors:
            return self._wait(conv, conv.clock, 3600, "Follow-up withheld: " + ", ".join(errors))
        conv.sent_bodies.append(body)
        conv.turns.append({"from": "merchant_on_behalf" if conv.customer_id else "vera", "body": body, "ts": conv.clock.isoformat()})
        self.outreach.audit.append({"conversation_id": conv.conversation_id, "trigger_id": conv.trigger_id,
                                   "prompt_version": composer.VERSION, "model": "deterministic",
                                   "suppression_key": snapshot[2].payload.get("suppression_key", ""),
                                   "validator_result": "passed", "body": body,
                                   "contexts": [{"scope": r.scope, "id": r.context_id, "version": r.version, "hash": r.hash, "stored_at": r.stored_at} for r in snapshot if r]})
        result = {"action": "send", "body": body, "cta": cta, "rationale": rationale}
        recipient = (conv.merchant_id, conv.customer_id)
        inbound = self.outreach.last_inbound.get(recipient)
        if not inbound or conv.clock - inbound >= timedelta(hours=24):
            result.update(template_name=get_policy(snapshot[2].payload["kind"]).template_name,
                          template_params=[body])
        self.outreach.last_outbound[recipient] = conv.clock
        return result

    def handle(self, req):
        started = time.monotonic()
        audit_count = len(self.outreach.audit)
        result = self._handle(req)
        if result.get("action") == "send" and len(self.outreach.audit) > audit_count:
            self.outreach.audit[-1]["latency_ms"] = (time.monotonic() - started) * 1000
        return result

    def _handle(self, req):
        conv = self.convs.get(req.conversation_id)
        if conv is None:
            raise HTTPException(404, detail="unknown_conversation")
        if ((req.merchant_id is not None and req.merchant_id != conv.merchant_id)
                or (req.customer_id is not None and req.customer_id != conv.customer_id)
                or req.from_role != conv.scope):
            raise HTTPException(400, detail="conversation_recipient_mismatch")
        recipient = (conv.merchant_id, conv.customer_id)
        if conv.state == ENDED or recipient in self.outreach.suppressed_recipients:
            return {"action": "end", "rationale": "Conversation ended or recipient opted out; no further outreach."}
        # Retries with a stable turn/timestamp are idempotent. A changed body on
        # the same numbered turn is invalid rather than a second side effect.
        cache_key = (req.turn_number,) if req.turn_number else ((req.received_at, req.message) if req.received_at else None)
        if cache_key in conv.reply_cache:
            old_message, response = conv.reply_cache[cache_key]
            if old_message != req.message:
                raise HTTPException(409, detail="conflicting_reply_turn")
            return response.copy()
        now = parse_time(req.received_at) if req.received_at else conv.clock
        if now < conv.clock or (req.turn_number and any(t.get("turn", 0) >= req.turn_number for t in conv.turns)):
            return {"action": "wait", "wait_seconds": 1, "rationale": "Stale reply ignored."}
        conv.clock, conv.updated_at = now, time.time()
        conv.turns.append({"from": req.from_role, "body": req.message, "ts": now.isoformat(), "turn": req.turn_number})
        result = self._respond(conv, req.message, now)
        if cache_key is not None:
            conv.reply_cache[cache_key] = (req.message, result.copy())
        return result

    def _respond(self, conv, message, now):
        recipient = (conv.merchant_id, conv.customer_id)
        kind = classify(message)
        if kind == "opt_out":
            conv.state, conv.suppressed = ENDED, True
            self.outreach.suppressed_recipients[recipient] = "opt_out"
            return {"action": "end", "rationale": "Recipient opted out; closing and suppressing this recipient's outreach."}
        fp = normalize(message)
        conv.fingerprint_counts[fp] = conv.fingerprint_counts.get(fp, 0) + 1
        repeated = len(fp) > 10 and conv.fingerprint_counts[fp] >= 3 and kind not in {"intent", "question"}
        if kind == "auto_reply" or repeated:
            conv.auto_reply_count += 1
            if conv.auto_reply_count >= 3:
                conv.state = ENDED
                self.outreach.cooldowns[recipient] = now + timedelta(hours=24)
                return {"action": "end", "rationale": "Repeated automated replies; closed without more nudges."}
            if conv.auto_reply_count >= 2 or conv.customer_id:
                return self._wait(conv, now, 86400, "Automated reply; waiting for a human response.")
        else:
            conv.auto_reply_count = 0
            self.outreach.last_inbound[recipient] = now
            self.outreach.unanswered[recipient] = 0
            self.outreach.cooldowns.pop(recipient, None)
        if kind == "defer":
            return self._wait(conv, now, 86400, "Recipient asked for time; outreach paused for a day.")
        if kind == "rejection":
            conv.state = ENDED
            return {"action": "end", "rationale": "Recipient declined the proposed action; conversation closed."}
        merchant_rec = self.contexts.get("merchant", conv.merchant_id)
        trigger_rec = self.contexts.get("trigger", conv.trigger_id)
        if not merchant_rec or not trigger_rec:
            return self._wait(conv, now, 3600, "Required context is missing.")
        merchant, trigger = merchant_rec.payload, trigger_rec.payload
        category_rec = self.contexts.get("category", merchant.get("category_slug"))
        customer_rec = self.contexts.get("customer", conv.customer_id) if conv.customer_id else None
        if not category_rec or trigger_ids(trigger) != recipient or trigger.get("scope") != conv.scope:
            return self._wait(conv, now, 3600, "Current contexts no longer match this conversation.")
        category = category_rec.payload
        customer = customer_rec.payload if customer_rec else None
        if conv.customer_id and (not customer or customer.get("merchant_id") != conv.merchant_id or not consent_allows(get_policy(trigger.get("kind")), customer, now)):
            conv.state = ENDED
            return {"action": "end", "rationale": "Current customer consent or ownership no longer permits this purpose."}
        snapshot = [category_rec, merchant_rec, trigger_rec, customer_rec]
        current = composer.compose(category, merchant, trigger, customer, now=now)
        if not current:
            return self._wait(conv, now, 3600, "Event expired or current facts do not support a follow-up.")
        if kind == "auto_reply" or repeated:
            conv.state = WAITING_FOR_REPLY
            self.outreach.cooldowns[recipient] = now + timedelta(hours=4)
            return self._send(conv, "This looks like an automatic reply. When the owner is available, reply YES to continue.", "binary_yes_no", "One owner-directed nudge; later auto-replies back off.", category, snapshot)
        if kind == "off_topic":
            return self._send(conv, "I can help with the business update here; that request needs the appropriate specialist. " + current.fact, "none", "Acknowledged unrelated request and returned to the current event.", category, snapshot)
        slots = composer.slot_labels(trigger.get("payload", {}), now)
        if conv.customer_id and message.strip() in {"1", "2"}:
            index = int(message.strip()) - 1
            if index >= len(slots):
                return self._wait(conv, now, 3600, "Selected slot is not in the current available options.")
            kind = "intent"
            current.next_step = f"Your requested time: {slots[index]}. The business still needs to confirm the booking."
        if kind == "intent":
            conv.intent_detected, conv.state = True, ACTION_PENDING
            if not conv.customer_id and re.search(r"\b(?:i want to join|join karna hai)\b", message, re.I):
                body = f'Onboarding request draft: "I would like to join magicpin with {composer._merchant_name(merchant)}." Submit this through the official merchant onboarding channel; registration has not been completed here.'
            else:
                body = current.next_step or "No executable action is defined for this question yet. Share the service you want the draft to describe."
            if composer.language(merchant, customer) == "hi":
                body = "Yeh raha agla step: " + body
            return self._send(conv, body, "none", "Explicit intent: supplied a concrete draft or request status without claiming external execution.", category, snapshot)
        conv.state = ENGAGED
        if trigger.get("kind") in {"curious_ask_due", "scheduled_recurring"} and kind == "text":
            body = f'Draft for review: "Asked about {message.strip()}? Contact {composer._merchant_name(merchant)} for details."'
        elif re.search(r"\b(price|cost|kitna|fee|amount)\b", message, re.I):
            if trigger.get("kind") == "renewal_due":
                body = current.fact
            elif conv.customer_id:
                body = "A confirmed price for this request is not available in the supplied booking details. The business needs to confirm it."
            else:
                offers = composer._active_offers(merchant)
                body = "Current listed offers: " + "; ".join(o["title"] for o in offers) + "." if offers else "No active offer price is supplied for this business."
        elif kind == "question":
            body = "Here are the available details: " + current.fact
        else:
            return self._wait(conv, now, 1800, "No clear action requested; allowing time instead of repeating the pitch.")
        return self._send(conv, body, "none", "Answered using current event or merchant facts.", category, snapshot)
