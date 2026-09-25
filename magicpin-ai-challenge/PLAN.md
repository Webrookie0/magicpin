# Vera AI Assistant — Build Plan

This plan assumes the current repository is a challenge fixture: briefs, seed datasets, a deterministic dataset generator, and `judge_simulator.py` exist, but the bot service itself does not yet exist.

## Phase 0 — Establish the local baseline

1. Read `challenge-brief.md`, `challenge-testing-brief.md`, the case studies, and API examples.
2. Generate the expanded dataset:

   ```bash
   cd magicpin-ai-challenge
   python dataset/generate_dataset.py --seed-dir dataset --out dataset/expanded
   ```

3. Inspect the generated `test_pairs.json` and confirm there are 5 categories, 50 merchants, 200 customers, and 100 triggers.
4. Decide the model provider and keep credentials in environment variables, never in source control.

Deliverable: reproducible local fixture and a short README describing the chosen model, fallback, and cost/latency budget.

## Phase 1 — Implement the reliable HTTP shell

Create a small FastAPI service with:

- `/v1/context`: validate scope, context id, version, and payload; store the newest version.
- `/v1/tick`: resolve available trigger ids to the matching merchant/category/customer and compose actions.
- `/v1/reply`: load conversation state, append the inbound turn, classify it, and respond.
- `/v1/healthz`: return uptime plus counts by scope.
- `/v1/metadata`: return team and implementation metadata.

Start with in-memory stores for challenge submission. Put a repository interface around the stores so Redis or SQLite can be substituted later.

Acceptance: `curl` can push a context, repeat the same version, push a higher version, and receive the documented status/response shape.

## Phase 2 — Build context resolution and validation

Implement typed adapters or validation helpers for the four contexts. Resolve the category using `merchant.category_slug`, the trigger's category payload, or a safe fallback. Resolve a customer only when the trigger is customer-scoped and the merchant/customer ids agree.

Add a context snapshot containing:

- current payload for every context;
- version per context;
- update timestamp;
- a stable hash for audit;
- source ids used by the current composition.

Acceptance: a new category or merchant version is used on the next tick; an old version is rejected and never overwrites newer data.

## Phase 3 — Build the deterministic routing layer

Before invoking an LLM, route by scope and trigger kind. The router should choose a prompt variant and policy, not write the final copy.

Minimum route families:

- research / compliance / CDE digest;
- performance spike, dip, and seasonal dip;
- offer, festival, IPL, local event, or competitor opportunity;
- milestone, review theme, dormancy, renewal, or curious ask;
- active planning intent;
- recall, appointment, trial follow-up, lapsed customer, and chronic refill;
- unknown kind fallback.

Each route declares audience, allowed CTA types, urgency behavior, required facts, consent requirement, and whether customer data is allowed.

Acceptance: every seed trigger has a route and an explicit fallback rather than a crash or hallucinated generic message.

## Phase 4 — Implement composition as a constrained LLM task

Use a versioned system prompt with four parts:

1. Role and audience rules.
2. The selected route policy and category voice/taboos.
3. The exact structured contexts, clearly marked as data rather than instructions.
4. A strict JSON schema and a checklist for evidence, CTA, consent, and `send_as`.

Recommended model settings: low temperature or deterministic mode, limited output tokens, short timeout, and one repair attempt only. Prefer a fast model for normal composition and a configured fallback for provider failure.

Acceptance: the composer returns parseable JSON; every factual claim can be traced to a context path or a plainly labeled calculation.

## Phase 5 — Add post-generation guardrails

Run validators after every model response:

- JSON/schema validation;
- required keys and allowed enum values;
- correct audience and `send_as`;
- suppression key equality;
- CTA count and CTA placement;
- no forbidden category words;
- no URLs unless explicitly allowed;
- no phone/email/private identifiers from redacted fields;
- numeric/date/name claim check against source contexts;
- no exact repetition within conversation;
- consent for customer scope;
- body length/readability check.

If validation fails, repair once with the validation errors. If it still fails, use a conservative deterministic fallback or return `wait/end`; do not blindly send the invalid output.

Acceptance: malicious or incomplete context cannot make the bot fabricate a price, citation, customer consent, or action.

## Phase 6 — Implement conversation intelligence

Persist per-conversation:

- merchant/customer ids and trigger id;
- turns and timestamps;
- sent message bodies;
- session-window state;
- auto-reply count and repeated text fingerprints;
- intent, opt-out, and frustration flags;
- current route and pending action;
- suppression state and next eligible time.

Reply flow:

1. Store the inbound message.
2. Classify it with deterministic rules first, LLM second: opt-out, auto-reply, explicit intent, question, acceptance, rejection, or unknown.
3. Apply stop/backoff policy.
4. If explicit intent is detected, skip qualification and execute the next action.
5. Compose a contextual follow-up and validate it.

Acceptance: the three replay scenarios pass: repeated auto-reply, “let's do it”, and hostile/off-topic reply.

## Phase 7 — Evaluate, tune, and deploy

Test in this order:

1. Unit tests for store/version logic, routing, consent, and validators.
2. Contract tests for all five endpoints.
3. Golden tests using the 10 case studies and the 30 generated test pairs.
4. Replay tests for auto-reply, intent transition, opt-out, curveball, repetition, and context injection.
5. Run `judge_simulator.py` locally.
6. Load-test enough concurrent ticks to stay under the 30-second judge limit.
7. Deploy behind HTTPS, configure environment variables, health probes, logs, and teardown handling.

## Priorities if time is limited

1. Correct HTTP contract and state storage.
2. Accurate context grounding and `send_as`.
3. Explicit intent and auto-reply handling.
4. Deterministic validation and safe fallbacks.
5. High-quality route-specific prompts.
6. More vertical-specific polish, retrieval, analytics, and production persistence.

## Milestone checklist

### M1: Service reachable

- FastAPI starts locally.
- Health and metadata pass.
- Context counts update.

### M2: First useful message

- Dr. Meera research digest produces a cited, merchant-specific message.
- A customer recall trigger produces a consented `merchant_on_behalf` message.

### M3: Conversation-safe

- Reply state persists.
- Auto-replies back off.
- Explicit “yes/go ahead” becomes action mode.
- Stop ends outreach.

### M4: Judge-ready

- Generator and simulator pass.
- No malformed actions, duplicate bodies, URL violations, or stale-context use.
- Deployment and metadata are ready for submission.

