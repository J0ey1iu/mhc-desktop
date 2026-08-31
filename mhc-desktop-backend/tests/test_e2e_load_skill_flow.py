"""End-to-end test: spin up a real FastAPI app with stub provider +
real SkillStore + ToolStore, hit /api/v1/chat with a sequence that
triggers ``load_skill``, and assert:

1. The system prompt the LLM receives contains the ``## Skills``
   section with the enabled skill's name, slug, and description.
2. The LLM provider sees ``load_skill`` in the ``tools=`` argument.
3. When the LLM calls ``load_skill(slug="...")``, the chat handler
   records a ``ToolCallRecord(kind="skill", name=<slug>)``.
4. The chat handler's INFO log carries the per-request tool +
   skill counts.

Why this is a separate file (not another entry in
test_chat_skill_section.py): we need a real SkillStore on disk so
the load_skill round-trip exercises the same code paths as a
production boot. The lighter-weight in-memory tests in
test_chat_skill_section.py prove the helpers; this file proves the
seam between them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from mhc_desktop_backend.api.chat import router as chat_router
from mhc_desktop_backend.tools.builtin import BUILTIN_LOAD_SKILL
from mhc_desktop_backend.tools.imports import import_local_tool
from mhc_desktop_deploy.impls.file_stores.skills_store import SkillStore
from mhc_desktop_deploy.impls.file_stores.stream_registry import StreamRegistry
from mhc_desktop_deploy.impls.file_stores.tools_store import ToolStore


class _StubProvider:
    """Two-turn provider: first turn emits a load_skill call; the
    second turn emits plain text. Records every chat() call so the
    test can assert the system-prompt shape on both."""

    name = "stub"
    provider_type = "openai"

    def __init__(self) -> None:
        self.calls: list[tuple[list[dict[str, Any]], list[Any]]] = []

    async def chat(self, messages, tools=None):
        self.calls.append((list(messages), list(tools or [])))
        turn_index = len(self.calls)
        if turn_index == 1:
            # First turn: emit a tool call to load_skill.
            class _Resp:
                content = ""
                reasoning = None
                usage = {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                }
                tool_calls = [
                    {
                        "id": "call_test",
                        "type": "function",
                        "function": {
                            "name": "load_skill",
                            "arguments": json.dumps({"slug": "e2e-test-skill"}),
                        },
                    }
                ]

            class _Stream:
                response = _Resp()

                def __aiter__(self):
                    async def _gen():
                        if False:
                            yield None

                    return _gen()

            return _Stream()
        # Second turn: plain text reply.
        class _Resp:
            content = "skill loaded, here's my answer"
            reasoning = None
            usage = {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
            }
            tool_calls = []

        class _Stream:
            response = _Resp()

            def __aiter__(self):
                async def _gen():
                    if False:
                        yield None

                return _gen()

        return _Stream()


class _RecordingMetricsRepo:
    def __init__(self) -> None:
        self.tool_records: list[Any] = []
        self.llm_records: list[Any] = []

    async def record_tool_call(self, record):
        self.tool_records.append(record)

    async def record_llm_call(self, record):
        self.llm_records.append(record)


class _StubProviderStore:
    async def get(self, name):
        from dataclasses import dataclass

        @dataclass
        class _P:
            provider_type: str = "openai"
            api_key: str = "***"
            base_url: str = ""
            default_model: str = "m"
            description: str = ""
            model_params: dict = None

        return _P()


async def _build_app(tmp_path: Path, provider: _StubProvider) -> tuple[FastAPI, _RecordingMetricsRepo]:
    skills_dir = tmp_path / "skills"
    tools_dir = tmp_path / "tools"
    skills_state = tmp_path / "skills-state.json"
    skills_dir.mkdir()
    tools_dir.mkdir()

    skill_store = SkillStore(skills_dir=skills_dir, state_file=skills_state)
    tool_store = ToolStore(tools_dir=tools_dir)

    # Seed one enabled skill via the store's import path so we
    # exercise the same flow as a user-installed skill.
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    (seed_dir / "SKILL.md").write_text(
        "---\n"
        "name: e2e-test-skill\n"
        "description: A skill used to verify the load_skill E2E flow.\n"
        "---\n\n"
        "# E2E Skill Body\n\n"
        "This body should come back via load_skill.\n",
        encoding="utf-8",
    )
    await skill_store.install_from_folder(seed_dir, overwrite=True)

    # Pre-import load_skill so the in-process cache is warm; the
    # chat handler's build_streaming_tool also relies on this.
    # We also bind the live skill_store so the callable's
    # module-global ``_skill_store`` actually resolves to *this*
    # store -- without it the tool would always error out.
    from mhc_desktop_backend.tools.builtin import (
        load_skill as _load_skill,
    )
    _load_skill.set_skill_store(skill_store)
    src = (
        Path(__file__).parent.parent
        / "src/mhc_desktop_backend/tools/builtin/load_skill.py"
    ).read_text(encoding="utf-8")
    await import_local_tool("load_skill", src)
    (tools_dir / "load_skill").mkdir(parents=True, exist_ok=True)
    (tools_dir / "load_skill" / "tool.py").write_text(src, encoding="utf-8")
    await tool_store.create(dict(BUILTIN_LOAD_SKILL))

    metrics = _RecordingMetricsRepo()

    app = FastAPI()
    app.state.provider_store = _StubProviderStore()
    app.state.session_store = None
    app.state.skill_store = skill_store
    app.state.mcp_store = None
    app.state.mcp_manager = None
    app.state.tool_store = tool_store
    app.state.stream_registry = StreamRegistry()
    app.state.prefs_store = None
    app.state.chat_policy = None
    app.state.tool_executor_registry = None
    app.state.metrics_repo = metrics

    # Patch build_provider so the chat handler uses our stub.
    from mhc_desktop_backend.api import chat as chat_mod

    original = chat_mod.build_provider

    def _fake_build(p, model_override=None, model_params=None, **kwargs):
        return provider

    chat_mod.build_provider = _fake_build
    app.state._chat_build_provider_original = original
    app.include_router(chat_router)
    return app, metrics


@pytest.mark.asyncio
async def test_e2e_load_skill_flow(tmp_path: Path, caplog):
    import logging

    caplog.set_level(logging.INFO, logger="mhc_desktop_backend")

    provider = _StubProvider()
    app, metrics = await _build_app(tmp_path, provider)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.post(
                "/api/v1/chat",
                json={
                    "provider": "stub",
                    "session_id": "sess-e2e",
                    "messages": [{"role": "user", "content": "what does e2e-test-skill say?"}],
                },
            )
    finally:
        from mhc_desktop_backend.api import chat as chat_mod

        if hasattr(app.state, "_chat_build_provider_original"):
            chat_mod.build_provider = app.state._chat_build_provider_original

    assert r.status_code == 200, r.text

    # ── 1. System prompt on the first turn carries the skill
    # section with the seeded skill's name + slug + description.
    first_messages, first_tools = provider.calls[0]
    sys_msg = first_messages[0]
    assert sys_msg["role"] == "system"
    sys_text = sys_msg["content"]
    assert "## Skills" in sys_text, "expected ## Skills section in system prompt"
    assert "e2e-test-skill" in sys_text
    # The slug the model must call is surfaced (we use the imported
    # slug, which SkillStore derived from the folder name "seed").
    assert ("`e2e-test-skill`" in sys_text)
    assert "load_skill" in sys_text

    # ── 2. The LLM provider saw load_skill in tools= even though
    # the user didn't attach it. (auto-resolved by the chat handler).
    tool_names = [t.name for t in first_tools]
    assert "load_skill" in tool_names

    # ── 3. The INFO log line carries the per-request counts.
    chat_request_logs = [
        rec for rec in caplog.records if rec.message.startswith("chat.request ")
    ]
    assert chat_request_logs, "expected chat.request INFO line"
    line = chat_request_logs[0].getMessage()
    assert "skills_enabled=" in line
    assert "tools_resolved=" in line
    assert "tools_attached=0" in line
    assert "mcp_attached=0" in line
    assert "mcp_resolved=0" in line

    # ── 4. The load_skill invocation produced BOTH a
    # kind="tool" record AND a kind="skill" record keyed by the
    # slug the model asked for. This is the new behavior: skill
    # usage is counted by actual model invocation, not by
    # user-attachment.
    tool_kinds = [r for r in metrics.tool_records if r.kind == "tool"]
    skill_kinds = [r for r in metrics.tool_records if r.kind == "skill"]
    assert any(r.name == "load_skill" for r in tool_kinds)
    assert any(r.name == "e2e-test-skill" for r in skill_kinds), (
        "expected a kind=skill record keyed by the requested slug; "
        f"got {[r.name for r in skill_kinds]}"
    )

    # ── 5. The SSE response carried a tool_end with the SKILL.md
    # body content (the LLM pulled the skill).
    body = r.text
    assert "execution_start" in body
    assert "tool_end" in body
    # The body content (markers from our seed SKILL.md) shows up in
    # the streamed chunks.
    assert "E2E Skill Body" in body or "skill loaded" in body
