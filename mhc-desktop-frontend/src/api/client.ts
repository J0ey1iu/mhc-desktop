// Typed API client for mhc-desktop-backend.
//
// All endpoints are relative so the same code works through the Vite
// proxy in dev. In production the Electron host injects
// ``window.__MHC_BACKEND_URL`` (e.g. ``http://127.0.0.1:8765``) into
// the SPA before loading it, so the renderer can find the bundled
// Python backend without depending on a proxy or reverse origin.
//
// ``API_BASE`` is resolved once at module load. In dev it's the empty
// string (relative URLs hit the vite proxy); in production it's the
// absolute backend URL the Electron host picked at startup.

import { locale } from "../i18n"
import { getAuthToken, getUpstreamCredential } from "./auth-token"

/** ``window.__MHC_BACKEND_URL`` injected by the Electron host when
 *  loading the SPA from disk (file://) in production. Undefined in
 *  dev mode (vite proxy handles the relative URLs). */
function resolveApiBase(): string {
  if (typeof window === "undefined") return ""
  const v = (window as unknown as { __MHC_BACKEND_URL?: string }).__MHC_BACKEND_URL
  return typeof v === "string" && v ? v.replace(/\/+$/, "") : ""
}

const API_BASE = resolveApiBase()

/** Wrap ``window.fetch`` so every call automatically attaches the
 *  bearer token the auth store holds (if any). ``init.headers`` is
 *  preserved when the caller supplies its own.
 *
 *  We deliberately do not pass through ``init.headers`` verbatim
 *  because the dev path uses the vite proxy (relative URLs) and
 *  the prod path injects ``__MHC_BACKEND_URL`` so the same code can
 *  hit either. Adding ``Authorization`` here means callers don't
 *  have to remember per-call.
 */
function authedFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const token = getAuthToken()
  const upstream = getUpstreamCredential()
  if (!token && !upstream) return fetch(input, init)
  const headers: Record<string, string> = {}
  // Coerce whatever the caller passed (Headers / object / undefined)
  // into a plain dict so we can merge our header on top.
  if (init.headers) {
    const h = init.headers as
      | Headers
      | Record<string, string>
      | Array<[string, string]>
    if (typeof (h as Headers).forEach === "function") {
      ;(h as Headers).forEach((v, k) => {
        headers[k] = v
      })
    } else if (Array.isArray(h)) {
      for (const [k, v] of h) headers[k] = v
    } else {
      Object.assign(headers, h as Record<string, string>)
    }
  }
  // Kernel middleware picks up anything prefixed with
  // ``x-mhc-upstream-`` and stashes it (prefix-stripped) under
  // ``request.state.upstream_headers`` for deploy adapters (e.g. a
  // marketplace provider) to forward upstream.
  if (upstream) headers["X-MHC-Upstream-Auth"] = upstream
  if (token) headers["Authorization"] = `Bearer ${token}`
  return fetch(input, { ...init, headers })
}

export type ProviderType = "openai" | "anthropic"

// ── Auth ────────────────────────────────────────────────────────────
export interface AuthUser {
  id: string
  username: string
  display_name: string
  avatar_url: string | null
}

// ── Tools (third concept — local / bundled / script / remote) ────

export type ToolKind = "local" | "script" | "remote"

export interface Tool {
  slug: string
  name: string
  description: string
  kind: ToolKind
  parameters: Record<string, unknown>
  endpoint_url?: string
  script_path?: string
  /** What the LLM sees as the function.name. Empty means use slug. */
  model_name?: string
  enabled: boolean
  origin: "bundled" | "imported" | "local"
  /** Localized display names keyed by language tag (e.g.
   *  "en", "zh"). The LLM never sees these — they're for
   *  the UI only. Falls back to ``name`` when the current
   *  locale has no entry. */
  display_name_i18n?: Record<string, string>
  source_path: string
  version: string
  license: string
  created_at: string
  updated_at: string
}

export interface ToolExport {
  schema: "mhc-tool.v1"
  slug: string
  name: string
  description: string
  kind: ToolKind
  parameters: Record<string, unknown>
  endpoint_url?: string
  endpoint_auth_header?: string
  script_path?: string
}

export interface ProviderModel {
  code: string
  display_name?: string
  max_context?: number
}

export interface Provider {
  name: string
  provider_type: ProviderType
  api_key: string  // already masked to "***xxxx" by the backend
  base_url: string
  default_model: string
  description: string
  /** Default context budget for any model that doesn't carry its own
   *  ``max_context``. Zero means unknown — the context meter hides
   *  itself when the model has no max_context info. */
  max_context_default?: number
  /** Extra request-body fields merged into every LLM call for this
   *  provider (e.g. ``{"reasoning_effort": "high"}`` so vendors that
   *  only emit ``reasoning_content`` when asked actually do). */
  model_params?: Record<string, unknown>
  models: ProviderModel[]
  enabled?: boolean
  created_at: string
  updated_at: string
}

