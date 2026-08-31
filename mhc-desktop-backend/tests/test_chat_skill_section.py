"""Tests for the per-request ``## Skills`` system-prompt section and
the built-in ``load_skill`` tool.

What's covered:

* ``_build_skill_section`` renders the per-request enabled-skills
  listing with name + description and the slug the LLM needs to
  call ``load_skill(slug=...)``.
* ``_build_system_prompt(..., enabled_skills=...)`` appends the
  skill section between the base and the user addition.
* The chat endpoint builds the same per-request listing from the
  live ``skill_store`` — re-read on every request, so toggling a
  skill's enabled flag in the configuration page takes effect on
  the very next message (without a backend restart).
* The built-in ``load_skill`` tool is registered in the catalog on
  app startup and exposes a correct schema; calling it records a
  ``ToolCallRecord(kind="skill", name=<slug>)`` so the dashboard's
  "技能使用排名" rolls up actual model invocations, not user
  attachments.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from mhc_desktop_backend.api.chat import (
    _build_skill_section,
    _build_system_prompt,
)
from mhc_desktop_backend.api.chat import (
    router as chat_router,
)
from mhc_desktop_backend.skills.models import Skill
from mhc_desktop_backend.tools.builtin import (
    BUILTIN_LOAD_SKILL,
    ensure_builtin_tools,
)

# ── _build_skill_section (unit) ─────────────────────────────────────────────


def test_section_empty_when_no_enabled_skills() -> None:
    assert _build_skill_section([]) == ""
    # Disabled skills also produce nothing — the section is per-request
    # *enabled* skills, not "all installed skills".
    assert _build_skill_section(
        [Skill(slug="x", name="X", description="d", enabled=False)]
    ) == ""


def test_section_renders_name_slug_description() -> None:
    out = _build_skill_section(
        [
            Skill(
                slug="file-helper",
                name="File Helper",
                description="Read / write files in a sandbox.",
                enabled=True,
            ),
        ]
    )
    assert "## Skills" in out
    assert "load_skill" in out
    # Slug is surfaced so the model knows what to pass to load_skill
    # without having to guess from the human label.
    assert "`file-helper`" in out
    assert "**File Helper**" in out
    assert "Read / write files in a sandbox." in out


def test_section_multiple_bullets() -> None:
    out = _build_skill_section(
        [
            Skill(slug="a", name="A", description="alpha", enabled=True),
            Skill(slug="b", name="B", description="beta", enabled=True),
        ]
    )
    assert out.count("- **") == 2
    assert "`a`" in out and "`b`" in out


def test_section_tolerates_dict_shape() -> None:
    """The chat endpoint passes Skill dataclasses from the live
    store, but tests / future callers may pass plain dicts.
    Either shape must render the same bullet."""
    out = _build_skill_section(
        [
            {
                "slug": "x",
                "name": "X",
                "description": "x desc",
                "enabled": True,
            }
        ]
    )
    assert "`x`" in out
    assert "**X**" in out
    assert "x desc" in out


def test_section_escapes_triple_backticks() -> None:
    """A skill that documents itself with a fenced code block
    shouldn't break the section's bullet formatting. We replace
    triple-backticks with a zero-width-space variant so the
    markdown stays valid."""
    out = _build_skill_section(
        [
            Skill(
                slug="x",
                name="X",
                description="use ```python\nx = 1\n```",
                enabled=True,
            )
        ]
    )
    # Triple-backticks are no longer consecutive; they can't break
    # the section's outer formatting.
    assert "```python" in out
    assert "```\n" not in out  # the closing fence from the skill desc
    # …but the rendered description still contains the actual code.
    assert "x = 1" in out


def test_section_skips_disabled_inside_mixed_list() -> None:
    out = _build_skill_section(
        [
            Skill(slug="on", name="On", description="on", enabled=True),
            Skill(slug="off", name="Off", description="off", enabled=False),
        ]
    )
    assert "**On**" in out
    assert "**Off**" not in out


# ── _build_system_prompt with enabled_skills ────────────────────────────────


def test_system_prompt_includes_skills_section() -> None:
    out = _build_system_prompt(
        "",
        enabled_skills=[
            Skill(slug="s1", name="S1", description="d1", enabled=True),
        ],
    )
    # Base is empty; the skill section IS the only header. No
    # user addition (empty → no divider).
    assert "## Skills" in out
    assert "`s1`" in out
    assert "# User-specified system prompt" not in out
    # The base no longer leaks the skill location either.
    assert "~/.mhc-desktop" not in out


def test_system_prompt_skill_section_between_addition_and_empty_base() -> None:
    """Order matters: skill section first (so the model sees
    skill context before any user instructions), then the
    user-specified addition. The kernel base is empty so the
    only thing above ``## Skills`` is whatever the deploy's
    system_prompt_base override injects (None → empty here)."""
    out = _build_system_prompt(
        "be concise",
        enabled_skills=[
            Skill(slug="s", name="S", description="d", enabled=True),
        ],
    )
    skills_idx = out.find("## Skills")
    addition_idx = out.find("# User-specified system prompt")
    # skills section must come before the user addition.
    assert skills_idx < addition_idx
    # And before "be concise" (the user addition's body).
    assert skills_idx < out.find("be concise")


def test_system_prompt_no_enabled_skills_omits_section() -> None:
    """A user with zero enabled skills shouldn't see a dangling
    '## Skills' heading. Same goes for the divider."""
    out = _build_system_prompt("")
    assert "## Skills" not in out
    assert "load_skill" not in out


def test_system_prompt_disabled_skills_excluded() -> None:
    out = _build_system_prompt(
        "",
        enabled_skills=[
            Skill(slug="off", name="Off", description="off", enabled=False),
        ],
    )
    assert "## Skills" not in out


# ── Built-in tool: schema + binding ─────────────────────────────────────────


def test_load_skill_catalog_entry_is_well_formed() -> None:
    """Pin the schema so a kernel-side edit can't silently break
    the LLM contract."""
    meta = BUILTIN_LOAD_SKILL
    assert meta["slug"] == "load_skill"
    assert meta["model_name"] == "load_skill"
    assert meta["kind"] == "local"
    assert meta["origin"] == "bundled"
    assert meta["enabled"] is True
    params = meta["parameters"]
    assert params["type"] == "object"
    assert "slug" in params["properties"]
    assert params["properties"]["slug"]["type"] == "string"
    assert params["required"] == ["slug"]


def test_load_skill_tool_run_reads_bound_store(tmp_path: Path) -> None:
    """Bind a fake store, call tool_run with a known slug, get the
    body back. The cache is process-local, so we set + reset around
    the test to avoid leaking state."""
    from mhc_desktop_backend.tools.builtin import load_skill as _builtin

    class _FakeStore:
        async def get_body(self, slug: str):
            return {"alpha": "# alpha body\n", "beta": "# beta body\n"}.get(slug)

    prev = _builtin._skill_store
    try:
        _builtin.set_skill_store(_FakeStore())
        import asyncio

        async def _go():
            out = []
            async for chunk in _builtin.tool_run(slug="alpha"):
                out.append(chunk)
            return "".join(out)

        assert asyncio.run(_go()) == "# alpha body\n"
    finally:
        _builtin.set_skill_store(prev)


def test_load_skill_tool_run_handles_unknown_slug() -> None:
    """An LLM that guesses a wrong slug should get a clear
    not-found message rather than a stack trace. The handler can
    then decide to retry or ask the user for the right name."""
    from mhc_desktop_backend.tools.builtin import load_skill as _builtin

    class _EmptyStore:
        async def get_body(self, slug: str):
            return None

    prev = _builtin._skill_store
    try:
        _builtin.set_skill_store(_EmptyStore())
        import asyncio

        async def _go():
            out = []
            async for chunk in _builtin.tool_run(slug="nope"):
                out.append(chunk)
            return "".join(out)

        assert asyncio.run(_go()) == "(skill 'nope' not found)"
    finally:
        _builtin.set_skill_store(prev)


def test_load_skill_tool_run_handles_missing_store() -> None:
    """If the kernel somehow didn't call ``set_skill_store`` (a
    configuration mistake, not a user error), the tool surfaces
    a clear message rather than crashing the chat stream."""
    from mhc_desktop_backend.tools.builtin import load_skill as _builtin

    prev = _builtin._skill_store
    try:
        _builtin.set_skill_store(None)
        import asyncio

        async def _go():
            out = []
            async for chunk in _builtin.tool_run(slug="x"):
                out.append(chunk)
            return "".join(out)

        msg = asyncio.run(_go())
        assert "not initialised" in msg
    finally:
        _builtin.set_skill_store(prev)


def test_load_skill_tool_run_appends_source_path_note(tmp_path: Path) -> None:
    """The body should be followed by a footer pointing the model
    at the skill's on-disk location so relative paths in the
    skill body can be resolved."""
    from mhc_desktop_backend.tools.builtin import load_skill as _builtin

    class _FakeStore:
        async def get_body(self, slug: str):
            return "# alpha body\n"

        async def get(self, slug: str):
            return type("S", (), {"path": str(tmp_path / "alpha")})()

    prev = _builtin._skill_store
    try:
        _builtin.set_skill_store(_FakeStore())
        import asyncio

        async def _go():
            out = []
            async for chunk in _builtin.tool_run(slug="alpha"):
                out.append(chunk)
            return "".join(out)

        result = asyncio.run(_go())
        assert result.startswith("# alpha body\n")
        assert "dedicated storage location is" in result
        assert str(tmp_path / "alpha") in result
    finally:
        _builtin.set_skill_store(prev)


def test_load_skill_tool_run_skips_note_when_get_fails() -> None:
    """Older / read-only skill sources may not implement ``get``.
    The body must still come back without the note."""
    from mhc_desktop_backend.tools.builtin import load_skill as _builtin

    class _BodyOnlyStore:
        async def get_body(self, slug: str):
            return "# alpha body\n"

    prev = _builtin._skill_store
    try:
        _builtin.set_skill_store(_BodyOnlyStore())
        import asyncio

        async def _go():
            out = []
            async for chunk in _builtin.tool_run(slug="alpha"):
                out.append(chunk)
            return "".join(out)

        assert asyncio.run(_go()) == "# alpha body\n"
    finally:
        _builtin.set_skill_store(prev)


# ── ensure_builtin_tools registers in tool_store ────────────────────────────


class _InMemoryToolStore:
    """Minimal in-memory ToolStore-shaped object that satisfies the
    contract ``ensure_builtin_tools`` consumes (``get``, ``create``,
    ``update``, ``set_enabled``). We don't pull in the file-backed
    store to keep the test hermetic — a test that boots a real
    ToolStore would also work but is slower and pulls disk."""

    def __init__(self) -> None:
        self._rows: dict[str, dict[str, Any]] = {}
        self._dir = None

    async def get(self, slug: str):
        row = self._rows.get(slug)
        if row is None:
            return None
        return _StoredTool(**row)

    async def create(self, data: dict[str, Any]):
        if data["slug"] in self._rows:
            raise ValueError("already exists")
        self._rows[data["slug"]] = dict(data)
        return _StoredTool(**self._rows[data["slug"]])

    async def update(self, slug: str, data: dict[str, Any]):
        if slug not in self._rows:
            raise ValueError("not found")
        self._rows[slug].update(data)
        return _StoredTool(**self._rows[slug])

    async def set_enabled(self, slug: str, enabled: bool):
        self._rows[slug]["enabled"] = enabled
        return _StoredTool(**self._rows[slug])


class _StoredTool:
    """Bare dataclass to mimic the Tool model's attribute access."""

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)

    def resolved_model_name(self) -> str:
        """Mirror :meth:`Tool.resolved_model_name` — fall back to slug."""
        return (self.model_name or "").strip() or self.slug


