<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue"
import { RouterView, useRoute, useRouter } from "vue-router"
import AppNav from "./components/AppNav.vue"
import ConfirmModal from "./components/ConfirmModal.vue"
import LoadingSplash from "./components/LoadingSplash.vue"
import AppToast from "./components/AppToast.vue"
import Onboarding from "./components/Onboarding.vue"
import SessionList from "./components/SessionList.vue"
import TitleBar from "./components/TitleBar.vue"
import { useAuthStore } from "./stores/auth"
import { useThemeStore } from "./stores/theme"
import { useOnboardingStore } from "./stores/onboarding"
import { useSessionsStore } from "./stores/sessions"
import { useSessionStreamsStore } from "./stores/sessionStreams"
import { api } from "./api/client"
import { startSyncPolling, stopSyncPolling } from "./lib/marketSync"
import { reportAuth, reportTheme } from "./lib/globalVoice"
import { t } from "./i18n"

// ── Usage reporter ──
// Independent of sync: every N minutes the desktop aggregates local
// skill usage (loads from the metrics store + a downloads ledger) and
// posts it to the market service so the cloud ops dashboard stays warm.
let usageReporterStarted = false
function startUsageReporter() {
  if (usageReporterStarted) return
  usageReporterStarted = true
  const report = async () => {
    try {
      await api.reportMarketUsage()
    } catch {
      /* best-effort, non-blocking */
    }
  }
  report()
  setInterval(report, 10 * 60 * 1000)
  // 应用级自动同步轮询：45s 检查计划，无冲突 push/pull 自动执行。
  // 放在应用级（而非市场页 tab）保证切页后仍会同步。
  startSyncPolling(45_000)
  onUnmounted(() => stopSyncPolling())
}

const router = useRouter()
const route = useRoute()
const sessionsStore = useSessionsStore()
const streamsStore = useSessionStreamsStore()
const auth = useAuthStore()
const themeStore = useThemeStore()

// Global voice input is gated on a logged-in session: the Electron
// host ignores Alt+Shift+W until the user has authenticated, so the
// feature can't fire on the login screen.
watch(
  () => auth.isAuthenticated,
  (v) => reportAuth(v),
  { immediate: true },
)

// The voice overlay follows the app theme (light/dark), not a
// hardcoded black. Re-sent on every toggle.
watch(
  () => themeStore.theme,
  () => reportTheme(),
  { immediate: true },
)

// Hide the side panels + titlebar chrome on the login route so the
// LoginView owns the entire viewport. The router guard already
// prevents authenticated users from landing on /login, so this
// only fires for the actual login screen.
const isLoginRoute = computed(() => route.path === "/login")

// Backend boot handshake. The Electron host spawns the Python backend
// as a child process; on a fresh Win11 install the first launch can
// take 30–90 s while AV scans the bundled files. We poll /health
// every 1.5 s; while it fails we render a fullscreen splash with the
// brand logo so the user sees something moving (instead of a
// frameless black window they double-click again).
const backendReady = ref(false)
const backendSplashFading = ref(false)
let probeTimer: ReturnType<typeof setInterval> | null = null

// ── Session-completion toasts ────────────────────────────────────────────
// Streams run in the background bus, so a session can finish while the
// user is on another session or another view entirely. The bus fires
// ``setOnComplete`` once per finished stream; we surface it as a small
// clickable toast top-right. Clicking jumps to that session — the user
// is never forced to sit in one session waiting for it to finish.
interface DoneToast {
  id: string
  sessionId: string
  title: string
}
const toasts = ref<DoneToast[]>([])
let toastSeq = 0
interface ToastTimer {
  [sessionId: string]: ReturnType<typeof setTimeout>
}
const toastTimers: ToastTimer = {}

function _dismissToast(sessionId: string) {
  toasts.value = toasts.value.filter((x) => x.sessionId !== sessionId)
  const tm = toastTimers[sessionId]
  if (tm) {
    clearTimeout(tm)
    delete toastTimers[sessionId]
  }
}

function _pushDoneToast(sessionId: string) {
  _dismissToast(sessionId) // replace an older toast for the same session
  const title =
    sessionsStore.items.find((s) => s.id === sessionId)?.title ??
    t("sessions.new")
  toastSeq += 1
  toasts.value.push({ id: `t${toastSeq}`, sessionId, title })
  // Auto-dismiss after a while; clicking also dismisses.
  toastTimers[sessionId] = setTimeout(() => _dismissToast(sessionId), 10000)
}