export interface ChatMessage {
  role: "system" | "user" | "assistant" | "tool"
  content: string
  /** Persisted display-tool-call id (for ``role: "tool"`` segments
   *  the UI replays from the segment timeline on reload). */
  tool_call_id?: string
  /** Skill slugs the user attached to this turn. Persisted on user
   * messages so the UI can show which skills were in scope when the
   * reply was generated. The backend reads this back on each LLM
   * call so the controller's agent loop re-injects the same skills
   * on every request inside one run. */
  skills?: string[]
  /** MCP slugs the user attached to this turn. Mirrors ``skills``
   * — same per-turn scope, same persistence, same UI badge. */
  mcp?: string[]
  /** Tool slugs the user attached to this turn. Same per-turn
   *  scope as ``skills`` / ``mcp``; the backend reads this to know
   *  which tool schemas to expose to the LLM for THIS message. */
  tools?: string[]
  /** Files the user attached to this turn. Persisted as metadata
   * only — the binary stays on disk; the model receives only the
   * absolute paths, spliced into the user message content by the
   * backend just before the LLM call. The same augmented content
   * is reused across the controller's loop iterations so the
   * provider-side prompt cache key stays stable. On reload the
   * UI rebuilds the chip row from this metadata. */
  files?: ChatFileAttachment[]
  /** Persisted tool calls on an assistant message. The chat SSE
   *  stream does NOT re-send tool calls for every event; it emits
   *  ``tool_call_start`` / ``tool_call_done`` while streaming and
   *  the frontend accumulates them locally. On persist we ship the
   *  full tool_calls array so a session reload can replay the same
   *  capsule row. Backend ignores this field for LLM-routing; it
   *  is purely a display-persistence concern. */
  tool_calls?: ToolCallPersistence[]
  /** Ordered assistant-message timeline; see ``MessageSegmentPersistence``. */
  segments?: MessageSegmentPersistence[]
  /** ``true`` when an assistant message was cancelled mid-stream
   *  (user pressed stop). Persisted so a reloaded session shows the
   *  same partial reply + cancellation hint as the live view.
   *  Backend treats it as display metadata — never sent to the
   *  LLM as part of a message. */
  cancelled?: boolean
}

/** Wire-format shape of a chat message in the outgoing ``/api/v1/chat``
 *  request payload — mirrors what the OpenAI-compatible adapter
 *  actually reads. Distinct from ``ChatMessage`` because the wire
 *  needs OpenAI-shaped ``tool_calls`` (``{id, type, function:{...}}``)
 *  whereas persistence needs the frontend's display shape
 *  (``ToolCallPersistence``). Keeping them as separate types forces
 *  the conversion at one boundary (``ChatView.buildLLMMessages``)
 *  instead of letting two shapes hide under the same field name. */
export interface ChatRequestMessage {
  role: "system" | "user" | "assistant" | "tool"
  content: string
  tool_call_id?: string
  mcp?: string[]
  tools?: string[]
  files?: ChatFileAttachment[]
  tool_calls?: LLMToolCall[]
}

export interface ChatFileAttachment {
  /** Display name; the basename of the original file. */
  name: string
  /** Absolute path on the user's machine — what the local Agent
   *  passes to its tools to actually read the file. The model
   *  never receives the bytes. */
  path: string
  /** Best-effort size hint (bytes). Optional because not every
   *  browser exposes it before the user finishes picking the
   *  file. */
  size?: number
  /** MIME type from the OS picker (or empty string if unknown).
   *  Used by the chip tooltip, not by the LLM. */
  type?: string
}

export interface ToolCallPersistence {
  call_id: string
  kind: "mcp" | "tool"
  name: string  // "<mcp-slug>::<tool-name>" OR "<tool-name>"
  args: Record<string, unknown>
  result?: string
  error?: string
  ok?: boolean
  cancelled?: boolean
  status: "pending" | "executing" | "success" | "error"
  /** Epoch ms when the tool_start event landed. Drives the
   *  elapsed-time counter in the capsule on reload. */
  startedAt?: number
  /** Final duration in ms for completed calls. Shown as
   *  "ran in 3.2s" once the call ends. */
  durationMs?: number
}

/** Wire shape for a tool call emitted on an assistant message in
 *  the chat request payload. Mirrors OpenAI's format (``arguments``
 *  is a JSON-encoded string) so the LLM provider gets the structure
 *  it already understands — the frontend just translates from its
 *  internal ``ToolCallPersistence`` at send time. */
export interface LLMToolCall {
  id: string
  type: "function"
  function: {
    name: string
    arguments: string
  }
}

/** Persisted form of ``MessageSegment``: the ordered timeline of an
 *  assistant message (text runs + tool calls in delivery order).
 *  Shipped on the message payload so a session reload renders the
 *  reply exactly as it streamed (thinking, prose and capsules
 *  interleaved), not as all-text-then-all-capsules. */
export type MessageSegmentPersistence =
  | { kind: "text"; content: string }
  | { kind: "thinking"; content: string }
  | { kind: "tool"; call: ToolCallPersistence }

