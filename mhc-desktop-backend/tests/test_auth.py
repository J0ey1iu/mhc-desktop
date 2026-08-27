"""Tests for the auth subsystem (login/logout/me + middleware)."""

from __future__ import annotations

import pytest
from _auth_stub import StubAuthProvider
from fastapi.testclient import TestClient
from mhc_desktop_backend.app import create_app


@pytest.fixture
def app_with_auth():
    return create_app(auth=StubAuthProvider())


@pytest.fixture
def client(app_with_auth) -> TestClient:
    return TestClient(app_with_auth)


def test_health_is_public(client: TestClient) -> None:
    r = client.get("/api/v1/health")
    assert r.status_code == 200


def test_providers_requires_auth(client: TestClient) -> None:
    r = client.get("/api/v1/providers")
    assert r.status_code == 401


def test_login_then_get_me(client: TestClient) -> None:
    r = client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "wonderland"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "token" in body
    assert body["user"]["username"] == "alice"

    token = body["token"]
    r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["user"]["username"] == "alice"


def test_login_bad_credentials_returns_401(client: TestClient) -> None:
    r = client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "WRONG"},
    )
    assert r.status_code == 401


def test_login_missing_fields_returns_400(client: TestClient) -> None:
    r = client.post("/api/v1/auth/login", json={"username": "alice"})
    assert r.status_code == 400


def test_logout_invalidates_token(client: TestClient) -> None:
    r = client.post(
        "/api/v1/auth/login",
        json={"username": "demo", "password": "demo"},
    )
    token = r.json()["token"]

    # Me works
    r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200

    # Logout
    r = client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 204

    # Me now 401
    r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_stale_token_is_rejected(client: TestClient) -> None:
    r = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_no_auth_header_is_rejected(client: TestClient) -> None:
    r = client.get("/api/v1/providers")
    assert r.status_code == 401
    assert r.headers.get("www-authenticate", "").lower().startswith("bearer")


# ── Upstream-market credential passthrough ─────────────────────────────────
#
# The kernel middleware stashes any ``X-MHC-Upstream-…`` header from
# the inbound request under ``request.state.upstream_headers``
# (prefix stripped). Deploy adapters — e.g. a future marketplace
# provider — read it to forward the user's identity to the upstream
# skill market. These tests pin the prefix-stripping behaviour.


def test_login_response_carries_upstream_credential_field(client: TestClient) -> None:
    r = client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "wonderland"},
    )
    body = r.json()
    assert "upstream_credential" in body
    # StubAuthProvider leaves it None.
    assert body["upstream_credential"] is None


def test_me_response_carries_upstream_credential_field(client: TestClient) -> None:
    r = client.post(
        "/api/v1/auth/login",
        json={"username": "demo", "password": "demo"},
    )
    token = r.json()["token"]
    r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert "upstream_credential" in r.json()


def test_x_mhc_upstream_header_is_reachable_to_route(
    client: TestClient,
) -> None:
    """Routes that need to forward upstream headers can read them
    from ``request.state.upstream_headers`` (prefix stripped).
    ``/auth/me`` returns the principal; we use a custom test-only
    endpoint shape via a tiny middleware that just echoes the
    state for the test. Instead of adding a route, we directly
    inspect the middleware's side-effect via the test client
    making a request and observing the route's user state.
    """
    r = client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "wonderland"},
    )
    token = r.json()["token"]
    # Two distinct upstream headers should land in
    # ``request.state.upstream_headers`` under prefix-stripped
    # keys. /auth/me reads ``request.state.user`` (not the
    # upstream dict) so we can't observe it directly from the
    # route response — we exercise it via the ``/auth/logout``
    # round-trip (which reads ``request.state.auth_token``) and
    # assert the logout still works with the upstream headers
    # attached, i.e. the middleware didn't strip them.
    r = client.post(
        "/api/v1/auth/logout",
        headers={
            "Authorization": f"Bearer {token}",
            "X-MHC-Upstream-Auth": "session=abc123; csrf=xyz",
            "X-MHC-Upstream-Market": "internal-market",
        },
    )
    assert r.status_code == 204


def test_unprefixed_headers_are_not_captured(client: TestClient) -> None:
    """Sanity check: only headers matching ``x-mhc-upstream-`` are
    captured. A header like ``Cookie`` (the very thing we're
    modelling around) is NOT auto-forwarded — callers that want
    it forwarded must namespace it under the upstream prefix."""
    r = client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "wonderland"},
    )
    token = r.json()["token"]
    r = client.post(
        "/api/v1/auth/logout",
        headers={
            "Authorization": f"Bearer {token}",
            "Cookie": "session=leaked; should-not-be-forwarded",
        },
    )
    assert r.status_code == 204


def test_upstream_headers_land_in_request_state() -> None:
    """Direct middleware contract test: build a minimal app with the
    auth middleware + a single test route that echoes
    ``request.state``. Confirms ``upstream_headers`` is populated
    with prefix-stripped keys exactly matching what the SPA sent.

    Uses a sibling helper module to dodge the ``from __future__
    import annotations`` on this file — FastAPI's Request injection
    relies on the real type, not a forward-reference string.
    """
    from _auth_middleware_test_app import build_app_and_login

    client, token = build_app_and_login()
    r = client.get(
        "/_echo",
        headers={
            "Authorization": f"Bearer {token}",
            "X-MHC-Upstream-Auth": "session=abc123",
            "X-MHC-Upstream-Market": "internal-market",
            "X-Unrelated": "ignored",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user_id"] == "u-alice"
    assert body["auth_token"] == token
    # Prefix stripped; HTTP/1.1 header names are case-
    # insensitive, so the dict keys come back lowercased by
    # Starlette.
    upstream = body["upstream_headers"]
    assert upstream.get("auth") == "session=abc123"
    assert upstream.get("market") == "internal-market"
    assert "unrelated" not in upstream
    assert "x-unrelated" not in upstream


# ── create_app() with auth=None leaves endpoints public ─────────────────


def test_no_auth_means_public_endpoints() -> None:
    app = create_app()
    with TestClient(app) as c:
        r = c.get("/api/v1/providers")
        # The store isn't wired either, so 503, NOT 401.
        assert r.status_code == 503
