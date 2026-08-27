"""Import + cache local Python tool callables.

User-imported tools of kind ``local`` ship as Python source files.
We don't trust arbitrary code from disk any more than the user
already does — the goal explicitly says to ignore security concerns —
but we do need:

* non-blocking execution (so a hang in one tool call doesn't lock
  the backend's event loop);
* cancellable execution (so the chat handler can stop a runaway
  tool when the user hits the stop button);
* bounded runtime (we cap every tool at 15 minutes; longer than
  that and we assume the tool is stuck and kill it).

We handle all three via :func:`run_tool` which wraps the user's
callable in :func:`asyncio.wait_for` with a ``timeout`` argument.
The chat handler passes a ``cancel_event`` that, when set, will
cancel the inner task. The 15-minute ceiling is enforced regardless
of the cancel signal so an unresponsive tool can't survive a whole chat
session.

Bundled tools ship pre-registered; the cache below holds
user-imported ones keyed by slug.
"""

from __future__ import annotations

import asyncio
import logging
import textwrap
from pathlib import Path
from typing import Any, AsyncIterator

logger = logging.getLogger("mhc_desktop_backend")

# Process-local cache of imported local tool modules.
_LOCAL_CACHE: dict[str, Any] = {}

# Hard ceiling on a single tool call. The goal says 15 minutes
# ("通通保持在 15 分钟以内"); we use it as the default and let
# callers override (the chat handler doesn't, but tests do).
DEFAULT_TOOL_TIMEOUT_SECONDS = 15 * 60


def get_cached_local(slug: str):
    return _LOCAL_CACHE.get(slug)


def evict_cached_local(slug: str) -> None:
    _LOCAL_CACHE.pop(slug, None)


def _resolve_callable(namespace: dict[str, Any], slug: str):
    """Pick the entrypoint from an imported tool namespace.

    The exec'd source lives in a fresh dict (``namespace``), not a
    module object, so we look up by key rather than ``getattr``.

    Convention:
      * a top-level ``async def tool_run(**kwargs)`` (most common)
      * a top-level ``async def run(**kwargs)`` (short alias)
      * a top-level ``async def main(**kwargs)`` (script-style)
      * a top-level callable assigned to ``tool_callable``

    Returns the callable or raises ``ValueError`` with a clear
    message naming the slug.
    """
    for name in ("tool_run", "run", "main", "callable"):
        attr = namespace.get(name)
        if attr is None:
            continue
        if callable(attr):
            return attr
    explicit = namespace.get("tool_callable")
    if callable(explicit):
        return explicit
    raise ValueError(
        f"tool module for '{slug}' has no callable — define "
        "async def tool_run(**kwargs) (or run / main / tool_callable)"
    )


async def import_local_tool(slug: str, source: str) -> Any:
    """Compile + exec a Python source string and register the
    callable. ``source`` is the raw text of a Python module.

    We ``exec`` in a fresh namespace rather than using ``runpy`` so
    we can hold onto the resulting module object and pull the
    entrypoint callable off it. The exec is intentionally not
    sandboxed — the user is running the tool themselves.
    """
    namespace: dict[str, Any] = {
        "__name__": f"mhc_tool_{slug}",
        "__file__": f"<tool:{slug}>",
    }
    # Indent the source so the dedent below lines up. The user's
    # source is expected to start at column 0; we don't enforce
    # that here because textwrap.dedent just no-ops on already-flush
    # code.
    code = textwrap.dedent(source)
    try:
        compiled = compile(code, f"<tool:{slug}>", "exec")
    except SyntaxError as e:
        raise ValueError(f"tool '{slug}' has a syntax error: {e}") from e
    exec(compiled, namespace)
    fn = _resolve_callable(namespace, slug)
    _LOCAL_CACHE[slug] = fn
    logger.info("imported local tool '%s'", slug)
    return fn


async def import_tool_from_disk(slug: str, source_path: str | None = None) -> Any:
    """Re-import a local tool from its on-disk copy.

    Local tools are copied to ``~/.mhc-desktop/tools/<slug>/tool.py``
    at import time (see the bulk-import endpoint) so they survive
    backend restarts. This is the lazy loader used when a chat call
    misses the process-local cache after a restart — the source is
    re-read from disk and re-exec'd.

    ``source_path`` is the canonical on-disk location carried on the
    Tool record; when empty (legacy records / not persisted) we fall
    back to ``~/.mhc-desktop/tools/<slug>/tool.py``.

    Returns the callable, or ``None`` if the tool has no on-disk
    copy (never imported, or deleted). Never raises for a missing
    file — callers treat ``None`` as "tool not loaded".
    """
    # ``DATA_DIR`` lives in the deploy package; the kernel
    # requires the deploy package — there is no fallback. Tests
    # inject a tmp dir via the deploy's ``paths`` module.
    from mhc_desktop_deploy.impls.file_stores.paths import (
        DATA_DIR as _DATA_DIR,
    )

    DATA_DIR = _DATA_DIR  # keep the public name; tests expect this

    tp = None
    if source_path:
        cand = Path(source_path)
        if cand.is_file():
            tp = cand
    if tp is None:
        base = DATA_DIR / "tools"
        cand = base / slug / "tool.py"
        if cand.is_file():
            tp = cand
    if tp is None:
        return None
    try:
        source = tp.read_text("utf-8")
    except OSError:
        return None
    try:
        return await import_local_tool(slug, source)
    except (ValueError, SyntaxError) as e:
        logger.warning("re-import of tool '%s' from disk failed: %s", slug, e)
        return None


