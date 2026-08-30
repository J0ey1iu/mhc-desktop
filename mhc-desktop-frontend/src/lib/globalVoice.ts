// Bridges the Electron global voice shortcut (Alt+Shift+W) to the
// in-renderer sherpa recognizer.
//
// main.ts (Electron) owns the shortcut + the floating overlay window.
// When toggled it sends ``voice:run`` start/stop; here we run the
// same ``voiceInput`` singleton the composer mic button uses and
// stream status / partial transcript / loudness back over IPC so
// the overlay can paint them.
//
// Imported once from the SPA entry so it lives for the whole app
// lifetime (independent of routing or the backend splash).

import { startVoice, stopVoice, cancelPendingVoice, getVoiceState, onVoiceState, onVoiceLevel } from "./voiceInput"

const SHORTCUT_LS_KEY = "mhc.voice.shortcut"
const DEFAULT_SHORTCUT = "Alt+Shift+W"

function report(type: string, value: string | number): void {
  // window.mhc is absent in plain-browser dev (vite without electron) — no-op there.
  window.mhc?.voice?.report(type, value)
}

/** Tell the host whether the user is logged in — the global voice
 *  shortcut must not fire before auth (App.vue watches the auth
 *  store and forwards transitions). */
export function reportAuth(ready: boolean): void {
  report("auth", ready ? "ready" : "idle")
}

/** Send the active theme palette (App.vue watches the theme store)
 *  so the overlay follows the app's look instead of being hardcoded. */
export function reportTheme(): void {
  const cs = getComputedStyle(document.documentElement)
  const take = (n: string) => cs.getPropertyValue(n).trim() || undefined
  const p = {
    bg1: take("--bg-subtle"),
    bg2: take("--bg-panel"),
    text: take("--text"),
    mid: take("--text-mid"),
    faint: take("--text-faint"),
    border: take("--border"),
  }
  report("theme", JSON.stringify(p))
}

/** The chosen shortcut preset, persisted in localStorage (client-
 *  side concern — free-form capture would collide with IME/app
 *  hotkeys we can't enumerate, so Settings only offers presets). */
export function getVoiceShortcut(): string {
  try {
    return localStorage.getItem(SHORTCUT_LS_KEY) ?? DEFAULT_SHORTCUT
  } catch {
    return DEFAULT_SHORTCUT
  }
}

export function setVoiceShortcut(acc: string): void {
  try {
    localStorage.setItem(SHORTCUT_LS_KEY, acc)
  } catch {
    /* ignore */
  }
  report("shortcut", acc)
}

/** Re-apply the persisted preset on boot so a non-default choice
 *  survives restarts (main re-registers on the report). */
function reportStoredShortcut(): void {
  try {
    const v = localStorage.getItem(SHORTCUT_LS_KEY)
    if (v && v !== DEFAULT_SHORTCUT) report("shortcut", v)
  } catch {
    /* ignore */
  }
}
reportStoredShortcut()

// Voice only reports level while listening (~60/s); the overlay's
// bar meter doesn't need that rate, so throttle to ~30/s.
let lastLevelAt = 0

onVoiceState((s) => report("status", s))

onVoiceLevel((l) => {
  const now = Date.now()
  if (now - lastLevelAt < 33) return
  lastLevelAt = now
  report("level", l)
})

window.mhc?.voice?.onRun((action) => {
  if (action === "start") {
    void runGlobal()
  } else {
    stopGlobal()
  }
})

// In-app commit: when OUR window is focused the run's text lands
// here (instead of clipboard-paste) so it always reaches the
// composer, regardless of which element has focus. ChatView listens
// for the DOM event and fills the input.
window.mhc?.voice?.onInAppCommit((text) => {
  window.dispatchEvent(new CustomEvent("mhc:voice-commit", { detail: text }))
})

async function runGlobal(): Promise<void> {
  const s = getVoiceState()
  if (s === "mic" || s === "loading" || s === "listening") return // already running (in-app or global)
  try {
    await startVoice((partial) => report("partial", partial))
  } catch (e) {
    report("status", "error")
    report("message", String(e instanceof Error ? e.message : e))
  }
}

function stopGlobal(): void {
  let text = ""
  const s = getVoiceState()
  if (s === "mic" || s === "loading") {
    // Mid-start: abort cleanly instead of racing the model load.
    cancelPendingVoice()
  } else if (s === "listening") {
    text = stopVoice()
  }
  window.mhc?.voice?.done(text)
}