/** Jump to the chat tab and open the given session. Used by the
 *  completion toasts and the sidebar rows. */
async function _openSession(sessionId: string) {
  _dismissToast(sessionId)
  if (route.path !== "/chat") {
    await router.push("/chat")
  }
  await sessionsStore.select(sessionId)
}

let completeHookInstalled = false
onMounted(() => {
  // ── usage reporter: independent timer, forwards local skill usage
  // to the cloud ops dashboard (decoupled from sync). ──
  startUsageReporter()
  // The bus is the only place that knows when a stream ends; register
  // once (HMR remounts would otherwise stack listeners).
  if (completeHookInstalled) return
  completeHookInstalled = true
  streamsStore.setOnComplete((sessionId) => {
    // Exit flush cancels every stream; don't toast during shutdown.
    if (exiting.value) return
    // Already watching that session — a toast would be noise.
    if (sessionsStore.currentId === sessionId) return
    // A new request replaced the old stream (same session re-ran);
    // still going, so no "finished" toast yet.
    if (streamsStore.isStreaming(sessionId)) return
    _pushDoneToast(sessionId)
  })
})

// Exit overlay. The Electron main process calls ``flushForExit`` on
async function checkBackend() {
  try {
    const base = (window as unknown as { __MHC_BACKEND_URL?: string }).__MHC_BACKEND_URL ?? ""
    const resp = await fetch(`${base}/api/v1/health`, { cache: "no-store" })
    if (resp.ok) {
      backendReady.value = true
      // Once the backend is up, restore the auth state from
      // localStorage + ``/auth/me``. Done here (not in main.ts) so
      // a stored token survives a slow backend boot without being
      // discarded as "stale". The main.ts bootstrap is a no-op when
      // it already ran and succeeded; we re-run it here in case the
      // backend wasn't ready at main.ts time.
      void auth.bootstrap()
      // Fade the splash out, then unmount. 250 ms matches the CSS
      // transition so the splash is invisible by the time we hide it.
      if (!backendSplashFading.value) {
        backendSplashFading.value = true
        window.setTimeout(() => {
          backendSplashFading.value = false
        }, 280)
      }
      if (probeTimer) {
        clearInterval(probeTimer)
        probeTimer = null
      }
    }
  } catch {
    /* still booting */
  }
}

onMounted(() => {
  // If the backend was already up before we mounted (dev mode),
  // checkBackend's first iteration flips backendReady and we skip
  // the splash entirely — no fade-in/fade-out flash.
  void checkBackend()
  probeTimer = setInterval(checkBackend, 1500)
})
onUnmounted(() => {
  if (probeTimer) clearInterval(probeTimer)
})

// Exit overlay. The Electron main process calls ``flushForExit`` on
// the renderer via IPC when the user picks "Quit" from the tray
// prompt; if any SSE streams are still running we want a fullscreen
// "正在退出…" splash covering the UI so the user doesn't click
// anything mid-flush. The renderer signals back when flush is done.
const exiting = ref(false)
const exitHint = ref<string>("")
let exitHookInstalled = false

function _exitSplashHint(): string {
  const active = streamsStore.handles.size
  if (active === 0) return t("splash.exiting")
  return `${t("splash.exiting")} (${active})`
}

onMounted(() => {
  // Expose the IPC contract the main process uses to ask the renderer
  // to flush before exit. We register on first mount only — HMR
  // remounts would otherwise stack duplicate listeners.
  if (exitHookInstalled) return
  exitHookInstalled = true
  const w = window as unknown as {
    __mhcFlush?: () => Promise<number | null>
    __mhcStartExit?: (cb: () => Promise<void>) => void
  }
  w.__mhcStartExit = (cb: () => Promise<void>) => {
    exiting.value = true
    exitHint.value = _exitSplashHint()
    // Wait one tick so the splash paints before we start the (sync,
    // but yields to the event loop) flush. Otherwise the user would
    // see no feedback during a fast "no streams running" exit.
    window.setTimeout(async () => {
      try {
        await cb()
      } finally {
        exiting.value = false
      }
    }, 50)
  }
})

const LS_LEFT = "mhc.layout.leftOpen"
const LS_RIGHT = "mhc.layout.rightOpen"

const leftOpen = ref(true)
const rightOpen = ref(true)

// Market pages own the full width — the sessions sidebar is chat
// chrome and just wastes space there. (Not collapsed: fully unmounted,
// so the collapse toggle never persists it.)
const isMarketRoute = computed(
  () => route.path.startsWith("/skills") || route.path.startsWith("/market"),
)

