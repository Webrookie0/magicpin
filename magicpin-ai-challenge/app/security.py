"""Optional bearer authentication for public deployments; local fixtures stay usable."""
import os
import secrets

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer = HTTPBearer(auto_error=False, description="Public API token from VERA_API_TOKEN")


def require_api_token(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    expected = os.environ.get("VERA_API_TOKEN", "")
    if not expected:
        if os.environ.get("VERA_REQUIRE_AUTH", "false").lower() in {"true", "1", "yes"}:
            raise HTTPException(503, detail="API authentication has not been configured")
        return
    if credentials is None or not secrets.compare_digest(credentials.credentials.encode(), expected.encode()):
        raise HTTPException(401, detail="Invalid or missing API token", headers={"WWW-Authenticate": "Bearer"})
