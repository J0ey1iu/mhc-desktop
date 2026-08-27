# AGENTS.md

Self-hosted Skill/MCP agent client: Electron host + Vue SPA + FastAPI backend.

## Layout

| Path | Role |
| --- | --- |
| `mhc-desktop-backend/` | Kernel — FastAPI, chat loop, ~25 Protocol slots. The only package with HTTP/SSE surface. |
| `mhc-desktop-deploy/` | Shell — file-backed stores, `MockAuthProvider`, `build_default_app(...)`. Override kwargs here, don't fork the kernel. |
| `mhc-desktop-frontend/` | Vue 3 + Vite SPA (Pinia, vue-router). |
| `mhc-desktop-app/` | Electron host — spawns bundled Python, serves SPA from same origin. |
| `scripts/` | `dev-*.sh`, `build-spa.sh`, `build-bundled-python.sh`. |
| `docs/` | `PACKAGING.md`, `BUILTIN-CONTENT.md`, `UPDATE-MECHANISM.md`, `BRANDING.md`. Read these before changing build, bundled content, update flow, or product-name surfaces. |
| `e2e/smoke.cjs` | 17-check post-refactor HTTP smoke. No screenshots. |

## Dev loop

```bash
# Install (uv workspace)
uv sync --all-packages

# Backend on :8765
bash scripts/dev-mhc-desktop.sh

# Frontend on :5180
cd mhc-desktop-frontend && npm install && npm run dev
```

Demo users (no IdP needed): `alice/wonderland`, `bob/builder`, `demo/demo`.

## Test, type-check, lint

```bash
# Backend
pytest                                  # all backend tests
ruff check .                            # lint
pyright                                 # type-check (kernel + deploy)

# Updater (Electron app)
cd mhc-desktop-app
npm test                                # 43 unit tests across manifest/downloader/applier/rollout
npm run test:scenarios                  # 14-scenario integration suite (local HTTP server + fake packaged layout)

# Frontend
cd mhc-desktop-frontend
npm run type-check                      # vue-tsc
npm run build                           # vue-tsc -b && vite build

# End-to-end (backend must be up)
(python -m mhc_desktop_deploy &) ; sleep 5
node e2e/smoke.cjs                      # expect: 17 passed, 0 failed
pkill -f mhc_desktop_deploy
```

## Build

```bash
bash scripts/build-spa.sh              # SPA → backend wheel's static/
bash scripts/build-bundled-python.sh   # PBS + minimal-harness + desktop wheel
cd mhc-desktop-app && npm run package  # → dist/mhc-desktop Setup *.exe (NSIS)
```

## Conventions

- **Kernel vs shell**: never put business defaults in `mhc-desktop-backend/`. Add a kwarg to `create_app` / `build_default_app` and wire it from `mhc-desktop-deploy`.
- **Auth is fail-closed**: kernel refuses to boot without an `AuthProvider` in non-debug mode. Don't add a permissive default.
- **Same-origin SPA**: frontend is served by backend on the same origin so `/api/v1/...` is relative. No CORS, no `file://`.
- **Dependencies**: PyPI index is the aliyun mirror (see `pyproject.toml`). Don't add a new dep when stdlib / `minimal-harness` covers it.
- **Python ≥ 3.12**, **Vue 3 + TS strict** (vue-tsc must pass before build).
- **Electron packaging quirks**: see `docs/PACKAGING.md` before touching `mhc-desktop-app/` build config.

## Where to look first

- Touching HTTP/SSE or chat loop → `mhc-desktop-backend/README.md`.
- Adding enterprise auth / storage / RBAC / presets → `mhc-desktop-deploy/README.md`.
- Adding / changing product-name brand strings → `docs/BRANDING.md`.
- Adding bundled Skill / Tool / MCP → `docs/BUILTIN-CONTENT.md`.
- Packaging / installer issues → `docs/PACKAGING.md`.
- Auto-update flow / manifest / Tier 1–3 channels → `docs/UPDATE-MECHANISM.md`.
