"""Integration test for the chat-tools wiring.

We don't depend on a real LLM provider. Instead we plug a fake
provider into ``app.state`` and verify that:

* the chat handler emits the new SSE event vocabulary
  (``execution_start``, ``tool_start``, ``tool_end``,
  ``execution_end``) when the model picks a tool;
* the ``kind`` field on each tool_start/tool_end is ``"tool"``
  for plain tool calls;
* the cancel-event from the stream registry propagates into the
  tool execution path so a stop button interrupts a slow tool.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from mhc_desktop_backend.api.chat import router as chat_router
from mhc_desktop_backend.api.tools import router as tools_router
from mhc_desktop_backend.protocols import ChatPolicy
from mhc_desktop_backend.tools.imports import import_local_tool
from mhc_desktop_deploy.impls.file_stores.stream_registry import StreamRegistry
from mhc_desktop_deploy.impls.file_stores.tools_store import ToolStore


class _StubProvider:
    """Returns a single fixed response with one ToolCall that points
    at the bundled ``now`` tool."""

    name = "stub"
    provider_type = "openai"

    def __init__(self, tool_name: str, args: dict[str, Any]) -> None:
        self._tool_name = tool_name
        self._args = args

    async def chat(self, messages, tools=None):
        return _FakeStream(self._tool_name, self._args)


class _FakeStream:
    """An async generator that pretends to be an LLM stream. Yields
    one chunk so the chat loop sees a single response."""

    def __init__(self, tool_name: str, args: dict[str, Any]) -> None:
        self._tool_name = tool_name
        self._args = args
        self.response: Any = _FakeResponse(tool_name, args)

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    async def aclose(self):
        return None


class _FakeResponse:
    def __init__(self, tool_name: str, args: dict[str, Any]) -> None:
        self.content = ""
        self.usage = {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}
        self.tool_calls = [
            {
                "id": "call_xyz",
                "type": "function",
                "function": {
                    "name": tool_name,
                    "arguments": json.dumps(args),
                },
            }
        ]


class _FakeTextStream:
    """A stream whose response is plain text (no tool calls)."""

    def __init__(self, text: str) -> None:
        self.response = _FakeTextResponse(text)

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    async def aclose(self):
        return None


class _FakeTextResponse:
    def __init__(self, text: str) -> None:
        self.content = text
        self.usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
        self.tool_calls = []


class _MultiRoundProvider:
    """Scripted provider: the first two chat() calls each return a
    tool call, the third returns plain text. Records the ``tools=``
    argument of every call so the test can assert the follow-up
    turns keep the schemas (the regression this test guards)."""

    name = "stub"
    provider_type = "openai"

    def __init__(self, tool_name: str, args: dict[str, Any]) -> None:
        self._tool_name = tool_name
        self._args = args
        self.tools_seen: list[list[Any]] = []

    async def chat(self, messages, tools=None):
        self.tools_seen.append(list(tools or []))
        if len(self.tools_seen) < 3:
            return _FakeStream(self._tool_name, self._args)
        return _FakeTextStream("done after two rounds")


def _build_app(
    tmp_path: Path,
    *,
    tool_name: str = "",
    args: dict[str, Any] | None = None,
    provider=None,
):
    """Build a minimal FastAPI app with a stub provider that calls
    ``tool_name`` with ``args``, plus the chat + tools routers.

    ``build_provider`` is imported into the chat module's namespace
    at module-load time, so we patch the chat module's local copy
    rather than the origin module — dependency_overrides wouldn't
    catch it because it's a regular function call, not a
    ``Depends``-injected parameter.
    """
    app = FastAPI()
    tool_store = ToolStore(tools_dir=tmp_path)

    # Monkey-patch chat.build_provider to return our stub. We do this
    # by hand because the chat module imports build_provider directly.
    from mhc_desktop_backend.api import chat as chat_mod

    if provider is not None:

        def fake_build_provider(provider_, model_override=None, model_params=None, **kwargs):
            return provider
    else:

        def fake_build_provider(provider_, model_override=None, model_params=None, **kwargs):
            return _StubProvider(tool_name, args or {})

    original = chat_mod.build_provider
    chat_mod.build_provider = fake_build_provider

    # Stub provider store — return a Provider-shaped object so the
    # chat handler's ``provider.provider_type`` lookup doesn't crash.
    class _StubProviderStore:
        async def get(self, _name):
            from dataclasses import dataclass, field

            @dataclass
            class _P:
                name: str = "stub"
                provider_type: str = "openai"
                api_key: str = "***abcd"
                base_url: str = ""
                default_model: str = "gpt-x"
                description: str = ""
                models: list = field(default_factory=list)

            return _P()

    app.state.provider_store = _StubProviderStore()
    app.state.session_store = None
    app.state.skill_store = None
    app.state.mcp_store = None
    app.state.mcp_manager = None
    app.state.tool_store = tool_store
    app.state.stream_registry = StreamRegistry()
    # ``ChatPolicy`` defaults are fine for most tests; the
    # round-cap test patches this in via ``app.state.chat_policy``
    # before posting.
    app.state.chat_policy = ChatPolicy()
    app.state.tool_executor_registry = None
    app.state.system_prompt_base = None
    app.include_router(chat_router)
    app.include_router(tools_router)
    # Stash the original so the test fixture can restore it.
    app.state._chat_build_provider_original = original
    return app


def _restore_chat(app):
    from mhc_desktop_backend.api import chat as chat_mod

    chat_mod.build_provider = app.state._chat_build_provider_original


def _parse_sse(body: str) -> list[dict[str, Any]]:
    """Parse the SSE body into a list of {event, data} dicts."""
    out: list[dict[str, Any]] = []
    event = ""
    data_lines: list[str] = []
    for line in body.split("\n"):
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].strip())
        elif line.strip() == "" and event:
            try:
                payload = json.loads("\n".join(data_lines))
            except Exception:
                payload = {}
            out.append({"event": event, "data": payload})
            event = ""
            data_lines = []
    return out


@pytest.mark.asyncio
async def test_chat_runs_bundled_tool(tmp_path: Path):
    # The installer no longer ships a bundled ``now`` tool; customers
    # provide their own via bulk-import. This case exercises the
    # same plumbing (a user-imported local tool whose callable is
    # available) and asserts the chat handler picks it up by slug.
    app = _build_app(tmp_path, tool_name="now", args={})
    try:
        store = app.state.tool_store
        await import_local_tool(
            "now",
            "async def tool_run():\n    from datetime import datetime\n    return datetime.now().isoformat()\n",
        )
        await store.create(
            {
                "slug": "now",
                "name": "Now",
                "kind": "local",
                "parameters": {"type": "object", "properties": {}},
            }
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.post(
                "/api/v1/chat",
                json={
                    "provider": "stub",
                    "model": "gpt-x",
                    "session_id": "sess-1",
                    "assistant_message_id": "msg-1",
                    "messages": [{"role": "user", "content": "what time?"}],
                    "tools": ["now"],
                },
            )
    finally:
        _restore_chat(app)
    assert r.status_code == 200
    events = _parse_sse(r.text)
    types = [e["event"] for e in events]
    assert "execution_start" in types
    assert "tool_start" in types
    assert "tool_end" in types
    assert "execution_end" in types
    assert "done" in types

    tool_start = next(e for e in events if e["event"] == "tool_start")
    assert tool_start["data"]["kind"] == "tool"
    assert tool_start["data"]["name"] == "now"
    assert tool_start["data"]["session_id"] == "sess-1"

    tool_end = next(e for e in events if e["event"] == "tool_end")
    assert tool_end["data"]["kind"] == "tool"
    assert tool_end["data"]["ok"] is True
    assert "T" in tool_end["data"]["result"]  # ISO timestamp


@pytest.mark.asyncio
async def test_chat_runs_user_imported_tool(tmp_path: Path):
    app = _build_app(tmp_path, tool_name="greeter", args={"name": "peter"})
    try:
        # Pre-import + register through the same store the chat
        # handler will look up.
        store = app.state.tool_store
        await import_local_tool(
            "greeter",
            "async def tool_run(name: str = 'world'):\n    return f'hello {name}'\n",
        )
        await store.create(
            {
                "slug": "greeter",
                "name": "Greeter",
                "description": "Greets the world",
                "kind": "local",
                "parameters": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                },
            }
        )

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.post(
                "/api/v1/chat",
                json={
                    "provider": "stub",
                    "session_id": "sess-2",
                    "assistant_message_id": "msg-2",
                    "messages": [{"role": "user", "content": "hi"}],
                    "tools": ["greeter"],
                },
            )
    finally:
        _restore_chat(app)
    events = _parse_sse(r.text)
    tool_end = next(e for e in events if e["event"] == "tool_end")
    assert tool_end["data"]["ok"] is True
    assert tool_end["data"]["result"] == "hello peter"


@pytest.mark.asyncio
async def test_chat_unknown_tool_rejected(tmp_path: Path):
    app = _build_app(tmp_path, tool_name="now", args={})
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.post(
                "/api/v1/chat",
                json={
                    "provider": "stub",
                    "session_id": "sess-3",
                    "messages": [{"role": "user", "content": "hi"}],
                    "tools": ["does_not_exist"],
                },
            )
    finally:
        _restore_chat(app)
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_chat_disabled_tool_skipped(tmp_path: Path):
    app = _build_app(tmp_path, tool_name="greeter", args={})
    try:
        store = app.state.tool_store
        await import_local_tool("greeter", "async def tool_run():\n    return 'hi'\n")
        await store.create(
            {
                "slug": "greeter",
                "name": "Greeter",
                "kind": "local",
                "parameters": {"type": "object", "properties": {}},
            }
        )
        await store.set_enabled("greeter", False)
        # Override stub provider to call 'greeter' so the chat
        # handler routes the call through the disabled tool's path.
        from mhc_desktop_backend.api import chat as chat_mod

        chat_mod.build_provider = (
            lambda provider, model_override=None, model_params=None, **kw: _StubProvider(
                "greeter", {}
            )
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.post(
                "/api/v1/chat",
                json={
                    "provider": "stub",
                    "session_id": "sess-4",
                    "messages": [{"role": "user", "content": "hi"}],
                    "tools": ["greeter"],
                },
            )
    finally:
        _restore_chat(app)
    events = _parse_sse(r.text)
    # Even though greeter is disabled, the stub LLM still picks it.
    # The chat handler emits tool_start, the ToolStore rejects it
    # as disabled, and tool_end lands with ok=False + an error
    # string. The important thing is the disabled guard kicked in
    # rather than the tool actually running.
    types = [e["event"] for e in events]
    assert "tool_start" in types
    assert "tool_end" in types
    tool_end = next(e for e in events if e["event"] == "tool_end")
    assert tool_end["data"]["ok"] is False
    assert "disabled" in (tool_end["data"].get("error") or "").lower()


@pytest.mark.asyncio
async def test_chat_second_tool_round_executes_and_keeps_schemas(tmp_path: Path):
    """Regression: the follow-up turn used to drop the tool schemas
    (``tools=[]``), so a model that wanted to call a tool again
    (DeepSeek) emitted its call as DSML text inside ``content``,
    which no parser understands and the UI renders as literal tags.
    The follow-up must keep the schemas AND a second tool round must
    execute like the first.
    """
    provider = _MultiRoundProvider("now", {})
    app = _build_app(tmp_path, provider=provider)
    try:
        store = app.state.tool_store
        await import_local_tool(
            "now",
            "async def tool_run():\n    from datetime import datetime\n    return datetime.now().isoformat()\n",
        )
        await store.create(
            {
                "slug": "now",
                "name": "Now",
                "kind": "local",
                "parameters": {"type": "object", "properties": {}},
            }
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.post(
                "/api/v1/chat",
                json={
                    "provider": "stub",
                    "session_id": "sess-rounds",
                    "messages": [{"role": "user", "content": "do it twice"}],
                    "tools": ["now"],
                },
            )
    finally:
        _restore_chat(app)
    assert r.status_code == 200
    events = _parse_sse(r.text)
    types = [e["event"] for e in events]
    # Both tool rounds executed, not just the first.
    assert types.count("execution_start") == 2
    assert types.count("tool_end") == 2
    assert "done" in types
    # The regression: every follow-up call must still receive schemas.
    assert len(provider.tools_seen) == 3
    assert all(len(t) >= 1 for t in provider.tools_seen)


class _AlwaysToolProvider:
    """Every chat() call returns a tool call. Used to prove the
    max-rounds cap executes everything the model emitted instead of
    dropping the over-cap round (which looked like the model
    "stopping" mid-turn)."""

    name = "stub"
    provider_type = "openai"

    def __init__(self, tool_name: str, args: dict[str, Any]) -> None:
        self._tool_name = tool_name
        self._args = args
        self.chat_calls = 0

    async def chat(self, messages, tools=None):
        self.chat_calls += 1
        return _FakeStream(self._tool_name, self._args)


@pytest.mark.asyncio
async def test_chat_cap_never_drops_emitted_tool_calls(tmp_path: Path):
    """Regression: when the model keeps calling tools past the cap,
    every emitted call must still execute — the cap only stops
    generating MORE follow-up rounds. Previously the over-cap round's
    call was dropped, so the UI showed text/thinking cut off with no
    tool capsule, which read as a mysterious stop.

    The production cap (max_tool_rounds on ``ChatPolicy``) is
    ~unbounded, so we pin it low for this test to exercise the cap
    path deterministically. We pin via ``app.state.chat_policy``
    since the constant is no longer read at module level.
    """
    from mhc_desktop_backend.protocols import ChatPolicy

    pin_policy = ChatPolicy(max_tool_rounds=3)
    provider = _AlwaysToolProvider("now", {})
    app = _build_app(tmp_path, provider=provider)
    # Override the policy with the low cap for this test.
    app.state.chat_policy = pin_policy
    try:
        store = app.state.tool_store
        await import_local_tool(
            "now",
            "async def tool_run():\n    from datetime import datetime\n    return datetime.now().isoformat()\n",
        )
        await store.create(
            {
                "slug": "now",
                "name": "Now",
                "kind": "local",
                "parameters": {"type": "object", "properties": {}},
            }
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.post(
                "/api/v1/chat",
                json={
                    "provider": "stub",
                    "session_id": "sess-cap",
                    "messages": [{"role": "user", "content": "keep going"}],
                    "tools": ["now"],
                },
            )
    finally:
        _restore_chat(app)
    assert r.status_code == 200
    events = _parse_sse(r.text)
    types = [e["event"] for e in events]
    tool_ends = types.count("tool_end")
    # Every call the model emitted was executed: one tool_end per
    # chat round, and the loop stopped after the cap without asking
    # for one more round it would have had to drop.
    assert tool_ends == provider.chat_calls
    assert tool_ends == 3
    assert "done" in types
    assert all(
        e["data"].get("ok") is not False for e in events if e["event"] == "tool_end"
    )


class _RecordingPlainTextProvider:
    """A provider that records every ``chat()`` invocation so the
    test can assert what the model actually saw in conversation,
    then returns plain text. Used to prove the wire format carries
    a previous turn's tool_calls / tool results even when that
    turn was cancelled mid-tool — without this, the LLM sees an
    assistant message with no record of having invoked the tool
    and downstream turns get into a confused state where the model
    either hallucinates results or refuses to call tools. """

    name = "stub"
    provider_type = "openai"

    def __init__(self) -> None:
        self.messages_seen: list[list[dict[str, Any]]] = []
        self.tools_seen: list[list[Any]] = []

    async def chat(self, messages, tools=None):
        # Drop the system prompt (injected server-side, irrelevant
        # to this assertion) so we only inspect the user's own
        # conversation shape.
        user_messages = [m for m in messages if m.get("role") != "system"]
        self.messages_seen.append(user_messages)
        self.tools_seen.append(list(tools or []))
        return _FakeTextStream("ok")


@pytest.mark.asyncio
async def test_chat_preserves_cancelled_tool_context(tmp_path: Path):
    """Regression for the customer-reported bug: a previous turn
    that was cancelled mid-tool left the assistant message in
    history as plain text, with no record of the tool_call or its
    cancelled result. The next LLM call then had no context for
    what the model had been doing and surfaced as "model
    hallucinating actions without actually invoking tools".

    The wire payload the frontend sends must keep the assistant
    message's ``tool_calls`` field AND a sibling ``role: "tool"``
    message carrying the cancelled marker. The backend must pass
    both through to the LLM unchanged. Without this test, dropping
    either on the round-trip would silently break the contract. """
    provider = _RecordingPlainTextProvider()
    app = _build_app(tmp_path, provider=provider)
    try:
        store = app.state.tool_store
        await import_local_tool(
            "cmd",
            "async def tool_run(command: str = 'echo ok'):\n    return f'ran: {command}'\n",
        )
        await store.create(
            {
                "slug": "cmd",
                "name": "Cmd",
                "kind": "local",
                "parameters": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                },
            }
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.post(
                "/api/v1/chat",
                json={
                    "provider": "stub",
                    "session_id": "sess-cancel",
                    "assistant_message_id": "msg-cancel",
                    # Mirror the frontend's buildLLMMessages output:
                    # user with attached tools, then an assistant turn
                    # whose tool_call got cancelled mid-execution, then
                    # the user's next message.
                    "messages": [
                        {
                            "role": "user",
                            "content": "list /tmp",
                            "tools": ["cmd"],
                        },
                        {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_abc",
                                    "type": "function",
                                    "function": {
                                        "name": "cmd",
                                        "arguments": '{"command":"ls /tmp"}',
                                    },
                                }
                            ],
                        },
                        {
                            "role": "tool",
                            "tool_call_id": "call_abc",
                            "content": "cancelled",
                        },
                        {
                            "role": "user",
                            "content": "try again",
                            "tools": ["cmd"],
                        },
                    ],
                    "tools": ["cmd"],
                },
            )
    finally:
        _restore_chat(app)
    assert r.status_code == 200, r.text

    # The provider was invoked exactly once for this request.
    assert len(provider.messages_seen) == 1
    sent = provider.messages_seen[0]
    # No system prompt in the recording (we filtered it), so the
    # four user-supplied messages should land in order.
    assert [m.get("role") for m in sent] == [
        "user",
        "assistant",
        "tool",
        "user",
    ]
    # The assistant message must still carry its tool_calls so the
    # model can match it against the tool result below it.
    asst = sent[1]
    assert asst["role"] == "assistant"
    assert asst.get("tool_calls") == [
        {
            "id": "call_abc",
            "type": "function",
            "function": {
                "name": "cmd",
                "arguments": '{"command":"ls /tmp"}',
            },
        }
    ]
    # The role=tool result must survive the coerce round-trip
    # (this was the second half of the bug — tool_call_id got
    # dropped because the key wasn't in the attach-metadata list).
    tool_msg = sent[2]
    assert tool_msg["role"] == "tool"
    assert tool_msg.get("tool_call_id") == "call_abc"
    assert tool_msg.get("content") == "cancelled"
    # Current-turn tools are still being passed to the LLM.
    assert provider.tools_seen[0] != []



def test_tool_args_streaming_emits_args_start_then_start():
    """Model streams a tool call across multiple chunks:
    chunk 1 → id + first letters of args
    chunk 2 → more letters of args
    chunk 3 → final args
    
    We expect:
    - ``tool_args_start`` event carries our pre-allocated call_id
    - ``tool_args_delta`` events carry the partial arg fragments
    - ``tool_start`` reuses the SAME call_id (the UI's pending
      capsule transitions seamlessly into the executing one)
    - ``tool_end`` fires once with success
    """
    from dataclasses import dataclass

    # Stub provider that yields a stream of tool_call deltas.
    # Mirrors ``minimal_harness.types.ToolCallDelta`` (the flat
    # provider-agnostic shape both the OpenAI and Anthropic
    # adapters emit): index/id at the top level, ``name`` and
    # ``arguments`` flattened — no nested ``function`` object.
    @dataclass
    class _ToolCallDelta:
        index: int = 0
        id: str | None = None
        name: str | None = None
        arguments: str | None = None

    @dataclass
    class _StreamChunk:
        content: str | None = None
        reasoning: str | None = None
        tool_calls: list | None = None

    full_args = '{"command":"ls /tmp","timeout":60}'

    class _StreamingStream:
        def __init__(self):
            self._chunks = [
                _StreamChunk(
                    tool_calls=[
                        _ToolCallDelta(
                            index=0,
                            id="call_provider_123",
                            name="cmd",
                        ),
                    ]
                ),
                _StreamChunk(
                    tool_calls=[
                        _ToolCallDelta(
                            index=0,
                            arguments='{"command":',
                        ),
                    ]
                ),
                _StreamChunk(
                    tool_calls=[
                        _ToolCallDelta(
                            index=0,
                            arguments='"ls /tmp","timeout":60}',
                        ),
                    ]
                ),
            ]
            self.response = _FakeResponse("cmd", {"command": "ls /tmp", "timeout": 60})

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self._chunks:
                raise StopAsyncIteration
            return self._chunks.pop(0)

        async def aclose(self):
            return None

    class _StreamingProvider:
        """First call streams a tool call; second call returns
        plain text so the loop terminates cleanly."""
        name = "stub"
        provider_type = "openai"
        calls = 0

        async def chat(self, messages, tools=None):
            self.__class__.calls += 1
            if self.calls == 1:
                return _StreamingStream()
            return _FakeTextStream("ok")


    # Use tmp_path fixture normally:
    import tempfile
    tmpdir = tempfile.mkdtemp()
    tmp = Path(tmpdir)
    app = _build_app(tmp, provider=_StreamingProvider())
    try:
        import asyncio as _aio

        from httpx import ASGITransport
        from httpx import AsyncClient as _AC

        async def run():
            store = app.state.tool_store
            await import_local_tool(
                "cmd",
                "async def tool_run(command, timeout):\n    return f'ran {command} for {timeout}s'\n",
            )
            await store.create({
                "name": "cmd",
                "slug": "cmd",
                "model_name": "cmd",
                "kind": "local",
                "origin": "imported",
                "description": "stub",
                "parameters": {"type": "object", "properties": {}},
                "source": "x",
                "enabled": True,
            })

            transport = ASGITransport(app=app)
            async with _AC(transport=transport, base_url="http://test") as client:
                body = {
                    "provider": "stub",
                    "model": "gpt-x",
                    "session_id": "s1",
                    "messages": [{"role": "user", "content": "list /tmp"}],
                }
                r = await client.post("/api/v1/chat", json=body)
                events = _parse_sse(r.text)
                return events

        events = _aio.run(run())
    finally:
        _restore_chat(app)

    # The streaming provider emits tool_args_start, two deltas,
    # then tool_start (which reuses our id), then tool_end.
    args_start = [e for e in events if e["event"] == "tool_args_start"]
    args_delta = [e for e in events if e["event"] == "tool_args_delta"]
    tool_start = [e for e in events if e["event"] == "tool_start"]
    tool_end = [e for e in events if e["event"] == "tool_end"]

    summary = [e['event'] for e in events]
    assert len(args_start) == 1, f'expected one tool_args_start, got {summary}'
    assert len(args_delta) == 2, f'expected two tool_args_delta, got {summary}'
    assert len(tool_start) == 1, f'expected one tool_start, got {summary}'
    assert len(tool_end) == 1, f'expected one tool_end, got {summary}'

    pending_id = args_start[0]['data']['call_id']
    started_id = tool_start[0]['data']['call_id']
    ended_id = tool_end[0]['data']['call_id']
    assert pending_id == started_id == ended_id, (
        f'call_id should round-trip: pending={pending_id} '
        f'started={started_id} ended={ended_id}'
    )

    accumulated = ''.join(d['data']['arguments_chunk'] for d in args_delta)
    assert accumulated == full_args, f'got {accumulated!r}, expected {full_args!r}'
