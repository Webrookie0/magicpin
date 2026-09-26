# Vera Assistant API — Live Testing cURL Suite

This guide contains the cURL commands to test the Vera Bot end-to-end.

---

## 1. Quick Automated Test Script

An executable script has been generated at [`test_bot.sh`](file:///Users/sumit/developer/magicpin/magicpin-ai-challenge/test_bot.sh).

```bash
# Test local server (http://127.0.0.1:8080)
./test_bot.sh

# Test deployed server (https://magicpin-u1n3mv1.verdent.app)
BASE_URL="https://magicpin-u1n3mv1.verdent.app" ./test_bot.sh

# If your deployment or preview requires a cookie or token:
VERDENT_COOKIE="your_cookie_here" BASE_URL="https://magicpin-u1n3mv1.verdent.app" ./test_bot.sh
```

---

## 2. Individual cURL Commands (Copy-Pasteable)

Set your base URL:
```bash
export BASE_URL="http://127.0.0.1:8080"
# or export BASE_URL="https://magicpin-u1n3mv1.verdent.app"
```

### Step 1: Health & Metadata Check
```bash
# Health Check
curl -s -L "$BASE_URL/v1/healthz" | jq

# Bot / Team Metadata
curl -s -L "$BASE_URL/v1/metadata" | jq
```

---

### Step 2: Push Contexts (`POST /v1/context`)

#### 2.1 Push Category Context
```bash
curl -s -L -X POST "$BASE_URL/v1/context" \
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

#### 2.2 Push Merchant Context
```bash
curl -s -L -X POST "$BASE_URL/v1/context" \
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

#### 2.3 Push Trigger Context
```bash
curl -s -L -X POST "$BASE_URL/v1/context" \
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

### Step 3: Trigger Proactive Outreach (`POST /v1/tick`)
```bash
curl -s -L -X POST "$BASE_URL/v1/tick" \
  -H "Content-Type: application/json" \
  -d '{
    "now": "2026-04-26T10:35:00Z",
    "available_triggers": ["trg_001_research_digest_dentists"]
  }' | jq
```

*Note: Copy the `conversation_id` from the output `actions[0].conversation_id` for Step 4.*

---

### Step 4: Interactive Merchant Reply (`POST /v1/reply`)

#### Option A: Positive Intent ("Yes, send me the draft")
```bash
curl -s -L -X POST "$BASE_URL/v1/reply" \
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
curl -s -L -X POST "$BASE_URL/v1/reply" \
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
curl -s -L -X POST "$BASE_URL/v1/reply" \
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

### Step 5: Teardown / Reset (`POST /v1/teardown`)
```bash
curl -s -L -X POST "$BASE_URL/v1/teardown" | jq
```
