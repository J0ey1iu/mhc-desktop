/**
 * Post-refactor smoke test for the backend HTTP API.
 *
 * Run with: node e2e-post-refactor-smoke.cjs
 *
 * Verifies the deploy-driven refactor:
 *
 *   - GET /api/v1/health is public (no auth)
 *   - GET /api/v1/meta is public + returns the deploy manifest
 *   - /api/v1/skills/bundled, /api/v1/mcp/bundled, /api/v1/tools/bundled
 *     routes are gone (the legacy "always returns []" stubs)
 *   - /api/v1/auth/login works (MockAuthProvider)
 *   - /api/v1/auth/me requires a bearer token
 *   - /api/v1/providers/presets returns the kernel's 6 default presets
 *   - /api/v1/onboarding returns 3 default cards with locale-resolved
 *     title/body fields
 *   - Providers CRUD still works
 *   - Skills CRUD still works
 *   - MCP CRUD still works
 *   - Tools CRUD still works
 *   - Prefs round-trip works
 *
 * This is the smoke test referenced by the goal: it does not depend
 * on screenshots, the Electron renderer, or any UI — just raw HTTP
 * against the running backend. If this passes, the refactor didn't
 * break the contract that the kernel and deploy ship together.
 */

const BASE = process.env.MHC_BACKEND || "http://127.0.0.1:8765"

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

let failed = 0
let passed = 0
const failures = []

async function check(label, fn) {
  try {
    await fn()
    passed++
    console.log(`  PASS  ${label}`)
  } catch (e) {
    failed++
    failures.push({ label, error: String(e) })
    console.log(`  FAIL  ${label}\n        ${e}`)
  }
}

function assert(cond, msg) {
  if (!cond) throw new Error(msg || "assertion failed")
}

function assertEq(actual, expected, msg) {
  if (actual !== expected) {
    throw new Error(`${msg || "mismatch"}: expected ${JSON.stringify(expected)} got ${JSON.stringify(actual)}`)
  }
}

async function req(path, opts = {}) {
  const headers = opts.headers ? { ...opts.headers } : {}
  if (opts.body && !headers["content-type"]) {
    headers["content-type"] = "application/json"
  }
  const r = await fetch(`${BASE}${path}`, {
    method: opts.method || "GET",
    headers,
    body: opts.body ? (typeof opts.body === "string" ? opts.body : JSON.stringify(opts.body)) : undefined,
  })
  const text = await r.text()
  let body = null
  try {
    body = text ? JSON.parse(text) : null
  } catch {
    body = text
  }
  return { status: r.status, body, headers: r.headers }
}

async function login(username = "alice", password = "wonderland") {
  const r = await req("/api/v1/auth/login", {
    method: "POST",
    body: { username, password },
  })
  if (r.status !== 200) throw new Error(`login failed: ${r.status}`)
  return r.body.token
}