export interface ChatRequest {
  provider: string
  model?: string
  messages: ChatRequestMessage[]
  skills?: string[]  // active skill slugs for this message
  skill_authors?: Record<string, string>  // slug → market author (for same-name disambiguation)
  mcp?: string[]    // active MCP slugs for this message
  /** Session id echoed back on every SSE event so the frontend can
   *  route events into the right consumer when multiple sessions
   *  are streaming in parallel. */
  session_id?: string
  /** Frontend-assigned uuid for the assistant message we're filling.
   *  Used by the backend's ``cancelled`` event so the frontend knows
   *  which in-flight node to mark partial. */
  assistant_message_id?: string
}

// ── Skills ──────────────────────────────────────────────────────────────────

export interface Skill {
  slug: string
  sha?: string  // content fingerprint (list endpoint)
  name: string
  description: string
  files: string[]
  enabled: boolean
  origin: "bundled" | "imported" | "local" | "market"
  source_path: string
  version: string
  license: string
  icon?: string
  created_at: string
  updated_at: string
}

export interface SkillDetail extends Skill {
  body: string  // markdown body of SKILL.md (no frontmatter)
}

// ── Skill market ─────────────────────────────────────────────────────────

export interface MarketSkill {
  slug: string
  display_name: string
  description: string
  category: string
  author: string
  icon?: string
  sha: string
  size: number
  downloads: number
  updated_at: number
  published_at: number
  /** Publish-time extension fields forwarded by the kernel
   *  (e.g. source_type / source_ref), empty when absent. */
  meta?: Record<string, string>
}

export interface MarketStory {
  id: string
  title: string
  author: string
  skill_slug: string
  content: string
  created_at: number
}

export interface MarketFile {
  path: string
  content: string
}

export type SyncAction =
  | "up-to-date"
  | "push"
  | "pull"
  | "conflict"
  | "cloud-deleted"

export interface SyncPlan {
  actions: Record<
    string,
    {
      action: SyncAction
      local_sha: string | null
      remote_sha: string | null
      base_sha: string | null
    }
  >
  conflicts: string[]
  authors?: Record<string, string>  // cloud copy slug → market entry author
  market_slugs?: Record<string, string>  // cloud copy slug → matched market key
  delisted?: Record<string, boolean>  // cloud copy slug → matched entry delisted
  local_set_sha?: string
  remote_set_sha?: string
  in_sync?: boolean
}

export interface SyncResult {
  pushed: string[]
  pulled: string[]
  conflicts: string[]
  errors: { slug: string; detail: string }[]
}

// ── MCP ────────────────────────────────────────────────────────────────────

export interface MCPServer {
  slug: string
  name: string
  description: string
  command: string
  args: string[]
  env: Record<string, string>
  enabled: boolean
  origin: "bundled" | "imported" | "local"
  /** Localized display names keyed by language tag. UI-only;
   *  the LLM never sees these. */
  display_name_i18n?: Record<string, string>
  tools: MCPToolSchema[]
  last_connected_at: string
  last_error: string
  created_at: string
  updated_at: string
}

export interface MCPToolSchema {
  name: string
  description: string
  inputSchema: {
    type: "object"
    properties?: Record<string, unknown>
    required?: string[]
    additionalProperties?: boolean
  }
}

// ── Sessions ────────────────────────────────────────────────────────────────

export interface SessionSummary {
  id: string
  title: string
  provider: string
  model: string
  created_at: string
  updated_at: string
}

export interface Session extends SessionSummary {
  messages: ChatMessage[]
}

// ── User preferences ──────────────────────────────────────────────────────────

/** Cross-session user preferences. Mirrors the backend PrefsStore. */
export interface Prefs {
  /** The user's own addition to the system prompt. Sent as a system-role
   *  message on every chat; the server prepends a fixed base (skill root,
   *  cwd discipline) so the user can edit this without erasing those. */
  system_prompt_addition: string
  updated_at: string
}

const j = async <T>(r: Response): Promise<T> => {
  if (!r.ok) {
    let detail = `${r.status} ${r.statusText}`
    try {
      const body = (await r.json()) as { detail?: unknown }
      if (body && typeof body.detail === "string") detail = body.detail
    } catch {
      // ignore body parse error
    }
    throw new Error(detail)
  }
  if (r.status === 204) return undefined as unknown as T
  return (await r.json()) as T
}

// ── Usage metrics ─────────────────────────────────────────────────────────────

/** Per-model stats inside a summary. */
export interface ModelPerf {
  provider: string
  model: string
  call_count: number
  avg_duration_ms: number
  avg_tokens: number
  p50_ms: number
  p95_ms: number
  p99_ms: number
}

/** Top-card totals for a date range (defaults to all history when both bounds are null). */
export interface MetricsSummary {
  generated_at: string
  date_from: string | null
  date_to: string | null
  llm_call_count: number
  llm_error_count: number
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  avg_duration_ms: number
  error_rate: number
  avg_tokens_per_call: number
  tool_call_count: number
  tool_error_count: number
  skill_call_count: number
  mcp_call_count: number
  conversation_count: number
  model_perf: ModelPerf[]
}

