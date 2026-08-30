# mhc-desktop-deploy

`mhc-desktop-deploy` is the **enterprise integration layer** for the
`mhc-desktop` Skill/MCP client. The kernel package (`mhc-desktop-backend`)
defines the HTTP surface, the chat pipeline, the storage / auth / tool-execution
contracts, and a `create_app(...)` factory with every integration point exposed
as a kwarg. This deploy package supplies the **concrete, opinionated defaults**:
file-backed stores, a mock auth provider, Shanghai-time logging, and a
`build_default_app(**overrides)` helper that wires everything.

**The deploy package is the only thing enterprises need to fork or
replace.** Every kernel contract is a Protocol; the only stable integration
points the kernel exposes are the kwargs of `create_app`. This document walks
through how to adapt each one for a real deployment.

---

## Table of contents

1. [Quick start](#quick-start)
2. [The integration surface at a glance](#the-integration-surface-at-a-glance)
3. [Forking strategy](#forking-strategy)
4. [Auth / SSO integration](#auth--sso-integration)
5. [Storage backends (Postgres, S3, Vault, …)](#storage-backends-postgres-s3-vault-)
6. [Tool execution (sandboxing, custom kinds)](#tool-execution-sandboxing-custom-kinds)
7. [RBAC / scopes](#rbac--scopes)
8. [LLM provider presets](#llm-provider-presets)
9. [System prompt, onboarding, branding](#system-prompt-onboarding-branding)
10. [Chat policy & governance knobs](#chat-policy--governance-knobs)
11. [Content packs](#content-packs)
12. [CORS & runtime metadata](#cors--runtime-metadata)
13. [Reference: every kwarg](#reference-every-kwarg)
14. [Testing your fork](#testing-your-fork)
15. [Packaging & shipping](#packaging--shipping)

---

## Quick start

```bash
# 1. Run as-is (mock auth + file-backed stores under ~/.mhc-desktop):
uv run python -m mhc_desktop_deploy

# 2. Override specific kwargs without forking:
from mhc_desktop_deploy.assemble import build_default_app
from my_pkg.oidc import OIDCAuthProvider
from my_pkg.stores import PostgresSessionStore, PostgresProviderStore

app = build_default_app(
    auth=OIDCAuthProvider(issuer="https://idp.corp/"),
    sessions=PostgresSessionStore(dsn=os.environ["PG_DSN"]),
    providers=PostgresProviderStore(dsn=os.environ["PG_DSN"]),
)

# 3. Use build_default_app from uvicorn:
# uvicorn "my_pkg.entrypoint:_factory" --factory
```

The deploy's reference `__main__` entrypoint uses `build_default_app()` with
no overrides — fork `__main__.py` if you need to wire your adapters at the
process boundary, or import `build_default_app` from your own entrypoint and
pass your overrides there.

---

## The integration surface at a glance

`create_app(**kwargs)` is the **only** integration point you need to learn.
Every kwarg has a kernel default; the deploy's `build_default_app` ships a
sensible choice for each (file-backed, mock, etc.). Override the ones you need:

| Concern                  | Kwarg                     | Default                              |
| ------------------------ | ------------------------- | ------------------------------------ |
| Identity / SSO           | `auth`                    | `MockAuthProvider` (alice/bob/demo)  |
| Auth route whitelist     | `auth_exempt_paths`       | login, health, meta, onboarding, …   |
| Reverse-proxy header     | `auth_upstream_header_prefix` | `x-mhc-upstream-`                |
| RBAC scopes              | `scope_required_for`      | `None` (no scope checks)             |
| Sessions                 | `sessions`                | file-backed JSON in `data_dir`       |
| Provider config          | `providers`               | file-backed JSON in `data_dir`       |
| Skills                   | `skills`                  | file-backed folders in `data_dir`    |
| MCP servers              | `mcp_store`               | file-backed JSON in `data_dir`       |
| MCP subprocesses         | `mcp_manager`             | local subprocess manager             |
| Tools                    | `tools`                   | file-backed JSON in `data_dir`       |
| Per-session cancel token | `stream_registry`        | in-process asyncio registry          |
| User prefs               | `prefs`                   | file-backed JSON in `data_dir`       |
| Usage metrics            | `metrics`                 | JSONL on disk in `data_dir`          |
| LLM presets              | `provider_presets`        | 6 built-ins (openai/anthropic/…)     |
| Provider type whitelist  | `provider_types`          | `{openai, anthropic}`               |
| System prompt base       | `system_prompt_base`      | kernel default (skill root hint)     |
| Onboarding cards         | `onboarding_cards`        | 3 default cards                      |
| Chat governance          | `chat_policy`             | kernel defaults (15-min tool etc.)   |
| Tool execution strategy  | `tool_executor_registry`  | `None` (kernel local-only fallback)  |
| Bundled content packs    | `content_packs_root`      | `None` (or `MHC_RESOURCES_PATH`)     |
| Runtime manifest         | `meta`                    | data_dir + bundled slots             |
| CORS origins             | `cors_origins`            | debug-mode `*`, prod `None`         |
| Data directory root      | (assemble-only) `data_dir` / `MHC_DATA_DIR` | `~/.mhc-desktop`        |

Every kwarg except `config` and `data_dir` is forwarded verbatim by
`build_default_app(**overrides)`; if `build_default_app` doesn't know a kwarg,
it passes it straight to `create_app`.

---

## Forking strategy

You have three options. Pick the one that matches your release cadence.

### Option 1 — extend in place (recommended for small changes)

Install the kernel + deploy from PyPI, then call `build_default_app` with your
overrides. Zero fork, instant upgrades from upstream.

```python
# your_app.py
from mhc_desktop_deploy.assemble import build_default_app

app = build_default_app(
    auth=MySSOProvider(...),
    sessions=PostgresSessionStore(...),
    meta={"brand": {"name": "Acme Agent"}, "theme_color": "#ff0000"},
)
```

Run with uvicorn: `uvicorn your_pkg.your_app:app`.

### Option 2 — subclass `build_default_app` (recommended for repeated customisation)

If your enterprise ships the same set of overrides for every team, write a
helper that pins them:

```python
# your_deploy/assemble.py
from mhc_desktop_deploy.assemble import build_default_app
from your_pkg.auth import OIDCAuthProvider
from your_pkg.stores import PostgresStores

def build_acme_app(**overrides):
    defaults = dict(
        auth=OIDCAuthProvider(issuer="https://idp.acme/"),
        sessions=PostgresSessions(),
        providers=PostgresProviders(),
        skills=PostgresSkills(),
        # …all the Acme-specific knobs…
        meta={
            "brand": {"name": "Acme Agent"},
            "default_locale": "en",
            "locales_supported": ["en", "fr"],
        },
    )
    defaults.update(overrides)
    return build_default_app(**defaults)
```

Teams downstream call `build_acme_app(...)` and never see the upstream kwargs.

### Option 3 — fork the package (last resort)

Only for changes that need a different entrypoint, different packaging, or
substantial custom code that doesn't fit the kwarg surface. If you must fork,
the smallest viable fork is:

```
your_company_mhc_desktop_deploy/
├── pyproject.toml            # depends on mhc-desktop-backend, your extras
└── src/your_company_mhc_desktop_deploy/
    ├── __main__.py           # the entrypoint
    ├── assemble.py           # calls build_default_app with your defaults
    ├── auth/
    │   └── oidc.py
    └── stores/
        ├── sessions.py
        └── providers.py
```

Your `assemble.py` should be a 30-line wrapper around the upstream
`build_default_app`. Don't re-implement the protocol surface — keep the
kernel contract as your upgrade boundary.

---

## Auth / SSO integration

The kernel calls `auth.login(username, password)` and `auth.resolve(token)` on
every request. Implement `AuthProviderProtocol`:

```python
from mhc_desktop_backend.protocols import AuthProviderProtocol, AuthUser

class OIDCAuthProvider(AuthProviderProtocol):
    def __init__(self, issuer: str, client_id: str, client_secret: str):
        self._oidc = OIDCClient(issuer, client_id, client_secret)

    async def login(self, username: str, password: str):
        # OIDC doesn't usually take (username, password) — adapt to your
        # IdP's auth flow. The kernel only calls login() if the SPA
        # POSTs to /api/v1/auth/login. If you use OIDC redirect flow
        # instead, the SPA should call your /auth/oidc/* endpoints
        # and mint a token via a custom auth method.
        ...

    async def resolve(self, token: str):
        claims = await self._oidc.verify(token)
        if claims is None:
            return None
        return AuthUser(
            id=claims["sub"],
            username=claims["preferred_username"],
            display_name=claims.get("name", ""),
            avatar_url=claims.get("picture"),
            upstream_credentials={"id_token": claims["raw_id_token"]},
            scopes=frozenset(claims.get("scopes", [])),
        )

    async def logout(self, token: str):
        # Token revocation is your IdP's concern.
        await self._oidc.revoke(token)
```

Pass it via `auth=`:

```python
app = build_default_app(
    auth=OIDCAuthProvider(issuer="https://idp.corp/realms/prod", ...),
)
```

### Authorisation headers the kernel preserves

Any inbound request header matching `auth_upstream_header_prefix` (default
`x-mhc-upstream-`) is captured into `request.state.upstream_headers` with the
prefix stripped. Use this when a reverse proxy (nginx, Envoy) injects the user's
identity and you want the deploy to forward it to a downstream service (skill
marketplace, internal API):

```python
@app.get("/api/v1/marketplace/orders")
async def list_orders(request: Request):
    headers = request.state.upstream_headers  # {"auth": "Bearer abc", ...}
    return await marketplace_client.get("/orders", headers=headers)
```

### Adding a new auth method (e.g. SAML, magic-link)

The kernel ships `POST /api/v1/auth/login` expecting `{username, password}`.
If your IdP doesn't fit that flow, add a new route in your deploy package:

```python
# your_deploy/routes/sso.py
from fastapi import APIRouter, Request, Response
from .auth import OIDCAuthProvider

router = APIRouter()

@router.post("/api/v1/auth/sso/callback")
async def sso_callback(request: Request):
    code = (await request.json())["code"]
    token = await OIDCAuthProvider.exchange(code)
    return {"token": token, "user": ...}
```

Then mount it on the app:

```python
app = build_default_app(auth=...)
app.include_router(your_sso_router)
```

The kernel doesn't care — your route mints a token via your provider's
`resolve(...)` path.

### Exempt paths

`auth_exempt_paths` is the set of URL prefixes that **bypass** the auth
middleware entirely. The kernel's default list is:

```python
DEFAULT_EXEMPT_PATHS = (
    "/api/v1/auth/login", "/api/v1/health", "/api/v1/meta",
    "/api/v1/onboarding", "/ready", "/docs", "/openapi.json",
    "/favicon.svg", "/assets", "/fonts",
)
```

Add your SSO callback path if you use one:

```python
app = build_default_app(
    auth=MySSO(),
    auth_exempt_paths=(
        *DEFAULT_EXEMPT_PATHS,
        "/api/v1/auth/sso/callback",  # public by design
        "/api/v1/health/live",         # k8s liveness probe
    ),
)
```

---

## Storage backends (Postgres, S3, Vault, …)

Every storage slot is a Protocol. The deploy ships file-backed reference
implementations; you can replace any subset. Below is the canonical
"Postgres everywhere" wiring.

### Sessions

```python
from mhc_desktop_backend.protocols import SessionStoreProtocol, Session
import asyncpg

class PostgresSessionStore:
    def __init__(self, dsn: str):
        self._pool = await asyncpg.create_pool(dsn)

    async def list(self):
        async with self._pool.acquire() as c:
            rows = await c.fetch("SELECT * FROM sessions ORDER BY updated_at DESC")
        return [self._row_to_session(r) for r in rows]

    # … list/get/create/update/delete/delete_many/clear_all/count_by_day/close …
```

Same shape for `ProviderStoreProtocol`, `SkillStoreProtocol`, `MCPStoreProtocol`,
`ToolStoreProtocol`, `MetricsRepositoryProtocol`, `PrefsStoreProtocol`. Wire
them in `assemble.py`:

```python
app = build_default_app(
    sessions=PostgresSessionStore(os.environ["PG_DSN"]),
    providers=PostgresProviderStore(os.environ["PG_DSN"]),
    skills=PostgresSkillStore(os.environ["PG_DSN"], media_bucket=S3SkillMedia()),
    mcp_store=PostgresMCPStore(os.environ["PG_DSN"]),
    tools=PostgresToolStore(os.environ["PG_DSN"]),
    metrics=PostgresMetricsRepository(os.environ["WAREHOUSE_DSN"]),
    prefs=PostgresPrefsStore(os.environ["PG_DSN"]),
)
```

### A note on lifecycle

Every store Protocol declares `async def close(self)`. The kernel calls it
during the FastAPI lifespan shutdown (after the active chat streams are
cancelled). Make sure your `close()` releases connections cleanly — failing
to do so shows up as "shutting down … close failed" log spam at exit.

### A note on idempotence

`bulk_install_skills / bulk_install_tools / bulk_install_mcps` (the content-packs
materialiser) calls `store.create(...)` / `update(...)` and depends on the store
raising on duplicate slug for create. The file-backed stores do; make sure
yours does too.

### Stream registry

The `StreamRegistryProtocol` is per-session cancel-token bookkeeping.
The default in-process implementation is fine for one process; if you
horizontally scale the backend, build a Redis-backed variant so all
workers can cancel a chat running on a sibling.

---

## Tool execution (sandboxing, custom kinds)

The kernel ships `kind: local` tools that `exec()` user Python source via
`run_tool`. That's fine for trusted internal users. For untrusted input
(skill marketplace downloads, user-uploaded scripts) you need sandboxing.

### Wrap the default executor

The deploy's `LocalToolExecutor` wraps the kernel's `run_tool`; you can
subclass to add an AST whitelist, drop privileges, or run in a
subprocess:

```python
import ast
from mhc_desktop_backend.protocols import ToolExecutor, ToolExecution, Tool

_ALLOWED = (
    ast.Expression, ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
    ast.Return, ast.Yield, ast.Assign, ast.AugAssign,
    ast.Constant, ast.Name, ast.Load, ast.Store,
    ast.Call, ast.Attribute,
    ast.If, ast.For, ast.While, ast.Break, ast.Continue,
    ast.Try, ast.ExceptHandler, ast.Raise,
    ast.List, ast.Tuple, ast.Dict, ast.Set,
    ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare,
    ast.Import, ast.ImportFrom,  # we audit imports explicitly
)

class SandboxedToolExecutor:
    def __init__(self, allowed_imports: set[str]):
        self._allowed_imports = allowed_imports

    async def execute(self, tool, args, *, cancel_event, timeout):
        tree = ast.parse(tool.source_path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, _ALLOWED):
                return ToolExecution(ok=False, error=f"node {type(node).__name__} not allowed")
        # also: audit ImportFrom/Import nodes against self._allowed_imports
        # ... actually run the tool via the kernel's run_tool ...
        from mhc_desktop_backend.tools.imports import run_tool, import_local_tool
        fn = await import_local_tool(tool.slug, source_text)
        chunks = []
        async for chunk in run_tool(fn, args, cancel_event=cancel_event, timeout=timeout):
            chunks.append(chunk)
        return ToolExecution(ok=True, chunks=chunks)
```

### Register executors by `kind`

A `ToolExecutorRegistryProtocol` maps a `ToolKind` string to an
`ToolExecutor`. The default registry returns `None` for every kind, which
makes the kernel fall back to its historical `run_tool` path. Replace
either per-kind or wholesale:

```python
from mhc_desktop_backend.protocols import ToolExecutorRegistryProtocol

class AcmeToolRegistry:
    def __init__(self, local_executor, sandboxed_executor, http_executor):
        self._local = local_executor
        self._sandboxed = sandboxed_executor
        self._http = http_executor

    def resolve(self, kind):
        return {
            "local": self._local,         # trusted dev tools
            "remote": self._sandboxed,    # untrusted scripts
            "http": self._http,            # outbound API calls
        }.get(kind)

app = build_default_app(
    tool_executor_registry=AcmeToolRegistry(
        local_executor=LocalToolExecutor(),
        sandboxed_executor=SandboxedToolExecutor(allowed_imports={"json", "csv"}),
        http_executor=HTTPExecutor(timeout=30),
    ),
)
```

### Custom kinds (e.g. `kind="wasm"`, `kind="grpc"`)

The kernel doesn't enumerate `ToolKind` values; the literal type in
`mhc_desktop_backend.tools.models` is a starting point. Add a new kind
without touching the kernel:

1. Set `tool.kind = "wasm"` (or any string) when creating a tool.
2. Register an executor for `"wasm"` in your registry.

The kernel surfaces your tool to the LLM via `name=tool.resolved_model_name()`
and routes its calls through your executor. The data flow is identical to the
built-in kinds.

---

## RBAC / scopes

`AuthUser.scopes: frozenset[str]` is the principal's permission set.
`install_auth(scope_required_for=path -> set[str])` returns the set of
scopes a request to `path` needs. The kernel enforces subset-of-scopes
and 403s with a list of missing scopes.

The deploy owns the vocabulary. Pick a pattern that fits your IdP:

```python
# Option A: identity-aware (recommended for OAuth/OIDC)
def scope_required_for(path: str) -> frozenset[str]:
    # Path-based rules. The middleware calls this for every
    # non-exempt request and checks user.scopes ⊇ required.
    if path.startswith("/api/v1/metrics"):
        return frozenset({"metrics:read"})
    if path.startswith("/api/v1/admin"):
        return frozenset({"admin"})
    if path.startswith("/api/v1/auth/logout"):
        return frozenset({"session:write"})
    return frozenset()  # no scope required

# Option B: role-aware (recommended for LDAP)
ROLE_SCOPES = {
    "user": frozenset({"metrics:read", "session:write"}),
    "admin": frozenset({"metrics:read", "admin", "providers:write", "session:write"}),
}
def scope_required_for(path: str) -> frozenset[str]:
    if path.startswith("/api/v1/admin"):
        return frozenset({"admin"})
    return frozenset({"session:write"})  # every authenticated user
```

Then on the auth side, set `scopes` from your IdP's claims:

```python
async def resolve(self, token):
    claims = await self._oidc.verify(token)
    roles = claims.get("realm_access", {}).get("roles", [])
    scopes = set()
    for r in roles:
        scopes |= ROLE_SCOPES.get(r, set())
    return AuthUser(
        id=claims["sub"], username=claims["preferred_username"],
        display_name=claims.get("name", ""),
        scopes=frozenset(scopes),
    )
```

Wire the rule via:

```python
app = build_default_app(
    auth=MyOIDC(),
    scope_required_for=scope_required_for,
)
```

`scope_required_for(path)` is called for every non-exempt request. The
default `None` disables scope enforcement — every authenticated user
can hit every non-exempt route.

---

## LLM provider presets

`provider_presets` is the list returned by `GET /api/v1/providers/presets`
(seed for the "add provider" form in the UI). Each entry is a `Preset`
dataclass:

```python
from mhc_desktop_backend.llm.presets import Preset

PRESETS = [
    Preset(
        id="acme-internal",
        label="Acme Internal (Azure)",
        description="Acme's private Azure OpenAI deployment",
        provider_type="openai",
        base_url="https://acme.openai.azure.com/openai/deployments/gpt-4o",
        default_model="gpt-4o",
        models=[
            {"code": "gpt-4o", "display_name": "GPT-4o", "max_context": 128000},
        ],
    ),
    # …hide the upstream openai/deepseek/ollama presets if you only allow
    # Acme-vetted vendors…
]

app = build_default_app(provider_presets=PRESETS)
```

`provider_types` is the **whitelist** for the `provider_type` field on
`POST /api/v1/providers` and `PUT /api/v1/providers/{name}`. The kernel
default is `{"openai", "anthropic"}`. Tighten it to match your vendor policy:

```python
app = build_default_app(
    provider_types={"acme_azure", "acme_bedrock"},
    provider_presets=PRESETS,
)
```

`provider_type` values that don't appear in your whitelist are rejected
with 400 by the kernel.

### Per-request LLM headers

`llm_extra_headers_provider` lets you attach custom HTTP headers to
**every outbound LLM call** (chat + auto-title), computed per request
from the resolved principal — the seam for carrying the SSO user's
identity (username / tenant / IdP session token) so an upstream
gateway can enforce quotas or attribute spend:

```python
import os
from mhc_desktop_deploy.assemble import build_default_app

async def upstream_headers(request_user) -> dict[str, str]:
    """request_user is kernel's AuthUser (or None in debug mode)."""
    creds = (request_user.upstream_credentials or {}) if request_user else {}
    return {
        "X-Mhc-User-Id": request_user.username if request_user else "",
        "X-Mhc-Tenant-Id": os.environ["MHC_TENANT"],
        **({"Authorization": f"Bearer {creds['auth']}"} if creds.get("auth") else {}),
    }

app = build_default_app(llm_extra_headers_provider=upstream_headers)
```

The factory is awaited once per LLM call; static per-provider headers
(``Provider.headers`` in `providers.json`) are merged underneath and
your per-request result wins on conflicts.

---

## System prompt, onboarding, branding

### System prompt base

`system_prompt_base` is the string injected at the head of every chat's
system prompt. The kernel default tells the model where the skills live:

```
Skills are folders on this machine.
Their contents (scripts, references, assets) live under:
  /home/user/.mhc-desktop/skills/<slug>/
```

Override with a per-tenant or compliance-specific base:

```python
app = build_default_app(
    system_prompt_base=(
        "You are the Acme Engineering Assistant.\n"
        "- Never reveal internal codenames.\n"
        "- Prefer code from acme-internal/skills/ when available.\n"
        f"- Today's date: {date.today()}\n"
    ),
)
```

The user-authored system-prompt addition (Settings → "AI behavior" →
"System prompt addition") is appended after the base. Deploys use the
base for hard policy, users use the addition for tone and identity.

### Onboarding cards

`onboarding_cards` is the list served by `GET /api/v1/onboarding` on the
renderer's cold start. The card schema is in
`mhc_desktop_backend.onboarding.OnboardingCard`:

```python
from mhc_desktop_backend.onboarding import OnboardingCard

CARDS = [
    OnboardingCard(
        id="welcome",
        type="centered",
        title="Welcome to Acme Agent",
        body="A focused agent workspace for Acme engineers.",
        title_i18n={"en": "Welcome to Acme Agent", "zh": "欢迎使用 Acme Agent"},
        body_i18n={"en": "...", "zh": "..."},
        media_kind="none",
    ),
    OnboardingCard(
        id="acme-skills",
        type="media-text",
        title="Acme skills",
        body="Drop a SKILL.md from acme-internal/skills/ to share it.",
        title_i18n={"en": "Acme skills", "zh": "Acme 技能"},
        body_i18n={"en": "...", "zh": "..."},
        media_kind="image",
        media_image="/onboarding/acme-skills.svg",  # your asset
        media_color="#5b8def",
        media_label="ACME",
    ),
    # … more cards …
]

app = build_default_app(onboarding_cards=CARDS)
```

The renderer reads these on cold start; missing fields are tolerated (the
card just renders as text-only). The renderer doesn't need to be
rebuilt to swap card content — `GET /api/v1/onboarding` is polled on
every launch.

### Runtime manifest (`/api/v1/meta`)

The renderer hits `/api/v1/meta` to read brand, data_dir, default
locale, and the bundled-content catalogue. The deploy seeds a minimal
manifest; merge in your brand:

```python
app = build_default_app(
    meta={
        "brand": {
            "name": "Acme Agent",
            "primary_color": "#ff0000",
            "logo_url": "/brand.svg",
        },
        "default_locale": "en",
        "locales_supported": ["en", "fr"],
        "bundled": {
            "skills": ["acme-skill-1", "acme-skill-2"],
            "mcps": ["acme-jira"],
            "tools": ["acme-internal-tool"],
        },
    },
)
```

The deploy deep-merges `bundled` so you can set just `bundled.skills`
without losing the empty defaults for `mcps` / `tools`. The
`/api/v1/meta` endpoint is in the default auth-exempt set — the
renderer can read it before login.

---

## Chat policy & governance knobs

`ChatPolicy` is a frozen dataclass with the chat-loop limits that used
to be module-level constants. Tighten them for compliance:

```python
from mhc_desktop_backend.protocols import ChatPolicy

policy = ChatPolicy(
    tool_timeout_seconds=30,        # was 900; tighter for finance
    inline_file_max_bytes=8 * 1024, # was 16 KiB
    inline_skill_max_bytes=32 * 1024,
    max_tool_rounds=20,             # was 2000; cap the agent's chain
    system_prompt_addition_max_bytes=2 * 1024,
)

app = build_default_app(chat_policy=policy)
```

These are read by the chat loop on every request, so the dataclass must
be `frozen=True` (it is). The deploy's `build_default_app` keeps the
kernel defaults if you don't pass one — pick the field you want to
override, the rest stay put.

---

## Content packs

`content_packs_root` is the directory the kernel walks at boot to install
bundled skills / tools / MCPs into the user data dir. The deploy's
default is the legacy Electron-host convention:

```
$MHC_RESOURCES_PATH/content-packs/
  skills/<slug>/SKILL.md
  tools/<slug>/tool.py
  mcp/<slug>/config.json
```

The deploy's `assemble.py` reads `MHC_RESOURCES_PATH` if you don't pass
`content_packs_root`. The Electron app's `main.ts` sets the env var
when it spawns the Python backend. If your deployment doesn't ship
through Electron, pass the path explicitly:

```python
app = build_default_app(
    content_packs_root=Path("/srv/mhc/bundled-packs"),
)
```

The materialiser skips units whose slug already exists (so user edits
to bundled skills aren't overwritten by the next launch). To force a
re-install on every boot (CI / kiosk mode), point at a different root
per release.

---

## CORS & runtime metadata

### CORS

`cors_origins` is the list of allowed origins for the CORS middleware.
The default is:

| `cfg.debug` | `cors_origins` value | Behaviour |
| ----------- | -------------------- | --------- |
| `True`      | `["*"]`               | open in dev |
| `False`     | `None`               | no CORS middleware (Electron talks to localhost without CORS) |

For a browser-only deploy, set explicit origins:

```python
app = build_default_app(
    cors_origins=["https://agent.acme.com"],
)
```

For a multi-tenant SaaS, generate the list from your tenant registry:

```python
def build_for_tenant(tenant: str):
    return build_default_app(
        cors_origins=[f"https://{tenant}.acme.com"],
    )
```

### Runtime manifest

See [Runtime manifest](#runtime-manifest-api-v1meta) above.

---

## Reference: every kwarg

Alphabetised, with kernel default and deploy wiring:

| Kwarg | Type | Kernel default | Deploy wires |
| ----- | ---- | -------------- | ------------ |
| `auth` | `AuthProviderProtocol` | required (fail-closed in non-debug) | `MockAuthProvider()` |
| `auth_exempt_paths` | `tuple[str, ...]` | `DEFAULT_EXEMPT_PATHS` (login, health, meta, onboarding, ready, docs, openapi, favicon, /assets, /fonts) | inherited |
| `auth_upstream_header_prefix` | `str` | `"x-mhc-upstream-"` | inherited |
| `chat_policy` | `ChatPolicy` | `ChatPolicy()` defaults | inherited |
| `config` | `Config \| None` | `load_config()` | `load_config()` |
| `content_packs_root` | `Path \| None` | `None` | `Path(MHC_RESOURCES_PATH)/"content-packs"` if env set |
| `cors_origins` | `list[str] \| None` | `["*"]` if `cfg.debug` else `None` | inherited |
| `data_dir` | `Path \| None` *(assemble-only)* | n/a | `MHC_DATA_DIR` env or `~/.mhc-desktop` |
| `mcp_manager` | `MCPManagerProtocol` | required for MCP tool calls | `MCPManager(default_mcp_store(data_dir))` |
| `mcp_store` | `MCPStoreProtocol` | required for MCP config CRUD | `default_mcp_store(data_dir)` |
| `meta` | `dict \| None` | `{}` (empty) | `{version, data_dir, debug, bundled: {…}}` |
| `llm_extra_headers_provider` | `Callable[[AuthUser \| None], Awaitable[dict[str, str]]] \| None` | `None` (no extra headers) | inherited |
| `metrics` | `MetricsRepositoryProtocol` | required for `/api/v1/metrics/*` | `default_metrics_repo()` |
| `onboarding_cards` | `list[OnboardingCard] \| None` | `DEFAULT_ONBOARDING_CARDS` (3 cards) | inherited |
| `prefs` | `PrefsStoreProtocol` | required for `/api/v1/prefs/*` | `default_prefs_store(data_dir)` |
| `provider_presets` | `list[Preset]` | 6 built-in presets | inherited |
| `provider_types` | `frozenset[str]` | `{"openai", "anthropic"}` | inherited |
| `providers` | `ProviderStoreProtocol` | required for `/api/v1/providers/*` | `default_provider_store(data_dir)` |
| `scope_required_for` | `Callable[[str], frozenset[str]] \| None` | `None` (no scope checks) | inherited |
| `sessions` | `SessionStoreProtocol` | required for `/api/v1/sessions/*` | `default_session_store(data_dir)` |
| `skills` | `SkillStoreProtocol` | required for `/api/v1/skills/*` | `default_skill_store(data_dir)` |
| `stream_registry` | `StreamRegistryProtocol` | required for chat cancel/cleanup | `default_stream_registry()` |
| `system_prompt_base` | `str \| None` | kernel default base | inherited |
| `tool_executor_registry` | `ToolExecutorRegistryProtocol \| None` | `None` (kernel local-only) | inherited |
| `tools` | `ToolStoreProtocol` | required for `/api/v1/tools/*` | `default_tool_store(data_dir)` |

---

## Testing your fork

### Unit tests

The deploy ships `packages/mhc-desktop-deploy/tests/test_assemble.py`
covering the wiring contract. Add tests next to it:

```python
# your_pkg/tests/test_acme.py
import pytest
from mhc_desktop_deploy.assemble import build_default_app
from your_pkg.auth import OIDCAuthProvider

def test_oidc_provider_is_wired():
    oidc = OIDCAuthProvider(issuer="https://idp.test/")
    app = build_default_app(auth=oidc)
    assert app.state.auth_provider is oidc

def test_metrics_endpoints_401_without_token():
    app = build_default_app(auth=OIDCAuthProvider(...))
    from fastapi.testclient import TestClient
    c = TestClient(app)
    assert c.get("/api/v1/metrics/summary").status_code == 401
```

Run with:

```bash
uv run pytest packages/mhc-desktop-deploy/tests/ your_pkg/tests/ -v
```

### End-to-end smoke test

The kernel ships `e2e-post-refactor-smoke.cjs` (root of the repo) — an
HTTP-only smoke that exercises the public API surface. Reuse it for CI:

```bash
# 1. Start the backend
(uv run python -m mhc_desktop_deploy &) ; sleep 5
# 2. Run the smoke
node e2e-post-refactor-smoke.cjs
# 3. Tear down
pkill -f mhc_desktop_deploy
```

The smoke is 17 checks: public endpoints, auth flow, `/bundled` route
removal, provider CRUD, skill CRUD, prefs round-trip. The renderer
in the Electron host is a separate concern (covered by the
`packages/mhc-desktop-frontend/scripts/e2e-*.mjs` CDP-driven tests).

### Pinning the contract

The deploy wires every Protocol slot; if a downstream consumer (CI, a
forked deploy, your own integration test) wants to assert the contract,
build a single `build_default_app(**kwargs)` and snapshot the
resulting `app.state` keys:

```python
def test_state_keys():
    app = build_default_app()
    assert set(app.state.__dict__.keys()) >= {
        "provider_store", "session_store", "skill_store",
        "mcp_store", "mcp_manager", "tool_store",
        "stream_registry", "prefs_store", "metrics_repo",
        "auth_provider", "provider_presets", "provider_types",
        "chat_policy", "onboarding_cards", "meta",
    }
```

---

## Packaging & shipping

### As a deployable artifact

For non-Electron deployments (a Linux server, a Kubernetes pod, a
containerised daemon), the smallest shipping unit is your fork's
`assemble.py` plus the kernel + deploy wheels. A typical Dockerfile:

```dockerfile
FROM python:3.12-slim
RUN pip install --no-cache-dir \
    mhc-desktop-backend==0.1.0 \
    mhc-desktop-deploy==0.1.0 \
    your-company-deploy==1.0.0
COPY your_company_deploy/ /app/your_company_deploy/
ENV MHC_DATA_DIR=/var/lib/mhc
ENV MHC_HOST=0.0.0.0
ENV MHC_PORT=8765
EXPOSE 8765
CMD ["uvicorn", "your_company_deploy.entrypoint:app", "--host", "0.0.0.0", "--port", "8765", "--factory"]
```

Your `entrypoint.py` calls `build_your_app()` and returns the FastAPI
app. Mount it in your reverse proxy (nginx / Envoy / Traefik) with TLS
and SSO header injection; the `auth_upstream_header_prefix` defaults
to `x-mhc-upstream-` so a typical "X-Forwarded-User + X-Forwarded-Email"
setup needs a 5-line adapter.

### As an Electron installer

If you're shipping a desktop product (the same shape as the upstream
`mhc-desktop-app` Electron build), you can:

1. Fork `packages/mhc-desktop-app/` and `packages/mhc-desktop-frontend/`.
2. Use the upstream `scripts/build-spa.ps1` (or `.sh`) to stage the
   built SPA into the backend's `static/` dir.
3. Use `scripts/build-bundled-python.ps1` (or `.sh`) to install the
   kernel + your fork of deploy into the bundled PBS Python.
4. Run `npx electron-builder --win --x64` (or `--mac` / `--linux`).

The bundled Python runs `python -m your_company_deploy` instead of
`python -m mhc_desktop_deploy`. Edit `main.ts` (or your fork's
equivalent) to point at your entrypoint.

### Versioning

The deploy pins the kernel via `pyproject.toml`:

```toml
[project]
dependencies = ["mhc-desktop-backend==0.1.0"]
```

In your fork, pin a range:

```toml
dependencies = [
    "mhc-desktop-backend>=0.1,<0.3",
    "mhc-desktop-deploy>=0.1,<0.3",
]
```

Read the kernel's release notes for each bump — the `create_app` signature
is the only thing the kernel can break without warning, and the release
notes call it out. New kwargs are added without breaking changes; renamed
or removed kwargs trigger a major version.

---

## Common questions

**Q. Where does the kernel stop and the deploy begin?**
A. The kernel is the FastAPI app + the protocols + the chat loop + the
tool execution contract. The deploy supplies concrete adapters (file
backed, mock, etc.) and the `build_default_app` helper. Anything you
override via a `create_app` kwarg lives in the deploy; anything you reach
by importing `mhc_desktop_backend.*` is kernel.

**Q. Can I add a new HTTP route in the deploy without touching the kernel?**
A. Yes. `build_default_app` returns a `FastAPI` instance — mount your
routers on it:

```python
app = build_default_app(auth=MySSO(), scope_required_for=...)
app.include_router(my_custom_router)
```

Just remember to mark your route as auth-exempt if it's a public callback
(see `auth_exempt_paths`).

**Q. Can I change the SystemPromptBaseProvider (the per-request callable shape)?**
A. The kernel now treats `system_prompt_base` as `str | None`. A callable
is no longer supported in the kernel signature — use a string and compute
its content in your `assemble.py` from a closure over tenant state.

**Q. How do I add a new storage slot that the kernel doesn't know about?**
A. The kernel's storage is closed-set (the Protocols in
`mhc_desktop_backend.protocols`). If you need a new kind of store
(e.g. an "audit log" with kernel-wide query), open a feature request
upstream; the kernel Protocol is the contract that consumers depend on.

**Q. The mock auth ships with three demo users. Is that safe for production?**
A. **No.** The kernel's fail-closed check refuses to boot in non-debug
mode with `auth=None`, so production deploys **must** wire a real auth
provider via the `auth=` kwarg. The mock is dev-only.

**Q. Where do I add my custom `ToolKind`?**
A. Don't modify the kernel. The literal type
`Literal["local", "script", "remote"]` in
`mhc_desktop_backend.tools.models` is documentation, not a runtime
constraint. Set `tool.kind = "wasm"` (or any string) when creating a
tool, and register an executor for that key in your
`ToolExecutorRegistryProtocol` implementation.
