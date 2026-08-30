<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue"
import { useThemeStore } from "../stores/theme"
import { useAppearanceStore } from "../stores/appearance"
import { useOnboardingStore } from "../stores/onboarding"
import { useAppMetaStore } from "../stores/appMeta"
import { usePrefsStore } from "../stores/prefs"
import { useUpdateStore } from "../stores/update"
import { getVoiceShortcut, setVoiceShortcut } from "../lib/globalVoice"
import { locale, setLocale, t, type Locale } from "../i18n"

const theme = useThemeStore()
const appearance = useAppearanceStore()
const onboarding = useOnboardingStore()
const appMeta = useAppMetaStore()
const prefs = usePrefsStore()
const updateStore = useUpdateStore()
const isDark = computed(() => theme.theme === "dark")
const isZh = computed(() => locale.value === "zh")
const updaterAvailable = computed(() => Boolean(window.mhc?.update))

// Global voice input shortcut: fixed presets only. Users can't
// record free-form keys — we can't enumerate the hotkeys their IME
// or other software has claimed, so arbitrary bindings would just
// collide silently.
const SHORTCUT_PRESETS = [
  "Alt+Shift+W",
  "Ctrl+Alt+W",
  "Ctrl+Alt+D",
  "Ctrl+Alt+V",
  "Alt+Shift+D",
]
const voiceShortcut = ref(getVoiceShortcut())
function pickVoiceShortcut(acc: string) {
  if (voiceShortcut.value === acc) return
  voiceShortcut.value = acc
  setVoiceShortcut(acc)
}

const stateLabel = computed(() => {
  const k = `settings.update${updateStore.status.state.charAt(0).toUpperCase()}${updateStore.status.state.slice(1)}`
  // i18n.t() returns the key itself if missing — fall back to raw state.
  const label = t(k as any)
  return label === k ? updateStore.status.state : label
})

const showInstall = computed(() => updateStore.status.state === "update_available")
const showApply = computed(() => updateStore.status.state === "staged")

// Local edit buffer so typing doesn't snap to defaults until the user
// blurs / presses Enter; the store stays the source of truth on save.
const titleDraft = ref(appMeta.title)
watch(titleDraft, (v) => appMeta.setTitle(v))

// Same pattern for the system-prompt addition: keep an editable draft,
// flush to the backend on Save (explicit) so we don't hit the API on
// every keystroke. The server strips whitespace; the draft is what
// the user is currently typing, so we keep leading/trailing spaces
// in the textarea but trim before sending.
const promptDraft = ref<string>("")
const promptSaving = ref(false)
const promptError = ref<string | null>(null)
const promptSavedAt = ref<string>("")

onMounted(async () => {
  await prefs.load()
  promptDraft.value = prefs.systemPromptAddition
  await updateStore.refresh()
  // Subscribe is invoked many times during Settings navigation; keep
  // the unsubscribe handle so we don't leak listeners across mounts.
  const off = updateStore.subscribe()
  onUnmounted(off)
})

async function savePromptAddition() {
  promptSaving.value = true
  promptError.value = null
  try {
    const next = await prefs.save(promptDraft.value)
    promptDraft.value = next.system_prompt_addition
    promptSavedAt.value = next.updated_at
  } catch (e) {
    promptError.value = e instanceof Error ? e.message : String(e)
  } finally {
    promptSaving.value = false
  }
}

function pickLocale(l: Locale) {
  if (locale.value !== l) setLocale(l)
}

// Reset the dismissal flag so the overlay re-opens on next show.
// The store handles loading the card list if it isn't already cached.
async function replayTour() {
  onboarding.reset()
  onboarding.index = 0
  await onboarding.load()
  onboarding.open()
}
</script>

