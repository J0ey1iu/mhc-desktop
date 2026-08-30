"""Integration test: reasoning content flows to the UI.

Providers like DeepSeek-R1 stream ``reasoning_content`` deltas before
the reply text. The chat handler must emit those as a ``reasoning``
SSE event (in chronological position, i.e. before the ``chunk`` that
follows) so the frontend can render a thinking block.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from mhc_desktop_backend.api.chat import router as chat_router
from mhc_desktop_deploy.impls.file_stores.stream_registry import StreamRegistry


class _ReasonChunk:
    """One LLM delta; mimics LLMChunkDelta (content/reasoning attrs)."""

    def __init__(self, reasoning: str = "", content: str = "") -> None:
        self.reasoning = reasoning or None
        self.content = content or None


class _StubStream:
    def __init__(self, deltas: list[_ReasonChunk]) -> None:
        self._deltas = iter(deltas)
        self.response: Any = _Resp()

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._deltas)
        except StopIteration:
            raise StopAsyncIteration

    async def aclose(self):
        return None


class _Resp:
    content = "done"
    reasoning_content = "I reasoned"
    tool_calls: list[Any] = []
    usage = {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}
    finish_reason: str | None = "stop"


class _StubProvider:
    name = "stub"
    provider_type = "openai"

    def __init__(self, deltas: list[_ReasonChunk]) -> None:
        self._deltas = deltas

    async def chat(self, messages, tools=None):
        return _StubStream(self._deltas)


def _build_app(tmp_path, deltas: list[_ReasonChunk]) -> FastAPI:
    app = FastAPI()
    app.include_router(chat_router)

    from mhc_desktop_backend.api import chat as chat_mod

    def fake_build_provider(provider, model_override=None, model_params=None, **kwargs):
        return _StubProvider(deltas)

    chat_mod.build_provider = fake_build_provider

    @dataclass
    class _P:
        name: str = "stub"
        provider_type: str = "openai"
        api_key: str = "****"
        base_url: str = ""
        default_model: str = "gpt-x"
        description: str = ""
        models: list = field(default_factory=list)

    class _Store:
        async def get(self, _name):
            return _P()

    app.state.provider_store = _Store()
    app.state.session_store = None
    app.state.skill_store = None
    app.state.mcp_store = None
    app.state.mcp_manager = None
    app.state.tool_store = None
    app.state.stream_registry = StreamRegistry()
    return app


async def _stream_events(app: FastAPI) -> list[dict[str, Any]]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        async with client.stream(
            "POST",
            "/api/v1/chat",
            json={
                "provider": "stub",
                "messages": [{"role": "user", "content": "hi"}],
            },
        ) as resp:
            body = (await resp.aread()).decode()
    events: list[dict[str, Any]] = []
    for line in body.splitlines():
        if line.startswith("event: "):
            events.append({"event": line[7:], "data": ""})
        elif line.startswith("data: ") and events:
            import json

            events[-1]["data"] = json.loads(line[5:])
    return events


@pytest.mark.asyncio
async def test_reasoning_emitted_before_content(tmp_path):
    app = _build_app(
        tmp_path,
        [
            _ReasonChunk(reasoning="Let me think hard about this. ", content=""),
            _ReasonChunk(reasoning="More thinking. ", content="Hello! "),
            _ReasonChunk(reasoning="", content="The answer is 42."),
        ],
    )
    events = await _stream_events(app)
    evs = [e["event"] for e in events]

    assert "reasoning" in evs, f"no reasoning event in {evs}"
    assert "chunk" in evs

    # Reasoning events must come before their adjacent content events.
    reasoning = [e for e in events if e["event"] == "reasoning"]
    joined = "".join(e["data"]["content"] for e in reasoning)
    assert "Let me think hard" in joined and "More thinking" in joined
    # First reasoning precedes first chunk in the stream order.
    assert evs.index("reasoning") < evs.index("chunk")
    # Ordering across the whole sequence: reasoning 0, reasoning 1,
    # then chunk 0, chunk 1.
    seqs = [
        e["data"].get("seq") for e in events if e["event"] in ("reasoning", "chunk")
    ]
    assert seqs == sorted(seqs), f"seq order broken: {seqs}"
    # Every reasoning event carries a session_id like everything else.
    assert all("session_id" in e["data"] for e in events if e["event"] == "reasoning")


@pytest.mark.asyncio
async def test_no_reasoning_no_event(tmp_path):
    app = _build_app(tmp_path, [_ReasonChunk(content="plain reply")])
    events = await _stream_events(app)
    assert not any(e["event"] == "reasoning" for e in events)
    assert any(e["event"] == "chunk" for e in events)
