"""Vera challenge API. Run with one worker: uvicorn bot:app --port 8080."""
import asyncio
import time
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app import composer, conversation, llm
from app.guardrails import validate
from app.metadata import get_metadata
from app.models import ContextPush, ReplyRequest, TickRequest
from app.security import require_api_token
from app.routing import trigger_ids
from app.stores import ContextStore, ConversationStore, OutreachState, canonical_hash
from app.timeutils import parse_time

START = time.monotonic()
app = FastAPI(title="Vera AI Assistant", version="2.0.0")
contexts = ContextStore()
convs = ConversationStore()
outreach = OutreachState()
reply_engine = conversation.ReplyEngine(contexts, convs, outreach)
MAX_ACTIONS_PER_TICK = 20
tick_lock = asyncio.Lock()


@app.exception_handler(RequestValidationError)
async def validation_handler(request, exc):
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(p) for p in first.get("loc", []) if p != "body")
    reason = {"scope": "invalid_scope", "version": "invalid_version"}.get(field, "invalid_request")
    return JSONResponse(status_code=400, content={"accepted": False, "reason": reason,
                                                  "details": f"{field}: {first.get('msg', 'malformed request')}"})


@app.middleware("http")
async def context_size_limit(request: Request, call_next):
    if request.url.path == "/v1/context" and request.method == "POST":
        # FastAPI's middleware caches the body for the downstream parser.
        length = request.headers.get("content-length", "")
        if (length.isdigit() and int(length) > 500 * 1024) or len(await request.body()) > 500 * 1024:
            return JSONResponse(status_code=413, content={"accepted": False, "reason": "payload_too_large"})
    return await call_next(request)


def compose(category, merchant, trigger, customer=None):
    """Stateless submission interface. Empty body means no authorized, grounded send."""
    result = composer.compose(category, merchant, trigger, customer)
    if result:
        return result.public()
    return {"body": "", "cta": "none", "send_as": "merchant_on_behalf" if trigger.get("scope") == "customer" else "vera",
            "suppression_key": trigger.get("suppression_key", ""),
            "rationale": "Withheld: missing or invalid context, insufficient event facts, or consent does not cover this purpose."}


@app.get("/v1/healthz")
async def healthz():
    return {"status": "ok", "uptime_seconds": int(time.monotonic() - START), "contexts_loaded": contexts.counts()}


@app.get("/")
async def index():
    return {"service": "Vera AI Assistant", "docs": "/docs", "health": "/v1/healthz"}


@app.get("/v1/metadata")
async def metadata():
    return get_metadata()


@app.post("/v1/context", dependencies=[Depends(require_api_token)])
async def push_context(body: ContextPush):
    record, stored = contexts.put(body.scope, body.context_id, body.version, body.payload)
    if not stored:
        return JSONResponse(status_code=409, content={"accepted": False, "reason": "stale_version", "current_version": record.version})
    return {"accepted": True, "ack_id": f"ack_{body.context_id}_v{body.version}",
            "stored_at": datetime.fromtimestamp(record.stored_at, timezone.utc).isoformat()}


def _history_session(merchant, recipient, now):
    if recipient[1] is not None:
        return
    for turn in merchant.get("conversation_history", []):
        if turn.get("from") != "merchant":
            continue
        try:
            ts = parse_time(turn.get("ts"))
        except (TypeError, ValueError):
            continue
        if ts <= now and (recipient not in outreach.last_inbound or ts > outreach.last_inbound[recipient]):
            outreach.last_inbound[recipient] = ts
        if ts <= now and conversation.classify(turn.get("body", "")) == "opt_out":
            outreach.suppressed_recipients[recipient] = "historical_opt_out"


@app.post("/v1/tick", dependencies=[Depends(require_api_token)])
async def tick(body: TickRequest):
    # The model call yields the event loop so health/context/replies stay live.
    # Serialize tick commits to prevent two overlapping sends for one event.
    deadline = time.monotonic() + 24
    try:
        async with asyncio.timeout(27):
            async with tick_lock:
                return await _tick(body, deadline)
    except TimeoutError:
        return {"actions": []}


