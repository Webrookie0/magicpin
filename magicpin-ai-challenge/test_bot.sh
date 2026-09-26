#!/usr/bin/env bash
# ==============================================================================
# Vera Merchant Assistant - Live API Test Script
# Usage:
#   ./test_bot.sh                               # Defaults to local http://127.0.0.1:8080
#   BASE_URL=https://magicpin-u1n3mv1.verdent.app ./test_bot.sh   # Against deployed app
#   VERA_TOKEN=your_token ./test_bot.sh         # With optional Bearer auth
# ==============================================================================

set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8080}"
VERA_TOKEN="${VERA_TOKEN:-}"

# Optional Auth & Cookie headers
EXTRA_ARGS=()
if [ -n "$VERA_TOKEN" ]; then
  EXTRA_ARGS+=(-H "Authorization: Bearer $VERA_TOKEN")
fi
if [ -n "${VERDENT_COOKIE:-}" ]; then
  EXTRA_ARGS+=(-H "Cookie: $VERDENT_COOKIE")
fi

echo "=========================================================="
echo " Testing Vera Assistant at: $BASE_URL"
echo "=========================================================="
echo ""

# Helper to format JSON if jq is installed
format_json() {
  if command -v jq >/dev/null 2>&1; then
    jq .
  else
    cat
  fi
}

# 1. Healthz
echo "▶ 1. Checking GET /v1/healthz..."
curl -s -L ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"} \
  "$BASE_URL/v1/healthz" | format_json
echo -e "\n"

# 2. Metadata
echo "▶ 2. Checking GET /v1/metadata..."
curl -s -L ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"} \
  "$BASE_URL/v1/metadata" | format_json
echo -e "\n"

# 3. Teardown / Reset state
echo "▶ 3. Resetting state with POST /v1/teardown..."
curl -s -L -X POST ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"} \
  "$BASE_URL/v1/teardown" | format_json
echo -e "\n"

# 4. Push Category Context
echo "▶ 4. Pushing Category Context (dentists)..."
curl -s -L -X POST ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"} \
  -H "Content-Type: application/json" \
  "$BASE_URL/v1/context" \
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
  }' | format_json
echo -e "\n"

# 5. Push Merchant Context
echo "▶ 5. Pushing Merchant Context (Dr. Meera)..."
curl -s -L -X POST ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"} \
  -H "Content-Type: application/json" \
  "$BASE_URL/v1/context" \
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
  }' | format_json
echo -e "\n"

# 6. Push Trigger Context
echo "▶ 6. Pushing Trigger Context (research_digest)..."
curl -s -L -X POST ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"} \
  -H "Content-Type: application/json" \
  "$BASE_URL/v1/context" \
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
  }' | format_json
echo -e "\n"

# 7. Check Healthz (verify contexts loaded)
echo "▶ 7. Checking GET /v1/healthz (contexts_loaded should show counts)..."
curl -s -L ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"} \
  "$BASE_URL/v1/healthz" | format_json
echo -e "\n"

# 8. Post Tick
echo "▶ 8. Simulating Tick with POST /v1/tick (generating proactive message)..."
TICK_RESP=$(curl -s -L -X POST ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"} \
  -H "Content-Type: application/json" \
  "$BASE_URL/v1/tick" \
  -d '{
    "now": "2026-04-26T10:35:00Z",
    "available_triggers": ["trg_001_research_digest_dentists"]
  }')

echo "$TICK_RESP" | format_json
echo -e "\n"

# Extract conversation_id if jq is available
CONV_ID=""
if command -v jq >/dev/null 2>&1; then
  CONV_ID=$(echo "$TICK_RESP" | jq -r '.actions[0].conversation_id // empty')
fi

if [ -z "$CONV_ID" ] || [ "$CONV_ID" = "null" ]; then
  # Fallback extraction with grep/sed
  CONV_ID=$(echo "$TICK_RESP" | grep -o '"conversation_id":"[^"]*' | cut -d'"' -f4 || echo "")
fi

if [ -n "$CONV_ID" ]; then
  echo "✔ Captured conversation_id: $CONV_ID"
  echo ""
  
  # 9. Reply positive intent
  echo "▶ 9. Simulating Merchant Reply ('Yes please send me the draft') with POST /v1/reply..."
  curl -s -L -X POST ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"} \
    -H "Content-Type: application/json" \
    "$BASE_URL/v1/reply" \
    -d "{
      \"conversation_id\": \"$CONV_ID\",
      \"merchant_id\": \"m_001_drmeera_dentist_delhi\",
      \"from_role\": \"merchant\",
      \"message\": \"Yes please send me the draft\",
      \"received_at\": \"2026-04-26T10:38:00Z\",
      \"turn_number\": 2
    }" | format_json
  echo -e "\n"
else
  echo "⚠ No actions returned in tick, skipping reply step."
fi

echo "=========================================================="
echo " Test sequence completed successfully!"
echo "=========================================================="
