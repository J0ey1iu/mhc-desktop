"use strict";
/**
 * Electron main process for mhc-desktop.
 *
 * Responsibilities, in order:
 *   1. Single-instance lock — a second exe launch focuses the existing
 *      window instead of spawning a second backend.
 *   2. Create the BrowserWindow IMMEDIATELY and load the built SPA
 *      from disk (file://). The renderer paints the loading splash
 *      with the brand logo while the bundled Python backend is
 *      still booting in the background — first launch on a fresh
 *      Win11 install can take 30–90 s while AV scans the bundle.
 *   3. Spawn the Python backend, poll /ready in the background,
 *      hand the port to the renderer via a small injected config
 *      script so the SPA's fetch() calls can hit it directly.
 *   4. System tray icon with "Open / Quit" menu; clicking the X
 *      prompts the user to either exit or minimise to the tray.
 *   5. Graceful shutdown: when the user picks "Exit" we ask the
 *      renderer to cancel running SSE streams + flush the bus
 *      persists before killing the backend child.
 */
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_child_process_1 = require("node:child_process");
const electron_1 = require("electron");
const electron_integration_1 = require("./updater/electron-integration");
const node_fs_1 = require("node:fs");
const node_net_1 = __importDefault(require("node:net"));
const node_os_1 = __importDefault(require("node:os"));
const node_path_1 = __importDefault(require("node:path"));
// ---------- logging ----------
//
// On a packaged install the only way to diagnose "double-click did
// nothing" is the userdata log file. Without it we get a black hole.
// Mirror everything we print to console.log to a rotating log in
// ``app.getPath('userData')``. Backend child stdout/stderr is also
// piped through the same file.
let _logPath = null;
function logPath() {
    if (_logPath)
        return _logPath;
    const dir = electron_1.app.getPath("userData");
    _logPath = node_path_1.default.join(dir, "mhc-desktop.log");
    return _logPath;
}
async function appendLog(line) {
    try {
        await node_fs_1.promises.appendFile(logPath(), line + "\n", "utf8");
    }
    catch {
        /* userdata not writable — best effort */
    }
}
function ts() {
    // Asia/Shanghai is fixed UTC+8 (no DST) — shift manually so the
    // Electron log lines match the backend's Shanghai-time lines no
    // matter which timezone the host machine is set to.
    return new Date(Date.now() + 8 * 3600 * 1000).toISOString().replace("Z", "+08:00");
}
const _origLog = console.log;
const _origErr = console.error;
console.log = (...args) => {
    const line = `[${ts()}] [electron] ${args.map(String).join(" ")}`;
    _origLog(line);
    void appendLog(line);
};
console.error = (...args) => {
    const line = `[${ts()}] [electron:ERR] ${args.map(String).join(" ")}`;
    _origErr(line);
    void appendLog(line);
};
async function openLogFolder() {
    try {
        await electron_1.shell.openPath(node_path_1.default.dirname(logPath()));
    }
    catch {
        /* ignore */
    }
}
const DEV_URL = process.env.MHC_DEV_URL || "http://127.0.0.1:5180";
const BACKEND_PORT = parseInt(process.env.MHC_PORT || "8765", 10);
// Cold-start on a fresh Win11 install can easily take 60–90 s:
//   - AV scans every DLL / .pyc on first encounter
//   - mechanical HDD random I/O for ~7k files
//   - uvicorn factory import chain
// 30 s was unrealistic for first launch. We now display the loading
// splash from the start, so the wait feels less stuck — but we
// still cap the synchronous wait so a permanently-broken backend
// doesn't hide the window forever.
const READY_TIMEOUT_MS = 30_000;
const READY_BACKGROUND_TIMEOUT_MS = 180_000;
const SPA_PORT_SCAN = [BACKEND_PORT, 8766, 8767, 8768, 8769, 8770];
// ---------- global voice input (Alt+Shift+W) ----------
//
// The recognizer (sherpa-onnx WASM, ~200 MB model) runs inside the
// main window's renderer — it is the only place it can live without
// duplicating the model in memory. The overlay is a separate
// frameless, transparent, always-on-top window that NEVER takes
// focus (focusable:false + ignore-mouse), so the foreground app
// keeps focus while the user dictates; when the run ends we commit
// the text there via clipboard + simulated Ctrl+V.
const DEFAULT_VOICE_SHORTCUT = "Alt+Shift+W";
/** Current global dictation shortcut — the user picks from a fixed
 *  preset list in Settings (free-form recording would risk colliding
 *  with IME / app-specific hotkeys we can't see). */
let voiceShortcut = DEFAULT_VOICE_SHORTCUT;
/** Bottom-center overlay size (px). The card is 312 wide + 12 px
 *  transparent margins so the rounded corners are truly rounded. */
const OVERLAY_W = 336;
const OVERLAY_H = 148;
/** Overlay sits this far above the bottom of the work area. */
const OVERLAY_BOTTOM_MARGIN = 40;
let overlayWin = null;
let voiceRunning = false;
/** The SPA only enables the shortcut after login (reported via
 *  ``voice:event`` type "auth") — dictation before auth would be
 *  wrong (login screen active). Defaults to false: fail-closed. */
let voiceAuthOk = false;
/** Latest renderer event, replayed to a freshly-loaded overlay so
 *  the fast "mic → loading" transition isn't missed while the
 *  overlay window is still booting. */
