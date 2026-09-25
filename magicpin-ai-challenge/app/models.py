"""Request/response models for the Vera HTTP surface."""
from typing import Any, Optional

from pydantic import BaseModel, field_validator


SCOPES = ("category", "merchant", "customer", "trigger")


class ContextPush(BaseModel):
    scope: str
    context_id: str
    version: int
    payload: dict[str, Any]
    delivered_at: Optional[str] = None

    @field_validator("scope")
    @classmethod
    def scope_known(cls, v: str) -> str:
        if v not in SCOPES:
            raise ValueError(f"invalid_scope: {v}")
        return v

    @field_validator("context_id")
    @classmethod
    def id_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("context_id must be non-empty")
        return v

    @field_validator("version")
    @classmethod
    def version_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("version must be >= 1")
        return v


class TickRequest(BaseModel):
    now: str
    available_triggers: list[str] = []


class ReplyRequest(BaseModel):
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    from_role: str = "merchant"
    message: str
    received_at: Optional[str] = None
    turn_number: int = 0
