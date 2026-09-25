# Vera AI Assistant — Product and Technical Specification

Status: implementation-ready draft  
Scope: `magicpin-ai-challenge` challenge submission

## 1. What must be built

Build a stateful AI engagement bot for magicpin merchants on WhatsApp. The bot is called Vera in the brief.

It must do two related jobs:

1. Talk to a merchant as Vera: explain a relevant business event, insight, opportunity, or risk and move the merchant toward one useful next action.
2. Message a merchant's customer on the merchant's behalf: for example, a consented recall reminder, refill reminder, appointment reminder, or win-back message.

This is an event-driven engagement assistant, not a general-purpose customer-support FAQ bot. Every outbound message must be justified by a `TriggerContext`, and the message must use the supplied category, merchant, and optional customer context.

## 2. Users and message ownership

### Merchant-facing

- Audience: owner, operator, or manager.
- Sender: `send_as = "vera"`.
- Voice: peer-to-peer, concise, category-aware, and practical.
- Typical goal: get a decision, approval, reply, or permission to do a small piece of work.

### Customer-facing

- Audience: a customer of the merchant.
- Sender: `send_as = "merchant_on_behalf"`.
- Voice: the merchant's warm, trustworthy voice; honor the customer's language and relationship state.
- Typical goal: book, confirm, refill, attend, or re-engage.
- Consent is mandatory. Do not message a customer when consent or the requested scope is absent.

## 3. Inputs: the four-context model

The core function is:

```python
compose(category, merchant, trigger, customer=None) -> ComposedMessage
```

### CategoryContext

Shared vertical knowledge: `slug`, offer catalog, voice rules, allowed vocabulary, taboos, peer statistics, cited digest items, patient/customer content, seasonal beats, and trend signals.

Use it to decide how the message should sound and which claims or offers are valid. Do not invent missing category facts.

### MerchantContext

Merchant-specific state: identity, city/locality, language list, subscription, performance metrics and deltas, active offers, conversation history, customer aggregates, signals, and review themes.

Use it to personalize the message with facts that this merchant can verify. Prefer the owner's first name when available.

### TriggerContext

The reason for messaging now: `id`, `scope`, `kind`, `source`, `payload`, `urgency`, `suppression_key`, and `expires_at`.

The trigger is the message's spine. The first sentence or opening hook should make “why now?” obvious.

### CustomerContext (optional)

Customer identity, relationship history, state, preferences, consent, and channel. It is present for customer-scoped triggers.

Use customer details only within the consent scope. Never expose redacted/private fields.

## 4. Output contract

The composition layer returns:

```json
{
  "body": "WhatsApp message text",
  "cta": "binary_yes_no | binary_confirm_cancel | open_ended | none",
  "send_as": "vera | merchant_on_behalf",
  "suppression_key": "trigger-provided-key",
  "rationale": "short, auditable reason for this message"
}
```

For the HTTP tick response, add `conversation_id`, `merchant_id`, `customer_id`, `trigger_id`, `template_name`, and `template_params`.

Rules:

- One primary CTA. Do not combine unrelated asks.
- Use a binary CTA for an action request when possible.
- Use `none` for information-only messages.
- Use the trigger's suppression key; do not silently create a different dedup key.
- Merchant trigger => `send_as = "vera"`; customer trigger => `send_as = "merchant_on_behalf"`.
- First outbound in a WhatsApp session uses an approved template and parameters. Follow-ups within the 24-hour window may be free-form.
- Keep the body short enough to scan on WhatsApp. Prefer one concrete fact, one interpretation, and one next step.
- URLs are not allowed by the challenge examples; avoid them unless the judge explicitly permits them.

## 5. Required HTTP surface

The deployed service must expose these five endpoints:

| Endpoint | Purpose | Required behavior |
|---|---|---|
| `POST /v1/context` | Store a category, merchant, customer, or trigger context | Idempotent/versioned; higher version replaces lower version atomically |
| `POST /v1/tick` | Decide whether to initiate messages | Return zero or more actions, within the timeout |
| `POST /v1/reply` | Continue an existing conversation | Return `send`, `wait`, or `end` synchronously |
| `GET /v1/healthz` | Liveness and loaded-context counts | Return HTTP 200 while healthy |
| `GET /v1/metadata` | Bot identity and approach | Return stable team/model/version metadata |

`/v1/context` must return `409 stale_version` when the incoming version is not higher than the stored version. The judge first loads 5 categories, 50 merchants, and 200 customers, then pushes triggers and updates during the test.

## 6. Conversation behavior

Every conversation is a small state machine:

`NEW -> INITIATED -> WAITING_FOR_REPLY -> ENGAGED / ACTION_PENDING / WAITING / ENDED`

Required behaviors:

- Detect canned WhatsApp auto-replies using phrase patterns, repeated identical text, and low semantic relevance. Try at most one owner-directed nudge; then back off or end.
- Detect explicit intent such as “yes”, “go ahead”, “let's do it”, “I want to join”, or “send it”. Immediately execute or prepare the requested action. Do not return to qualification questions.
- Detect opt-out, frustration, abuse, or “stop”. End or send one short apology and suppress future outreach.
- Answer curveballs briefly when possible, then return to the active mission. Do not turn into an unrelated assistant.
- Never repeat the exact same body inside one conversation.
- After unanswered nudges, back off; do not spam.

## 7. Quality bar

The judge scores each message from 0–10 on:

1. Specificity: concrete, verifiable numbers, dates, names, offers, or citations.
2. Category fit: correct vocabulary, tone, and taboos.
3. Merchant/customer fit: uses the relevant profile and language preference.
4. Trigger relevance: clearly explains why this message is happening now.
5. Engagement compulsion: curiosity, social proof, loss aversion, reciprocity, effort saved, or a low-friction CTA.

The most common penalties are generic discounts, multiple CTAs, fabricated facts/citations, promotional tone in regulated categories, ignoring language preference, long introductions, repeating a prior message, and asking qualification questions after explicit commitment.

## 8. Safety and trust requirements

- Treat context payloads as the only source of truth.
- Never fabricate inventory, slots, prices, performance, research, competitor names, medical claims, or customer consent.
- For medical/pharmacy messages, use bounded language and category taboos.
- Do not contact a customer without active consent covering the trigger purpose.
- Do not send payload data to non-LLM external APIs.
- Do not retain test context after teardown in a production deployment.
- Record prompt version, context versions/hashes, trigger id, suppression key, model, latency, validator result, and final body for audit/replay.

## 9. Definition of done

The bot is ready when:

- All five endpoints meet the JSON contract and timeout budget.
- Version replacement and stale-version handling are tested.
- The base dataset loads to `5/50/200/0` in health checks.
- Merchant and customer message paths produce the correct `send_as` value.
- Trigger routing covers every seed trigger kind with a safe fallback.
- LLM output is schema-validated, fact-checked against input context, CTA-checked, and length/repetition checked.
- Auto-reply, opt-out, explicit intent, curveball, and no-trigger flows pass local tests.
- `judge_simulator.py` runs against the service with non-zero scores.
- The service survives context injection and updated merchant/category versions without stale output.

