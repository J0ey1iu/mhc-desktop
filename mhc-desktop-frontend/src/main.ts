import { createApp } from "vue"
import { createPinia } from "pinia"
import App from "./App.vue"
import { router } from "./router"
import { useThemeStore } from "./stores/theme"
import { useAppearanceStore } from "./stores/appearance"
import { useAppMetaStore } from "./stores/appMeta"
import { useSessionStreamsStore } from "./stores/sessionStreams"
import { locale, setLocale } from "./i18n"
import "./lib/globalVoice"
import "./styles/fonts.css"
import "./styles/tokens.css"

const app = createApp(App)
app.use(createPinia())
app.use(router)

// Sync Vue-side theme state with what index.html set on <html>.
const theme = useThemeStore()
theme.init()
const appearance = useAppearanceStore()
appearance.init()
const appMeta = useAppMetaStore()
appMeta.init()

// ``App.vue`` handles auth bootstrap once the backend is reachable
// (its ``/health`` poller triggers ``auth.bootstrap()`` on the first
// 200 response). This way a stored token survives a slow backend
// boot — main.ts no longer races the backend with its own fetch.
app.mount("#app")

// Backend boot handshake lives in App.vue: it shows a fullscreen
// splash and polls `${__MHC_BACKEND_URL}/api/v1/health` every 1.5 s
// until the uvicorn factory answers (30–90 s on a cold Win11
// install under AV). Stores that need fresh data after the backend
// comes up should re-fetch on their own rather than rely on a page
// reload — a reload is wrong here for two reasons:
//
//   1. The Electron host injects the SPA's index.html from a temp
//      file in ``os.tmpdir()`` (so ``__MHC_BACKEND_URL`` can be
//      stamped in), then ``unlink``s it after ``did-finish-load``.
//      Any reload afterwards races against that delete and lands on
//      ERR_FILE_NOT_FOUND — the renderer goes blank.
//
//   2. Even if the temp file survived, the SPA's assets use
//      ``./assets/...`` relative paths and the injected
//      ``<base href="file:///.../resources/spa/dist/">`` points at
//      the install dir. ``window.location.reload()`` against the
//      base-relative URL then fails to find ``index.html`` at the
//      resolved path on some Chromium builds (reproduced as
//      ``SPA failed to load (-6) .../resources/spa/dist/#/settings``
//      in user logs three minutes after backend ready).
//
// An earlier iteration of this file polled ``/api/v1/health`` and
// reloaded when the backend was down at mount. The probe URL was a
// plain relative path, which under the ``<base href>`` resolved to
// a ``file://`` URL that always throws — so the reload fired
// unconditionally after the 180 s budget and white-screened the
// app. Removing the reload entirely; the splash + per-store retry
// covers the boot window.

// Graceful-shutdown hook for the Electron host. main.ts calls
// ``executeJavaScript("window.__mhcFlush()")`` from its
// ``before-quit`` handler so any running SSE stream gets its
// cancel signal + the bus's terminal persist can flush before the
// backend child process dies. Always exported (DEV + PROD) —
// ``executeJavaScript`` only ever runs in the Electron host.
const w = window as unknown as {
  __mhcFlush?: () => Promise<number | null>
  __i18n?: unknown
}
w.__mhcFlush = async () => {
  const streams = useSessionStreamsStore()
  if (!streams) return null
  // Snapshot count of sessions that were streaming when the user
  // hit close, then flush. Flush awaits the per-session
  // cancel calls + a 2 s budget for the bus's debounced persist
  // callbacks to land.
  const wasStreaming = streams.handles.size
  await streams.flush(2000)
  return wasStreaming
}

// Dev / test escape hatch: CDP-driven e2e scripts need to flip
// the locale without going through the Settings UI. Exposing the
// i18n module on window is a one-line contract; production users
// never see this path.
if (import.meta.env.DEV) {
  w.__i18n = { locale, setLocale }
}