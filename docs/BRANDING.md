# Rebranding the desktop client

`mhc-desktop-backend` ships with **no upstream brand strings baked in**.
Every product-name surface is read at boot from a single source of
truth, with a kernel-default fallback of the module name
`mhc-desktop-backend` (not the product name — by design, so a fork
that forgets to wire one knob still doesn't leak the old brand).

This document is for **fork maintainers**. It lists every place the
product name is rendered, the priority order each knob is resolved,
and the minimal recipe to wire a new brand end-to-end.

## TL;DR — three knobs, in priority order

1. **`MHC_APP_NAME` environment variable** → controls the boot
   log banner and the kernel default for everything else.
2. **`build_default_app(meta={"brand": {"name": "..."}})`** →
   controls the FastAPI `/docs` title, the MCP `clientInfo.name`
   sent to every downstream MCP server, the `{brand_name}`
   placeholder rendered inside the default onboarding cards, and
   what `GET /api/v1/meta` returns to the frontend.
3. **`build_default_app(brand_name="...")`** → same effect as #2
   but doesn't require constructing the full `meta` dict; just
   override the brand token.

A fork that wants to rebrand sets **at least one** of these.
Setting more than one is fine — the priority chain is
explicit > env > kernel default.

## What gets rebranded

| Surface | Source | Notes |
|---------|--------|-------|
| `Starting <brand> <ver> on <host>:<port>` boot banner | `Config.app_name` ← `MHC_APP_NAME` | Read in `main.py` before `create_app()` runs. |
| `<brand> ready (debug=<b>)` lifespan banner | `app.state.meta["brand"]["name"]` | Seeded by `create_app()`; falls back to `cfg.app_name`. |
| `FastAPI(title=...)` → `/docs` H1 + `openapi.json.info.title` | same as above | Browsed by API gateways, Postman auto-import, etc. |
| MCP `initialize.clientInfo.name` sent to every MCP server | `MCPManager(client_name=...)` ← `build_default_app(brand_name=...)` | Visible in downstream MCP server audit logs. |
| `Welcome to {brand_name}` / `欢迎使用 {brand_name}` first-run card | `app.state.meta["brand"]["name"]` | Default cards ship the placeholder; the renderer substitutes at request time. |
| `GET /api/v1/meta` → `meta.brand.name` for the frontend | `app.state.meta["brand"]["name"]` | Frontend reads this for login screen, settings, etc. |
| `MCPManager.initialize.clientInfo.version` | `__version__` (kernel constant) | Not a brand surface — leave alone unless you're versioning the whole product. |

## Resolution chain

Every consumer reads the same value, but the chain differs by
when it can be read:

```
# Boot-time log line (main.py, runs before create_app()):
cfg.app_name = os.getenv("MHC_APP_NAME") or __app_name__  # = "mhc-desktop-backend"

# create_app() — late-boot consumers:
brand = (meta or {}).get("brand", {}).get("name") or cfg.app_name
app.state.meta.setdefault("brand", {})["name"] = brand   # always populated

# MCPManager — built by deploy, not create_app():
brand = brand_name_kwarg or __app_name__  # plumbed by build_default_app()
```

## Recipes

### Recipe 1 — env-only (no code change)

For dev / staging / packaging-time overrides:

```bash
export MHC_APP_NAME="Acme Assistant"
python -m mhc_desktop_deploy
```

The boot banner reads it via `Config.app_name`. Every other
surface falls back to `"mhc-desktop-backend"` because
`build_default_app()` reads `MHC_APP_NAME` too and propagates it
into `meta["brand"]["name"]` (see `assemble.py:90-96`).

### Recipe 2 — explicit brand_name on build_default_app()

For forks whose entry point is `build_default_app(...)`:

```python
from mhc_desktop_deploy.assemble import build_default_app

app = build_default_app(brand_name="Acme Assistant")
```

This sets `MHC_APP_NAME` via env (via `assemble.py`) **and**
populates `meta["brand"]["name"]` **and** plumbs
`client_name=brand_name` into `default_mcp_manager(...)`. All
three downstream readers pick up the new name without any further
wiring.

### Recipe 3 — full meta override (when you also want
`primary_color`, `support_url`, etc.)

```python
app = build_default_app(
    meta={
        "brand": {
            "name": "Acme Assistant",
            "primary_color": "#0ea5e9",
            "support_url": "https://acme.example/help",
        },
        # any other keys the frontend reads go here too
    },
)
```