let lastOverlayEvent = null;
function overlayURL() {
    if (isDev()) {
        const u = new URL(DEV_URL);
        return `${u.origin}/overlay.html`;
    }
    return `file:///${node_path_1.default.join(spaDistDir(), "overlay.html").replace(/\\/g, "/")}`;
}
function positionOverlay() {
    if (!overlayWin || overlayWin.isDestroyed())
        return;
    const wa = electron_1.screen.getPrimaryDisplay().workArea;
    const [w, h] = overlayWin.getSize();
    const x = Math.round(wa.x + (wa.width - w) / 2);
    const y = Math.round(wa.y + wa.height - h - OVERLAY_BOTTOM_MARGIN);
    overlayWin.setPosition(x, y);
}
function ensureOverlay() {
    if (overlayWin && !overlayWin.isDestroyed()) {
        positionOverlay();
        overlayWin.showInactive();
        return overlayWin;
    }
    overlayWin = new electron_1.BrowserWindow({
        width: OVERLAY_W,
        height: OVERLAY_H,
        frame: false,
        transparent: true,
        // Required on Windows: without an alpha background the default
        // white base color shows through the page's transparent areas,
        // turning the rounded corners into a sharp square.
        backgroundColor: "#00000000",
        resizable: false,
        movable: false,
        minimizable: false,
        maximizable: false,
        closable: false,
        alwaysOnTop: true,
        skipTaskbar: true,
        show: false,
        webPreferences: {
            preload: node_path_1.default.join(__dirname, "preload.js"),
            contextIsolation: true,
            nodeIntegration: false,
            sandbox: true,
            backgroundThrottling: false,
        },
    });
    overlayWin.setAlwaysOnTop(true, "screen-saver");
    overlayWin.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
    // Clicks must never reach the overlay — it must not steal focus
    // from the app being dictated into; everything is shortcut-driven.
    // Note: focusable:false is deliberately NOT set — on Windows that
    // combination with transparent:true leaves the window unpainted
    // (known Electron compositor quirk). showInactive + ignored mouse
    // events already guarantee it never takes focus.
    overlayWin.setIgnoreMouseEvents(true);
    overlayWin.on("closed", () => {
        overlayWin = null;
    });
    const url = overlayURL();
    void overlayWin.loadURL(url).catch((e) => {
        console.error("[voice] overlay failed to load:", e);
    });
    positionOverlay();
    overlayWin.showInactive();
    return overlayWin;
}
function hideOverlay() {
    if (overlayWin && !overlayWin.isDestroyed())
        overlayWin.hide();
}
/** Alt+Shift+W toggles a global dictation run. Start = show the
 *  overlay + tell the renderer to open the mic; stop = finalize,
 *  hide the overlay and commit the transcript to the focused app. */
async function toggleGlobalVoice() {
    if (!voiceAuthOk) {
        // Fail-closed: no auth yet (splash / login / logout) → ignore.
        console.log("[voice] Alt+Shift+W ignored — session not authenticated");
        return;
    }
    const win = mainWindow;
    if (!win || win.isDestroyed()) {
        console.warn("[voice] shortcut pressed but main window is gone");
        return;
    }
    if (voiceRunning) {
        voiceRunning = false;
        // Stop is answered by the renderer with ``voice:done``.
        win.webContents.send("voice:run", "stop");
        return;
    }
    if (win.webContents.isLoading())
        return;
    voiceRunning = true;
    ensureOverlay();
    win.webContents.send("voice:run", "start");
}
/** Put the transcript into whatever app currently has focus. The
 *  overlay never steals focus, so the pre-shortcut foreground app
 *  is still foreground at stop time — clipboard + Ctrl+V is the
 *  one mechanism that works for ANY target (Unicode-safe, no
 *  window handle needed). The user's previous clipboard content
 *  is restored after the paste lands. */
function commitVoiceText(text) {
    const prev = electron_1.clipboard.readText();
    electron_1.clipboard.writeText(text);
    const restore = () => electron_1.clipboard.writeText(prev);
    if (process.platform === "win32") {
        const ps = (0, node_child_process_1.spawn)("powershell", ["-NoProfile", "-STA", "-Command", "$w = New-Object -ComObject wscript.shell; $w.SendKeys('^v')"], { windowsHide: true });
        // Restore fast — the target app reads the clipboard synchronously
        // on Ctrl+V, so 800 ms never races it, and our transcript is out
        // of the clipboard within a second.
        ps.on("exit", () => setTimeout(restore, 800));
        ps.on("error", (e) => {
            console.error("[voice] paste failed:", e.message);
            setTimeout(restore, 300);
        });
    }
    else if (process.platform === "darwin") {
        const osa = (0, node_child_process_1.spawn)("osascript", ["-e", 'tell application \"System Events\" to keystroke \"v\" using command down']);
        osa.on("exit", () => setTimeout(restore, 800));
    }
    else {
        // Linux: nothing generic without extra deps — text stays on the
        // clipboard for the user to paste manually.
        setTimeout(restore, 5000);
    }
}
function registerVoiceShortcut() {
    // Re-register is idempotent: unregister of a never-registered
    // accelerator is a no-op, register() replaces any bound handler.
    electron_1.globalShortcut.unregister(voiceShortcut);
    const ok = electron_1.globalShortcut.register(voiceShortcut, () => {
        void toggleGlobalVoice();
    });
    if (!ok) {
        console.warn(`[voice] globalShortcut ${voiceShortcut} is already taken by another app — voice input disabled`);
    }
    else {
        console.log(`[voice] globalShortcut ${voiceShortcut} registered`);
    }
}
//
// A second exe launch forwards its argv to the original instance and
// focuses the existing window. Without this the user double-clicks
// the shortcut twice because nothing appeared, and we end up with
// two Electron processes each holding port 8765 → one fails to bind
// → user sees the broken state and gives up.
const gotLock = electron_1.app.requestSingleInstanceLock();
if (!gotLock) {
    console.log("[electron] another instance is already running; exiting");
    electron_1.app.exit(0);
}
electron_1.app.on("second-instance", () => {
    // Bring the existing window to the front so the user sees
    // something — that's the whole reason they re-launched.
    if (mainWindow && !mainWindow.isDestroyed()) {
        if (mainWindow.isMinimized())
            mainWindow.restore();
        mainWindow.show();
        mainWindow.focus();
    }
});
async function pickPort() {
    // Probe each candidate in order; the first one we can bind+release
    // is the one we tell the backend to use. Skipping this step and
    // asking the backend to bind 8765 directly would fail if another
    // process (zombie listener, dev backend, etc.) holds it.
    for (const port of SPA_PORT_SCAN) {
        if (await canBind(port))
            return port;
    }
    return BACKEND_PORT;
}
function pickPortSync() {
    if (backendPort !== null)
        return backendPort;
    return BACKEND_PORT;
}
function canBind(port) {
    return new Promise((res) => {
        const tester = node_net_1.default.createServer();
        tester.unref();
        tester.once("error", () => res(false));
        tester.once("listening", () => tester.close(() => res(true)));
        tester.listen(port, "127.0.0.1");
    });
}
// ---------- shared state ----------
let backend = null;
let backendExited = null;
let mainWindow = null;
/** Temp index.html with the backend URL injected (see
 *  ``stageInjectedIndex``). Regenerated when the window is recreated. */
