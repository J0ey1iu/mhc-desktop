"""LLM outbound-header seam: factory composition + request-scoped closure.

Issue #20 — the deploy can now attach per-request identity headers to
every LLM call via ``create_app(llm_extra_headers_provider=...)``.
"""

from __future__ import annotations

import mhc_desktop_backend.llm.factory as factory_mod
import pytest
from fastapi import FastAPI, Request
from mhc_desktop_backend.api._user_context import llm_extra_headers
from mhc_desktop_backend.llm.factory import build_provider
from mhc_desktop_backend.protocol_models import Provider


class _Recorder:
    """Fake OpenAILLMProvider — keeps the kwargs build_provider passed."""

    def __init__(self, **kw):
        _Recorder.seen = kw


@pytest.mark.asyncio
async def test_factory_composes_static_and_dynamic_headers(monkeypatch):
    monkeypatch.setattr(factory_mod, "OpenAILLMProvider", _Recorder)
    provider = Provider(
        name="p",
        api_key="k",
        default_model="m",
        headers={"X-Static": "1"},
    )

    async def dynamic():
        return {"X-Static": "overridden", "X-User": "alice"}

    build_provider(provider, extra_headers_provider=dynamic)

    hp = _Recorder.seen["llm_extra_headers_provider"]
    llm_kwargs = _Recorder.seen.get("llm_kwargs") or {}
    assert "llm_extra_headers_provider" not in llm_kwargs
    assert await hp() == {"X-Static": "overridden", "X-User": "alice"}


@pytest.mark.asyncio
async def test_factory_without_headers_ships_no_provider(monkeypatch):
    monkeypatch.setattr(factory_mod, "OpenAILLMProvider", _Recorder)
    build_provider(Provider(name="p", api_key="k", default_model="m"))
    seen = _Recorder.seen
    assert not seen.get("llm_kwargs")
    assert seen.get("llm_extra_headers_provider") is None


@pytest.mark.asyncio
async def test_anthropic_path_ships_headers_provider(monkeypatch):
    monkeypatch.setattr(factory_mod, "AnthropicLLMProvider", _Recorder)

    class _FakeClient:
        def __init__(self, **kw):
            pass

    monkeypatch.setattr(factory_mod, "AsyncAnthropic", _FakeClient)
    build_provider(
        Provider(
            name="p",
            api_key="k",
            default_model="m",
            provider_type="anthropic",
            headers={"X-Static": "1"},
        )
    )
    hp = _Recorder.seen["llm_extra_headers_provider"]
    assert await hp() == {"X-Static": "1"}


@pytest.mark.asyncio
async def test_request_closure_passes_resolved_user():
    app = FastAPI()
    captured = {}

    async def factory(user):
        captured["user"] = user
        return {"X-User": user.username if user else ""}

    app.state.llm_extra_headers_provider = factory
    req = Request({"type": "http", "method": "POST", "path": "/api/v1/chat", "app": app})
    req.state.user = type("U", (), {"username": "alice"})()

    provider = llm_extra_headers(req)
    assert await provider() == {"X-User": "alice"}
    assert captured["user"].username == "alice"


@pytest.mark.asyncio
async def test_request_closure_none_when_deploy_not_wired():
    app = FastAPI()
    req = Request({"type": "http", "method": "POST", "path": "/api/v1/chat", "app": app})
    assert llm_extra_headers(req) is None