<template>
  <div class="settings">
    <div class="body">
      <header class="head">
        <h2>{{ t("settings.title") }}</h2>
        <p class="sub">{{ t("settings.subtitle") }}</p>
      </header>

      <section class="group">
        <h3>{{ t("settings.appearance") }}</h3>

        <div class="row">
          <div class="row-text">
            <div class="row-title">{{ t("settings.theme") }}</div>
            <div class="row-desc">{{ t("settings.themeDesc") }}</div>
          </div>
          <div class="seg" role="radiogroup" :aria-label="t('settings.theme')">
            <button
              type="button"
              role="radio"
              :aria-checked="!isDark"
              :class="['seg-opt', { active: !isDark }]"
              @click="theme.theme !== 'light' && theme.toggle()"
            >
              {{ t("settings.themeLight") }}
            </button>
            <button
              type="button"
              role="radio"
              :aria-checked="isDark"
              :class="['seg-opt', { active: isDark }]"
              @click="theme.theme !== 'dark' && theme.toggle()"
            >
              {{ t("settings.themeDark") }}
            </button>
          </div>
        </div>

        <div class="row">
          <div class="row-text">
            <div class="row-title">{{ t("settings.language") }}</div>
            <div class="row-desc">{{ t("settings.languageDesc") }}</div>
          </div>
          <div
            class="seg"
            role="radiogroup"
            :aria-label="t('settings.language')"
          >
            <button
              type="button"
              role="radio"
              :aria-checked="!isZh"
              :class="['seg-opt', { active: !isZh }]"
              @click="pickLocale('en')"
            >
              {{ t("settings.languageEn") }}
            </button>
            <button
              type="button"
              role="radio"
              :aria-checked="isZh"
              :class="['seg-opt', { active: isZh }]"
              @click="pickLocale('zh')"
            >
              {{ t("settings.languageZh") }}
            </button>
          </div>
        </div>

        <div class="row">
          <div class="row-text">
            <div class="row-title">{{ t("settings.appTitle") }}</div>
            <div class="row-desc">{{ t("settings.appTitleDesc") }}</div>
          </div>
          <input
            class="title-input"
            type="text"
            maxlength="64"
            :value="titleDraft"
            :placeholder="appMeta.title"
            :aria-label="t('settings.appTitle')"
            @input="titleDraft = ($event.target as HTMLInputElement).value"
          />
        </div>

        <div class="row">
          <div class="row-text">
            <div class="row-title">{{ t("settings.fontSize") }}</div>
            <div class="row-desc">{{ t("settings.fontSizeDesc") }}</div>
          </div>
          <div class="font-size-control">
            <input
              class="font-size-range"
              type="range"
              :min="appearance.min"
              :max="appearance.max"
              step="1"
              :value="appearance.fontSize"
              :aria-label="t('settings.fontSize')"
              @input="
                appearance.setFontSize(
                  Number(($event.target as HTMLInputElement).value),
                )
              "
            />
            <span class="font-size-value">{{ appearance.fontSize }}px</span>
          </div>
        </div>
      </section>

      <section class="group">
        <h3>{{ t("settings.voiceInput") }}</h3>
        <div class="row row-stack">
          <div class="row-text">
            <div class="row-title">{{ t("settings.voiceShortcut") }}</div>
            <div class="row-desc">{{ t("settings.voiceShortcutDesc") }}</div>
          </div>
          <div class="seg seg-wrap" role="radiogroup" :aria-label="t('settings.voiceShortcut')">
            <button
              v-for="acc in SHORTCUT_PRESETS"
              :key="acc"
              type="button"
              role="radio"
              :aria-checked="voiceShortcut === acc"
              :class="['seg-opt', { active: voiceShortcut === acc }]"
              @click="pickVoiceShortcut(acc)"
            >
              {{ acc }}
            </button>
          </div>
        </div>
      </section>

      <section class="group">
        <h3>{{ t("settings.aiBehavior") }}</h3>
        <div class="row row-stack">
          <div class="row-text">
            <div class="row-title">{{ t("settings.systemPromptAddition") }}</div>
            <div class="row-desc">
              {{ t("settings.systemPromptAdditionDesc") }}
            </div>
          </div>
          <textarea
            class="prompt-input"
            rows="6"
            :value="promptDraft"
            :placeholder="t('settings.systemPromptAdditionPlaceholder')"
            :aria-label="t('settings.systemPromptAddition')"
            :disabled="promptSaving"
            @input="
              promptDraft = ($event.target as HTMLTextAreaElement).value
            "
          />
          <div class="prompt-actions">
            <button
              class="seg-opt"
              type="button"
              :disabled="
                promptSaving
                || promptDraft.trim() === prefs.systemPromptAddition
              "
              @click="savePromptAddition"
            >
              {{ promptSaving ? t("settings.saving") : t("settings.save") }}
            </button>
            <span v-if="promptError" class="prompt-error">{{ promptError }}</span>
            <span v-else-if="promptSavedAt" class="prompt-saved">
              {{ t("settings.savedAt", { time: promptSavedAt }) }}
            </span>
          </div>
        </div>
      </section>

      <section v-if="updaterAvailable" class="group">
        <h3>{{ t("settings.updates") }}</h3>
        <div class="row">
          <div class="row-text">
            <div class="row-title">{{ t("settings.updateStatus") }}</div>
            <div class="row-desc">
              {{ stateLabel }}
              <template v-if="updateStore.status.available">
                · {{ Object.entries(updateStore.status.available).map(([k, v]) => `${k}=${v}`).join(", ") }}
              </template>
              <template v-if="updateStore.status.error">
                · <span class="update-error">{{ updateStore.status.error }}</span>
              </template>
              <template v-if="updateStore.status.state === 'downloading' && updateStore.status.progressBytes && updateStore.status.progressTotal">
                · {{ Math.round((updateStore.status.progressBytes / updateStore.status.progressTotal) * 100) }}%
              </template>
            </div>
          </div>
          <div class="update-actions">
            <button
              type="button"
              class="seg-opt"
              :disabled="updateStore.busy"
              @click="updateStore.checkNow()"
            >
              {{ t("settings.updateCheck") }}
            </button>
            <button
              v-if="showInstall"
              type="button"
              class="seg-opt seg-opt-primary"
              :disabled="updateStore.busy"
              @click="updateStore.install()"
            >
              {{ t("settings.updateInstall") }}
            </button>
            <button
              v-if="showApply"
              type="button"
              class="seg-opt seg-opt-primary"
              :disabled="updateStore.busy"
              @click="updateStore.applyNow()"
            >
              {{ t("settings.updateApply") }}
            </button>
          </div>
        </div>
      </section>

      <section class="group">
        <h3>{{ t("settings.about") }}</h3>
        <div class="row">
          <div class="row-text">
            <div class="row-title">{{ t("common.brand") }}</div>
            <div class="row-desc">{{ t("settings.aboutDesc") }}</div>
          </div>
        </div>
        <div class="row">
          <div class="row-text">
            <div class="row-title">{{ t("settings.tour") }}</div>
            <div class="row-desc">{{ t("settings.tourDesc") }}</div>
          </div>
          <button class="seg-opt" type="button" @click="replayTour">
            {{ t("onboarding.start") }}
          </button>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.settings {
  height: 100%;
  overflow-y: auto;
  background: var(--bg);
  color: var(--text);
}
.body {
  max-width: clamp(720px, 78vw, 960px);
  margin: 0 auto;
  padding: 32px 24px;
}
.head h2 {
  margin: 0;
  font-size: 22px;
  font-weight: 600;
  color: var(--text);
}
.head .sub {
  margin: 6px 0 28px;
  color: var(--text-mid);
  font-size: 13px;
}
.group {
  border-top: 1px solid var(--border-faint);
  padding-top: 24px;
  margin-top: 24px;
}
.group h3 {
  margin: 0 0 14px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--text-faint);
}
.row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  padding: 16px 0;
  border-bottom: 1px solid var(--border-faint);
}
.row:last-of-type {
  border-bottom: 0;
}
.row-text {
  min-width: 0;
}
.row-title {
  font-size: var(--app-font-size, 14px);
  font-weight: 500;
  color: var(--text);
}
.row-desc {
  margin-top: 4px;
  font-size: var(--app-font-size, 14px);
  color: var(--text-mid);
  line-height: 1.5;
}
.seg {
  flex-shrink: 0;
  display: inline-flex;
  background: var(--bg-subtle);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 2px;
}
.seg-wrap {
  flex-wrap: wrap;
  gap: 4px;
}
.seg-opt {
  border: 0;
  background: transparent;
  color: var(--text-mid);
  font-size: 12.5px;
  padding: 5px 14px;
  border-radius: 6px;
  cursor: pointer;
  transition: background 120ms ease, color 120ms ease;
  font-family: inherit;
}
.seg-opt:hover {
  color: var(--text);
}
.seg-opt.active {
  background: var(--bg);
  color: var(--text);
  box-shadow: var(--shadow-toggle);
}
.about {
  padding: 12px 0;
}
.title-input {
  font: inherit;
  font-size: 13px;
  color: var(--text);
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 6px 10px;
  width: 220px;
  outline: none;
  transition: border-color 120ms ease, box-shadow 120ms ease;
}
.title-input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}
.row.row-stack {
  flex-direction: column;
  align-items: stretch;
  gap: 12px;
}
.prompt-input {
  font: inherit;
  font-size: var(--app-font-size, 14px);
  color: var(--text);
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 10px 12px;
  width: 100%;
  outline: none;
  resize: vertical;
  min-height: 120px;
  font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace;
  transition: border-color 120ms ease, box-shadow 120ms ease;
}
.prompt-input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}
.prompt-input:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.prompt-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 12px;
  color: var(--text-mid);
}
.prompt-error {
  color: #d44;
}
.prompt-saved {
  color: var(--text-faint);
}

.update-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}
.update-error {
  color: #d44;
}
.seg-opt-primary {
  border-color: var(--accent);
  color: var(--accent);
}
.seg-opt-primary:hover:not(:disabled) {
  background: var(--accent-soft);
}

.font-size-control {
  display: inline-flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}
.font-size-range {
  width: 180px;
  cursor: pointer;
  accent-color: var(--accent);
}
.font-size-value {
  font-variant-numeric: tabular-nums;
  font-size: 12.5px;
  color: var(--text-mid);
  min-width: 36px;
  text-align: right;
}
</style>
