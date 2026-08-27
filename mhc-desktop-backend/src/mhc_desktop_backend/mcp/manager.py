"""MCP server connection manager.

Spawns user-configured MCP servers as subprocesses, speaks JSON-RPC
2.0 over their stdio (newline-delimited), and caches:

* the discovered tool list (``tools/list``)
* a persistent asyncio subprocess per active MCP server

The manager is intentionally simple: each MCP runs in its own
subprocess, and the chat handler asks for ``list_tools(slug)`` and
``call_tool(slug, name, args)``. We don't try to multiplex multiple
slugs through one subprocess because MCP servers are stateless
JSON-RPC endpoints \u2014 spawning N procs is cheap and matches what
real MCP hosts do.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from mhc_desktop_backend.mcp.models import MCPServer
from mhc_desktop_backend import __app_name__, __version__

if TYPE_CHECKING:  # pragma: no cover — type-check only
    from mhc_desktop_backend.protocols import MCPStoreProtocol


# ponytail: backend kernel owns the exception shape; the concrete
# store in deploy raises it too. Enterprise adapters raising their
# own exception is fine — we catch ``Exception`` broadly in the chat
# loop — but the protocol defaults to this class for callers that
# want to discriminate.
class MCPError(ValueError):
    """Raised when an MCP server cannot start or returns an error.

    Kept in the kernel so deploy's concrete store + the chat router
    can share the type without a backend→deploy import edge.
    """


logger = logging.getLogger("mhc_desktop_backend")


@dataclass
class MCPConnection:
    """A live subprocess + asyncio streams for one MCP server."""

    process: asyncio.subprocess.Process
    next_id: int = 1
    lock: asyncio.Lock = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.lock is None:
            self.lock = asyncio.Lock()


class MCPManager:
    """Owns per-server subprocess lifecycles and the JSON-RPC client."""

    def __init__(
        self,
        store: "MCPStoreProtocol",
        *,
        client_name: str | None = None,
    ) -> None:
        self._store = store
        self._conns: dict[str, MCPConnection] = {}
        self._lock = asyncio.Lock()
        # ``clientInfo`` fields we send during the MCP ``initialize``
        # handshake. Defaults to the kernel module name + version so
        # downstream MCP servers' audit logs don't see a hardcoded
        # upstream brand. The deploy passes its brand via
        # ``build_default_app(meta={"brand":{"name": ...}})`` →
        # ``default_mcp_manager(client_name=...)``.
        self._client_name = client_name or __app_name__

    async def connect(self, server: MCPServer) -> MCPConnection:
        """Spawn the subprocess (or reuse an existing one) and handshake."""
        async with self._lock:
            conn = self._conns.get(server.slug)
            if conn is not None and conn.process.returncode is None:
                return conn
            cmd = server.command
            if not cmd:
                raise MCPError(f"MCP '{server.slug}' has empty command")
            # shutil.which lets users pass "python" instead of a full
            # path on PATH-based systems; on Windows, `python` usually
            # resolves via the App Execution Aliases.
            resolved = shutil.which(cmd) or cmd
            env = {**os.environ, **server.env}
            try:
                proc = await asyncio.create_subprocess_exec(
                    resolved,
                    *server.args,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
            except FileNotFoundError as e:
                raise MCPError(
                    f"MCP '{server.slug}' command not found: {resolved}"
                ) from e
            except OSError as e:
                raise MCPError(f"MCP '{server.slug}' spawn failed: {e}") from e
            conn = MCPConnection(process=proc)
            self._conns[server.slug] = conn
            try:
                await self._rpc(
                    conn,
                    "initialize",
                    {
                        "protocolVersion": "2024-11-05",
                        "clientInfo": {
                            "name": self._client_name,
                            "version": __version__,
                        },
                        "capabilities": {},
                    },
                )
                # Best-effort notification; some servers don't reply.
                try:
                    await self._notify(conn, "notifications/initialized", {})
                except Exception:
                    pass
            except Exception as e:
                await self._discard(server.slug)
                raise MCPError(f"MCP '{server.slug}' initialize failed: {e}") from e
            return conn

    async def list_tools(self, server: MCPServer) -> list[dict[str, Any]]:
        """Return the MCP's tool catalog, persisting it to the store."""
        conn = await self.connect(server)
        try:
            res = await self._rpc(conn, "tools/list", {})
        except Exception as e:
            await self._store.record_discovery(server.slug, [], error=str(e))
            raise
        tools = res.get("tools") or []
        await self._store.record_discovery(server.slug, tools)
        return tools

    async def call_tool(
        self,
        server: MCPServer,
        name: str,
        arguments: dict[str, Any],
    ) -> str:
        """Invoke one tool. Returns concatenated text content or raises.

        We always go through ``connect`` first so the subprocess is
        alive; ``connect`` reuses an existing connection if possible.
        """
        conn = await self.connect(server)
        try:
            res = await self._rpc(
                conn,
                "tools/call",
                {"name": name, "arguments": arguments},
            )
        except Exception as e:
            raise MCPError(f"MCP '{server.slug}' tool '{name}' failed: {e}") from e
        if res.get("isError"):
            text = _flatten_content(res.get("content") or [])
            raise MCPError(
                f"MCP '{server.slug}' tool '{name}' returned error: {text or 'unspecified'}"
            )
        return _flatten_content(res.get("content") or [])

    async def disconnect(self, slug: str) -> None:
        async with self._lock:
            conn = self._conns.pop(slug, None)
            if conn is None:
                return
            await self._terminate(conn)

    async def shutdown(self) -> None:
        async with self._lock:
            conns = list(self._conns.values())
            self._conns.clear()
        for conn in conns:
            await self._terminate(conn)

    # ── Internals ──────────────────────────────────────────────────

    async def _discard(self, slug: str) -> None:
        async with self._lock:
            conn = self._conns.pop(slug, None)
        if conn is not None:
            await self._terminate(conn)

    async def _terminate(self, conn: MCPConnection) -> None:
        proc = conn.process
        try:
            if proc.returncode is None:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=3)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
        except ProcessLookupError:
            pass

    async def _rpc(
        self,
        conn: MCPConnection,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        async with conn.lock:
            req_id = conn.next_id
            conn.next_id += 1
            envelope = {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": method,
                "params": params,
            }
            line = (json.dumps(envelope, ensure_ascii=False) + "\n").encode("utf-8")
            assert conn.process.stdin is not None
            try:
                conn.process.stdin.write(line)
                await conn.process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError) as e:
                raise MCPError(f"subprocess closed: {e}") from e
            assert conn.process.stdout is not None
            while True:
                raw = await conn.process.stdout.readline()
                if not raw:
                    raise MCPError("subprocess closed before reply")
                text = raw.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    msg = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if msg.get("id") != req_id:
                    # Not our reply; skip stray server-side frames.
                    continue
                if "error" in msg:
                    err = msg["error"]
                    raise MCPError(
                        f"server error {err.get('code')}: {err.get('message')}"
                    )
                return msg.get("result") or {}

    async def _notify(
        self,
        conn: MCPConnection,
        method: str,
        params: dict[str, Any],
    ) -> None:
        async with conn.lock:
            envelope = {
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
            }
            line = (json.dumps(envelope, ensure_ascii=False) + "\n").encode("utf-8")
            assert conn.process.stdin is not None
            try:
                conn.process.stdin.write(line)
                await conn.process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError) as e:
                raise MCPError(f"subprocess closed: {e}") from e


def _flatten_content(content: list[dict[str, Any]]) -> str:
    """Concatenate ``content[].text`` entries for the common MCP tool
    response shape. Non-text blocks (image, audio, etc.) are skipped
    for now \u2014 we don't render them in v1."""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "\n".join(parts)