@pytest.mark.asyncio
async def test_ensure_builtin_tools_registers_load_skill(tmp_path: Path):
    store = _InMemoryToolStore()
    store._dir = tmp_path
    app = FastAPI()
    app.state.skill_store = None  # binding still happens; None is fine
    app.state.tool_store = store

    await ensure_builtin_tools(app)

    row = await store.get("load_skill")
    assert row is not None
    assert row.slug == "load_skill"
    assert row.model_name == "load_skill"
    assert row.kind == "local"
    assert row.origin == "bundled"
    assert row.enabled is True
    # Source persisted to disk so a uvicorn reload can lazy-reimport.
    assert (tmp_path / "load_skill" / "tool.py").is_file()


@pytest.mark.asyncio
async def test_ensure_builtin_tools_respects_user_disable(tmp_path: Path):
    """load_skill is seeded enabled on first boot, but afterwards
    the Tools-page toggle is authoritative — a user who disables
    it must stay disabled across restarts (no force re-enable)."""
    store = _InMemoryToolStore()
    store._dir = tmp_path
    app = FastAPI()
    app.state.skill_store = None
    app.state.tool_store = store

    await ensure_builtin_tools(app)
    row = await store.get("load_skill")
    assert row is not None and row.enabled is True

    # User turns it off in the Tools page, then restarts.
    await store.set_enabled("load_skill", False)
    await ensure_builtin_tools(app)
    row = await store.get("load_skill")
    assert row is not None
    assert row.enabled is False  # user choice preserved


