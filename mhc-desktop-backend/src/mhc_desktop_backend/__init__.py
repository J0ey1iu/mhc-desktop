"""mhc-desktop-backend — Skill/MCP HTTP API for the desktop client.

The public surface falls in two layers:

* The HTTP app + bundled reference stores — what a single-process
  Electron client uses out of the box (``create_app()`` with no args).
* The :mod:`mhc_desktop_backend.protocols` adapters — what enterprise
  deployments substitute for Postgres / Vault / S3 / custom auth.
  Any class whose methods match the structural Protocol passes
  ``isinstance(obj, Protocol)`` at runtime; see ``protocols.py`` for
  the contract.
"""

from __future__ import annotations

# Module-level brand/version constants must be defined BEFORE any
# submodule imports — ``mcp.manager`` imports them at module load,
# and ``mcp`` is imported by ``protocols`` which is imported below,
# so a partially-initialized ``mhc_desktop_backend`` would crash.
__version__ = "0.1.0"
# Module name (not the upstream product name). Brand fallback used
# by ``Config.app_name``, the MCP ``clientInfo.name`` default, and
# the onboarding placeholder. See ``docs/BRANDING.md``.
__app_name__ = "mhc-desktop-backend"

from mhc_desktop_backend.protocols import (
    AuthProviderProtocol,
    AuthUser,
    MCPManagerProtocol,
    MCPStoreProtocol,
    Provider,
    ProviderStoreProtocol,
    Session,
    SessionStoreProtocol,
    Skill,
    SkillStoreProtocol,
    MCPServer,
    StreamRegistryProtocol,
    Tool,
    ToolStoreProtocol,
)

# ``create_app`` is imported lazily so importing the package stays
# cheap — the factory pulls in every router, which costs a few hundred
# ms; downstream code that only needs the Protocol types or value
# objects shouldn't pay for that. Use ``from
# mhc_desktop_backend.app import create_app`` explicitly when needed.


def __getattr__(name: str):
    # PEP 562 lazy attribute access. Importers that touch
    # ``mhc_desktop_backend.create_app`` get the function without
    # any other module doing a top-level import.
    if name == "create_app":
        from mhc_desktop_backend.app import create_app as _ca

        return _ca
    raise AttributeError(name)


__all__ = [
    "__version__",
    # Protocols — for adapters to import against.
    "AuthProviderProtocol",
    "AuthUser",
    "SessionStoreProtocol",
    "ProviderStoreProtocol",
    "SkillStoreProtocol",
    "MCPStoreProtocol",
    "MCPManagerProtocol",
    "ToolStoreProtocol",
    "StreamRegistryProtocol",
    # Value objects — exported so adapters don't need a second path.
    "Session",
    "Provider",
    "Skill",
    "MCPServer",
    "Tool",
]