let injectedHtmlPath = null;
let backendPort = null;
let backendReady = false;
let tray = null;
let isQuitting = false;
// The user can tell us they want the X button to go to tray (rather
// than quit) and we remember it across launches. Stored in
// userData/config.json via electron-store so the choice survives an
// app update.
const closePrefStore = {
    read() {
        try {
            const v = require("electron-store");
            // Lazy-load so the dev path (where electron-store is bundled
            // but the cwd differs) still works without throwing on import.
            const Store = v.default ?? v;
            const s = new Store({ name: "mhc-desktop-prefs", defaults: { closeAction: "tray", closeRemember: false } });
            return {
                action: s.get("closeAction") === "exit" ? "exit" : "tray",
                remember: Boolean(s.get("closeRemember")),
            };
        }
        catch (e) {
            console.warn("[electron] electron-store unavailable, defaults:", e instanceof Error ? e.message : e);
            return { action: "tray", remember: false };
        }
    },
    write(action, remember) {
        try {
            const v = require("electron-store");
            const Store = v.default ?? v;
            const s = new Store({ name: "mhc-desktop-prefs" });
            s.set("closeAction", action);
            s.set("closeRemember", remember);
        }
        catch (e) {
            console.warn("[electron] could not persist close pref:", e instanceof Error ? e.message : e);
        }
    },
};
function isDev() {
    // ``--mhc-dev-url=...`` is what the npm scripts pass. Plain
    // ``--mhc-dev-url`` without a value also works (the URL falls
    // back to ``DEV_URL`` below). We check both forms so an
    // operator can pass either.
    return (process.argv.some((a) => a === "--mhc-dev-url" || a.startsWith("--mhc-dev-url=")) ||
        !!process.env.MHC_DEV_URL);
}
/** Where the bundled Python+venv lives when packaged. ``process.resourcesPath``
 *  is set by electron to the asar's ``resources/`` dir at runtime. The
 *  optional ``MHC_BUNDLED_BACKEND`` env lets us point at an alternate
 *  build-resources path during local testing — but ONLY in dev. A
 *  stray env var from a previous dev session would otherwise send the
 *  packaged app looking for a path that doesn't exist on the user's
 *  machine. */
function bundledBackendDir() {
    // Dev: honour MHC_BUNDLED_BACKEND if set (lets us point at a
    // build-resources dir without rebuilding). In dev we never look
    // at ``process.resourcesPath`` because Electron sets that to its
    // own internals (``electron/dist/resources``) regardless of
    // packaging state, and the bundled backend obviously doesn't
    // live there — trying to spawn it would just ENOENT.
    if (!electron_1.app.isPackaged) {
        return process.env.MHC_BUNDLED_BACKEND ?? null;
    }
    const rp = process.resourcesPath;
    if (!rp)
        return null;
    return node_path_1.default.join(rp, "backend");
}
/** Path to the SPA ``dist/`` directory. In dev mode main.js lives at
 *  ``packages/mhc-desktop-app/main.js`` (``__dirname`` is the app
 *  package), so the dist is one level up at
 *  ``packages/mhc-desktop-frontend/dist``. In packaged mode the SPA
 *  is staged under ``resources/spa/dist`` via the ``extraResources``
 *  block in package.json — NOT inside the asar. (Earlier revisions
 *  tried to put it in the asar via a ``../mhc-desktop-frontend/dist``
 *  ``files`` glob, but electron-builder silently drops globs that
 *  cross ``..`` outside the package root, so the SPA never made it
 *  onto disk and the renderer got a white screen. See
 *  ``docs/PACKAGING-MHC-DESKTOP.md`` §3.3 for the same pitfall.) */
function spaDistDir() {
    if (!electron_1.app.isPackaged) {
        return node_path_1.default.resolve(__dirname, "..", "mhc-desktop-frontend", "dist");
    }
    const rp = process.resourcesPath;
    if (!rp)
        return node_path_1.default.resolve(__dirname, "..", "mhc-desktop-frontend", "dist");
    return node_path_1.default.join(rp, "spa", "dist");
}
function spaIndexPath() {
    return node_path_1.default.join(spaDistDir(), "index.html");
}
/** Inject a small config script + a base href into the SPA's
 *  index.html so the renderer can locate the backend AND find its
 *  assets when loaded from a non-dist directory (we stage a copy
 *  into ``os.tmpdir()`` and ``<base>`` redirects all relative
 *  asset URLs back into the actual ``dist/`` directory).
 *
 *  In dev mode the vite proxy makes the ``window.__MHC_BACKEND_URL``
 *  injection unnecessary because relative URLs are rewritten
 *  server-side, so we return the original HTML untouched.
 *
 *  We materialise the modified HTML to a temp file because
 *  ``loadURL`` with a ``data:`` URL breaks the relative asset paths
 *  that the Vite build emits (and has a ~2 MB URL-length cap).
 *
 *  Implementation note: we PREPEND our ``<script>`` to ``<head>``
 *  rather than splicing into an existing script tag. Inserting
 *  inside the theme bootstrap script corrupts the parser: the
 *  inner ``<script>...</script>`` becomes JS syntax and the
 *  entire document fails to parse — the renderer then shows the
 *  raw JS source as text. Prepending is unambiguous. */