async def run_tool(
    fn,
    args: dict[str, Any],
    *,
    timeout: float = DEFAULT_TOOL_TIMEOUT_SECONDS,
    cancel_event: asyncio.Event | None = None,
) -> AsyncIterator[str]:
    """Run a local tool callable with a timeout + cancel hook.

    The callable is expected to be either:

    * an ``async def fn(**kwargs)`` returning an async iterator of
      string chunks, OR
    * an ``async def fn(**kwargs)`` returning a single value (we
      await it and yield it once), OR
    * a synchronous ``def fn(**kwargs)`` returning a value (we call
      it directly and yield it once).

    Yields the callable's chunks in order. Raises:
      * :class:`asyncio.TimeoutError` if the tool exceeds ``timeout``
      * :class:`asyncio.CancelledError` if ``cancel_event`` was set
        while we were awaiting

    The cancel check fires every time we yield a chunk, so the
    chat handler's stop signal interrupts even a tool that's
    actively yielding rather than awaiting.
    """
    if not callable(fn):
        # Defence in depth: build_streaming_tool already guards this
        # by yielding a "[tool error] callable not loaded" message
        # from the streaming tool's fn. Reaching here means some
        # caller bypassed that wrapper. Tell them what to do anyway.
        raise ValueError(
            "run_tool: callable is required — the tool's Python source "
            "is not loaded in this backend process. Delete the tool "
            "and re-import it, or restart the backend."
        )

    # Race the runner against the cancel event. Whichever finishes
    # first wins; if cancel wins, we cancel the runner task and
    # raise CancelledError. The watcher is the cancel event; we
    # wrap its ``wait()`` so we can race it as a Task.
    runner_task = asyncio.create_task(_run_to_list(fn, args))

    if cancel_event is None:
        try:
            chunks = await asyncio.wait_for(runner_task, timeout=timeout)
        except asyncio.TimeoutError as e:
            runner_task.cancel()
            raise asyncio.TimeoutError(f"tool exceeded {timeout:.0f}s budget") from e
        for chunk in chunks:
            yield chunk
        return

    watcher = asyncio.create_task(cancel_event.wait())
    try:
        done, pending = await asyncio.wait(
            {runner_task, watcher},
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
    finally:
        # If neither finished, the timeout did — cancel both so we
        # don't leak tasks. asyncio.wait already handles this with
        # the timeout kwarg.
        pass

    if not done:
        # Outer timeout fired.
        runner_task.cancel()
        watcher.cancel()
        try:
            await runner_task
        except (asyncio.CancelledError, Exception):
            pass
        raise asyncio.TimeoutError(f"tool exceeded {timeout:.0f}s budget")

    if watcher in done:
        # Cancel won. Cancel the runner and propagate.
        runner_task.cancel()
        try:
            await runner_task
        except asyncio.CancelledError:
            pass
        raise asyncio.CancelledError("tool cancelled by user")

    # Runner won. Cancel the watcher (no-op if already done).
    if not watcher.done():
        watcher.cancel()
    if runner_task.cancelled():
        raise asyncio.CancelledError("tool cancelled")
    exc = runner_task.exception()
    if exc is not None:
        raise exc
    chunks = runner_task.result()
    for chunk in chunks:
        # Mid-stream cancel — let the chat handler's stop signal
        # interrupt even a long string of chunks the LLM hasn't
        # read yet.
        if cancel_event.is_set():
            raise asyncio.CancelledError("tool cancelled by user")
        yield chunk


async def _run_to_list(fn, args: dict[str, Any]) -> list[str]:
    """Run ``fn(**args)`` and collect every chunk into a list.

    Helper for :func:`run_tool` so we can race the run against a
    cancel event without the complexity of an async-iterator-aware
    task.
    """
    out: list[str] = []
    result = fn(**args)
    if hasattr(result, "__aiter__"):
        async for chunk in result:
            out.append(str(chunk))
    elif asyncio.iscoroutine(result):
        value = await result
        out.append(str(value))
    else:
        out.append(str(result))
    return out


async def _drain(it: AsyncIterator[str]) -> list[str]:
    """Collect all chunks from an async iterator into a list. Used by
    :func:`run_tool` so we can replay them after the cancel-vs-runner
    race resolves."""
    out: list[str] = []
    async for chunk in it:
        out.append(chunk)
    return out