/** One row of a ranking table. */
export interface MetricsRankingItem {
  name: string
  count: number
  error_count: number
  error_rate: number
  avg_duration_ms: number
  p50_ms: number
  p95_ms: number
  p99_ms: number
  avg_tokens: number
}

export type MetricsRankingKind = "tools" | "skills" | "mcps" | "models"

export interface MetricsRankingPage {
  kind: MetricsRankingKind
  items: MetricsRankingItem[]
  total: number
  page: number
  page_size: number
}

/** One day of aggregated metrics for trend charts. */
export interface MetricsTrendPoint {
  date: string
  llm_calls: number
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  tool_calls: number
  skill_calls: number
  mcp_calls: number
  conversations: number
  avg_duration_ms: number
}

export interface MetricsTrend {
  date_from: string | null
  date_to: string | null
  points: MetricsTrendPoint[]
}

export const api = {
  health: () => authedFetch(`${API_BASE}/api/v1/health`).then(j<{ status: string; version: string }>),

  /** Runtime manifest — brand, data dir, bundled-content catalogue.
   *  Populated by the deploy via ``create_app(meta=...)``. Always
   *  returns at least ``{"{"}meta: {}}``; missing keys are
   *  caller-handled as "use the bundled default". No auth required
   *  — the renderer wants this before login. */
  meta: () =>
    authedFetch(`${API_BASE}/api/v1/meta`).then(
      j<{ meta: Record<string, any> }>,
    ),

  // ── Auth ──────────────────────────────────────────────────────────────────
  // ``login`` uses the raw ``fetch`` because the wrapper would
  // otherwise attach a stale token to the login call (which the
  // server would happily reject as 401, masking the real cause of
  // failure). ``logout`` and ``me`` go through the wrapper.
  login: (username: string, password: string) =>
    fetch(`${API_BASE}/api/v1/auth/login`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ username, password }),
    }).then(
      j<{ token: string; user: AuthUser; upstream_credential: string | null }>,
    ),

  logout: () =>
    authedFetch(`${API_BASE}/api/v1/auth/logout`, { method: "POST" }).then(
      j<undefined>,
    ),

  me: () =>
    authedFetch(`${API_BASE}/api/v1/auth/me`).then(
      j<{ user: AuthUser; upstream_credential: string | null }>,
    ),

  getPrefs: () => authedFetch(`${API_BASE}/api/v1/prefs`).then(j<Prefs>),
  updatePrefs: (body: Partial<Prefs>) =>
    authedFetch(`${API_BASE}/api/v1/prefs`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then(j<Prefs>),

  listProviders: () => authedFetch(`${API_BASE}/api/v1/providers`).then(j<Provider[]>),

  createProvider: (body: Partial<Provider> & { name: string }) =>
    authedFetch(`${API_BASE}/api/v1/providers`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then(j<Provider>),

  updateProvider: (name: string, body: Partial<Provider>) =>
    authedFetch(`${API_BASE}/api/v1/providers/${encodeURIComponent(name)}`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then(j<Provider>),

  deleteProvider: (name: string) =>
    authedFetch(`${API_BASE}/api/v1/providers/${encodeURIComponent(name)}`, { method: "DELETE" }).then(
      j<undefined>,
    ),

  listSessions: () => authedFetch(`${API_BASE}/api/v1/sessions`).then(j<SessionSummary[]>),

  createSession: (body: Partial<Session> = {}) =>
    authedFetch(`${API_BASE}/api/v1/sessions`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then(j<Session>),

  getSession: (id: string) =>
    authedFetch(`${API_BASE}/api/v1/sessions/${encodeURIComponent(id)}`).then(j<Session>),

  updateSession: (id: string, body: Partial<Session>) =>
    authedFetch(`${API_BASE}/api/v1/sessions/${encodeURIComponent(id)}`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then(j<Session>),

  deleteSession: (id: string) =>
    authedFetch(`${API_BASE}/api/v1/sessions/${encodeURIComponent(id)}`, { method: "DELETE" }).then(
      j<undefined>,
    ),

  deleteManySessions: (ids: string[]) =>
    authedFetch(`${API_BASE}/api/v1/sessions/delete-many`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ ids }),
    }).then(j<{ removed: number }>),

  clearSessions: () =>
    authedFetch(`${API_BASE}/api/v1/sessions/clear`, { method: "POST" }).then(
      j<{ removed: number }>,
    ),

  renameSession: (id: string, title: string) =>
    authedFetch(`${API_BASE}/api/v1/sessions/${encodeURIComponent(id)}`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ title }),
    }).then(j<Session>),

  /** Ask the backend to summarize the first user message into a
   *  Chinese title ≤10 chars and persist it on the session. Used
   *  once after the user sends the first message of a new session;
   *  the backend refuses to touch titles the user already renamed.
   *
   *  ``source`` is "llm" when the model produced the title,
   *  "fallback" when the LLM call failed and the backend fell back
   *  to a hard truncate, and "kept" when the session already had a
   *  user-set title (no-op). */
  autoTitleSession: (
    id: string,
    body: { user_message: string; provider: string; model: string },
  ) =>
    authedFetch(`${API_BASE}/api/v1/sessions/${encodeURIComponent(id)}/auto-title`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then(j<{ title: string; source: "llm" | "fallback" | "kept" }>),

  // ── Skills ────────────────────────────────────────────────────────────────────

  listSkills: () => authedFetch(`${API_BASE}/api/v1/skills`).then(j<Skill[]>),

  // The legacy ``/api/v1/skills/bundled`` endpoint was a stub that
  // always returned ``[]``. The bundled content catalogue moved to
  // ``GET /api/v1/meta`` (``meta.bundled.skills``) — deployments
  // that ship content packs populate that field via
  // ``create_app(meta=...)``. This shim reads the new shape and
  // returns an empty array when nothing is staged, so callers don't
  // need to handle the old vs new endpoint distinction.
  listBundledSkills: async () => {
    try {
      const m = await api.meta()
      const list = (m?.meta?.bundled?.skills ?? []) as string[]
      return list
    } catch {
      return [] as string[]
    }
  },

  getSkill: (slug: string) =>
    authedFetch(`${API_BASE}/api/v1/skills/${encodeURIComponent(slug)}`).then(j<SkillDetail>),

  importSkillFolder: (source: string, overwrite = false) =>
    authedFetch(`${API_BASE}/api/v1/skills/import-folder`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ source, overwrite }),
    }).then(j<Skill>),

  importBulkSkillFolder: (source: string, overwrite = false) =>
    authedFetch(`${API_BASE}/api/v1/skills/import-bulk`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ source, overwrite }),
    }).then(
      j<{
        installed: Skill[]
        skipped: { path: string; reason: string }[]
        errors: { path: string; error: string }[]
      }>,
    ),

  importBulkSkillZip: (blob: Blob, overwrite = false) =>
    blob.arrayBuffer().then((buf) => {
      const bytes = new Uint8Array(buf)
      let bin = ""
      for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i])
      const b64 = btoa(bin)
      return authedFetch(`${API_BASE}/api/v1/skills/import-bulk`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ data: b64, overwrite }),
      }).then(
        j<{
          installed: Skill[]
          skipped: { path: string; reason: string }[]
          errors: { path: string; error: string }[]
        }>,
      )
    }),

  importSkillZip: (name: string, blob: Blob) =>
    blob.arrayBuffer().then((buf) => {
      const bytes = new Uint8Array(buf)
      let bin = ""
      for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i])
      const b64 = btoa(bin)
      return authedFetch(`${API_BASE}/api/v1/skills/import-zip`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ name, data: b64 }),
      }).then(j<Skill>)
    }),

  exportSkillUrl: (slug: string) =>
    `/api/v1/skills/${encodeURIComponent(slug)}/download`,

  setSkillEnabled: (slug: string, enabled: boolean) =>
    authedFetch(`${API_BASE}/api/v1/skills/${encodeURIComponent(slug)}/enabled`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ enabled }),
    }).then(j<Skill>),

  updateSkill: (
    slug: string,
    body: { description?: string; body?: string },
  ) =>
    authedFetch(`${API_BASE}/api/v1/skills/${encodeURIComponent(slug)}`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then(j<Skill>),

  deleteSkill: (slug: string) =>
    authedFetch(`${API_BASE}/api/v1/skills/${encodeURIComponent(slug)}`, {
      method: "DELETE",
    }).then(j<undefined>),

  // ── Skill market ──────────────────────────────────────────────────────

  listMarketSkills: (
    q = "",
    category = "",
    sort = "downloads",
    featured = false,
  ) =>
    authedFetch(
      `${API_BASE}/api/v1/market/skills?q=${encodeURIComponent(q)}&category=${encodeURIComponent(category)}&sort=${sort}&featured=${featured}`,
    ).then(j<MarketSkill[]>),

  publishSkill: (
    slug: string,
    category: string,
    sourceType: string = "local",
    sourceRef: string = "",
  ) =>
    authedFetch(
      `${API_BASE}/api/v1/market/skills/${encodeURIComponent(slug)}/publish`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          category,
          source_type: sourceType,
          ...(sourceRef ? { source_ref: sourceRef } : {}),
        }),
      },
    ).then(j<MarketSkill>),

  delistMarketSkill: (marketKey: string) =>
    authedFetch(
      `${API_BASE}/api/v1/market/skills/${encodeURIComponent(marketKey)}`,
      { method: "DELETE" },
    ).then(j<undefined>),

  getMarketSkillFiles: (slug: string) =>
    authedFetch(
      `${API_BASE}/api/v1/market/skills/${encodeURIComponent(slug)}/files`,
    ).then(j<MarketFile[]>),

  addMarketSkill: (slug: string) =>
    authedFetch(
      `${API_BASE}/api/v1/market/skills/${encodeURIComponent(slug)}/add`,
      { method: "POST" },
    ).then(j<{ skill: Skill; cloud_backup: boolean }>),

  syncPlan: () =>
    authedFetch(`${API_BASE}/api/v1/market/sync`).then(j<SyncPlan>),

  syncExecute: () =>
    authedFetch(`${API_BASE}/api/v1/market/sync`, {
      method: "POST",
    }).then(j<SyncResult>),

  resolveConflict: (slug: string, choice: "local" | "remote") =>
    authedFetch(`${API_BASE}/api/v1/market/sync/resolve`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ slug, choice }),
    }).then(j<{ slug: string; resolved: string }>),

  listMarketStories: () =>
    authedFetch(`${API_BASE}/api/v1/market/stories`).then(j<MarketStory[]>),

  deleteMarketCopy: (slug: string) =>
    authedFetch(
      `${API_BASE}/api/v1/market/me/skills/${encodeURIComponent(slug)}`,
      { method: "DELETE" },
    ).then(j<undefined>),

  reportMarketUsage: () =>
    authedFetch(`${API_BASE}/api/v1/market/usage/report`, {
      method: "POST",
    }).then(j<{ ok: boolean; recorded: number }>),

  getMarketStory: (id: string) =>
    authedFetch(
      `${API_BASE}/api/v1/market/stories/${encodeURIComponent(id)}`,
    ).then(j<MarketStory>),

  createMarketStory: (body: {
    title: string
    skill_slug: string
    content: string
  }) =>
    authedFetch(`${API_BASE}/api/v1/market/stories`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then(j<MarketStory>),

  // ── MCP ──────────────────────────────────────────────────────────────────

  listMCPs: () => authedFetch(`${API_BASE}/api/v1/mcp`).then(j<MCPServer[]>),

  importBulkMcpFolder: (source: string) =>
    authedFetch(`${API_BASE}/api/v1/mcp/import-bulk`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ source }),
    }).then(
      j<{
        installed: MCPServer[]
        skipped: { path: string; reason: string }[]
        errors: { path: string; error: string }[]
      }>,
    ),

  importBulkMcpZip: (blob: Blob) =>
    blob.arrayBuffer().then((buf) => {
      const bytes = new Uint8Array(buf)
      let bin = ""
      for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i])
      const b64 = btoa(bin)
      return authedFetch(`${API_BASE}/api/v1/mcp/import-bulk`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ data: b64 }),
      }).then(
        j<{
          installed: MCPServer[]
          skipped: { path: string; reason: string }[]
          errors: { path: string; error: string }[]
        }>,
      )
    }),

  getMCP: (slug: string) =>
    authedFetch(`${API_BASE}/api/v1/mcp/${encodeURIComponent(slug)}`).then(j<MCPServer>),

  refreshMCPTools: (slug: string) =>
    authedFetch(`${API_BASE}/api/v1/mcp/${encodeURIComponent(slug)}/tools`).then(
      j<{ slug: string; tools: MCPToolSchema[] }>,
    ),

  upsertMCP: (body: {
    slug?: string
    name?: string
    description?: string
    command: string
    args: string[]
    env?: Record<string, string>
    origin?: string
  }) =>
    authedFetch(`${API_BASE}/api/v1/mcp`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then(j<MCPServer>),

  updateMCP: (slug: string, body: {
    name?: string
    description?: string
    command?: string
    args?: string[]
    env?: Record<string, string>
  }) =>
    authedFetch(`${API_BASE}/api/v1/mcp/${encodeURIComponent(slug)}`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then(j<MCPServer>),

  exportMCP: (slug: string) =>
    authedFetch(`${API_BASE}/api/v1/mcp/${encodeURIComponent(slug)}/export`).then((r) =>
      r.ok
        ? r.blob()
        : r.json().then((e) => Promise.reject(new Error(e.detail || "export failed")))
    ),

  setMCPEnabled: (slug: string, enabled: boolean) =>
    authedFetch(`${API_BASE}/api/v1/mcp/${encodeURIComponent(slug)}/enabled`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ enabled }),
    }).then(j<MCPServer>),

  deleteMCP: (slug: string) =>
    authedFetch(`${API_BASE}/api/v1/mcp/${encodeURIComponent(slug)}`, {
      method: "DELETE",
    }).then(j<undefined>),

  // ── Onboarding ─────────────────────────────────────────────────────────────

  listOnboarding: () =>
    authedFetch(`${API_BASE}/api/v1/onboarding`, {
      headers: { "Accept-Language": locale.value },
    }).then(j<OnboardingCard[]>),

  // ── Chat streams ──────────────────────────────────────────────────────────────

  cancelChat: (sessionId: string) =>
    authedFetch(`${API_BASE}/api/v1/chat/cancel/${encodeURIComponent(sessionId)}`, {
      method: "POST",
    }).then(j<undefined>),

  // ── Tools ──────────────────────────────────────────────────────────────────

  listTools: () => authedFetch(`${API_BASE}/api/v1/tools`).then(j<Tool[]>),

  // The legacy ``/api/v1/tools/bundled`` endpoint was a stub that
  // always returned ``[]``. Bundled content catalog moved to
  // ``GET /api/v1/meta`` (``meta.bundled.tools``) — see the
  // matching shim in the Skills section above.
  listBundledTools: async () => {
    try {
      const m = await api.meta()
      const list = (m?.meta?.bundled?.tools ?? []) as string[]
      return list
    } catch {
      return [] as string[]
    }
  },

  createTool: (
    body: Partial<Tool> & { name: string; slug?: string; kind?: ToolKind },
  ) =>
    authedFetch(`${API_BASE}/api/v1/tools`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then(j<Tool>),

  importBulkToolFolder: (source: string, overwrite = false) =>
    authedFetch(`${API_BASE}/api/v1/tools/import-bulk`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ source, overwrite }),
    }).then(
      j<{
        installed: Tool[]
        skipped: { path: string; reason: string }[]
        errors: { path: string; error: string }[]
      }>,
    ),

  importBulkToolZip: (blob: Blob, overwrite = false) =>
    blob.arrayBuffer().then((buf) => {
      const bytes = new Uint8Array(buf)
      let bin = ""
      for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i])
      const b64 = btoa(bin)
      return authedFetch(`${API_BASE}/api/v1/tools/import-bulk`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ data: b64, overwrite }),
      }).then(
        j<{
          installed: Tool[]
          skipped: { path: string; reason: string }[]
          errors: { path: string; error: string }[]
        }>,
      )
    }),

  updateTool: (slug: string, body: Partial<Tool>) =>
    authedFetch(`${API_BASE}/api/v1/tools/${encodeURIComponent(slug)}`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then(j<Tool>),

  setToolEnabled: (slug: string, enabled: boolean) =>
    authedFetch(`${API_BASE}/api/v1/tools/${encodeURIComponent(slug)}/enabled`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ enabled }),
    }).then(j<Tool>),

  deleteTool: (slug: string) =>
    authedFetch(`${API_BASE}/api/v1/tools/${encodeURIComponent(slug)}`, {
      method: "DELETE",
    }).then(j<undefined>),

  importToolSource: (
    body: {
      slug?: string
      name?: string
      description?: string
      parameters?: Record<string, unknown>
      source: string
      overwrite?: boolean
      origin?: string
      source_path?: string
    },
  ) =>
    authedFetch(`${API_BASE}/api/v1/tools/import-source`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then(j<Tool>),

  exportToolUrl: (slug: string) =>
    `/api/v1/tools/${encodeURIComponent(slug)}/export`,
  metrics: {
    summary: (params: { date_from?: string; date_to?: string } = {}) => {
      const q = new URLSearchParams()
      if (params.date_from) q.set("date_from", params.date_from)
      if (params.date_to) q.set("date_to", params.date_to)
      const qs = q.toString()
      return authedFetch(`${API_BASE}/api/v1/metrics/summary${qs ? `?${qs}` : ""}`).then(
        j<MetricsSummary>,
      )
    },
    ranking: (params: {
      kind: MetricsRankingKind
      page?: number
      page_size?: number
      date_from?: string
      date_to?: string
    }) => {
      const q = new URLSearchParams()
      q.set("kind", params.kind)
      if (params.page) q.set("page", String(params.page))
      if (params.page_size) q.set("page_size", String(params.page_size))
      if (params.date_from) q.set("date_from", params.date_from)
      if (params.date_to) q.set("date_to", params.date_to)
      return authedFetch(`${API_BASE}/api/v1/metrics/ranking?${q.toString()}`).then(
        j<MetricsRankingPage>,
      )
    },
    trend: (params: { date_from?: string; date_to?: string } = {}) => {
      const q = new URLSearchParams()
      if (params.date_from) q.set("date_from", params.date_from)
      if (params.date_to) q.set("date_to", params.date_to)
      const qs = q.toString()
      return authedFetch(`${API_BASE}/api/v1/metrics/trend${qs ? `?${qs}` : ""}`).then(
        j<MetricsTrend>,
      )
    },
  },
}