# ── Integration: per-request system prompt with live skill_store ────────────


class _FakeSkillStore:
    def __init__(self, skills: list[Skill]) -> None:
        self._skills = list(skills)

    async def list(self) -> list[Skill]:
        return list(self._skills)

    async def get_body(self, slug: str) -> str | None:
        for s in self._skills:
            if s.slug == slug:
                return s.body
        return None


class _ScriptedProvider:
    name = "stub"
    provider_type = "openai"

    def __init__(self, on_chat):
        self._on_chat = on_chat
        self.calls: list[list[dict[str, Any]]] = []

    async def chat(self, messages, tools=None):
        # Record what the LLM was sent so the test can assert
        # the system-prompt shape on every request.
        self.calls.append(list(messages))
        return await self._on_chat()


class _RecordingMetricsRepo:
    def __init__(self) -> None:
        self.tool_records: list[Any] = []
        self.llm_records: list[Any] = []

    async def record_tool_call(self, record):
        self.tool_records.append(record)

    async def record_llm_call(self, record):
        self.llm_records.append(record)


def _build_chat_app(*, skill_store, provider, tool_store=None) -> FastAPI:
    app = FastAPI()
    app.state.provider_store = _StubProviderStore()
    app.state.session_store = None
    app.state.skill_store = skill_store
    app.state.mcp_store = None
    app.state.mcp_manager = None
    app.state.tool_store = tool_store
    app.state.stream_registry = _FakeRegistry()
    app.state.prefs_store = None
    app.state.chat_policy = None
    app.state.tool_executor_registry = None
    app.state.metrics_repo = _RecordingMetricsRepo()

    # Patch chat.build_provider so the route handler picks up our stub.
    from mhc_desktop_backend.api import chat as chat_mod

    def _fake_build(p, model_override=None, model_params=None, **kwargs):
        return provider

    chat_mod.build_provider = _fake_build
    app.state._chat_build_provider_original = (
        # Restore hook so the test fixture can roll back.
        getattr(chat_mod, "build_provider", None)
    )
    app.include_router(chat_router)
    return app


