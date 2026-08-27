"""Tiny harness app for testing the auth middleware in isolation.

Lives in its own module (no ``from __future__ import annotations``)
because FastAPI's Request-introspection chokes on forward-reference
annotations — the type hint must be the real ``Request`` class for
the dependency injector to recognise it.
"""

from __future__ import annotations

import asyncio
from typing import Any

from _auth_stub import StubAuthProvider
from fastapi import FastAPI, Request
from mhc_desktop_backend.auth.middleware import install_auth
from starlette.testclient import TestClient


def build_app_and_login() -> tuple[TestClient, str]:
    """Build a minimal FastAPI app with the auth middleware and a
    single test-only echo route, log in as alice, and return
    ``(client, token)`` ready for tests to make authenticated
    requests."""
    provider = StubAuthProvider()
    app = FastAPI()
    install_auth(app, provider)

    @app.get("/_echo")
    async def echo(request: Request) -> dict[str, Any]:
        return {
            "user_id": getattr(request.state.user, "id", None),
            "auth_token": getattr(request.state, "auth_token", None),
            "upstream_headers": dict(
                getattr(request.state, "upstream_headers", {}) or {}
            ),
        }

    token = asyncio.run(provider.login("alice", "wonderland"))[0]
    return TestClient(app), token


__all__ = ["build_app_and_login"]