async function injectBackendUrl(html, port, distDir) {
    // ``file:///`` URLs don't get the trailing slash treatment we
    // want on Windows path separators; normalise to forward slashes
    // so ``<base>`` resolves correctly across platforms.
    const baseHref = `file:///${distDir.replace(/\\/g, "/").replace(/^\/+/, "")}/`;
    const config = `<script>window.__MHC_BACKEND_URL="http://127.0.0.1:${port}";</script>`;
    const base = `<base href="${baseHref}">`;
    const out = html.replace(/<head>/, `<head>${config}${base}`);
    const tmp = node_path_1.default.join(node_os_1.default.tmpdir(), `mhc-desktop-${process.pid}.html`);
    await node_fs_1.promises.writeFile(tmp, out, "utf8");
    return tmp;
}
function startBackend(port) {
    const bundled = bundledBackendDir();
    let cmd;
    let args;
    if (bundled && !process.env.MHC_FORCE_UV) {
        // The bundled interpreter is a full install-only PBS build with
        // all backend deps baked into its own Lib\site-packages — no venv
        // layer, so the bundle stays relocatable (a venv's pyvenv.cfg pins
        // absolute build-machine paths and dies with exit 103 elsewhere).
        const py = process.platform === "win32"
            ? node_path_1.default.join(bundled, "python", "python.exe")
            : node_path_1.default.join(bundled, "python", "bin", "python3");
        cmd = py;
        args = ["-m", "mhc_desktop_deploy"];
        console.log(`[backend] spawning bundled: ${cmd} ${args.join(" ")}`);
    }
    else {
        cmd = "uv";
        args = ["run", "-m", "mhc_desktop_deploy"];
        console.log(`[backend] spawning via uv: ${cmd} ${args.join(" ")}`);
    }
    const child = (0, node_child_process_1.spawn)(cmd, args, {
        env: {
            ...process.env,
            MHC_PORT: String(port),
            MHC_HOST: "127.0.0.1",
            // Disable the dev autoreloader in the packaged app — it watches
            // for filesystem changes inside the asar and never matches in
            // production anyway, but it doubles startup time on Windows.
            // Dev mode (MHC_FORCE_UV=uv) keeps it on so backend code edits
            // hot-reload without restarting the whole Electron process.
            MHC_RELOAD: !electron_1.app.isPackaged && process.env.MHC_FORCE_UV ? "1" : "0",
            // Tell the Python backend where electron-builder staged its
            // extraResources (content-packs/ lives here in packaged
            // builds). Empty in dev — the backend treats that as a no-op.
            // See ``docs/PACKAGING-MHC-DESKTOP.md`` §3.6.
            MHC_RESOURCES_PATH: process.resourcesPath ?? "",
        },
        stdio: ["ignore", "pipe", "pipe"],
    });
    child.stdout?.on("data", (b) => {
        const s = b.toString().trimEnd();
        console.log(s);
    });
    child.stderr?.on("data", (b) => {
        const s = b.toString().trimEnd();
        console.error(s);
    });
    // Spawn failures (ENOENT, EACCES) surface here — without this
    // handler they were silently dropped and the user saw "nothing
    // happens" on double-click.
    child.on("error", (err) => {
        console.error(`[backend] spawn failed: ${err.message}`);
        if (mainWindow && !mainWindow.isDestroyed()) {
            electron_1.dialog.showMessageBox(mainWindow, {
                type: "error",
                message: "mhc-desktop backend failed to start",
                detail: `Could not launch the bundled Python backend:\n\n${err.message}\n\n` +
                    `Log: ${logPath()}`,
            }).catch(() => undefined);
        }
        else {
            electron_1.dialog.showErrorBox("mhc-desktop backend failed to start", `Could not launch the bundled Python backend:\n\n${err.message}\n\n` +
                `Log: ${logPath()}`);
        }
        electron_1.app.quit();
    });
    child.on("exit", (code, signal) => {
        console.log(`[backend] exited code=${code} signal=${signal ?? "none"}`);
        if (code !== 0 && code !== null && mainWindow && !mainWindow.isDestroyed() && !isQuitting) {
            console.error("[backend] crashed — closing window");
            electron_1.dialog.showMessageBox(mainWindow, {
                type: "error",
                message: "mhc-desktop backend stopped",
                detail: `The bundled Python backend exited with code ${code}.\n\nLog: ${logPath()}`,
            }).catch(() => undefined);
            mainWindow.close();
        }
        backend = null;
    });
    backendExited = new Promise((resolve) => child.once("exit", () => resolve()));
    return child;
}
async function waitFor(url, timeoutMs) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
        try {
            const r = await fetch(url);
            if (r.ok)
                return;
        }
        catch {
            // not yet
        }
        await new Promise((res) => setTimeout(res, 500));
    }
    throw new Error(`timeout waiting for ${url}`);
}
/** Promise that resolves when the backend first responds to /ready,
 *  or rejects if the child dies or the timeout elapses. Both the
 *  renderer splash poller and the updater commit path await this. */