function safeRead(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

function safeWrite(key: string, value: string) {
  try {
    localStorage.setItem(key, value)
  } catch {
    // ignore
  }
}

onMounted(() => {
  const l = safeRead(LS_LEFT)
  const r = safeRead(LS_RIGHT)
  if (l !== null) leftOpen.value = l === "1"
  if (r !== null) rightOpen.value = r === "1"
  // Show the first-run overlay if the user has never dismissed it.
  // The store owns the localStorage flag and the card list; we
  // just kick off the bootstrap and let it decide.
  void useOnboardingStore().bootstrap()
})

watch(leftOpen, (v) => safeWrite(LS_LEFT, v ? "1" : "0"))
watch(rightOpen, (v) => safeWrite(LS_RIGHT, v ? "1" : "0"))
</script>

<template>
  <div class="shell">
    <!-- Title bar stays visible on the login route too — it gives
         the brand a consistent frame and keeps the window controls
         draggable. Panels hide so the LoginView owns the rest. -->
    <TitleBar v-if="!isLoginRoute" />
    <div class="body">
      <template v-if="!isLoginRoute">
      <!-- Left panel: [toggle][content] -->
      <aside
        class="panel left"
        :class="{ collapsed: !leftOpen }"
        :aria-label="t('nav.workspace')"
      >
        <button
          class="pin"
          :aria-label="t('nav.workspace')"
          :title="leftOpen ? t('panel.collapse') : t('panel.expand')"
          @click="leftOpen = !leftOpen"
        >
          <span class="chev" :data-side="leftOpen ? 'left' : 'right'">‹</span>
        </button>
        <div class="pane">
          <AppNav />
        </div>
      </aside>
      </template>

      <!-- Center: always takes the rest, never collapses -->
      <main class="center" :class="{ login: isLoginRoute }">
        <RouterView />
      </main>

      <template v-if="!isLoginRoute && !isMarketRoute">
      <!-- Right panel: [content][toggle] -->
      <aside
        class="panel right"
        :class="{ collapsed: !rightOpen }"
        :aria-label="t('sessions.title')"
      >
        <div class="pane">
          <SessionList />
        </div>
        <button
          class="pin"
          :aria-label="t('sessions.title')"
          :title="rightOpen ? t('panel.collapse') : t('panel.expand')"
          @click="rightOpen = !rightOpen"
        >
          <span class="chev" :data-side="rightOpen ? 'right' : 'left'">›</span>
        </button>
      </aside>
      </template>
    </div>

    <!-- First-run overlay. Sits above the panel shell so it covers
         every route and fullscreen mode. The component itself only
         renders when the store says visible=true. -->
    <Onboarding />
    <ConfirmModal />

    <!-- Session-completion toasts. A stream finishes while the user
         is on another session/view; each toast is clickable and
         jumps to its session. Only sessions we are NOT actively
         watching produce a toast (see setOnComplete handler). -->
    <div v-if="toasts.length > 0" class="toasts" role="status" aria-live="polite">
      <button
        v-for="toast in toasts"
        :key="toast.id"
        class="toast"
        type="button"
        :title="t('sessions.toastJump')"
        @click="_openSession(toast.sessionId)"
      >
        <span class="toast-icon" aria-hidden="true">✓</span>
        <span class="toast-body">
          <span class="toast-title">{{ toast.title }}</span>
          <span class="toast-msg">{{ t("sessions.toastDone") }}</span>
        </span>
        <span
          class="toast-x"
          aria-hidden="true"
          @click.stop="_dismissToast(toast.sessionId)"
        >×</span>
      </button>
    </div>

    <!-- Loading splash: fullscreen, blocks interaction. Shown until
         the backend answers /health. We keep the DOM mounted for
         280 ms after health succeeds so the CSS fade-out can play;
         then unmount so the splash never paints over a route
         change. -->
    <LoadingSplash
      v-if="!backendReady || backendSplashFading"
      :class="{ fading: backendSplashFading }"
      :hint="t('splash.starting')"
    />

    <!-- Exit splash: shown while the renderer flushes streams
         before the Electron process quits. Prevents the user from
         clicking anything mid-cancel. -->
    <LoadingSplash v-if="exiting" :hint="exitHint" />

    <AppToast />
  </div>
</template>

<style>
* {
  box-sizing: border-box;
}
html,
body,
#app {
  margin: 0;
  padding: 0;
  height: 100%;
  width: 100%;
}
body {
  font-family: var(--font-sans);
  font-size: 14px;
  color: var(--text);
  background: var(--bg);
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}
button,
input,
textarea,
select {
  font-family: inherit;
}

