// Local speech-to-text for the composer, powered by sherpa-onnx WASM.
//
// The model + runtime live in ``public/sherpa/`` (copied from the
// official sherpa-onnx wasm-simd zipformer ASR package). Everything
// runs in-process in the browser — no audio ever leaves the machine.
//
// API shape (from the sherpa demo app-asr.js):
//   1. glue ``sherpa-onnx-asr.js`` (defines the global
//      ``createOnlineRecognizer(Module)`` helper) and
//      ``sherpa-onnx-wasm-main-asr.js`` (the emscripten module that
//      downloads the 199 MB model ``.data`` via ``Module.locateFile``
//      and then calls ``Module.onRuntimeInitialized``).
//   2. feed mic PCM at 16 kHz into a stream, decode incrementally,
//      expose the running transcript via ``onResult``.
//
// The recognizer is created lazily on first use and kept alive for
// the whole session; ``startVoice`` / ``stopVoice`` toggle a single
// reuseable stream.

export type VoiceState = "idle" | "mic" | "loading" | "listening" | "error"

type StateListener = (s: VoiceState) => void

const SAMPLE_RATE = 16000
const SHERPA_BASE = new URL("./sherpa/", document.baseURI).href

let state: VoiceState = "idle"
const listeners = new Set<StateListener>()

let loadPromise: Promise<void> | null = null
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let recognizer: any = null
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let stream: any = null
let audioCtx: AudioContext | null = null
let micStream: MediaStream | null = null
let source: MediaStreamAudioSourceNode | null = null
let processor: ScriptProcessorNode | null = null
let analyser: AnalyserNode | null = null

/** Finalized segments joined so far this recording (``onResult``
 *  keeps showing ``transcript + live partial``). */
let transcript = ""
let onResultCb: ((text: string) => void) | null = null
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let moduleRef: any = null

/** Set by ``cancelPendingVoice`` while ``startVoice`` is still
 *  awaiting mic permission / model load; checked after each await
 *  so a stop that lands mid-load cleanly aborts instead of leaving
 *  a dead recognizer in "listening". */
let canceledFlag = false

/** Abort a ``startVoice`` that hasn't reached "listening" yet
 *  (e.g. the user pressed the global shortcut twice quickly, or
 *  closed the app while the 199 MB model was still loading). */
export function cancelPendingVoice(): void {
  canceledFlag = true
  cleanupMic()
  setState("idle")
}

export function getVoiceState(): VoiceState {
  return state
}

export function onVoiceState(cb: StateListener): () => void {
  listeners.add(cb)
  cb(state)
  return () => listeners.delete(cb)
}

// ── Live loudness (0..1, eased) ─────────────────────────────────
// A single requestAnimationFrame loop reads the mic through an
// AnalyserNode and fans the level out to UI listeners. The value
// is exponentially damped — it rises quickly on attack but decays
// slowly afterwards, so the meter jumps up with each syllable and
// falls off softly instead of snapping to silence. RMS is boosted
// so ordinary speech reads well above the idle floor.
type LevelListener = (level: number) => void
const levelListeners = new Set<LevelListener>()
let levelRaf = 0
let smoothedLevel = 0

export function onVoiceLevel(cb: LevelListener): () => void {
  levelListeners.add(cb)
  return () => levelListeners.delete(cb)
}

function startLevelLoop(): void {
  stopLevelLoop()
  const tick = () => {
    if (state !== "listening" || !analyser) return
    const buf = new Uint8Array(analyser.fftSize)
    analyser.getByteTimeDomainData(buf)
    let sum = 0
    for (let i = 0; i < buf.length; i++) sum += Math.abs(buf[i] - 128) / 128
    const rms = Math.min(1, (sum / buf.length) * 2.5)
    // Attack snaps up, decay lags — this is what gives the meter its
    // damped, elastic feel. Asymptote to zero so it fully rests.
    if (rms >= smoothedLevel) {
      smoothedLevel += (rms - smoothedLevel) * 0.55
    } else {
      smoothedLevel += (rms - smoothedLevel) * 0.09
    }
    if (smoothedLevel < 0.001) smoothedLevel = 0
    for (const l of levelListeners) l(smoothedLevel)
    levelRaf = requestAnimationFrame(tick)
  }
  levelRaf = requestAnimationFrame(tick)
}

function stopLevelLoop(): void {
  if (levelRaf) cancelAnimationFrame(levelRaf)
  levelRaf = 0
}


function setState(s: VoiceState): void {
  state = s
  for (const l of listeners) l(s)
}

/** Load the two sherpa scripts + WASM runtime once. Resolves after
 *  ``Module.onRuntimeInitialized`` (model data is in memory). */
function ensureRuntime(): Promise<void> {
  if (loadPromise) return loadPromise
  loadPromise = initRuntime()
  return loadPromise
}

