"use strict";
/**
 * Minimal preload script — sets up a secure context bridge.
 *
 * The frontend uses fetch() for everything (which works through the
 * vite proxy in dev and direct localhost in production), so this
 * preload is intentionally tiny. Add API surfaces here as needed.
 */
Object.defineProperty(exports, "__esModule", { value: true });
const electron_1 = require("electron");
electron_1.contextBridge.exposeInMainWorld("mhc", {
    versions: {
        electron: process.versions.electron,
        node: process.versions.node,
    },
    platform: process.platform,
    window: {
        minimize: () => electron_1.ipcRenderer.invoke("window:minimize"),
        toggleMaximize: () => electron_1.ipcRenderer.invoke("window:toggle-maximize"),
        close: () => electron_1.ipcRenderer.invoke("window:close"),
        /** Force-quit the app, bypassing the close-to-tray prompt. Wired
         *  to the tray menu's "Quit" entry and to any UI button that
         *  means "really exit, no questions asked". */
        quit: () => electron_1.ipcRenderer.invoke("window:quit"),
        isMaximized: () => electron_1.ipcRenderer.invoke("window:is-maximized"),
        onMaximizeChange: (cb) => {
            const handler = (_e, max) => cb(max);
            electron_1.ipcRenderer.on("window:maximize-changed", handler);
            return () => electron_1.ipcRenderer.removeListener("window:maximize-changed", handler);
        },
    },
    // Skill import helpers — see main.ts for the IPC handlers.
    pickFolder: () => electron_1.ipcRenderer.invoke("dialog:pick-folder"),
    pickFile: (opts) => electron_1.ipcRenderer.invoke("dialog:pick-file", opts ?? {}),
    readFile: (p) => electron_1.ipcRenderer.invoke("fs:read-file", p),
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
    getPathForFile: (file) => {
        try {
            return electron_1.webUtils.getPathForFile(file) || "";
        }
        catch {
            return "";
        }
    },
    // Global voice input (Alt+Shift+W): the main window renderer runs
    // the sherpa-onnx recognizer; the overlay window shows status.
    // Main brokers events between the two via ``voice:event``,
    // ``voice:done``, ``voice:run`` and ``voice:overlay`` channels.
    voice: {
        /** Renderer → main: report progress (status/partial/level/message). */
        report: (type, value) => electron_1.ipcRenderer.send("voice:event", { type, value }),
        /** Renderer → main: recording finished; main commits the text. */
        done: (text) => electron_1.ipcRenderer.send("voice:done", { text }),
        /** Renderer → main: user clicked the composer mic — behave
         *  exactly like the global shortcut (toggle + committed to
         *  wherever focus is). */
        toggle: () => electron_1.ipcRenderer.send("voice:toggle"),
        /** Renderer → main: the user picked a shortcut preset in
         *  settings; main re-registers the global hook. */
        setShortcut: (acc) => electron_1.ipcRenderer.send("voice:shortcut", acc),
        /** Main window renderer: main asks us to start/stop a global run. */
        onRun: (cb) => {
            const handler = (_e, action) => cb(action);
            electron_1.ipcRenderer.on("voice:run", handler);
            return () => electron_1.ipcRenderer.removeListener("voice:run", handler);
        },
        /** Main window renderer: main commits the finished transcript
         *  straight into the composer (when the app itself is focused). */
        onInAppCommit: (cb) => {
            const handler = (_e, text) => cb(text);
            electron_1.ipcRenderer.on("voice:in-app-commit", handler);
            return () => electron_1.ipcRenderer.removeListener("voice:in-app-commit", handler);
        },
        /** Overlay window: main forwards renderer progress events. */
        onEvent: (cb) => {
            const handler = (_e, e) => cb(e);
            electron_1.ipcRenderer.on("voice:overlay", handler);
            return () => electron_1.ipcRenderer.removeListener("voice:overlay", handler);
        },
        /** Overlay window: pull the latest state on boot so early
         *  status updates sent before the overlay finished loading are
         *  not lost (first model load is slow). ``shortcut`` carries the
         *  currently registered global hotkey for the hint row. */
        sync: () => electron_1.ipcRenderer.invoke("voice:overlay-sync"),
    },
    // Updater — surface backend orchestrator state to the renderer.
    // Main owns the state; the renderer reads it on demand and listens
    // for the ``update:state`` push channel.
    update: {
        getStatus: () => electron_1.ipcRenderer.invoke("update:get-status"),
        checkNow: () => electron_1.ipcRenderer.invoke("update:check-now"),
        install: () => electron_1.ipcRenderer.invoke("update:install"),
        applyNow: () => electron_1.ipcRenderer.invoke("update:apply-now"),
        rollback: () => electron_1.ipcRenderer.invoke("update:rollback"),
        onState: (cb) => {
            const handler = (_e, s) => cb(s);
            electron_1.ipcRenderer.on("update:state", handler);
            return () => electron_1.ipcRenderer.removeListener("update:state", handler);
        },
    },
});
//# sourceMappingURL=preload.js.map