The `brand` sub-dict is consumed by:
- the FastAPI title (`name`)
- the MCP `clientInfo.name` (`name`)
- the onboarding placeholder renderer (`name`)
- `GET /api/v1/meta` (whole dict, for the frontend to pick from)

Extra keys (`primary_color`, `support_url`, …) are opaque to the
kernel; the frontend reads them via `/api/v1/meta`.

### Recipe 4 — custom onboarding cards with the placeholder

The kernel ships three default cards; the welcome card uses
`{brand_name}` in its title. If your fork ships its own card
catalogue, you can opt into the same substitution:

```python
from mhc_desktop_backend import OnboardingCard
from mhc_desktop_deploy.assemble import build_default_app

cards = [
    OnboardingCard(
        id="welcome",
        type="centered",
        title="Welcome to {brand_name}",          # substituted at render time
        body="Pick a model, drop in skills, ...",
        title_i18n={
            "en": "Welcome to {brand_name}",
            "zh": "欢迎使用 {brand_name}",
        },
        body_i18n={
            "en": "Pick a model, drop in skills, ...",
            "zh": "选模型、加技能、接工具。",
        },
    ),
    # ... more cards
]
app = build_default_app(onboarding_cards=cards, brand_name="Acme Assistant")
```

The renderer (`api/onboarding.py:_render`) uses
`str.format_map` with a default-dict so a missing or unknown
placeholder is left literal rather than raising — typos in card
copy don't crash the first-run overlay.

### Recipe 5 — direct create_app() (no deploy package)

Tests and one-off scripts that call `create_app()` without
`build_default_app()` should pass `meta=` explicitly:

```python
from mhc_desktop_backend.app import create_app

app = create_app(meta={"brand": {"name": "Acme Assistant"}})
# FastAPI title, MCP, onboarding all read "Acme Assistant".
# Boot-time log banner (main.py) reads MHC_APP_NAME separately.
```

## What is NOT a brand knob

These are package or directory names, not brand surfaces — leave
them alone:

- `mhc-desktop-backend` (Python package name) — `import`
  statements, `pyproject.toml` name.
- `mhc-desktop-deploy` (Python package name).
- `mhc-desktop-app` (Electron host package name).
- `mhc-desktop-frontend` (Vue SPA package name).
- `~/.mhc-desktop/` (default user data dir, owned by deploy).
- `MHC_*` env vars (`MHC_DEBUG`, `MHC_HOST`, `MHC_PORT`,
  `MHC_DATA_DIR`, `MHC_RESOURCES_PATH`, `MHC_RELOAD`) — these are
  configuration knobs, not brand surfaces.

The deploy log line `"mhc-desktop deploy wired (...)"` is the
deploy package talking about itself; not a product brand.

## Verification

After wiring, check the surfaces end-to-end:

```bash
export MHC_APP_NAME="Acme Assistant"
python -m mhc_desktop_deploy &
sleep 3

curl -s http://127.0.0.1:8765/api/v1/meta | jq .meta.brand
#  → {"name": "Acme Assistant"}

curl -s http://127.0.0.1:8765/api/v1/onboarding | jq '.[0].title_i18n'
#  → {"en": "Welcome to Acme Assistant", "zh": "欢迎使用 Acme Assistant"}

curl -s http://127.0.0.1:8765/openapi.json | jq .info.title
#  → "Acme Assistant API"
```

The boot log line should read:

```
Starting Acme Assistant 0.1.0 on 127.0.0.1:8765
Acme Assistant ready (debug=True)
```

## Why the kernel default is the module name, not the product name

If a fork forgets to wire any brand knob, the kernel still has to
render **something** in the FastAPI title and the MCP handshake.
Hardcoding the upstream product name there means every fork leaks
the upstream brand into their users' MCP server logs and API
gateway config — exactly the bug this plumbing exists to avoid.

The module name `mhc-desktop-backend` is a reasonable signal that
"the kernel is running but no brand was configured" — easy to
grep for in support tickets ("your MCP server says
`mhc-desktop-backend` connected, which fork are you running?").

## See also

- `mhc-desktop-backend/README.md` — kernel architecture
- `mhc-desktop-deploy/README.md` — fork & integration patterns
- `docs/PACKAGING.md` — how the bundled Python + Electron host
  ship the deploy package