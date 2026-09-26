"""Authentication: static API token (preserved) + Verdent-managed Supabase Auth.

A caller is authenticated when it presents either:
  - the shared VERA_API_TOKEN bearer (judge / service integrations), or
  - a Supabase Auth access token issued through Verdent-managed sign-in.

Supabase tokens are validated against the managed Auth server
(GET {SUPABASE_URL}/auth/v1/user); no signing secrets live in this codebase.
"""
import os
import time
import uuid

import httpx
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.security import require_api_token

bearer = HTTPBearer(auto_error=False, description="API token or Supabase access token")

_USER_CACHE: dict[str, tuple[float, dict | None]] = {}
_CACHE_TTL = 300


def _supabase_url() -> str:
    return (
        os.environ.get("SUPABASE_URL")
        or os.environ.get("VITE_SUPABASE_URL")
        or ""
    ).rstrip("/")


def _publishable_key() -> str:
    return (
        os.environ.get("SUPABASE_ANON_KEY")
        or os.environ.get("SUPABASE_PUBLISHABLE_KEY")
        or os.environ.get("VITE_SUPABASE_PUBLISHABLE_KEY")
        or ""
    )


def supabase_auth_configured() -> bool:
    return bool(_supabase_url() and _publishable_key())


def supabase_user_for_token(token: str) -> dict | None:
    """Validate a Supabase access token; returns the user or None.

    Result is cached briefly per token to avoid a round-trip on every call.
    """
    if not supabase_auth_configured():
        return None
    key = uuid.uuid5(uuid.NAMESPACE_URL, token).hex  # avoid storing raw tokens
    now = time.time()
    cached = _USER_CACHE.get(key)
    if cached and now - cached[0] < _CACHE_TTL:
        return cached[1]
    try:
        resp = httpx.get(
            f"{_supabase_url()}/auth/v1/user",
            headers={"apikey": _publishable_key(), "Authorization": f"Bearer {token}"},
            timeout=8.0,
        )
        user = resp.json() if resp.status_code == 200 else None
    except httpx.HTTPError:
        user = None
    _USER_CACHE[key] = (now, user)
    if len(_USER_CACHE) > 512:  # simple eviction keeps the cache bounded
        _USER_CACHE.pop(next(iter(_USER_CACHE)))
    return user


def auth_caller(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
    """Combined dependency: shared API token OR Supabase-managed sign-in token."""
    # 1) Legacy/shared API token path (unchanged behavior for the judge).
    expected = os.environ.get("VERA_API_TOKEN", "")
    if expected and credentials is not None and credentials.credentials == expected:
        return {"kind": "api_token"}
    try:
        require_api_token(credentials)
    except HTTPException:
        # 2) Verdent-managed Supabase Auth path.
        if credentials is None:
            raise
        user = supabase_user_for_token(credentials.credentials)
        if user and user.get("id"):
            return {
                "kind": "supabase",
                "user_id": user["id"],
                "email": user.get("email") or user.get("phone") or "",
            }
        raise HTTPException(
            401,
            detail="Invalid or missing credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return {"kind": "api_token"}


def owner_user_id(principal: dict) -> str | None:
    """User-owned rows are tagged only for Supabase-authenticated callers."""
    if principal.get("kind") == "supabase":
        return principal.get("user_id")
    return None
