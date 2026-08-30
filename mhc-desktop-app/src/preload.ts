/**
 * Minimal preload script — sets up a secure context bridge.
 *
 * The frontend uses fetch() for everything (which works through the
 * vite proxy in dev and direct localhost in production), so this
 * preload is intentionally tiny. Add API surfaces here as needed.
 */

import { contextBridge, ipcRenderer, webUtils } from "electron"
import type { UpdateInfo } from "./updater/rollout"

/** UpdateStatus is what the renderer sees across IPC — same shape as
 *  UpdateInfo minus the internal ``forceTier1`` flag. */
type UpdateStatus = Omit<UpdateInfo, "forceTier1">

contextBridge.exposeInMainWorld("mhc", {
  versions: {
    electron: process.versions.electron,
    node: process.versions.node,
  },
  platform: process.platform,
  window: {
    minimize: () => ipcRenderer.invoke("window:minimize"),
    toggleMaximize: () => ipcRenderer.invoke("window:toggle-maximize"),
    close: () => ipcRenderer.invoke("window:close"),
    /** Force-quit the app, bypassing the close-to-tray prompt. Wired
     *  to the tray menu's "Quit" entry and to any UI button that
     *  means "really exit, no questions asked". */
    quit: () => ipcRenderer.invoke("window:quit") as Promise<void>,
    isMaximized: () => ipcRenderer.invoke("window:is-maximized") as Promise<boolean>,
    onMaximizeChange: (cb: (max: boolean) => void) => {
      const handler = (_e: unknown, max: boolean) => cb(max)
      ipcRenderer.on("window:maximize-changed", handler)
      return () => ipcRenderer.removeListener("window:maximize-changed", handler)
    },
  },
  // Skill import helpers — see main.ts for the IPC handlers.
  pickFolder: () => ipcRenderer.invoke("dialog:pick-folder") as Promise<string | null>,
  pickFile: (opts?: {
    filters?: { name: string; extensions: string[] }[]
  }) =>
    ipcRenderer.invoke("dialog:pick-file", opts ?? {}) as Promise<{
      path: string
      name: string
    } | null>,
  readFile: (p: string) =>
    ipcRenderer.invoke("fs:read-file", p) as Promise<ArrayBuffer | null>,
  /**
   * Resolve a File from <input type="file"> to its absolute path on
   * disk. Electron 32+ removed the synchronous ``File.path``
   * attribute for security (it leaked OS paths into the renderer
   * without an explicit user gesture). The replacement is
   * ``webUtils.getPathForFile(file)``, which must be called from a
   * trusted context — so we expose it through the preload bridge
   * and never expose the raw ``webUtils`` object to the page.
   *
   * Returns an empty string when the file has no resolvable path
   * (e.g. dropped from another renderer, or non-Chromium File
   * instance). The caller treats "" as "no path available" — the
   * backend's ``_format_files_block`` renders a name-only line in
   * that case so the model still sees the attachment exists.
   */
  getPathForFile: (file: File): string => {
    try {
      return webUtils.getPathForFile(file) || ""
    } catch {
      return ""
    }
  },

  // Global voice input (Alt+Shift+W): the main window renderer runs
  // the sherpa-onnx recognizer; the overlay window shows status.
  // Main brokers events between the two via ``voice:event``,
  // ``voice:done``, ``voice:run`` and ``voice:overlay`` channels.
  voice: {
    /** Renderer → main: report progress (status/partial/level/message). */
    report: (type: string, value: string | number) =>
      ipcRenderer.send("voice:event", { type, value }),
    /** Renderer → main: recording finished; main commits the text. */
    done: (text: string) => ipcRenderer.send("voice:done", { text }),
    /** Renderer → main: user clicked the composer mic — behave
     *  exactly like the global shortcut (toggle + committed to
     *  wherever focus is). */
    toggle: () => ipcRenderer.send("voice:toggle"),
    /** Renderer → main: the user picked a shortcut preset in
     *  settings; main re-registers the global hook. */
    setShortcut: (acc: string) => ipcRenderer.send("voice:shortcut", acc),
    /** Main window renderer: main asks us to start/stop a global run. */
    onRun: (cb: (action: "start" | "stop") => void) => {
      const handler = (_e: unknown, action: "start" | "stop") => cb(action)
      ipcRenderer.on("voice:run", handler)
      return () => ipcRenderer.removeListener("voice:run", handler)
    },
    /** Main window renderer: main commits the finished transcript
     *  straight into the composer (when the app itself is focused). */
    onInAppCommit: (cb: (text: string) => void) => {
      const handler = (_e: unknown, text: string) => cb(text)
      ipcRenderer.on("voice:in-app-commit", handler)
      return () => ipcRenderer.removeListener("voice:in-app-commit", handler)
    },
    /** Overlay window: main forwards renderer progress events. */
    onEvent: (cb: (e: { type: string; value: string | number }) => void) => {
      const handler = (_e: unknown, e: { type: string; value: string | number }) => cb(e)
      ipcRenderer.on("voice:overlay", handler)
      return () => ipcRenderer.removeListener("voice:overlay", handler)
    },
    /** Overlay window: pull the latest state on boot so early
     *  status updates sent before the overlay finished loading are
     *  not lost (first model load is slow). ``shortcut`` carries the
     *  currently registered global hotkey for the hint row. */
    sync: () =>
      ipcRenderer.invoke("voice:overlay-sync") as Promise<{
        last: { type: string; value: string | number } | null
        shortcut: string
      }>,
  },

  // Updater — surface backend orchestrator state to the renderer.
  // Main owns the state; the renderer reads it on demand and listens
  // for the ``update:state`` push channel.
  update: {
    getStatus: () => ipcRenderer.invoke("update:get-status") as Promise<UpdateStatus>,
    checkNow: () => ipcRenderer.invoke("update:check-now") as Promise<UpdateStatus>,
    install: () => ipcRenderer.invoke("update:install") as Promise<UpdateStatus>,
    applyNow: () => ipcRenderer.invoke("update:apply-now") as Promise<UpdateStatus>,
    rollback: () => ipcRenderer.invoke("update:rollback") as Promise<{ rolled: string[] }>,
    onState: (cb: (s: UpdateStatus) => void) => {
      const handler = (_e: unknown, s: UpdateStatus) => cb(s)
      ipcRenderer.on("update:state", handler)
      return () => ipcRenderer.removeListener("update:state", handler)
    },
  },
})