let backendReadyPromise = null;
async function watchBackendReady() {
    // Polls /ready for up to READY_BACKGROUND_TIMEOUT_MS. Once the
    // backend responds we tell the renderer to reload — the SPA will
    // recover automatically from there. We exit early if the child
    // process dies (the did-fail-load handler then surfaces a dialog
    // and closes the window).
    if (backendReady || backendPort === null)
        return;
    const url = `http://127.0.0.1:${backendPort}/ready`;
    const deadline = Date.now() + READY_BACKGROUND_TIMEOUT_MS;
    while (Date.now() < deadline) {
        if (backendReady)
            return;
        // If the child has already exited there is no point polling.
        if (backendExited) {
            const exited = await Promise.race([
                backendExited.then(() => true),
                new Promise((res) => setTimeout(() => res(false), 0)),
            ]);
            if (exited) {
                console.warn("[electron] backend child exited before coming ready — stopping watcher");
                return;
            }
        }
        try {
            const r = await fetch(url);
            if (r.ok) {
                backendReady = true;
                console.log(`[electron] backend became ready in background (${((deadline - Date.now()) / 1000).toFixed(0)}s remaining)`);
                return;
            }
        }
        catch {
            // not yet
        }
        await new Promise((res) => setTimeout(res, 1000));
    }
    console.warn(`[electron] backend never came ready within ${(READY_BACKGROUND_TIMEOUT_MS / 1000).toFixed(0)}s background window`);
}
/** Build a 16x16 tray icon. We use the app's brand SVG by rendering
 *  the first 16x16 pixels of the .png icon (already bundled in
 *  ``build-resources/icon.png``); falling back to a flat colour
 *  square if the file is missing. */
function buildTrayIcon() {
    const iconPng = node_path_1.default.join(electron_1.app.getAppPath(), "build-resources", "icon.png");
    try {
        const img = electron_1.nativeImage.createFromPath(iconPng);
        if (!img.isEmpty())
            return img.resize({ width: 16, height: 16 });
    }
    catch {
        /* fall through */
    }
    // Fallback: 16x16 navy square so the tray is always visible.
    return electron_1.nativeImage.createFromBuffer(Buffer.from([
        0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x00, 0x00, 0x0d,
        0x49, 0x48, 0x44, 0x52, 0x00, 0x00, 0x00, 0x10, 0x00, 0x00, 0x00, 0x10,
        0x08, 0x02, 0x00, 0x00, 0x00, 0x90, 0x91, 0x68, 0x36, 0x00, 0x00, 0x00,
        0x0f, 0x49, 0x44, 0x41, 0x54, 0x78, 0x9c, 0x63, 0xfc, 0xcf, 0xc0, 0xc0,
        0xc0, 0xf0, 0x1f, 0xc4, 0x80, 0x81, 0x81, 0x21, 0x00, 0x00, 0x06, 0x84,
        0x02, 0x7e, 0x4f, 0xee, 0xa4, 0xa3, 0x00, 0x00, 0x00, 0x00, 0x49, 0x45,
        0x4e, 0x44, 0xae, 0x42, 0x60, 0x82,
    ]));
}
function createTray() {
    if (tray)
        return;
    tray = new electron_1.Tray(buildTrayIcon());
    tray.setToolTip("mhc-desktop");
    const menu = electron_1.Menu.buildFromTemplate([
        {
            label: "Open mhc-desktop",
            click: () => {
                if (!mainWindow || mainWindow.isDestroyed())
                    return;
                if (mainWindow.isMinimized())
                    mainWindow.restore();
                if (!mainWindow.isVisible())
                    mainWindow.show();
                mainWindow.focus();
            },
        },
        { type: "separator" },
        {
            label: "Quit",
            click: () => {
                isQuitting = true;
                electron_1.app.quit();
            },
        },
    ]);
    tray.setContextMenu(menu);
    tray.on("click", () => {
        if (!mainWindow || mainWindow.isDestroyed())
            return;
        if (mainWindow.isMinimized())
            mainWindow.restore();
        if (!mainWindow.isVisible())
            mainWindow.show();
        mainWindow.focus();
    });
}
function destroyTray() {
    if (!tray)
        return;
    try {
        tray.destroy();
    }
    catch {
        /* ignore */
    }
    tray = null;
}
/** Run the close-button prompt. Returns the user's chosen action and
 *  whether to remember it. The "remember" checkbox is only shown
 *  the first few times the user has neither quit nor chose tray
 *  exclusively; once they tick it we honour it on every subsequent
 *  close. */
async function promptCloseAction() {
    if (!mainWindow || mainWindow.isDestroyed())
        return null;
    const choice = await electron_1.dialog.showMessageBox(mainWindow, {
        type: "question",
        message: "关闭 mhc-desktop",
        detail: "选择“最小化到托盘”后，应用会继续在系统托盘运行；" +
            "选择“退出”则会停止后台进程。",
        buttons: ["最小化到托盘", "退出"],
        defaultId: 0,
        cancelId: 0,
        noLink: true,
        checkboxLabel: "记住我的选择",
        checkboxChecked: false,
    });
    // showMessageBox returns ``checkboxChecked`` when a checkboxLabel
    // is set. Map button index → action.
    const action = choice.response === 1 ? "exit" : "tray";
    return { action, remember: choice.checkboxChecked === true };
}
/** Stage the injected index.html (backend URL + base href) to a
 *  temp file. Returns null in dev (vite proxy handles it) or if
 *  staging fails. Re-runnable so a recreated window can regenerate
 *  the file after it was cleaned up. */
