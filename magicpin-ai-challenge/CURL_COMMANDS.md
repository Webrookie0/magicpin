# Vera Assistant API — Live Testing cURL Suite

This guide contains the cURL commands to test the Vera Bot end-to-end, including
Verdent-managed sign-in (Supabase Auth) and the new `/v1/whoami` + `/v1/conversations`
endpoints.

---

## 1. Quick Automated Test Script

An executable script has been generated at [`test_bot.sh`](file:///Users/sumit/developer/magicpin/magicpin-ai-challenge/test_bot.sh).

```bash
# Test local server (http://127.0.0.1:8080)
./test_bot.sh

# Test deployed server (Render)
BASE_URL="https://vera-merchant-assistant-zres.onrender.com" ./test_bot.sh
```

---

## 2. Individual cURL Commands (Copy-Pasteable)

Set your base URL:
```bash
export BASE_URL="https://vera-merchant-assistant-zres.onrender.com"
# or export BASE_URL="http://127.0.0.1:8080"
```

### Step 1: Health & Metadata Check (no auth needed)
```bash
# Health Check — shows database + auth status
curl -s "$BASE_URL/v1/healthz" | jq

# Bot / Team Metadata
curl -s "$BASE_URL/v1/metadata" | jq
```

---

### Step 2: Sign in (two ways)

All `/v1/*` POST endpoints and the new GET endpoints require a Bearer token.
You can use **either** the shared API token **or** a Verdent-managed Supabase
sign-in token.

#### 2.1 Shared API token (judge / service path)
```bash
export TOKEN="<your VERA_API_TOKEN>"
curl -s "$BASE_URL/v1/whoami" -H "Authorization: Bearer $TOKEN" | jq
# -> { "auth_kind": "api_token", ... }
```

#### 2.2 Verdent-managed sign-in (Supabase Auth, email + password)

Sign up a new user (one time):
```bash
SUPA_URL="https://supabase-api-prod.verdent.ai/p/p2ca0dd42956a4535ddd9"
SUPA_KEY="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhdWQiOiJhdXRoZW50aWNhdGVkIiwiZXhwIjoyMTA2MDM0MTA0LCJpYXQiOjE3OTA0MTQ5MDQsImlzcyI6InN1cGFiYXNlIiwicHJvamVjdF9yZWYiOiJwMmNhMGRkNDI5NTZhNDUzNWRkZDkiLCJyb2xlIjoiYW5vbiJ9.bUmf1kR9B3y9eQgMk94ZBlXNEvoU6LBLr5-bqQkadY0"

curl -s -X POST "$SUPA_URL/auth/v1/signup" \
  -H "apikey: $SUPA_KEY" \
  -H "Authorization: Bearer $SUPA_KEY" \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"Your-Strong-Password-1!"}' | jq '{id, email}'
```

Sign in (password grant) and store the access token:
```bash
export TOKEN=$(curl -s -X POST "$SUPA_URL/auth/v1/token?grant_type=password" \
  -H "apikey: $SUPA_KEY" \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"Your-Strong-Password-1!"}' | jq -r .access_token)

echo "$TOKEN"   # JWT access token
```

> Note: new Supabase users may need email confirmation before the password
> grant succeeds ("Email not confirmed" error). Ask the workspace admin to
> confirm the user, or use an already-confirmed user.

Check your identity on the API:
```bash
curl -s "$BASE_URL/v1/whoami" -H "Authorization: Bearer $TOKEN" | jq
# -> { "auth_kind": "supabase", "user_id": "...", "email": "you@example.com", ... }
```

---

### Step 3: Push Contexts (`POST /v1/context`)

#### 3.1 Push Category Context
```bash
curl -s -X POST "$BASE_URL/v1/context" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "scope": "category",
    "context_id": "dentists",
    "version": 1,
    "payload": {
      "slug": "dentists",
      "voice": { "tone": "peer_clinical", "vocab_taboo": ["guaranteed", "100% safe"] },
      "offer_catalog": [
        { "id": "den_001", "title": "Dental Cleaning @ ₹299", "value": "299", "audience": "new_user", "type": "service_at_price" }
      ],
      "peer_stats": { "avg_rating": 4.4, "avg_ctr": 0.030 },
      "digest": [{ "id": "d_2026W17_jida_fluoride", "kind": "research", "title": "3-month fluoride recall cuts caries 38% better", "source": "JIDA Oct 2026, p.14" }],
      "patient_content_library": [],
      "seasonal_beats": [{ "month_range": "Nov-Feb", "note": "exam-stress bruxism spike" }],
      "trend_signals": [{ "query": "clear aligners delhi", "delta_yoy": 0.62 }]
    }
  }' | jq
```

#### 3.2 Push Merchant Context
```bash
curl -s -X POST "$BASE_URL/v1/context" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "scope": "merchant",
    "context_id": "m_001_drmeera_dentist_delhi",
    "version": 1,
    "payload": {
      "merchant_id": "m_001_drmeera_dentist_delhi",
      "category_slug": "dentists",
      "identity": { "name": "Dr. Meera'\''s Dental Clinic", "city": "Delhi", "locality": "Lajpat Nagar", "languages": ["en", "hi"], "owner_first_name": "Meera" },
      "subscription": { "status": "active", "plan": "Pro", "days_remaining": 82 },
      "performance": { "window_days": 30, "views": 2410, "calls": 18, "directions": 45, "ctr": 0.021 },
      "offers": [{ "id": "o_meera_001", "title": "Dental Cleaning @ ₹299", "status": "active" }],
      "conversation_history": [],
      "customer_aggregate": { "total_unique_ytd": 540, "lapsed_180d_plus": 78, "retention_6mo_pct": 0.38, "high_risk_adult_count": 124 },
      "signals": ["stale_posts:22d", "ctr_below_peer_median", "high_risk_adult_cohort"]
    }
  }' | jq
```

#### 3.3 Push Trigger Context
```bash
curl -s -X POST "$BASE_URL/v1/context" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "scope": "trigger",
    "context_id": "trg_001_research_digest_dentists",
    "version": 1,
    "payload": {
      "id": "trg_001_research_digest_dentists",
      "scope": "merchant",
      "kind": "research_digest",
      "source": "external",
      "merchant_id": "m_001_drmeera_dentist_delhi",
      "customer_id": null,
      "payload": {
        "category": "dentists",
        "top_item_id": "d_2026W17_jida_fluoride"
      },
      "urgency": 2,
      "suppression_key": "research:dentists:2026-W17",
      "expires_at": "2026-05-03T00:00:00Z"
    }
  }' | jq
```

---

### Step 4: Trigger Proactive Outreach (`POST /v1/tick`)
```bash
curl -s -X POST "$BASE_URL/v1/tick" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "now": "2026-04-26T10:35:00Z",
    "available_triggers": ["trg_001_research_digest_dentists"]
  }' | jq
```

*Note: Copy the `conversation_id` from the output `actions[0].conversation_id` for Step 5.*

---

### Step 5: Interactive Merchant Reply (`POST /v1/reply`)

#### Option A: Positive Intent ("Yes, send me the draft")
```bash
curl -s -X POST "$BASE_URL/v1/reply" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "conversation_id": "conv_500de8aa7132067580c094ddbfa125c2",
    "merchant_id": "m_001_drmeera_dentist_delhi",
    "from_role": "merchant",
    "message": "Yes please send me the draft",
    "received_at": "2026-04-26T10:38:00Z",
    "turn_number": 2
  }' | jq
```

#### Option B: WhatsApp Auto-Reply Detection
```bash
curl -s -X POST "$BASE_URL/v1/reply" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "conversation_id": "conv_500de8aa7132067580c094ddbfa125c2",
    "merchant_id": "m_001_drmeera_dentist_delhi",
    "from_role": "merchant",
    "message": "Thank you for contacting Dr. Meera clinic! We will get back to you shortly.",
    "received_at": "2026-04-26T10:39:00Z",
    "turn_number": 3
  }' | jq
```

#### Option C: Merchant Opt-Out ("Stop messaging")
```bash
curl -s -X POST "$BASE_URL/v1/reply" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "conversation_id": "conv_500de8aa7132067580c094ddbfa125c2",
    "merchant_id": "m_001_drmeera_dentist_delhi",
    "from_role": "merchant",
    "message": "Stop messaging me, not interested",
    "received_at": "2026-04-26T10:40:00Z",
    "turn_number": 4
  }' | jq
```

---

### Step 6: Dynamic content from the database (`GET /v1/conversations`)
```bash
# List persisted conversations (requires DATABASE_URL to be configured; returns
# { "database": "disabled" } gracefully when not set)
curl -s "$BASE_URL/v1/conversations?limit=20" -H "Authorization: Bearer $TOKEN" | jq

# Messages of one conversation
curl -s "$BASE_URL/v1/conversations/<conversation_id>/messages" \
  -H "Authorization: Bearer $TOKEN" | jq
```

---

### Step 7: Teardown / Reset (`POST /v1/teardown`)
```bash
curl -s -X POST "$BASE_URL/v1/teardown" -H "Authorization: Bearer $TOKEN" | jq
```

---

## 3. Deployment

| Environment | URL | Notes |
|---|---|---|
| **Render (primary)** | https://vera-merchant-assistant-zres.onrender.com | Auto-deploys on push to `main` (repo `Webrookie0/magicpin`, rootDir `magicpin-ai-challenge`) |
| Verdent | https://magicpin-u1n3mv1.verdent.app | Private; requires Verdent sign-in through the browser bridge |
| Local | http://127.0.0.1:8080 | `uvicorn bot:app --port 8080` |

### Database note
`/v1/conversations` persistence uses `DATABASE_URL`. On Render it is currently
**not set** (`healthz` shows `"database": "disabled"`), so all endpoints work
and persistence is skipped. To enable it, add `DATABASE_URL` in the Render
dashboard (Service → Environment) with a Postgres connection string, e.g. from
Supabase → Project Settings → Database. The schema
(`vera_contexts`, `vera_conversations`, `vera_messages`) is already applied via
migration `20260926092954` and is idempotent.
