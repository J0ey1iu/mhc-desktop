/// <reference types="vite/client" />

/** Wire shape for the renderer-side update API. Mirrors the preload
 *  bridge in mhc-desktop-app/src/preload.ts. */
interface UpdateStatusShape {
  state: string
  releasedAt?: string
  available?: { spa?: string; content_packs?: string; backend?: string }
  error?: string
  progressBytes?: number
  progressTotal?: number
  channel?: string
}

declare module "*.vue" {
  import type { DefineComponent } from "vue"
  const component: DefineComponent<Record<string, unknown>, Record<string, unknown>, unknown>
  export default component
}

declare global {
  interface Window {
    mhc?: {
      versions: { electron: string; node: string }
      platform: NodeJS.Platform
      window: {
        minimize: () => Promise<void>
        toggleMaximize: () => Promise<void>
        close: () => Promise<void>
        isMaximized: () => Promise<boolean>
        onMaximizeChange: (cb: (max: boolean) => void) => () => void
      }
      pickFolder: () => Promise<string | null>
      pickFile: (opts?: {
        filters?: { name: string; extensions: string[] }[]
      }) => Promise<{ path: string; name: string } | null>
      readFile: (p: string) => Promise<ArrayBuffer | null>
      voice?: {
        report: (type: string, value: string | number) => void
        done: (text: string) => void
        toggle: () => void
        setShortcut: (acc: string) => void
        onRun: (cb: (action: "start" | "stop") => void) => () => void
        onInAppCommit: (cb: (text: string) => void) => () => void
        onEvent: (cb: (e: { type: string; value: string | number }) => void) => () => void
        sync: () => Promise<{
          last: { type: string; value: string | number } | null
          shortcut: string
        }>
      }
      update?: {
        getStatus: () => Promise<UpdateStatusShape>
        checkNow: () => Promise<UpdateStatusShape>
        install: () => Promise<UpdateStatusShape>
        applyNow: () => Promise<UpdateStatusShape>
        rollback: () => Promise<{ rolled: string[] }>
        onState: (cb: (s: UpdateStatusShape) => void) => () => void
      }
    }
  }
}

export {}
