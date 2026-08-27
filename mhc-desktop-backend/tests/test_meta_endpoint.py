"""Tests for ``GET /api/v1/meta`` and the deploy-injectable
runtime manifest.

Covers the post-refactor contract: the endpoint returns whatever
the deploy passed via ``create_app(meta=...)``; missing keys are
caller-handled (the endpoint always returns 200 with at least
``{"meta": {}}``). The endpoint is public — no auth required.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from mhc_desktop_backend.app import create_app


def test_meta_is_public_and_seeds_default_brand():
    # ``create_app`` always seeds ``meta["brand"]["name"]`` so
    # the FastAPI title, MCP clientInfo, and onboarding renderer
    # all read the same token. Without a deploy override the
    # default is the kernel module name, not the upstream
    # product name.
    app = create_app()
    with TestClient(app) as c:
        r = c.get("/api/v1/meta")
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["brand"]["name"] == "mhc-desktop-backend"
    # Caller-supplied keys still pass through verbatim.
    for k in ("version", "data_dir", "bundled"):
        assert k not in body["meta"]


def test_meta_returns_deploy_manifest():
    manifest = {
        "version": "1.2.3",
        "data_dir": "/srv/data",
        "brand": {"name": "Acme Agent", "primary_color": "#ff0000"},
        "bundled": {
            "skills": ["commit-message", "code-review"],
            "mcps": ["dummy"],
            "tools": ["now", "uuid"],
        },
    }
    app: FastAPI = create_app(meta=manifest)
    with TestClient(app) as c:
        r = c.get("/api/v1/meta")
    assert r.status_code == 200
    assert r.json() == {"meta": manifest}


def test_meta_is_a_copy_not_a_reference():
    """Mutating the returned dict must not affect app state.

    The endpoint returns a fresh dict per request so a careless
    caller can't accidentally pollute the deploy's source of
    truth.
    """
    app: FastAPI = create_app(meta={"version": "1.0"})
    with TestClient(app) as c:
        r1 = c.get("/api/v1/meta").json()
        r1["version"] = "tampered"
        r2 = c.get("/api/v1/meta").json()
    assert r2["meta"]["version"] == "1.0"


def test_meta_endpoint_does_not_require_auth():
    """The renderer hits ``/api/v1/meta`` before login to render
    brand / data_dir. The endpoint must be in the auth-exempt
    set; a deploy that customises ``auth_exempt_paths`` and
    removes it would 401 the renderer, which is a foot-gun we
    want to surface.
    """
    from _auth_stub import StubAuthProvider

    app = create_app(auth=StubAuthProvider())
    with TestClient(app) as c:
        # No Authorization header — meta must still answer.
        r = c.get("/api/v1/meta")
    assert r.status_code == 200