// ── Onboarding ───────────────────────────────────────────────────────────

export type OnboardingCardType = "centered" | "media-text" | "media-top"
export type OnboardingMediaKind = "none" | "color" | "image"

export interface OnboardingCard {
  id: string
  type: OnboardingCardType
  /** Locale resolved for the requester's Accept-Language at
   *  fetch time. The full i18n dicts below are always returned
   *  so locale switches don't need a re-fetch. */
  title: string
  body: string
  title_i18n: Record<string, string>
  body_i18n: Record<string, string>
  media_kind: OnboardingMediaKind
  media_color: string | null
  media_label: string | null
  /** Relative path to an SVG/PNG shipped under public/onboarding/.
   *  Falls back to media_color while the image loads. */
  media_image: string | null
}

export type StreamEvent =
  | { type: "chunk"; content: string; session_id?: string; seq?: number }
  | {
      type: "reasoning"
      /** One reasoning delta (``reasoning_content`` from the
       *  provider). Accumulated into a "thinking" block in the
       *  reply timeline, BEFORE the reply text it led to. */
      content: string
      session_id?: string
      seq?: number
    }
  | {
      type: "execution_start"
      call_ids: string[]
      names: string[]
      kinds: ("mcp" | "tool")[]
      session_id?: string
      seq?: number
    }
  | {
      type: "tool_args_start"
      call_id: string
      kind: "mcp" | "tool"
      name: string
      session_id?: string
      seq?: number
    }
  | {
      type: "tool_args_delta"
      call_id: string
      arguments_chunk: string
      session_id?: string
      seq?: number
    }
  | {
      type: "tool_start"
      call_id: string
      kind: "mcp" | "tool"
      name: string
      args: Record<string, unknown>
      session_id?: string
      seq?: number
    }
  | {
      type: "tool_progress"
      call_id: string
      kind: "mcp" | "tool"
      name: string
      chunk: string
      session_id?: string
      seq?: number
    }
  | {
      type: "tool_end"
      call_id: string
      kind: "mcp" | "tool"
      name: string
      ok: boolean
      result: string
      error?: string | null
      cancelled?: boolean
      session_id?: string
      seq?: number
    }
  | {
      type: "execution_end"
      ok: boolean
      cancelled: boolean
      count: number
      session_id?: string
      seq?: number
    }
  | {
      type: "cancelled"
      assistant_message_id?: string
      session_id?: string
      seq?: number
    }
  | {
      type: "done"
      usage?: Record<string, unknown>
      session_id?: string
      seq?: number
    }
  | {
      type: "error"
      message: string
      session_id?: string
      seq?: number
    }

