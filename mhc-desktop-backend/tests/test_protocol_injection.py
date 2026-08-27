"""End-to-end test for the Protocol injection layer.

These tests prove that the storage Protocols in
:mod:`mhc_desktop_backend.protocols` actually mean what they say: any
class that matches the structural surface can be plugged into
:meth:`mhc_desktop_backend.app.create_app` and the resulting HTTP app
behaves correctly.

The reference impls (file-backed JSON under ``~/.mhc-desktop/``) are
covered by the per-domain test files. This file covers the seam —
custom adapters wired via ``create_app(sessions=..., providers=..., )``.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from mhc_desktop_backend.app import create_app
from mhc_desktop_backend.protocols import (
    MCPManagerProtocol,
    MCPStoreProtocol,
    Provider,
    ProviderStoreProtocol,
    Session,
    SessionStoreProtocol,
    SkillStoreProtocol,
    StreamRegistryProtocol,
    ToolStoreProtocol,
)

# ── Stubs ─────────────────────────────────────────────────────────────────────
# Each stub satisfies its Protocol structurally. They live in-process
# only — no filesystem, no subprocess. If the injection layer is wired
# correctly the FastAPI app should serve real HTTP against them.


class _InMemorySessionStore:
    """SessionStoreProtocol implementation — pure in-process dict."""

    def __init__(self) -> None:
        self._items: dict[str, Session] = {}

    async def list(self) -> list[Session]:
        return list(self._items.values())

    async def get(self, sid: str) -> Session | None:
        return self._items.get(sid)

    async def create(self, data: dict[str, Any] | None = None) -> Session:
        import uuid
        from datetime import UTC, datetime

        data = data or {}
        sid = data.get("id") or str(uuid.uuid4())
        sess = Session(
            id=sid,
            title=data.get("title", "New chat"),
            messages=list(data.get("messages") or []),
            provider=data.get("provider", ""),
            model=data.get("model", ""),
            created_at=datetime.now(UTC).isoformat(),
            updated_at=datetime.now(UTC).isoformat(),
        )
        self._items[sid] = sess
        return sess

    async def update(self, sid: str, data: dict[str, Any]) -> Session:
        from datetime import UTC, datetime

        existing = await self.get(sid)
        if existing is None:
            raise ValueError(f"session '{sid}' not found")
        for key in ("title", "messages", "provider", "model"):
            if key in data:
                setattr(existing, key, data[key])
        existing.updated_at = datetime.now(UTC).isoformat()
        return existing

    async def delete(self, sid: str) -> None:
        self._items.pop(sid, None)

    async def delete_many(self, sids: list[str]) -> int:
        n = 0
        for s in sids:
            if s in self._items:
                del self._items[s]
                n += 1
        return n

    async def clear_all(self) -> int:
        n = len(self._items)
        self._items.clear()
        return n

    async def close(self) -> None:
        self._items.clear()


class _InMemoryProviderStore:
    """ProviderStoreProtocol implementation — pure in-process dict."""

    def __init__(self) -> None:
        self._items: dict[str, Provider] = {}

    async def list(self) -> list[Provider]:
        return list(self._items.values())

    async def get(self, name: str) -> Provider | None:
        return self._items.get(name)

    async def create(self, data: dict[str, Any]) -> Provider:
        name = (data.get("name") or "").strip()
        if not name:
            raise ValueError("name is required")
        if name in self._items:
            raise ValueError(f"provider '{name}' already exists")
        provider = Provider.from_dict(data)
        self._items[name] = provider
        return provider

    async def update(self, name: str, data: dict[str, Any]) -> Provider:
        existing = self._items.get(name)
        if existing is None:
            raise ValueError(f"provider '{name}' not found")
        merged = {**existing.to_dict(), **data, "name": name}
        self._items[name] = Provider.from_dict(merged)
        return self._items[name]

    async def delete(self, name: str) -> None:
        if name not in self._items:
            raise ValueError(f"provider '{name}' not found")
        del self._items[name]

    async def close(self) -> None:
        self._items.clear()


class _NullSkillStore:
    """SkillStoreProtocol — read-only, no skills installed."""

    async def list(self):
        return []

    async def get(self, slug):
        return None

    async def get_body(self, slug):
        return None

    async def get_file(self, slug, rel):
        raise FileNotFoundError(rel)

    async def install_from_folder(self, source, *, overwrite=False, origin="imported"):
        raise NotImplementedError("read-only stub")

    async def delete(self, slug):
        return None

    async def set_enabled(self, slug, enabled):
        raise NotImplementedError("read-only")

    async def update_meta(self, slug, *, description=None, body=None):
        raise NotImplementedError("read-only")

    async def export(self, slug):
        raise NotImplementedError("read-only")

    async def import_zip(self, data, *, origin="imported"):
        raise NotImplementedError("read-only")

    async def close(self):
        return None


class _NullMCPStore:
    """MCPStoreProtocol — read-only, no MCPs registered."""

    async def list(self):
        return []

    async def get(self, slug):
        return None

    async def upsert(self, data):
        raise NotImplementedError("read-only")

    async def delete(self, slug):
        return None

    async def set_enabled(self, slug, enabled):
        raise NotImplementedError("read-only")

    async def record_discovery(self, slug, tools):
        raise NotImplementedError("read-only")

    async def close(self):
        return None


class _NullMCPManager:
    """MCPManagerProtocol — refuses all connections, used to prove the
    manager seam is also injectable without dragging in subprocesses."""

    async def connect(self, server):
        raise NotImplementedError("no MCP in this test")

    async def list_tools(self, server):
        return []

    async def call_tool(
        self, server, tool_name, arguments, *, cancel=None, timeout=None
    ):
        raise NotImplementedError("no MCP in this test")

    async def disconnect(self, slug):
        return None

    async def shutdown(self):
        return None


class _NullToolStore:
    """ToolStoreProtocol — empty catalog."""

    async def list(self):
        return []

    async def get(self, slug):
        return None

    async def get_by_model_name(self, name):
        return None

    async def get_callable(self, slug):
        return None

    async def create(self, data):
        raise NotImplementedError("read-only")

    async def update(self, slug, data):
        raise NotImplementedError("read-only")

    async def delete(self, slug):
        return None

    async def set_enabled(self, slug, enabled):
        raise NotImplementedError("read-only")

    async def close(self):
        return None


# ── Structural conformance (the seam itself) ────────────────────────────────


def test_reference_impls_satisfy_protocols():
    """The default file-backed stores must satisfy the Protocols.

    This is the canary: if a new method is added to a Protocol and the
    reference store isn't updated, the abstraction is silently broken.
    This test fails the same day.
    """
    # Importing inside the test so the structural check reflects the
    # *current* state of the reference impls.
    from mhc_desktop_backend.mcp.manager import MCPManager
    from mhc_desktop_deploy.impls.file_stores.mcp_store import MCPStore as FileMCPStore
    from mhc_desktop_deploy.impls.file_stores.provider_store import (
        ProviderStore as FileProviderStore,
    )
    from mhc_desktop_deploy.impls.file_stores.session_store import (
        SessionStore as FileSessionStore,
    )
    from mhc_desktop_deploy.impls.file_stores.skills_store import (
        SkillStore as FileSkillStore,
    )
    from mhc_desktop_deploy.impls.file_stores.stream_registry import (
        StreamRegistry as FileReg,
    )
    from mhc_desktop_deploy.impls.file_stores.tools_store import (
        ToolStore as FileToolStore,
    )

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        assert isinstance(
            FileSessionStore(sessions_dir=tmp / "sessions"),
            SessionStoreProtocol,
        )
        assert isinstance(
            FileProviderStore(path=tmp / "providers.json"),
            ProviderStoreProtocol,
        )
        assert isinstance(FileSkillStore(), SkillStoreProtocol)
        assert isinstance(FileMCPStore(), MCPStoreProtocol)
        assert isinstance(MCPManager(FileMCPStore()), MCPManagerProtocol)
        assert isinstance(FileToolStore(), ToolStoreProtocol)
        assert isinstance(FileReg(), StreamRegistryProtocol)


# ── End-to-end: custom adapters via create_app ──────────────────────────────


@pytest.mark.asyncio
async def test_create_app_with_all_in_memory_stubs():
    """Build the whole app against in-memory adapters and exercise the
    HTTP surface. Proves the injection seam works for every Protocol
    simultaneously and that the routers transparently dispatch through
    whichever adapter is wired.

    Why we cover every Protocol at once (not one at a time): the
    routers' ``Depends`` helpers fall back to ``None`` for missing
    stores, so a partial injection could pass single-store tests
    while breaking under a real workload.
    """
    sessions = _InMemorySessionStore()
    providers = _InMemoryProviderStore()
    skills = _NullSkillStore()
    mcp_store = _NullMCPStore()
    mcp_manager = _NullMCPManager()
    tools = _NullToolStore()

    app: FastAPI = create_app(
        sessions=sessions,
        providers=providers,
        skills=skills,
        mcp_store=mcp_store,
        mcp_manager=mcp_manager,
        tools=tools,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Health/readiness — covers the lifespan + the routes that
        # don't touch any store.
        r = await client.get("/ready")
        assert r.status_code == 200
        r = await client.get("/api/v1/health")
        assert r.status_code == 200

        # Providers — wired through ProviderStoreProtocol.
        r = await client.post(
            "/api/v1/providers",
            json={
                "name": "openai",
                "provider_type": "openai",
                "api_key": "sk-test-1234",
                "default_model": "gpt-4o-mini",
            },
        )
        assert r.status_code == 201, r.text
        # The wire shape is the store's to_dict — not the file path.
        assert r.json()["name"] == "openai"
        assert r.json()["api_key"] == "***1234"  # masked

        r = await client.get("/api/v1/providers")
        assert r.status_code == 200
        names = {p["name"] for p in r.json()}
        assert names == {"openai"}

        r = await client.get("/api/v1/providers/openai")
        assert r.status_code == 200
        assert r.json()["name"] == "openai"

        # Sessions — wired through SessionStoreProtocol.
        r = await client.post(
            "/api/v1/sessions",
            json={
                "title": "Test session",
                "provider": "openai",
                "model": "gpt-4o-mini",
            },
        )
        assert r.status_code == 201, r.text
        sess = r.json()
        sid = sess["id"]
        assert sess["title"] == "Test session"

        r = await client.get("/api/v1/sessions")
        assert r.status_code == 200
        assert len(r.json()) == 1

        r = await client.get(f"/api/v1/sessions/{sid}")
        assert r.status_code == 200
        assert r.json()["id"] == sid

        r = await client.put(f"/api/v1/sessions/{sid}", json={"title": "Renamed"})
        assert r.status_code == 200
        assert r.json()["title"] == "Renamed"

        r = await client.delete(f"/api/v1/sessions/{sid}")
        assert r.status_code == 204

        r = await client.get("/api/v1/sessions")
        assert r.status_code == 200
        assert r.json() == []

        # Skills/MCP/Tools — null stores still answer their list endpoints.
        r = await client.get("/api/v1/skills")
        assert r.status_code == 200
        assert r.json() == []

        r = await client.get("/api/v1/mcp")
        assert r.status_code == 200
        assert r.json() == []

        r = await client.get("/api/v1/tools")
        assert r.status_code == 200
        assert r.json() == []


@pytest.mark.asyncio
async def test_update_provider_keeps_api_key_when_blank_or_masked():
    """PUT /providers/{name} treats an empty or masked api_key as
    "keep the existing value". Without this the SPA is forced to
    re-type the key on every edit (e.g. just to add a model),
    which is bad UX because api_key is masked on read and the user
    cannot paste back the real value.
    """
    from httpx import ASGITransport, AsyncClient

    providers = _InMemoryProviderStore()
    app = create_app(providers=providers)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Seed with a real key.
        r = await client.post(
            "/api/v1/providers",
            json={
                "name": "openai",
                "provider_type": "openai",
                "api_key": "sk-real-key-1234",
            },
        )
        assert r.status_code == 201

        # Edit: send empty api_key -> existing must be preserved.
        r = await client.put(
            "/api/v1/providers/openai",
            json={"default_model": "gpt-4o-mini", "api_key": ""},
        )
        assert r.status_code == 200, r.text
        fetched = await client.get("/api/v1/providers/openai")
        assert fetched.json()["api_key"] == "***1234"  # still masked old key

        # Edit: send masked api_key -> also "keep existing".
        r = await client.put(
            "/api/v1/providers/openai",
            json={"description": "updated", "api_key": "***1234"},
        )
        assert r.status_code == 200
        fetched = await client.get("/api/v1/providers/openai")
        assert fetched.json()["api_key"] == "***1234"
        assert fetched.json()["description"] == "updated"

        # Edit: send a NEW key -> it must replace the old one.
        r = await client.put(
            "/api/v1/providers/openai",
            json={"api_key": "sk-new-key-9999"},
        )
        assert r.status_code == 200
        fetched = await client.get("/api/v1/providers/openai")
        assert fetched.json()["api_key"] == "***9999"


@pytest.mark.asyncio
async def test_partial_injection_uses_default_for_unspecified(tmp_path):
    """Mix injected and default stores. The unspecified slots should
    fall back to the file-backed reference impls (pointed at tmp_path
    so the test doesn't pollute ``~/.mhc-desktop/``). This is the
    path customers use when they only want to swap one backend (e.g.
    ProviderStore → Vault, leave the rest on disk).
    """
    providers = _InMemoryProviderStore()
    # Point the default SessionStore at a temp dir so the test is hermetic.
    from mhc_desktop_deploy.impls.file_stores.session_store import SessionStore

    sessions = SessionStore(sessions_dir=tmp_path / "sessions")

    app = create_app(providers=providers, sessions=sessions)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # The injected ProviderStore should be wired.
        r = await client.post(
            "/api/v1/providers",
            json={"name": "vault-prod", "provider_type": "openai", "api_key": "v"},
        )
        assert r.status_code == 201, r.text
        # The injected (tmp_path) SessionStore is wired and round-trips
        # through HTTP — and the file actually lands in tmp_path, not
        # the user's home directory.
        r = await client.post("/api/v1/sessions", json={"title": "default-store"})
        assert r.status_code == 201, r.text
        assert list((tmp_path / "sessions").iterdir()), "session file should exist"


@pytest.mark.asyncio
async def test_create_app_with_auth_stub_smoke(tmp_path, monkeypatch):
    """``create_app(auth=StubAuthProvider())`` boots and the
    store-free routes respond. The store-backed surfaces
    (``/api/v1/{providers,sessions,skills,mcp,tools}``) are
    covered by the deploy-side ``test_assemble.py`` against
    ``build_default_app()``; the kernel contract here is just
    "the auth plumbing doesn't crash the kernel".
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    from _auth_stub import StubAuthProvider

    app = create_app(auth=StubAuthProvider())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/api/v1/health")).status_code == 200
        assert (await client.get("/ready")).status_code == 200
        # Auth wired: login then read principal via /auth/me.
        login = await client.post(
            "/api/v1/auth/login",
            json={"username": "demo", "password": "demo"},
        )
        assert login.status_code == 200, login.text
        token = login.json()["token"]
        r = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert r.json()["user"]["username"] == "demo"
