"""Validate envelope and consumed payload shapes before storing any context."""
from typing import Any, Literal

from pydantic import BaseModel, Field, StrictInt, field_validator, model_validator

from .timeutils import parse_time

Scope = Literal["category", "merchant", "customer", "trigger"]


class ContextPush(BaseModel):
    scope: Scope
    context_id: str = Field(min_length=1, max_length=256, pattern=r"^\S+$")
    version: StrictInt = Field(ge=1)
    payload: dict[str, Any]
    delivered_at: str | None = None

    @field_validator("delivered_at")
    @classmethod
    def timestamp(cls, value):
        if value is not None:
            parse_time(value)
        return value

    @model_validator(mode="after")
    def payload_shape(self):
        p = self.payload
        identity_key = {"category": "slug", "merchant": "merchant_id", "customer": "customer_id", "trigger": "id"}[self.scope]
        if p.get(identity_key) != self.context_id:
            raise ValueError(f"payload.{identity_key} must match context_id")
        for key in ("identity", "voice", "performance", "subscription", "customer_aggregate", "preferences", "consent", "relationship", "payload", "peer_stats"):
            if key in p and not isinstance(p[key], dict):
                raise ValueError(f"payload.{key} must be an object")
        for key in ("offers", "digest", "patient_content_library", "trend_signals", "seasonal_beats", "review_themes", "conversation_history"):
            if key in p and (not isinstance(p[key], list) or any(not isinstance(x, dict) for x in p[key])):
                raise ValueError(f"payload.{key} must be a list of objects")
        for container, keys in {
            "identity": ("languages",), "voice": ("vocab_taboo", "taboos", "vocab_allowed"),
            "consent": ("scope",),
        }.items():
            for key in keys:
                value = p.get(container, {}).get(key)
                if value is not None and (not isinstance(value, list) or any(not isinstance(x, str) for x in value)):
                    raise ValueError(f"payload.{container}.{key} must be a list of strings")
        for container, keys in {"preferences": ("channel",), "consent": ("status",)}.items():
            for key in keys:
                if key in p.get(container, {}) and not isinstance(p[container][key], str):
                    raise ValueError(f"payload.{container}.{key} must be a string")
        for container, keys in {"preferences": ("reminder_opt_in",), "consent": ("active", "opted_in")}.items():
            for key in keys:
                if key in p.get(container, {}) and type(p[container][key]) is not bool:
                    raise ValueError(f"payload.{container}.{key} must be a boolean")
        if self.scope in {"merchant", "customer"}:
            if not isinstance(p.get("identity", {}).get("name"), str) or not p["identity"]["name"].strip():
                raise ValueError("payload.identity.name is required")
            key = "category_slug" if self.scope == "merchant" else "merchant_id"
            if not isinstance(p.get(key), str) or not p[key].strip():
                raise ValueError(f"payload.{key} is required")
        if self.scope == "trigger":
            if p.get("scope") not in ("merchant", "customer") or not isinstance(p.get("kind"), str) or not p["kind"].strip():
                raise ValueError("trigger scope and kind are required")
            if not isinstance(p.get("payload"), dict):
                raise ValueError("trigger.payload must be an object")
            for key in ("merchant_id", "customer_id"):
                top, nested = p.get(key), p["payload"].get(key)
                if top and nested and top != nested:
                    raise ValueError(f"conflicting {key}")
                value = top or nested
                if value is not None and (not isinstance(value, str) or not value.strip()):
                    raise ValueError(f"invalid {key}")
            if not (p.get("merchant_id") or p["payload"].get("merchant_id")):
                raise ValueError("trigger merchant_id is required")
            if p["scope"] == "customer" and not (p.get("customer_id") or p["payload"].get("customer_id")):
                raise ValueError("customer trigger requires customer_id")
            if p["scope"] == "merchant" and (p.get("customer_id") or p["payload"].get("customer_id")):
                raise ValueError("merchant trigger must not target a customer")
            if type(p.get("urgency", 1)) is not int or not 1 <= p.get("urgency", 1) <= 5:
                raise ValueError("urgency must be an integer from 1 to 5")
            if not isinstance(p.get("suppression_key", ""), str):
                raise ValueError("suppression_key must be a string")
            for key in ("expires_at", "not_before"):
                if p.get(key) is not None:
                    parse_time(p[key])
        return self


class TickRequest(BaseModel):
    now: str
    available_triggers: list[str] = Field(default_factory=list)

    @field_validator("now")
    @classmethod
    def timestamp(cls, value):
        parse_time(value)
        return value


class ReplyRequest(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=256)
    merchant_id: str | None = None
    customer_id: str | None = None
    from_role: Literal["merchant", "customer"] = "merchant"
    message: str = Field(min_length=1, max_length=10000)
    received_at: str | None = None
    turn_number: StrictInt = Field(default=0, ge=0)

    @field_validator("message")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("message must not be blank")
        return value

    @field_validator("received_at")
    @classmethod
    def timestamp(cls, value):
        if value is not None:
            parse_time(value)
        return value