async function stageInjectedIndex() {
    if (isDev())
        return null;
    try {
        const original = await node_fs_1.promises.readFile(spaIndexPath(), "utf8");
        return await injectBackendUrl(original, backendPort ?? BACKEND_PORT, spaDistDir());
    }
    catch (e) {
        console.error("[electron] failed to stage injected SPA index:", e);
        return null;
    }
}
/** Create the main window and wire its per-window handlers. Also
 *  the recovery path: if the window is ever destroyed while the app
 *  stays alive in the tray (a renderer script ``window.close()`` —
 *  Ctrl+W — bypasses the main-process 'close' handler and destroys
 *  the window directly; verified) the closed-guard recreates it
 *  hidden so the tray "Open" and the global voice shortcut keep
 *  working instead of dying silently. */
function createMainWindow(startHidden) {
    const win = new electron_1.BrowserWindow({
        width: 1180,
        height: 760,
        minWidth: 760,
        minHeight: 480,
        title: "mhc-desktop",
        // No native title bar; the renderer draws its own in TitleBar.vue
        // (full control over theme, hover states, drag region). Window
        // resize borders are still handled by the OS frame.
        titleBarStyle: "hidden",
        backgroundColor: "#ffffff",
        show: false, // wait until first paint to avoid the white flash
        webPreferences: {
            preload: node_path_1.default.join(__dirname, "preload.js"),
            contextIsolation: true,
            nodeIntegration: false,
            sandbox: true,
            // The voice recognizer + level meter run in this renderer
            // even while the window is minimized to tray — without this
            // Chromium throttles rAF/timers in hidden windows.
            backgroundThrottling: false,
        },
    });
    mainWindow = win;
    // Show as soon as the DOM is ready so the user gets visual
    // feedback BEFORE the backend is up — unless this window is the
    // hidden tray-recovery instance, which stays in the tray until
    // summoned.
    win.once("ready-to-show", () => {
        if (!win.isDestroyed() && !startHidden)
            win.show();
    });
    win.on("maximize", () => win.webContents.send("window:maximize-changed", true));
    win.on("unmaximize", () => win.webContents.send("window:maximize-changed", false));
    // Close-button handling. We intercept the X click: if the user
    // remembers "tray" we just hide the window; otherwise we prompt
    // them once and persist their choice.
    let closePromptDone = false;
    win.on("close", async (event) => {
        if (isQuitting)
            return; // we're already on the way out
        if (!win || win.isDestroyed())
            return;
        // First close ever: respect the persisted pref from prior runs.
        const pref = closePrefStore.read();
        if (pref.remember && !closePromptDone) {
            closePromptDone = true;
            if (pref.action === "tray") {
                event.preventDefault();
                win.hide();
                return;
            }
            // pref.action === "exit" → fall through to actual quit
        }
        else if (!closePromptDone) {
            // First close, no remembered pref: ask the user.
            event.preventDefault();
            const r = await promptCloseAction();
            closePromptDone = true;
            if (!r)
                return;
            if (r.remember)
                closePrefStore.write(r.action, true);
            if (r.action === "tray") {
                win.hide();
                return;
            }
            // User picked exit. We already preventDefault'd above so
            // the window won't close on its own; re-fire close() with
            // isQuitting set so the recursive handler short-circuits.
            isQuitting = true;
            win.close();
            return;
        }
        else {
            // Subsequent closes within this session always honour the
            // first choice — no need to re-prompt.
            if (pref.action === "tray") {
                event.preventDefault();
                win.hide();
                return;
            }
        }
        isQuitting = true;
    });
    // Self-heal: any close path that destroys the window (renderer
    // script-close bypasses the 'close' preventDefault) must not
    // leave the tray/shortcut dead. Reset any in-flight voice run
    // (its renderer died with the window) and bring up a fresh
    // window hidden in the tray.
    win.on("closed", () => {
        if (mainWindow === win)
            mainWindow = null;
        if (isQuitting)
            return;
        console.warn("[electron] main window destroyed unexpectedly — recreating hidden in tray");
        voiceRunning = false;
        hideOverlay();
        void stageInjectedIndex().then((p) => {
            injectedHtmlPath = p ?? injectedHtmlPath;
            createMainWindow(true);
        });
    });
    const url = isDev()
        ? DEV_URL
        : injectedHtmlPath
            ? `file://${injectedHtmlPath.replace(/\\/g, "/")}`
            : `file://${spaIndexPath().replace(/\\/g, "/")}`;
    if (!isDev())
        console.log(`[electron] loading SPA at ${url}`);
    void win.loadURL(url);
    win.webContents.setWindowOpenHandler(({ url: u }) => {
        void electron_1.shell.openExternal(u);
        return { action: "deny" };
    });
    // If the SPA can't load (corrupt build, asar unpack failure, etc.)
    // the user would otherwise stare at a blank frameless window.
    // Surface the failure with a dialog instead. Note: a backend-not-
    // ready situation is NOT a failure here — the SPA renders the
    // splash on its own and polls /health, so ``did-fail-load`` for a
    // missing backend should never fire (file:// always loads).
    win.webContents.on("did-fail-load", (_e, code, desc, url, isMainFrame) => {
        if (!isMainFrame)
            return;
        console.error(`[electron] SPA failed to load (${code}) ${desc} at ${url}`);
        if (code === -102 /* ERR_CONNECTION_REFUSED */ || code === -106 /* ERR_CONNECTION_RESET */) {
            void electron_1.dialog
                .showMessageBox({
                type: "error",
                message: "mhc-desktop SPA failed to load",
                detail: `The bundled SPA could not be loaded.\n\n` +
                    `Open the log folder to see why, then relaunch.\n\nLog: ${logPath()}`,
                buttons: ["Open log folder", "Quit"],
                defaultId: 0,
                cancelId: 1,
            })
                .then(async (choice) => {
                if (choice.response === 0)
                    await openLogFolder();
                isQuitting = true;
                electron_1.app.quit();
            });
        }
    });
    // Clean up the temp injected index.html once the renderer has
    // booted. We don't await this — the file is only ever read on
    // cold start, so even a leaked handle is harmless.
    const injectedPathToClean = injectedHtmlPath;
    if (injectedPathToClean) {
        win.webContents.once("did-finish-load", () => {
            void node_fs_1.promises.unlink(injectedPathToClean).catch(() => undefined);
        });
    }
}
async function bootstrap() {
    await electron_1.app.whenReady();
    // Drop Electron's default menu bar — File / Edit / View / Window / Help
    // takes vertical space and isn't useful for an SPA shell.
    // On macOS the system menu stays by default (Apple HIG); we don't
    // suppress it since Cmd+Q etc. live there.
    if (process.platform !== "darwin") {
        electron_1.Menu.setApplicationMenu(null);
    }
    const isDevMode = isDev();
    // The updater bootstraps BEFORE the window paints so a freshly
    // applied Tier 2/3 is what the user sees on launch. We need to know
    // whether ``resourcesPath`` exists (packaged mode); in dev there's
    // no ``extraResources/`` so the updater is a no-op.
    const updaterEnabled = electron_1.app.isPackaged && !isDevMode;
    // Resolve the backend port NOW so the injected config script can
    // carry it. We pick the port before spawning so two consecutive
    // launches don't race for the same number.
    if (!isDevMode) {
        backendPort = await pickPort();
        backend = startBackend(backendPort);
        // Watch /ready in the background. The SPA is already painting
        // the loading splash; once /health answers, the renderer
        // transitions out of the splash on its own.
        // The promise is awaited by both the renderer splash logic and
        // the updater commit path; track it once and reuse.
        backendReadyPromise = watchBackendReady().catch((err) => {
            console.error("[electron] background ready watcher failed:", err);
            throw err;
        });
        // Stage a temp index.html with the backend URL injected so the
        // SPA's fetch() calls hit the right port. ``distDir`` is also
        // injected as a ``<base href>`` so the relative asset URLs
        // (``./assets/index-XYZ.js``, ``./fonts/...``) resolve back into
        // the actual ``dist/`` directory instead of "os.tmpdir()".
        // Re-staged on window recreation (the file gets unlinked after
        // each window's first load).
        injectedHtmlPath = (await stageInjectedIndex()) ?? injectedHtmlPath;
    }
    createMainWindow(false);
    // Window controls — TitleBar.vue drives these via IPC.
    electron_1.ipcMain.handle("window:minimize", () => {
        if (!mainWindow || mainWindow.isDestroyed())
            return;
        mainWindow.minimize();
    });
    electron_1.ipcMain.handle("window:toggle-maximize", () => {
        if (!mainWindow || mainWindow.isDestroyed())
            return;
        if (mainWindow.isMaximized())
            mainWindow.unmaximize();
        else
            mainWindow.maximize();
    });
    electron_1.ipcMain.handle("window:close", () => {
        if (!mainWindow || mainWindow.isDestroyed())
            return;
        mainWindow.close();
    });
    electron_1.ipcMain.handle("window:is-maximized", () => {
        return mainWindow && !mainWindow.isDestroyed() ? mainWindow.isMaximized() : false;
    });
    // ``window:quit`` skips the close prompt — used by the Tray menu's
    // "Quit" entry so the user can always force an exit, even when
    // they previously remembered "minimise to tray".
    electron_1.ipcMain.handle("window:quit", () => {
        isQuitting = true;
        electron_1.app.quit();
    });
    // Close-button handling + window load live in createMainWindow.
    // Skill import pickers. The renderer can't show a native dialog or
    // read arbitrary filesystem paths under contextIsolation, so we
    // hand the dialog through the main process. Folder pickers return
    // the absolute path; file pickers return {path, name} so the
    // renderer can use the name as a hint.
    electron_1.ipcMain.handle("dialog:pick-folder", async () => {
        if (!mainWindow || mainWindow.isDestroyed())
            return null;
        const r = await electron_1.dialog.showOpenDialog(mainWindow, {
            title: "Import skill folder",
            properties: ["openDirectory"],
        });
        if (r.canceled || r.filePaths.length === 0)
            return null;
        return r.filePaths[0];
    });
    electron_1.ipcMain.handle("dialog:pick-file", async (_evt, opts = {}) => {
        if (!mainWindow || mainWindow.isDestroyed())
            return null;
        const r = await electron_1.dialog.showOpenDialog(mainWindow, {
            title: "Import skill bundle",
            properties: ["openFile"],
            filters: opts.filters,
        });
        if (r.canceled || r.filePaths.length === 0)
            return null;
        const p = r.filePaths[0];
        return { path: p, name: node_path_1.default.basename(p) };
    });
    electron_1.ipcMain.handle("fs:read-file", async (_evt, p) => {
        if (!mainWindow || mainWindow.isDestroyed())
            return null;
        if (typeof p !== "string" || !p)
            return null;
        // Defense in depth: only allow paths inside the user's home dir.
        const home = electron_1.app.getPath("home");
        const norm = node_path_1.default.normalize(p);
        if (!norm.startsWith(home))
            return null;
        try {
            const data = await node_fs_1.promises.readFile(norm);
            return data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength);
        }
        catch {
            return null;
        }
    });
    // Build the tray early so closing the window to tray always works
    // even before the user has seen a single frame of the renderer.
    createTray();
    // Global voice dictation shortcut — registers regardless of dev /
    // packaged so it's testable in both.
    registerVoiceShortcut();
    // Renderer progress (status / partial / level / error) → overlay.
    // Errors also reset our toggle state so the next press restarts.
    electron_1.ipcMain.on("voice:event", (_e, ev) => {
        if (!ev || typeof ev.type !== "string")
            return;
        if (ev.type === "auth") {
            voiceAuthOk = ev.value === "ready";
            return;
        }
        if (ev.type === "shortcut") {
            const acc = String(ev.value);
            if (acc.trim() && acc !== voiceShortcut) {
                voiceShortcut = acc;
                registerVoiceShortcut();
            }
            // Keep a live overlay's hint in sync with the new preset.
            overlayWin?.webContents.send("voice:overlay", ev);
            return;
        }
        lastOverlayEvent = ev;
        overlayWin?.webContents.send("voice:overlay", ev);
        if (ev.type === "status" && ev.value === "error") {
            voiceRunning = false;
            // Keep the error visible a moment before hiding.
            setTimeout(() => hideOverlay(), 2800);
        }
    });
    electron_1.ipcMain.handle("voice:overlay-sync", () => ({
        last: lastOverlayEvent,
        shortcut: voiceShortcut,
    }));
    // Renderer finished a run → hide the overlay and commit the text.
    // When OUR window has focus the transcript goes straight into the
    // composer (clipboard-paste would land on whatever element has
    // focus, possibly a button, and vanish); otherwise it's pasted
    // into the focused foreign app.
    electron_1.ipcMain.on("voice:done", (_e, payload) => {
        voiceRunning = false;
        hideOverlay();
        const text = typeof payload?.text === "string" ? payload.text : "";
        if (!text.trim())
            return;
        if (mainWindow && !mainWindow.isDestroyed() && mainWindow.isFocused()) {
            mainWindow.webContents.send("voice:in-app-commit", text);
        }
        else {
            commitVoiceText(text);
        }
    });
    // Composer mic button = same flow as the shortcut (toggle + commit
    // to the focused app / composer). One code path, no duplication.
    electron_1.ipcMain.on("voice:toggle", () => {
        void toggleGlobalVoice();
    });
    // Settings picked a different preset → re-register the hook.
    electron_1.ipcMain.on("voice:shortcut", (_e, acc) => {
        if (typeof acc !== "string" || !acc.trim())
            return;
        voiceShortcut = acc;
        registerVoiceShortcut();
    });
    // Updater phase 2: IPC + background loop. The window exists by now,
    // so state transitions can reach the renderer.
    if (updaterEnabled && mainWindow) {
        void (0, electron_integration_1.wireUpdater)({
            mainWindow,
            enabled: true,
            isReadyForCommit: () => backendReadyPromise ?? Promise.resolve(),
        });
    }
    electron_1.app.on("activate", () => {
        if (electron_1.BrowserWindow.getAllWindows().length === 0) {
            // Re-create on macOS dock click
            void bootstrap();
        }
    });
}
// Don't auto-quit when the window is closed — the user might have
// asked to minimise to tray. ``mainWindow.on("close")`` sets the
// ``isQuitting`` flag if it actually wants to exit.
electron_1.app.on("window-all-closed", () => {
    if (!isQuitting) {
        // Stay alive in the tray. The Quit tray menu flips isQuitting
        // and calls app.quit() which fires this event again, then we
        // fall through.
        return;
    }
    if (process.platform !== "darwin")
        electron_1.app.quit();
});
electron_1.app.on("before-quit", async (event) => {
    if (shuttingDown)
        return; // already in flight
    shuttingDown = true;
    destroyTray();
    electron_1.globalShortcut.unregisterAll();
    hideOverlay();
    // Ask the renderer to cancel running SSE streams and flush the
    // bus persisters. The renderer's ``__mhcStartExit`` callback
    // shows the "正在退出…" splash and runs ``__mhcFlush()`` (which
    // cancels every active stream and waits for terminal persist).
    // Bound by 5 s so a hung renderer can't stall the quit forever.
    if (mainWindow && !mainWindow.isDestroyed()) {
        try {
            await Promise.race([
                mainWindow.webContents.executeJavaScript(`new Promise((resolve) => {
            const start = window.__mhcStartExit;
            const flush = window.__mhcFlush;
            if (start && flush) {
              start(async () => { await flush(); resolve(0); });
            } else if (flush) {
              flush().then(() => resolve(0));
            } else {
              resolve(0);
            }
            setTimeout(() => resolve(0), 5000); // safety bound
          })`),
                new Promise((r) => setTimeout(r, 5500)),
            ]);
        }
        catch (e) {
            console.warn("[electron] renderer flush failed:", e);
        }
    }
    // Kill the backend child. SIGTERM first; SIGKILL after 1.5 s if
    // it didn't honour SIGTERM (Windows TerminateProcess is forceful
    // enough that the second branch is rare).
    if (backend && !backend.killed) {
        console.log("[electron] backend still alive — terminating before quit");
        backend.kill();
        const exited = await Promise.race([
            backendExited ?? Promise.resolve(),
            new Promise((r) => setTimeout(r, 1500)),
        ]);
        if (!exited && backend && !backend.killed) {
            console.warn("[electron] backend did not exit in 1.5s; force-killing");
            backend.kill("SIGKILL");
            await Promise.race([
                backendExited ?? Promise.resolve(),
                new Promise((r) => setTimeout(r, 500)),
            ]);
        }
        console.log("[electron] backend gone, exiting");
    }
    // Drop the temp injected SPA file.
    try {
        const tmp = node_path_1.default.join(node_os_1.default.tmpdir(), `mhc-desktop-${process.pid}.html`);
        await node_fs_1.promises.unlink(tmp).catch(() => undefined);
    }
    catch {
        /* ignore */
    }
    // Re-emit the quit so Electron proceeds.
    electron_1.app.exit(0);
});
let shuttingDown = false;
void bootstrap();
//# sourceMappingURL=main.js.map