const textDecoder = new TextDecoder()

function parseSseEvent(block: string): StreamEvent | null {
  // block looks like:
  //   event: chunk\ndata: {"content": "..."}\n\n
  let event = ""
  const dataLines: string[] = []
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim()
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim())
  }
  const data = dataLines.join("\n")
  if (!event || !data) return null
  let parsed: Record<string, unknown> = {}
  try {
    parsed = JSON.parse(data)
  } catch {
    return null
  }
  const session_id =
    typeof parsed.session_id === "string" ? parsed.session_id : undefined
  const seq = typeof parsed.seq === "number" ? parsed.seq : undefined
  if (event === "chunk") {
    return {
      type: "chunk",
      content: String(parsed.content ?? ""),
      session_id,
      seq,
    }
  }
  if (event === "reasoning") {
    return {
      type: "reasoning",
      content: String(parsed.content ?? ""),
      session_id,
      seq,
    }
  }
  if (event === "tool_args_start") {
    return {
      type: "tool_args_start",
      call_id: String(parsed.call_id ?? ""),
      kind: parsed.kind === "tool" ? "tool" : "mcp",
      name: String(parsed.name ?? ""),
      session_id,
      seq,
    }
  }
  if (event === "tool_args_delta") {
    return {
      type: "tool_args_delta",
      call_id: String(parsed.call_id ?? ""),
      arguments_chunk: String(parsed.arguments_chunk ?? ""),
      session_id,
      seq,
    }
  }
  if (event === "tool_start") {
    return {
      type: "tool_start",
      call_id: String(parsed.call_id ?? ""),
      kind: parsed.kind === "tool" ? "tool" : "mcp",
      name: String(parsed.name ?? ""),
      args: (parsed.args as Record<string, unknown>) ?? {},
      session_id,
      seq,
    }
  }
  if (event === "tool_end") {
    return {
      type: "tool_end",
      call_id: String(parsed.call_id ?? ""),
      kind: parsed.kind === "tool" ? "tool" : "mcp",
      name: String(parsed.name ?? ""),
      ok: Boolean(parsed.ok),
      result: String(parsed.result ?? ""),
      error: (parsed.error as string | null) ?? null,
      cancelled: Boolean(parsed.cancelled),
      session_id,
      seq,
    }
  }
  if (event === "cancelled") {
    return {
      type: "cancelled",
      assistant_message_id:
        typeof parsed.assistant_message_id === "string"
          ? parsed.assistant_message_id
          : undefined,
      session_id,
      seq,
    }
  }
  if (event === "done") {
    return {
      type: "done",
      usage: parsed.usage as Record<string, unknown> | undefined,
      session_id,
      seq,
    }
  }
  if (event === "error") {
    return {
      type: "error",
      message: String(parsed.message ?? data),
      session_id,
      seq,
    }
  }
  return null
}

export async function* streamChat(
  req: ChatRequest,
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
  const r = await authedFetch(`${API_BASE}/api/v1/chat`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(req),
    signal,
  })
  if (!r.ok || !r.body) {
    yield { type: "error", message: `${r.status} ${r.statusText}` }
    return
  }
  const reader = r.body.getReader()
  let buffer = ""
  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += textDecoder.decode(value, { stream: true })
      let sep: number
      while ((sep = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, sep)
        buffer = buffer.slice(sep + 2)
        const ev = parseSseEvent(block)
        if (ev) yield ev
      }
    }
  } catch (e) {
    if (signal?.aborted) return
    yield { type: "error", message: e instanceof Error ? e.message : String(e) }
    return
  }
  if (buffer.trim()) {
    const ev = parseSseEvent(buffer)
    if (ev) yield ev
  }
}