/* Global thin scrollbar: ~6px visible, pill-shaped ends. Works on
   Windows + macOS + Linux Chromium. */
::-webkit-scrollbar {
  width: 6px;
  height: 6px;
}
::-webkit-scrollbar-track {
  background: transparent;
}
::-webkit-scrollbar-thumb {
  background: var(--text-faint);
  background-clip: content-box;
  border-radius: 999px;
  border: 1px solid transparent;
}
::-webkit-scrollbar-thumb:hover {
  background: var(--text-mute);
  background-clip: content-box;
}
::-webkit-scrollbar-corner {
  background: transparent;
}
</style>

<style scoped>
.shell {
  display: flex;
  flex-direction: column;
  height: 100vh;
  width: 100vw;
  overflow: hidden;
  background: var(--bg);
}

.body {
  flex: 1;
  min-height: 0;
  display: flex;
}

.panel {
  display: flex;
  height: 100%;
  overflow: hidden;
  background: var(--bg-panel);
  transition: none;
}

.panel.left {
  border-right: 1px solid var(--border);
}
.panel.right {
  border-left: 1px solid var(--border);
}

.pin {
  flex-shrink: 0;
  width: 28px;
  align-self: stretch;
  border: 0;
  background: transparent;
  color: var(--text-faint);
  font-size: 13px;
  cursor: pointer;
  transition: background 120ms ease, color 120ms ease;
  display: flex;
  align-items: center;
  justify-content: center;
}
.pin:hover {
  background: var(--bg);
  color: var(--text-mute);
}

.chev {
  display: inline-block;
  transition: transform 160ms ease;
}
/* When the panel is collapsed, show the chev pointing toward the inside
   of the screen so the click target reads as "open". The data-side
   attribute drives CSS rotation. */
.chev[data-side="right"] {
  transform: rotate(180deg);
}

.pane {
  position: relative;
  width: 240px;
  overflow: hidden;
  transition: width 220ms cubic-bezier(0.4, 0, 0.2, 1);
  min-width: 0;
  flex-shrink: 0;
}
/* Pin content to its natural 240px width and anchor it to the side
   next to the toggle pin. As .pane shrinks during the collapse
   animation the content is clipped (not reflowed), so text never gets
   squeezed into a single column mid-transition. Anchor direction
   makes the slide feel natural for each panel:
     - left panel: content slides toward the left (toward its pin)
     - right panel: content slides toward the right (toward its pin) */
.pane > * {
  position: absolute;
  top: 0;
  width: 240px;
  height: 100%;
}
.panel.left .pane > * {
  left: 0;
}
.panel.right .pane > * {
  right: 0;
}

.panel.collapsed .pane {
  width: 0;
}

.center {
  flex: 1 1 0;
  min-width: 0;
  height: 100%;
  overflow: hidden;
  background: var(--bg);
}

/* On the login route the center pane fills the whole shell (no
   panels around it) and lets the LoginView own the viewport. */
.center.login {
  overflow: auto;
}

/* Session-completion toasts. Fixed top-right, stacked; z-index above
   the loading splash so a toast is visible even while the exit splash
   paints. Click → jump to that session (see _openSession). */
.toasts {
  position: fixed;
  top: 48px;
  right: 16px;
  z-index: 3000;
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-width: 320px;
}
.toast {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  text-align: left;
  padding: 10px 12px;
  border-radius: 10px;
  border: 1px solid var(--border);
  background: var(--bg-panel);
  color: var(--text);
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.18);
  cursor: pointer;
  animation: toast-in 180ms ease-out;
  font-family: inherit;
}
.toast:hover {
  border-color: var(--accent);
}
.toast-icon {
  color: var(--accent);
  font-weight: 700;
  font-size: 14px;
  line-height: 1.4;
  flex-shrink: 0;
}
.toast-body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  flex: 1;
}
.toast-title {
  font-size: 13px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.toast-msg {
  font-size: 12px;
  color: var(--text-mid);
}
.toast-x {
  color: var(--text-faint);
  font-size: 14px;
  line-height: 1.2;
  padding: 0 2px;
  flex-shrink: 0;
}
.toast-x:hover {
  color: var(--text);
}
@keyframes toast-in {
  from {
    opacity: 0;
    transform: translateY(-6px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
</style>