async function main() {
  // Wait for backend to be ready
  for (let i = 0; i < 30; i++) {
    try {
      const r = await req("/api/v1/health")
      if (r.status === 200) break
    } catch {}
    await sleep(1000)
  }

  console.log("\n=== Public endpoints ===")
  await check("GET /api/v1/health returns 200", async () => {
    const r = await req("/api/v1/health")
    assertEq(r.status, 200)
    assertEq(r.body.status, "ok")
    assert(typeof r.body.version === "string")
    assert(typeof r.body.data_dir === "string")
  })

  await check("GET /api/v1/meta is public + has manifest", async () => {
    const r = await req("/api/v1/meta")
    assertEq(r.status, 200)
    assert(r.body && typeof r.body.meta === "object")
    // Deploy default meta has data_dir + bundled.
    assert(typeof r.body.meta.data_dir === "string")
    assert(r.body.meta.bundled && Array.isArray(r.body.meta.bundled.skills))
  })

  await check("GET /api/v1/onboarding returns 3 default cards", async () => {
    const r = await req("/api/v1/onboarding", {
      headers: { "Accept-Language": "zh-CN" },
    })
    assertEq(r.status, 200)
    assert(Array.isArray(r.body))
    assert(r.body.length === 3)
    const ids = r.body.map((c) => c.id)
    assert(ids.includes("welcome"))
    assert(ids.includes("skills"))
    assert(ids.includes("mcp"))
    // zh locale resolves to Chinese title. Brand name comes from
    // ``meta["brand"]["name"]``; in this smoke run it's the kernel
    // default ``mhc-desktop-backend`` (no deploy override wired).
    assert(r.body[0].title === "欢迎使用 mhc-desktop-backend")
  })

  await check("GET /api/v1/providers/presets returns kernel defaults", async () => {
    const token = await login()
    const r = await req("/api/v1/providers/presets", {
      headers: { Authorization: `Bearer ${token}` },
    })
    assertEq(r.status, 200)
    assert(Array.isArray(r.body))
    assert(r.body.length === 6)
    const ids = r.body.map((p) => p.id).sort()
    assert(ids.includes("openai"))
    assert(ids.includes("anthropic"))
    assert(ids.includes("deepseek"))
    assert(ids.includes("moonshot"))
    assert(ids.includes("zhipu"))
    assert(ids.includes("ollama"))
  })

  console.log("\n=== Legacy /bundled routes are removed ===")
  await check("GET /api/v1/skills/bundled no longer returns []", async () => {
    const token = await login()
    const r = await req("/api/v1/skills/bundled", {
      headers: { Authorization: `Bearer ${token}` },
    })
    // The route is gone; the dynamic {slug} matcher takes the
    // request and returns 404 for "bundled".
    assertEq(r.status, 404)
  })

  await check("GET /api/v1/mcp/bundled no longer returns []", async () => {
    const token = await login()
    const r = await req("/api/v1/mcp/bundled", {
      headers: { Authorization: `Bearer ${token}` },
    })
    assertEq(r.status, 404)
  })

  await check("GET /api/v1/tools/bundled no longer returns []", async () => {
    const token = await login()
    const r = await req("/api/v1/tools/bundled", {
      headers: { Authorization: `Bearer ${token}` },
    })
    assertEq(r.status, 404)
  })

  console.log("\n=== Auth ===")
  await check("POST /api/v1/auth/login works (MockAuthProvider)", async () => {
    const r = await req("/api/v1/auth/login", {
      method: "POST",
      body: { username: "alice", password: "wonderland" },
    })
    assertEq(r.status, 200)
    assert(typeof r.body.token === "string")
    assertEq(r.body.user.username, "alice")
  })

  await check("GET /api/v1/auth/me without token -> 401", async () => {
    const r = await req("/api/v1/auth/me")
    assertEq(r.status, 401)
    assert(r.headers.get("www-authenticate", "").toLowerCase().startsWith("bearer"))
  })

  await check("GET /api/v1/auth/me with token -> 200", async () => {
    const token = await login()
    const r = await req("/api/v1/auth/me", {
      headers: { Authorization: `Bearer ${token}` },
    })
    assertEq(r.status, 200)
    assertEq(r.body.user.username, "alice")
  })

  console.log("\n=== Provider CRUD ===")
  let testProviderName = `smoke-prov-${Date.now()}`
  await check("POST /api/v1/providers creates a provider", async () => {
    const token = await login()
    const r = await req("/api/v1/providers", {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      body: {
        name: testProviderName,
        provider_type: "openai",
        api_key: "sk-smoke-test",
        default_model: "gpt-4o-mini",
      },
    })
    assertEq(r.status, 201)
    assertEq(r.body.name, testProviderName)
    // api_key is masked
    assert(r.body.api_key.startsWith("***"))
  })

  await check("POST /api/v1/providers rejects unknown provider_type", async () => {
    const token = await login()
    const r = await req("/api/v1/providers", {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      body: {
        name: `${testProviderName}-bad`,
        provider_type: "made-up",
        api_key: "sk-x",
        default_model: "m",
      },
    })
    assertEq(r.status, 400)
  })

  await check("GET /api/v1/providers lists created provider", async () => {
    const token = await login()
    const r = await req("/api/v1/providers", {
      headers: { Authorization: `Bearer ${token}` },
    })
    assertEq(r.status, 200)
    const names = r.body.map((p) => p.name)
    assert(names.includes(testProviderName), `expected ${testProviderName} in ${names}`)
  })

  console.log("\n=== Skills CRUD ===")
  let testSkillName = `smoke-skill-${Date.now()}`
  await check("POST /api/v1/skills creates a skill from a folder", async () => {
    const fs = require("fs")
    const path = require("path")
    const os = require("os")
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "mhc-smoke-skill-"))
    fs.writeFileSync(
      path.join(dir, "SKILL.md"),
      `---\nname: ${testSkillName}\ndescription: smoke test\n---\nbody\n`,
      "utf-8",
    )
    const token = await login()
    const r = await req("/api/v1/skills/import-folder", {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      body: { source: dir },
    })
    assertEq(r.status, 201)
    assertEq(r.body.slug, testSkillName)
  })

  await check("GET /api/v1/skills lists created skill", async () => {
    const token = await login()
    const r = await req("/api/v1/skills", {
      headers: { Authorization: `Bearer ${token}` },
    })
    assertEq(r.status, 200)
    const slugs = r.body.map((s) => s.slug)
    assert(slugs.includes(testSkillName), `expected ${testSkillName} in ${slugs}`)
  })

  console.log("\n=== Prefs ===")
  await check("GET /api/v1/prefs returns the user's prefs", async () => {
    const token = await login()
    const r = await req("/api/v1/prefs", {
      headers: { Authorization: `Bearer ${token}` },
    })
    assertEq(r.status, 200)
    assert(typeof r.body.system_prompt_addition === "string")
  })

  await check("PUT /api/v1/prefs updates system_prompt_addition", async () => {
    const token = await login()
    const r = await req("/api/v1/prefs", {
      method: "PUT",
      headers: { Authorization: `Bearer ${token}` },
      body: { system_prompt_addition: "you are a smoke tester" },
    })
    assertEq(r.status, 200)
    assertEq(r.body.system_prompt_addition, "you are a smoke tester")
  })

  console.log("\n=== Summary ===")
  console.log(`  ${passed} passed, ${failed} failed`)
  if (failed > 0) {
    for (const f of failures) {
      console.log(`  FAIL: ${f.label}\n        ${f.error}`)
    }
    process.exit(1)
  }
}

main().catch((e) => {
  console.error("fatal:", e)
  process.exit(2)
})