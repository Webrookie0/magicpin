"""Shared output checks for both proactive and reply messages.

Facts are selected by deterministic route code. These checks are a second layer,
not a semantic verifier for arbitrary model-generated prose.
"""
import re

MAX_BODY_CHARS = 1600
CTAS = {"binary_yes_no", "binary_confirm_cancel", "open_ended", "none"}
URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.I)
PRIVATE_RE = re.compile(r"<\s*(?:phone|redacted|email)[^>]*>|[\w.+-]+@[\w.-]+\.[a-z]{2,}|(?<!\d)(?:\+91[ -]?)?\d[\d -]{8,}\d(?!\d)", re.I)
INSTRUCTION_RE = re.compile(r"ignore (?:all |any |the |previous )*(?:instructions|rules)|system prompt|developer message|reveal (?:secrets|credentials)|(?:api[_ -]?key|password)\s*[:=]", re.I)


def validate_text(body, cta, category, prior=()):
    errors = []
    if not isinstance(body, str) or not body.strip():
        return ["empty_body"]
    if len(body) > MAX_BODY_CHARS:
        errors.append("body_too_long")
    if cta not in CTAS:
        errors.append("invalid_cta")
    if body.count("?") > 1:
        errors.append("multiple_ctas")
    if URL_RE.search(body):
        errors.append("url")
    # ISO dates are not phone numbers.
    without_dates = re.sub(r"\d{4}-\d{2}-\d{2}", "", body)
    if PRIVATE_RE.search(without_dates):
        errors.append("private_identifier")
    if INSTRUCTION_RE.search(body):
        errors.append("instruction_in_context")
    voice = category.get("voice") or {}
    taboos = [*(voice.get("vocab_taboo") or []), *(voice.get("taboos") or [])]
    for taboo in taboos:
        if not isinstance(taboo, str):
            continue
        phrase = taboo.split(" (")[0].strip()
        if phrase and re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", body, re.I):
            errors.append("category_taboo")
            break
    if body in prior:
        errors.append("repetition")
    return errors


def validate(result, category, merchant, trigger, customer=None, prior=()):
    errors = validate_text(result.body, result.cta, category, prior)
    expected = "merchant_on_behalf" if trigger.get("scope") == "customer" else "vera"
    if result.send_as != expected:
        errors.append("wrong_audience")
    if result.suppression_key != trigger.get("suppression_key", ""):
        errors.append("wrong_suppression_key")
    if not result.rationale.strip():
        errors.append("missing_rationale")
    if result.template_name and result.template_params != [result.body]:
        errors.append("template_mismatch")
    return errors