function initRuntime(): Promise<void> {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const g = window as any
  moduleRef = g.Module ?? {}

  return new Promise((resolve, reject) => {
    let settled = false
    const fail = (msg: string) => {
      if (settled) return
      settled = true
      loadPromise = null
      reject(new Error(msg))
    }
    const ok = () => {
      if (settled) return
      settled = true
      resolve()
    }

    moduleRef.locateFile = (p: string) => SHERPA_BASE + p.split("/").pop()
    moduleRef.setStatus = () => {}
    moduleRef.onAbort = (msg: unknown) => fail(`sherpa-onnx failed to load: ${msg ?? "abort"}`)
    moduleRef.onRuntimeInitialized = () => {
      try {
        recognizer = g.createOnlineRecognizer(moduleRef)
        stream = recognizer.createStream()
        ok()
      } catch (e) {
        fail(`createOnlineRecognizer failed: ${String(e)}`)
      }
    }
    g.Module = moduleRef

    void loadScript(SHERPA_BASE + "sherpa-onnx-asr.js", fail)
      .then(() => loadScript(SHERPA_BASE + "sherpa-onnx-wasm-main-asr.js", fail))
      .catch(fail)
  })
}

function loadScript(src: string, fail: (msg: string) => void): Promise<void> {
  return new Promise((resolve) => {
    const el = document.createElement("script")
    el.src = src
    el.onload = () => resolve()
    el.onerror = () => fail(`failed to load ${src}`)
    document.head.appendChild(el)
  })
}

/** Append two transcript pieces, inserting a space only between
 *  non-CJK boundaries so Chinese flows naturally but English words
 *  don't fuse together when segments are joined. */
function joinSegments(a: string, b: string): string {
  if (!a || !b) return a || b
  const last = a.charCodeAt(a.length - 1)
  const first = b.charCodeAt(0)
  const isCjk = (c: number) =>
    (c >= 0x4e00 && c <= 0x9fff) || (c >= 0x3000 && c <= 0x30ff) || (c >= 0xff00 && c <= 0xffef)
  return isCjk(last) && isCjk(first) ? a + b : a + " " + b
}

/** Start recording. ``onResult`` fires with the running transcript
 *  (finalized segments + live partial) on every audio chunk.
 *  Model load and mic permission are resolved in parallel so the
 *  user isn't stuck staring at the loading state twice. */
export async function startVoice(onResult: (text: string) => void): Promise<void> {
  if (state === "listening" || state === "loading" || state === "mic") return
  // Two visible stages: mic permission first, then the model load.
  setState("mic")
  try {
    micStream = await navigator.mediaDevices.getUserMedia({ audio: true })
    if (canceledFlag) {
      canceledFlag = false
      cleanupMic()
      setState("idle")
      return
    }
    setState("loading")
    await ensureRuntime()
    if (canceledFlag) {
      canceledFlag = false
      cleanupMic()
      setState("idle")
      return
    }

    if (!audioCtx) {
      audioCtx = new AudioContext({ sampleRate: SAMPLE_RATE })
    }
    source = audioCtx.createMediaStreamSource(micStream)
    analyser = audioCtx.createAnalyser()
    analyser.fftSize = 1024
    processor = audioCtx.createScriptProcessor(4096, 1, 1)
    processor.onaudioprocess = onAudio
    source.connect(analyser)
    source.connect(processor)
    processor.connect(audioCtx.destination)

    transcript = ""
    onResultCb = onResult
    setState("listening")
    startLevelLoop()
  } catch (e) {
    cleanupMic()
    setState("error")
    throw e
  }
}

/** Stop recording and return the finalized transcript. */
export function stopVoice(): string {
  cleanupMic()
  if (recognizer && stream) {
    const text = recognizer.getResult(stream).text
    if (text) transcript = joinSegments(transcript, text)
    recognizer.reset(stream)
  }
  const out = transcript
  transcript = ""
  onResultCb = null
  setState("idle")
  return out
}

function cleanupMic(): void {
  processor?.disconnect()
  source?.disconnect()
  analyser?.disconnect()
  processor = null
  source = null
  analyser = null
  micStream?.getTracks().forEach((t) => t.stop())
  micStream = null
  smoothedLevel = 0
  stopLevelLoop()
}

function onAudio(e: AudioProcessingEvent): void {
  if (!recognizer || !stream || !onResultCb) return
  const samples = e.inputBuffer.getChannelData(0)

  // Loudness is read live via the AnalyserNode in ``getVoiceLevel``
  // (once per animation frame), so no level bookkeeping here.
  stream.acceptWaveform(SAMPLE_RATE, samples)
  while (recognizer.isReady(stream)) recognizer.decode(stream)

  const text = recognizer.getResult(stream).text
  const isEndpoint = recognizer.isEndpoint(stream)

  onResultCb(joinSegments(transcript, text))
  if (isEndpoint && text) {
    transcript = joinSegments(transcript, text)
    recognizer.reset(stream)
  }
}