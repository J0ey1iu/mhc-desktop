"""Brand resolution plumbing.

One test per consumer surface (FastAPI title, /api/v1/meta, the
onboarding welcome card, the MCP ``initialize`` clientInfo) plus
one precedence test (meta wins over env wins over kernel default).
``docs/BRANDING.md`` documents the recipe a fork follows; this
module is the safety net that catches regressions on each surface.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from mhc_desktop_backend import __app_name__
from mhc_desktop_backend.app import create_app
from mhc_desktop_backend.config import load_config
from mhc_desktop_backend.mcp import MCPManager
from mhc_desktop_deploy.impls.file_stores.mcp_store import MCPStore


def _client(meta: dict | None = None) -> TestClient:
    """Build a TestClient around ``create_app(meta=...)``."""
    return TestClient(create_app(config=load_config(), meta=meta))


def test_default_brand_seeds_kernel_module_name():
    """No deploy override → every consumer reads ``__app_name__``,
    never a hardcoded upstream brand.
    """
    with _client() as c:
        title = c.get("/openapi.json").json()["info"]["title"]
        meta = c.get("/api/v1/meta").json()["meta"]
        zh = c.get(
            "/api/v1/onboarding", headers={"Accept-Language": "zh"}
        ).json()[0]["title_i18n"]["zh"]
    assert title == f"{__app_name__} API"
    assert meta["brand"]["name"] == __app_name__
    assert zh == f"欢迎使用 {__app_name__}"


def test_meta_brand_drives_all_surfaces():
    """One ``meta["brand"]["name"]`` value flows to every surface."""
    with _client(meta={"brand": {"name": "Acme Corp"}}) as c:
        title = c.get("/openapi.json").json()["info"]["title"]
        meta = c.get("/api/v1/meta").json()["meta"]
        en = c.get("/api/v1/onboarding").json()[0]["title_i18n"]["en"]
    assert title == "Acme Corp API"
    assert meta["brand"]["name"] == "Acme Corp"
    assert en == "Welcome to Acme Corp"


def test_meta_wins_over_env_wins_over_default(monkeypatch):
    """Precedence chain is explicit kwarg > env > kernel default."""
    monkeypatch.setenv("MHC_APP_NAME", "Env Brand")
    # Explicit meta wins over env.
    a = create_app(meta={"brand": {"name": "Meta Brand"}})
    assert a.title == "Meta Brand API"
    # Env wins when meta has no brand key.
    b = create_app(meta={"version": "1.0"})
    assert b.title == "Env Brand API"
    # Default wins when neither set.
    monkeypatch.delenv("MHC_APP_NAME", raising=False)
    c = create_app()
    assert c.title == f"{__app_name__} API"


def test_mcp_manager_uses_brand_for_client_info():
    """The MCP handshake sends ``clientInfo.name = brand`` so
    downstream MCP server audit logs see the deploy's brand,
    not the upstream hardcode.
    """
    store = MCPStore(mcp_dir=None, state_file=None)  # type: ignore[arg-type]
    assert MCPManager(store)._client_name == __app_name__
    assert MCPManager(store, client_name="Acme Corp")._client_name == "Acme Corp"


def test_meta_extra_keys_survive_brand_resolution():
    """A deploy-supplied ``brand`` sub-dict keeps its extra keys
    (``primary_color`` etc.) alongside the kernel-seeded ``name``.
    """
    manifest = {
        "brand": {"name": "Acme Corp", "primary_color": "#ff0000"},
        "version": "1.2.3",
    }
    with _client(meta=manifest) as c:
        brand = c.get("/api/v1/meta").json()["meta"]["brand"]
    assert brand["name"] == "Acme Corp"
    assert brand["primary_color"] == "#ff0000"