class _StubProviderStore:
    async def get(self, _name):
        from dataclasses import dataclass

        @dataclass
        class _P:
            name: str = "stub"
            provider_type: str = "openai"
            api_key: str = "***"
            base_url: str = ""
            default_model: str = "m"
            description: str = ""

        return _P()


class _FakeRegistry:
    """Stub StreamRegistryProtocol: only ``register`` is called here,
    and we want it to return a stream with a usable cancel event."""

    async def register(self, session_id: str):
        import asyncio

        class _Stream:
            cancel = asyncio.Event()
            done: asyncio.Future = asyncio.get_event_loop().create_future()
            assistant_message_id = ""

        return _Stream()

    async def unregister(self, session_id: str):
        return None


@pytest.mark.asyncio
async def test_chat_request_skill_section_from_live_store(tmp_path: Path):
    """End-to-end: a chat request with two enabled + one disabled
    skill in the store produces a system prompt with the section
    listing only the enabled two. No per-message skill splice."""
    skill_store = _FakeSkillStore(
        [
            Skill(slug="a", name="Alpha", description="d-alpha", enabled=True),
            Skill(slug="b", name="Beta", description="d-beta", enabled=True),
            Skill(slug="c", name="Gamma", description="d-gamma", enabled=False),
        ]
    )

    async def _text_reply():
        class _Resp:
            content = "ok"
            usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
            tool_calls = []
            reasoning = None

        class _Stream:
            response = _Resp()

            def __aiter__(self):
                async def _gen():
                    if False:
                        yield None

                return _gen()

        return _Stream()

    provider = _ScriptedProvider(_text_reply)
    app = _build_chat_app(skill_store=skill_store, provider=provider)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.post(
                "/api/v1/chat",
                json={
                    "provider": "stub",
                    "session_id": "sess-skill-section",
                    "messages": [{"role": "user", "content": "hi"}],
                    # Frontend no longer sends ``skills`` — the body
                    # field is dropped on the floor. Sending it
                    # anyway must not crash.
                    "skills": ["a"],
                },
            )
    finally:
        # Restore the module-level build_provider.
        from mhc_desktop_backend.api import chat as chat_mod

        if hasattr(app.state, "_chat_build_provider_original"):
            chat_mod.build_provider = app.state._chat_build_provider_original

    assert r.status_code == 200, r.text
    assert len(provider.calls) == 1
    sys_msg = provider.calls[0][0]
    assert sys_msg["role"] == "system"
    sys_text = sys_msg["content"]
    # Both enabled skills land in the section …
    assert "`a`" in sys_text and "`b`" in sys_text
    # … disabled does not.
    assert "`c`" not in sys_text
    # The section advertises how to load a body.
    assert "load_skill" in sys_text and "## Skills" in sys_text