async def _tick(body, deadline):
    now = parse_time(body.now)
    actions, seen_recipients = [], set()
    records = [contexts.get("trigger", tid) for tid in dict.fromkeys(body.available_triggers)]
    records = sorted((r for r in records if r), key=lambda r: (-r.payload.get("urgency", 1), r.context_id))
    for record in records:
        if len(actions) >= MAX_ACTIONS_PER_TICK:
            break
        trigger, tid = record.payload, record.context_id
        mid, cid = trigger_ids(trigger)
        recipient = (mid, cid)
        if recipient in seen_recipients or recipient in outreach.suppressed_recipients:
            continue
        if recipient in outreach.cooldowns and now < outreach.cooldowns[recipient]:
            continue
        if recipient in outreach.last_outbound and now < outreach.last_outbound[recipient]:
            continue
        dedup = (mid, cid, trigger.get("suppression_key") or tid)
        if dedup in outreach.sent_suppression_keys:
            continue
        merchant_record = contexts.get("merchant", mid)
        if not merchant_record:
            continue
        merchant = merchant_record.payload
        category_record = contexts.get("category", merchant.get("category_slug"))
        customer_record = contexts.get("customer", cid) if cid else None
        if not category_record or (trigger["scope"] == "customer" and not customer_record):
            continue
        category = category_record.payload
        customer = customer_record.payload if customer_record else None
        _history_session(merchant, recipient, now)
        if recipient in outreach.suppressed_recipients:
            continue
        started = time.monotonic()
        result = composer.compose(category, merchant, trigger, customer, now=now)
        if not result:
            continue
        snapshot = [r for r in (category_record, merchant_record, record, customer_record) if r]
        result, generation_status = await llm.refine(result, category, merchant, trigger, customer,
                                                      budget=deadline - time.monotonic())
        # Contexts, consent and opt-out can change while awaiting the provider.
        if any(not (latest := contexts.get(r.scope, r.context_id)) or latest.hash != r.hash or latest.version != r.version for r in snapshot):
            continue
        if recipient in outreach.suppressed_recipients or dedup in outreach.sent_suppression_keys:
            continue
        if (recipient in outreach.cooldowns and now < outreach.cooldowns[recipient]) or (recipient in outreach.last_outbound and now < outreach.last_outbound[recipient]):
            continue
        _history_session(merchant, recipient, now)
        inbound = outreach.last_inbound.get(recipient)
        if inbound and timedelta(0) <= now - inbound < timedelta(hours=24):
            result.template_name, result.template_params = None, []
        history = [turn.get("body") for turn in merchant.get("conversation_history", []) if turn.get("from") == "vera"] if not cid else []
        if validate(result, category, merchant, trigger, customer, history):
            continue
        conv_id = "conv_" + canonical_hash([mid, cid, tid, record.version, dedup])[:32]
        if convs.get(conv_id):
            continue
        conv = convs.create(conversation_id=conv_id, merchant_id=mid, customer_id=cid,
                            trigger_id=tid, scope=trigger["scope"], route=trigger["kind"], clock=now)
        conv.sent_bodies.append(result.body)
        conv.turns.append({"from": result.send_as, "body": result.body, "ts": body.now})
        conv.pending_action = {"topic": trigger["kind"], "next_step": result.next_step}
        actions.append({"conversation_id": conv_id, "merchant_id": mid, "customer_id": cid,
                        "trigger_id": tid, "template_name": result.template_name,
                        "template_params": result.template_params, **result.public()})
        seen_recipients.add(recipient)
        outreach.sent_suppression_keys.add(dedup)
        outreach.last_outbound[recipient] = now
        count = outreach.unanswered.get(recipient, 0) + 1
        outreach.unanswered[recipient] = count
        outreach.cooldowns[recipient] = now + (timedelta(hours=24) if count >= 3 else timedelta(minutes=5))
        if count >= 3:
            outreach.unanswered[recipient] = 0
        outreach.audit.append({"conversation_id": conv_id, "trigger_id": tid,
                               "suppression_key": result.suppression_key, "prompt_version": llm.PROMPT_VERSION,
                               "model": llm.model_name() if generation_status.startswith("groq_") else "deterministic",
                               "generation_status": generation_status, "latency_ms": (time.monotonic() - started) * 1000,
                               "validator_result": "passed", "body": result.body,
                               "contexts": [{"scope": r.scope, "id": r.context_id, "version": r.version,
                                             "hash": r.hash, "stored_at": r.stored_at} for r in snapshot]})
    return {"actions": actions}


@app.post("/v1/reply", dependencies=[Depends(require_api_token)])
async def reply(body: ReplyRequest):
    return reply_engine.handle(body)


@app.post("/v1/teardown", dependencies=[Depends(require_api_token)])
async def teardown():
    contexts.wipe()
    convs.wipe()
    outreach.wipe()
    return {"wiped": True}
