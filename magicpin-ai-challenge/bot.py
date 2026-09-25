"""Vera AI Assistant — FastAPI entrypoint.

Run:  uvicorn bot:app --host 0.0.0.0 --port 8080
"""
import re
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app import composer, conversation
from app.metadata import get_metadata
from app.models import ContextPush, ReplyRequest, TickRequest
from app.routing import consent_allows, get_policy
from app.stores import ContextStore, ConversationStore, OutreachState

START = time.time()

app = FastAPI(title="Vera AI Assistant", version="1.0.0")


@app.exception_handler(RequestValidationError)
async def validation_handler(request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    reason = "invalid_request"
    field = ".".join(str(p) for p in first.get("loc", []) if p != "body")
    if field == "scope":
        reason = "invalid_scope"
    elif field == "version":
        reason = "invalid_version"
    return JSONResponse(status_code=400, content={
        "accepted": False,
        "reason": reason,
        "details": f"{field}: {first.get('msg', 'malformed request')}",
    })

contexts = ContextStore()
convs = ConversationStore()
outreach = OutreachState()
reply_engine = conversation.ReplyEngine(contexts, convs, outreach)

MAX_ACTIONS_PER_TICK = 10
MAX_BODY_CHARS = 900
URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)


# --------------------------------------------------------------- utilities --

def _parse_iso(ts: str) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _guardrails(composed: composer.Composed, trigger: dict) -> composer.Composed:
    """Post-composition validation: URLs, length, suppression-key equality."""
    body = URL_RE.sub("", composed.body).strip()
    if len(body) > MAX_BODY_CHARS:
        body = body[: MAX_BODY_CHARS - 3].rstrip() + "..."
    composed.body = body
    composed.suppression_key = trigger.get("suppression_key", composed.suppression_key)
    return composed


def _action_from_composed(conv_id: str, composed: composer.Composed,
                          merchant_id: str, customer_id: Optional[str],
                          trigger_id: str) -> dict:
    return {
        "conversation_id": conv_id,
        "merchant_id": merchant_id,
        "customer_id": customer_id,
        "send_as": composed.send_as,
        "trigger_id": trigger_id,
        "template_name": composed.template_name,
        "template_params": composed.template_params,
        "body": composed.body,
        "cta": composed.cta,
        "suppression_key": composed.suppression_key,
        "rationale": composed.rationale,
    }


# ---------------------------------------------------------------- endpoints --

@app.get("/v1/healthz")
async def healthz():
    return {
        "status": "ok",
        "uptime_seconds": int(time.time() - START),
        "contexts_loaded": contexts.counts(),
    }


@app.get("/v1/metadata")
async def metadata():
    return get_metadata()


@app.post("/v1/context")
async def push_context(body: ContextPush):
    record, stored = contexts.put(body.scope, body.context_id, body.version, body.payload)
    if not stored:
        raise HTTPException(status_code=409, detail={
            "accepted": False,
            "reason": "stale_version",
            "current_version": record.version,
        })
    return {
        "accepted": True,
        "ack_id": f"ack_{body.context_id}_v{body.version}",
        "stored_at": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/v1/tick")
async def tick(body: TickRequest):
    now = _parse_iso(body.now) or datetime.now(timezone.utc)
    actions = []
    seen_merchants = set()

    for tid in body.available_triggers:
        if len(actions) >= MAX_ACTIONS_PER_TICK:
            break
        trg_rec = contexts.get("trigger", tid)
        if trg_rec is None:
            continue
        trg = trg_rec.payload

        # Expired triggers are dead.
        expires = _parse_iso(trg.get("expires_at", ""))
        if expires and now > expires:
            continue

        merchant_id = trg.get("merchant_id", "")
        if not merchant_id or merchant_id in seen_merchants:
            continue  # one action per merchant per tick
        if merchant_id in outreach.suppressed_merchants:
            continue

        suppression_key = trg.get("suppression_key", "")
        if suppression_key and suppression_key in outreach.sent_suppression_keys:
            continue  # already messaged for this event

        merchant_rec = contexts.get("merchant", merchant_id)
        if merchant_rec is None:
            continue
        merchant = merchant_rec.payload

        category_rec = contexts.get("category", merchant.get("category_slug", ""))
        category = category_rec.payload if category_rec else {}

        customer = None
        customer_id = trg.get("customer_id")
        if trg.get("scope") == "customer":
            cust_rec = contexts.get("customer", customer_id or "")
            if cust_rec is None:
                continue  # never invent customer context
            customer = cust_rec.payload
            policy = get_policy(trg.get("kind", ""))
            if not consent_allows(policy, customer):
                continue  # consent does not cover this outreach

        composed = composer.compose(category, merchant, trg, customer)
        if composed is None:
            continue
        composed = _guardrails(composed, trg)
        if not composed.body:
            continue

        conv_id = f"conv_{merchant_id}_{trg.get('kind', 'evt')}_{abs(hash(suppression_key or tid)) % 10000}"
        conv = convs.create(
            conversation_id=conv_id,
            merchant_id=merchant_id,
            customer_id=customer_id,
            trigger_id=tid,
            scope=trg.get("scope", "merchant"),
            route=trg.get("kind", "unknown"),
        )
        conv.sent_bodies.append(composed.body)
        conv.pending_action = {"topic": trg.get("kind", "").replace("_", " "),
                               "merchant_name": merchant.get("identity", {}).get("name", "")}
        conv.state = "INITIATED"

        actions.append(_action_from_composed(conv_id, composed, merchant_id, customer_id, tid))
        seen_merchants.add(merchant_id)
        if suppression_key:
            outreach.sent_suppression_keys.add(suppression_key)

    return {"actions": actions}


@app.post("/v1/reply")
async def reply(body: ReplyRequest):
    return reply_engine.handle(body)


@app.post("/v1/teardown")
async def teardown():
    """Judge may call this at end of test; wipe all retained state."""
    contexts.wipe()
    convs.wipe()
    outreach.wipe()
    return {"wiped": True}