@pytest.mark.asyncio
async def test_chat_request_logs_tool_and_skill_counts(tmp_path: Path, caplog):
    """Per-request INFO log carries the tool + skill counts the
    operator sees on the wire. ``load_skill`` is auto-included
    even when the user didn't ask for it."""
    import logging

    caplog.set_level(logging.INFO, logger="mhc_desktop_backend")
    skill_store = _FakeSkillStore(
        [
            Skill(slug="a", name="Alpha", description="d", enabled=True),
        ]
    )

    async def _text_reply():
        class _Resp:
            content = "ok"
            usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
            tool_calls = []
            reasoning = None

        class _Stream:
            response = _Resp()

            def __aiter__(self):
                async def _g():
                    if False:
                        yield None

                return _g()

        return _Stream()

    provider = _ScriptedProvider(_text_reply)
    # Wire a real load_skill tool into the in-memory store so the
    # chat endpoint can auto-resolve it. Without this, the auto-include
    # path is skipped (degenerate "no tool store" deployment), which
    # isn't what we want to assert here.
    from mhc_desktop_backend.tools.imports import import_local_tool

    tool_store = _InMemoryToolStore()
    tool_store._dir = tmp_path
    # Pre-import the builtin callable into the process-local cache so
    # ``build_streaming_tool`` can resolve it without disk access.
    src = (
        Path(__file__).parent.parent
        / "src/mhc_desktop_backend/tools/builtin/load_skill.py"
    ).read_text(encoding="utf-8")
    await import_local_tool("load_skill", src)
    # Persist the source on disk so ``_resolve_callable`` (cache miss
    # path) also works in this test.
    (tmp_path / "load_skill").mkdir(parents=True, exist_ok=True)
    (tmp_path / "load_skill" / "tool.py").write_text(src, encoding="utf-8")
    await tool_store.create(dict(BUILTIN_LOAD_SKILL))

    app = _build_chat_app(
        skill_store=skill_store, provider=provider, tool_store=tool_store
    )

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.post(
                "/api/v1/chat",
                json={
                    "provider": "stub",
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
    finally:
        from mhc_desktop_backend.api import chat as chat_mod
        if hasattr(app.state, "_chat_build_provider_original"):
            chat_mod.build_provider = app.state._chat_build_provider_original

    assert r.status_code == 200
    matches = [r2 for r2 in caplog.records if r2.message.startswith("chat.request ")]
    assert matches, "expected chat.request INFO log line"
    line = matches[-1].getMessage()
    assert "skills_enabled=1" in line
    # load_skill is auto-resolved: count is 1 even though the user
    # didn't send a ``tools`` field.
    assert "tools_resolved=1" in line
    assert "tools_attached=0" in line


# ── load_skill metric recording ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_tool_emits_skill_metric_for_load_skill():
    """A load_skill invocation produces TWO metrics: one
    ``kind="tool"`` (the call itself) and one ``kind="skill"``
    (the loaded slug). This is the contract that powers the
    dashboard's "技能使用排名" — counting by actual model
    invocation, not by per-attachment side-effect.
    """
    from mhc_desktop_backend.api.chat import _record_tool
    from mhc_desktop_backend.metrics.types import ToolCallRecord

    captured: list[ToolCallRecord] = []

    class _Repo:
        async def record_tool_call(self, record):
            captured.append(record)

    import time as _time

    started_at = _time.monotonic() - 0.1  # ~100ms duration
    await _record_tool(
        _Repo(),
        session_id="s1",
        name="load_skill",
        started_at=started_at,
        ok=True,
        args={"slug": "mhc-investor"},
    )
    kinds = sorted(r.kind for r in captured)
    assert kinds == ["skill", "tool"]
    tool_rec = next(r for r in captured if r.kind == "tool")
    skill_rec = next(r for r in captured if r.kind == "skill")
    assert tool_rec.name == "load_skill"
    assert skill_rec.name == "mhc-investor"


@pytest.mark.asyncio
async def test_record_tool_no_skill_metric_for_other_tools():
    """Non-load_skill calls don't pollute the skill counter. The
    skill counter is exclusively for the slug the model actually
    pulled, not for any user-attached skill."""
    from mhc_desktop_backend.api.chat import _record_tool

    captured = []

    class _Repo:
        async def record_tool_call(self, record):
            captured.append(record)

    import time as _time

    await _record_tool(
        _Repo(),
        session_id="s1",
        name="cmd",
        started_at=_time.monotonic(),
        ok=True,
        args={"command": "ls"},
    )
    assert all(r.kind == "tool" for r in captured)
    assert all(r.name != "cmd" or r.kind == "tool" for r in captured)


@pytest.mark.asyncio
async def test_record_tool_skill_metric_skips_empty_slug():
    """A malformed call (e.g. ``load_skill()`` with no slug) must
    not crash the metric path. We record the ``kind="tool"`` line
    (the call happened) but skip the ``kind="skill"`` line (no
    slug to attribute it to)."""
    from mhc_desktop_backend.api.chat import _record_tool

    captured = []

    class _Repo:
        async def record_tool_call(self, record):
            captured.append(record)

    import time as _time

    await _record_tool(
        _Repo(),
        session_id="s1",
        name="load_skill",
        started_at=_time.monotonic(),
        ok=False,
        error="missing slug",
        args={},
    )
    assert len(captured) == 1
    assert captured[0].kind == "tool"
    assert captured[0].name == "load_skill"

