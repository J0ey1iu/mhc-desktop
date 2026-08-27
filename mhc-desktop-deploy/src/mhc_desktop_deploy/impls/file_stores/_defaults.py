"""Default factory functions for the file-backed deploy package.

Each ``default_*`` function returns one freshly-constructed instance
of the matching store. Cheap — the deploy caller doesn't need to
worry about singletons or memoisation.

Lives in its own module so the concrete store files (``session_store``,
``provider_store``, ...) don't have to import each other in a cycle.

``data_dir``
-----------

Every store factory takes an optional ``data_dir=Path``. When
supplied, the store is constructed with that exact path; when
``None`` (the default), the factory reads
:data:`mhc_desktop_deploy.impls.file_stores.paths.DATA_DIR` at call
time. The deploy's ``build_default_app`` monkey-patches the
``paths.*`` constants before calling the factories, so the
fall-through path also lands on the deploy's chosen directory.
"""

from __future__ import annotations

from pathlib import Path

from mhc_desktop_deploy.impls.file_stores.mcp_manager import MCPManager
from mhc_desktop_deploy.impls.file_stores.mcp_store import MCPStore
from mhc_desktop_deploy.impls.file_stores.metrics_store import JSONLMetricsRepository
from mhc_desktop_deploy.impls.file_stores.prefs_store import PrefsStore
from mhc_desktop_deploy.impls.file_stores.provider_store import ProviderStore
from mhc_desktop_deploy.impls.file_stores.session_store import SessionStore
from mhc_desktop_deploy.impls.file_stores.skills_store import SkillStore
from mhc_desktop_deploy.impls.file_stores.stream_registry import StreamRegistry
from mhc_desktop_deploy.impls.file_stores.tools_store import ToolStore


def default_session_store(data_dir: Path | None = None) -> SessionStore:
    from mhc_desktop_deploy.impls.file_stores.paths import SESSIONS_DIR

    return SessionStore((data_dir / "sessions") if data_dir else SESSIONS_DIR)


def default_provider_store(data_dir: Path | None = None) -> ProviderStore:
    from mhc_desktop_deploy.impls.file_stores.paths import PROVIDERS_FILE

    return ProviderStore((data_dir / "providers.json") if data_dir else PROVIDERS_FILE)


def default_skill_store(data_dir: Path | None = None) -> SkillStore:

    if data_dir:
        return SkillStore(
            skills_dir=data_dir / "skills",
            state_file=data_dir / "skills-state.json",
        )
    return SkillStore()


def default_mcp_store(data_dir: Path | None = None) -> MCPStore:

    if data_dir:
        return MCPStore(
            mcp_dir=data_dir / "mcp",
            state_file=data_dir / "mcp-state.json",
        )
    return MCPStore()


def default_mcp_manager(
    data_dir: Path | None = None,
    *,
    client_name: str | None = None,
) -> MCPManager:
    # ``client_name`` flows into the MCP ``initialize.clientInfo.name``
    # so downstream MCP server audit logs see the rebranded client,
    # not the upstream hardcode. The version is owned by the kernel.
    return MCPManager(default_mcp_store(data_dir), client_name=client_name)


def default_tool_store(data_dir: Path | None = None) -> ToolStore:
    from mhc_desktop_deploy.impls.file_stores.paths import TOOLS_DIR

    return ToolStore((data_dir / "tools") if data_dir else TOOLS_DIR)


def default_stream_registry() -> StreamRegistry:
    return StreamRegistry()


def default_prefs_store(data_dir: Path | None = None) -> PrefsStore:
    from mhc_desktop_deploy.impls.file_stores.paths import PREFS_FILE

    return PrefsStore((data_dir / "prefs.json") if data_dir else PREFS_FILE)


def default_metrics_repo(path: Path | None = None) -> JSONLMetricsRepository:
    # Read ``METRICS_FILE`` from the paths module at call time, not
    # at import time — tests monkeypatch ``paths.METRICS_FILE`` to
    # point at ``tmp_path`` and we want the patched value to land
    # here, not the value frozen when this module was first imported.
    from mhc_desktop_deploy.impls.file_stores.paths import METRICS_FILE

    return JSONLMetricsRepository(path or METRICS_FILE)


__all__ = [
    "default_mcp_manager",
    "default_mcp_store",
    "default_metrics_repo",
    "default_prefs_store",
    "default_provider_store",
    "default_session_store",
    "default_skill_store",
    "default_stream_registry",
    "default_tool_store",
]
