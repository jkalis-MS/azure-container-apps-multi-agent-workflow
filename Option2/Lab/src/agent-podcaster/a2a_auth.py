"""A2A authentication middleware — bearer token + API key validation."""

import os
from fastapi import HTTPException, Header
from typing import Annotated

A2A_AUTH_ENABLED = os.getenv("A2A_AUTH_ENABLED", "false").lower() == "true"
A2A_AUTH_TOKEN = os.getenv("A2A_AUTH_TOKEN")

# Fail-fast: if auth is enabled but no token configured, raise on import
if A2A_AUTH_ENABLED and not A2A_AUTH_TOKEN:
    raise RuntimeError("A2A_AUTH_ENABLED=true but A2A_AUTH_TOKEN is not set")


async def verify_a2a_token(
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
):
    """Validate Bearer token or X-API-Key header. Raises 401 if auth enabled and invalid."""
    if not A2A_AUTH_ENABLED:
        return

    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
    elif x_api_key:
        token = x_api_key

    if token != A2A_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")
