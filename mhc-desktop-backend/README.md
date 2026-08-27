# mhc-desktop-backend

Backend HTTP/SSE service for the mhc-desktop-backend Skill/MCP client. See
`docs/BRANDING.md` for the rebrand recipe when forking under a new product name.

- Stack: FastAPI + minimal-harness + mh-service-kit + openai + anthropic
- Default port: `8765` (`MHC_PORT`)
- Dev: hot-reload via uvicorn (`MHC_RELOAD=1`)

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET`  | `/api/v1/health` | Service identity + version + data_dir |
| `GET`  | `/ready` | Liveness probe |
| `GET`  | `/api/v1/providers` | List configured providers (`api_key` masked) |
| `GET`  | `/api/v1/providers/{name}` | Fetch one provider |
| `POST` | `/api/v1/providers[?preset_id=...]` | Create; `preset_id` seeds the body from a built-in preset |
| `PUT`  | `/api/v1/providers/{name}` | Update fields |
| `DELETE` | `/api/v1/providers/{name}` | Remove |
| `GET`  | `/api/v1/providers/presets` | Built-in preset templates |
| `POST` | `/api/v1/chat` | SSE chat — streams `event: chunk` / `event: done` / `event: error` |
| `GET`  | `/api/v1/onboarding` | First-run tour cards (centered / media-text / media-top), localised by `Accept-Language`; full i18n dicts included |

## Provider config

Lives at `~/.mhc-desktop/providers.json`. Schema is **byte-for-byte compatible
with mh-local's** `~/.config/mh-local/providers.json` — copy a file between the
two and it just works.

```json
[
  {
    "name": "openai",
    "provider_type": "openai",
    "api_key": "sk-...",
    "base_url": "https://api.openai.com/v1",
    "default_model": "gpt-4o-mini",
    "description": "OpenAI official API",
    "models": [{"code": "gpt-4o-mini", "display_name": "GPT-4o mini", "max_context": 128000}],
    "created_at": "...",
    "updated_at": "..."
  }
]
```

## Dev

```bash
uv run python -m mhc_desktop_backend
# health: http://127.0.0.1:8765/api/v1/health
# docs:   http://127.0.0.1:8765/docs
```

Or use the workspace helper:

```bash
bash scripts/dev-mhc-desktop.sh
```

Starts backend (hot reload) + frontend (HMR) on `:5180`, tails logs to `.logs/`.

## Env

| Var | Default | Purpose |
| --- | --- | --- |
| `MHC_HOST` | `127.0.0.1` | Bind address |
| `MHC_PORT` | `8765` | HTTP port |
| `MHC_DEBUG` | `1` | Toggle debug mode flag (also enables permissive CORS) |
| `MHC_RELOAD` | `1` | Uvicorn hot reload (dev only) |
| `MH_LOG_LEVEL` | `INFO` | Root log level |

## Build

```bash
uv build
```

Wheel: `dist/mhc_desktop_backend-*.whl`.
## Tools subsystem

A "Tool" is the third concept alongside Skills and MCP. See
`mhc_desktop_backend/tools/` for the implementation:

* `models.py` — `Tool` dataclass + slug rules; slugs disallow
  `::` to keep the MCP-vs-Tool distinction explicit in the
  `name` field of ToolCall TypedDicts.
* `store.py` — file-backed CRUD on `tools-state.json`; bundled
  tools are code-side, not on disk, but the store lists them
  alongside user tools.
* `bundled/` — `now` (ISO 8601 timestamp) and `uuid` (v4)
  trivial callables for E2E verification.
* `imports.py` — `import_local_tool` compiles + caches Python
  source strings; `run_tool` is the cancellable, time-bounded
  executor.
* `__init__.py` — public surface + `build_streaming_tool`
  wrapping a `Tool` into a minimal-harness `StreamingTool`.

### API

* `GET /api/v1/tools` — list (bundled first, then user).
* `GET /api/v1/tools/bundled` — bundled slugs only.
* `POST /api/v1/tools` — create / register.
* `GET /api/v1/tools/{slug}` — detail (auth header redacted).
* `PUT /api/v1/tools/{slug}` — update.
* `PUT /api/v1/tools/{slug}/enabled` — flip on/off.
* `DELETE /api/v1/tools/{slug}` — remove.
* `POST /api/v1/tools/import-source` — compile + register a
  Python source string.
* `GET /api/v1/tools/{slug}/export` — JSON manifest.

### Bundled skill `mcp-tool-mix`

Drives the goal's most complex scenario: a single skill that
instructs the model to call both an MCP (`dummy-mcp::add`) and a
local Tool (`now`). Used by `scripts/e2e-tools.mjs` Check 8 to
verify the capsule distinction works end-to-end.
