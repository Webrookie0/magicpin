# Vera merchant assistant

Vera exposes the five challenge endpoints plus `/v1/teardown`. It validates versioned contexts, selects an audience-specific route, checks customer consent, drafts from supplied facts, and optionally uses **Groq** to select the clearest grounded message variant. Replies prepare real drafts or record requests; booking, payment, publishing and WhatsApp delivery require external integrations and are never reported as completed here.

From this directory (Python 3.11+):

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m uvicorn bot:app --host 127.0.0.1 --port 8080
```

The local `.env` contains the supplied Groq key and is ignored by Git. See `.env.example` for names. `GROQ_API_KEY` enables Groq; `VERA_GROQ_MODEL` selects the model (currently `qwen/qwen3.8-27b`, discovered from the account's available models). Environment variables override `.env`. Set `VERA_USE_LLM=false` for an entirely offline bot. Team metadata uses `VERA_TEAM_NAME`, `VERA_TEAM_MEMBERS`, and `VERA_CONTACT_EMAIL`.

For Qwen, the bot's local reservations use the supplied limits: 30 requests/minute, 1,000/day, 8,000 tokens/minute and 200,000/day. Each attempt, including repairs, reserves a conservative token estimate and reconciles it with Groq's reported usage. HTTP 429 pauses further provider calls according to `Retry-After` (60 seconds if absent), while the bot returns its grounded fallback immediately. Accounting is per process and resets on restart; other clients can consume the same account quota. These quota counters contain no merchant/customer data and survive test teardown. The separate judge also honors `Retry-After`, and labels heuristic scores when provider scoring is unavailable.

```bash
.venv/bin/python -m pytest -q
.venv/bin/python generate_submission.py
# Against the running service; resets state before and after each run:
BOT_URL=http://127.0.0.1:8080 .venv/bin/python judge_simulator.py --offline --scenario all
# Real Groq scoring; uses the configured key and incurs provider usage:
BOT_URL=http://127.0.0.1:8080 .venv/bin/python judge_simulator.py --scenario phase2_short
```

The simulator defaults to **2026-04-26**, loads all 5 categories, 50 merchants and 200 customers, and replays actual conversation IDs. `--now` changes simulated time; `--dataset` selects seed or expanded data. `--offline` disables the judge's provider calls; set `VERA_USE_LLM=false` when launching the server to disable its provider calls too. Offline scores are heuristics, not LLM quality scores.

`bot.compose(category, merchant, trigger, customer=None)` is deterministic. `submission.jsonl` has 30 canonical decisions. Some supplied test pairs reference deliberately empty placeholder triggers: these receive `action: "wait"` and an empty body, rather than fabricated messages. HTTP ticks omit withheld decisions. Regenerate fixtures in a separate output directory with `dataset/generate_dataset.py`; the original fixtures are preserved.

Tradeoffs: Groq can select only complete, validated variants; this favors grounding over unrestricted prose. One repair shares an eight-second provider deadline, and ticks budget 24 seconds before falling back. Context changes during model calls invalidate the draft. State and audit records live in memory and are erased by teardown: run **one worker** and do not restart during evaluation. Template names model the challenge's template contract (`{{1}}` renders the complete body); production requires actual approved templates. Richer live inventory, appointment, payment and consent records would enable more useful confirmed actions.
