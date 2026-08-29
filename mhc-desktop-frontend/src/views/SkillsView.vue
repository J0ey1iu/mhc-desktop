<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue"
import {
  api,
  type Skill,
  type SkillDetail,
} from "../api/client"
import { useSkillsStore } from "../stores/skills"
import { useAuthStore } from "../stores/auth"
import Icon from "../components/Icon.vue"
import MarketIcon from "../components/MarketIcon.vue"
import { ask } from "../lib/confirm"
import { showToast, friendlyError } from "../lib/toast"
import { t } from "../i18n"

const props = defineProps<{ filterQuery?: string }>()

const store = useSkillsStore()
const auth = useAuthStore()
const syncTarget = computed(() => auth.user?.username ?? "")

// Local skill filter, driven by the search bar in the market tab bar.
function displayName(s: { name: string }): string {
  return s.name
}

// 市场条目作者（来自同步清单），「我的技能」卡片上显示。
function authorOf(slug: string): string {
  return skillAuthors.value[slug] ?? ""
}

const filteredSkills = computed(() => {
  const q = (props.filterQuery ?? "").trim().toLowerCase()
  if (!q) return store.items
  return store.items.filter(
    (s) =>
      s.name.toLowerCase().includes(q) ||
      s.slug.toLowerCase().includes(q) ||
      (s.description || "").toLowerCase().includes(q),
  )
})

const importing = ref(false)
type StatusLevel = "info" | "success" | "error"
const status = ref<{ level: StatusLevel; message: string } | null>(null)
const toggling = ref<string | null>(null)

// 同步状态/动作来自共享单例（lib/marketSync）——轮询在 App.vue 应用级
// 启动，不随本 tab 卸载而停止。
import {
  reminder,
  conflictSlugs,
  syncing,
  resolving,
  reminderIssues,
  refreshSync,
  execute as executeSync,
  resolveConflict as resolveConflictApi,
  skillAuthors,
  skillMarketKeys,
  delistedSlugs,
} from "../lib/marketSync"

function setStatus(level: StatusLevel, message: string) {
  status.value = { level, message }
}
function clearStatus() {
  status.value = null
}

const selected = ref<SkillDetail | null>(null)
const editing = ref(false)
const editDescription = ref("")
const editBody = ref("")
const saving = ref(false)
const publishing = ref(false)
const publishCategory = ref<string>("other")
const publishSource = ref<"local" | "repost">("local")
const publishSourceRef = ref<string>("")
const delisting = ref(false)

const CATEGORIES = ["efficiency", "writing", "coding", "office", "other"] as const

// 只有自己发布的技能才能下架：市场条目作者 == 当前用户，且条目未下架。
function canDelist(slug: string): boolean {
  if (delistedSlugs.value.has(slug)) return false
  const author = skillAuthors.value[slug]
  const key = skillMarketKeys.value[slug]
  return !!key && !!author && author === (auth.user?.username ?? "")
}

async function publishSelected() {
  if (!selected.value) return
  clearStatus()
  try {
    const m = await api.publishSkill(
      selected.value.slug,
      publishCategory.value,
      publishSource.value,
      publishSourceRef.value,
    )
    await refreshSync()  // 发布后立即刷新作者/市场 key，让下架按钮出现
    showToast(t("skills.published", { name: m.display_name }), "success")
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e)
    showToast(t("skills.publishFailed", { detail: friendlyError(msg) }), "error")
  }
}

async function delistSelected() {
  if (!selected.value) return
  const key = skillMarketKeys.value[selected.value.slug]
  if (!key) return
  const ok = await ask({
    title: t("skills.delistTitle"),
    message: t("skills.delistConfirm", { name: selected.value.name }),
    tone: "danger",
    confirmLabel: t("skills.delist"),
  })
  if (!ok) return
  delisting.value = true
  clearStatus()
  try {
    await api.delistMarketSkill(key)
    await Promise.all([store.refresh(), refreshSync()])
    const detail = await api.getSkill(selected.value.slug)
    selected.value = detail
    showToast(t("skills.delisted", { name: detail.name }), "success")
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e)
    showToast(t("skills.delistFailed", { detail: friendlyError(msg) }), "error")
  } finally {
    delisting.value = false
  }
}

async function runSync() {
  clearStatus()
  const res = await executeSync()
  if (res) {
    conflictSlugs.value = res.conflicts
    setStatus(
      res.conflicts.length ? "info" : "success",
      t("skills.synced", { pushed: res.pushed.length, pulled: res.pulled.length }),
    )
  }
}

async function resolveConflict(slug: string, choice: "local" | "remote") {
  await resolveConflictApi(slug, choice)
  setStatus("success", t("skills.conflictResolved", { name: slug }))
}

onMounted(() => {
  store.refresh()
  refreshSync()
  window.addEventListener("keydown", onKeydown)
})

onUnmounted(() => {
  window.removeEventListener("keydown", onKeydown)
})

// ESC 关闭详情侧栏（编辑中先取消编辑）
function onKeydown(e: KeyboardEvent) {
  if (e.key === "Escape" && selected.value) {
    dismissDetail()
  }
}

async function pickFolder() {
  clearStatus()
  if (!window.mhc?.pickFolder) {
    setStatus("error", t("skills.noPicker"))
    return
  }
  try {
    importing.value = true
    const path = await window.mhc.pickFolder()
    if (!path) return
    const created = await store.importFolder(path)
    const detail = await api.getSkill(created.slug)
    select(detail)
  } catch (e) {
    setStatus("error", e instanceof Error ? e.message : String(e))
  } finally {
    importing.value = false
  }
}

async function pickZip() {
  clearStatus()
  if (!window.mhc?.pickFile) {
    setStatus("error", t("skills.noPicker"))
    return
  }
  try {
    importing.value = true
    const file = await window.mhc.pickFile({
      filters: [{ name: "Skill bundle", extensions: ["zip"] }],
    })
    if (!file) return
    // File path on Windows looks like C:\Users\...\foo.zip — we just need
    // the filename as the slug hint and the bytes themselves.
    const buf = await fetch(`file://${file.path ?? ""}`).catch(() => null)
    let blob: Blob
    if (buf && buf.ok) {
      blob = await buf.blob()
    } else {
      // Fall back: read via electron preload if the browser can't fetch
      // file://. We treat the picker result as having an inline Buffer.
      const raw = await window.mhc.readFile?.(file.path ?? "")
      if (!raw) throw new Error("could not read picked file")
      blob = new Blob([new Uint8Array(raw)])
    }
    const name = file.name || "skill.zip"
    const created = await store.importZip(name, blob)
    const detail = await api.getSkill(created.slug)
    select(detail)
  } catch (e) {
    setStatus("error", e instanceof Error ? e.message : String(e))
  } finally {
    importing.value = false
  }
}

// Bulk import: pick a folder containing many SKILL.md subfolders
// (or a zip of the same shape) and install every one. The backend
// copies each into ~/.mhc-desktop/skills/<slug>/ — we never edit
// the user's source files.
async function pickBulkFolder() {
  clearStatus()
  if (!window.mhc?.pickFolder) {
    setStatus("error", t("skills.noPicker"))
    return
  }
  try {
    importing.value = true
    const path = await window.mhc.pickFolder()
    if (!path) return
    const summary = await api.importBulkSkillFolder(path)
    setStatus(bulkLevel(summary), formatBulkSummary(summary) ?? '')
    await store.refresh()
  } catch (e) {
    setStatus("error", e instanceof Error ? e.message : String(e))
  } finally {
    importing.value = false
  }
}

async function pickBulkZip() {
  clearStatus()
  if (!window.mhc?.pickFile) {
    setStatus("error", t("skills.noPicker"))
    return
  }
  try {
    importing.value = true
    const file = await window.mhc.pickFile({
      filters: [{ name: "Skill pack", extensions: ["zip"] }],
    })
    if (!file) return
    const buf = await fetch(`file://${file.path ?? ""}`).catch(() => null)
    let blob: Blob
    if (buf && buf.ok) {
      blob = await buf.blob()
    } else {
      const raw = await window.mhc.readFile?.(file.path ?? "")
      if (!raw) throw new Error("could not read picked file")
      blob = new Blob([new Uint8Array(raw)])
    }
    const summary = await api.importBulkSkillZip(blob)
    setStatus(bulkLevel(summary), formatBulkSummary(summary) ?? '')
    await store.refresh()
  } catch (e) {
    setStatus("error", e instanceof Error ? e.message : String(e))
  } finally {
    importing.value = false
  }
}

function formatBulkSummary(s: {
  installed: unknown[]
  skipped: { path: string; reason: string }[]
  errors: { path: string; error: string }[]
}): string | null {
  const parts: string[] = []
  if (s.installed.length)
    parts.push(`${t("skills.bulkInstalled")}: ${s.installed.length}`)
  if (s.skipped.length)
    parts.push(`${t("skills.bulkSkipped")}: ${s.skipped.length}`)
  if (s.errors.length)
    parts.push(`${t("skills.bulkErrors")}: ${s.errors.length}`)
  return parts.length ? parts.join(" · ") : null
}

// Tone of the bulk-import banner. Errors dominate — even one bad
// file flips the whole result to red — but a clean install is
// green and a fully-skipped re-import is neutral info.
function bulkLevel(s: {
  installed: unknown[]
  skipped: { path: string; reason: string }[]
  errors: { path: string; error: string }[]
}): StatusLevel {
  if (s.errors.length) return "error"
  if (s.installed.length) return "success"
  return "info"
}

async function select(s: SkillDetail) {
  selected.value = s
  editing.value = false
  editDescription.value = s.description
  editBody.value = s.body
}

async function selectBySlug(slug: string) {
  try {
    const detail = await api.getSkill(slug)
    select(detail)
  } catch (e) {
    setStatus("error", e instanceof Error ? e.message : String(e))
  }
}

function clearSelection() {
  selected.value = null
  editing.value = false
  clearStatus()
}

// 遮罩 / ESC 关闭侧栏：编辑中先取消编辑（保留面板），避免误丢未保存内容。
function dismissDetail() {
  if (editing.value) {
    cancelEdit()
    setStatus("info", t("skills.editCancelled"))
  } else {
    clearSelection()
  }
}

async function toggleEnabled(s: Skill) {
  if (toggling.value) return
  toggling.value = s.slug
  try {
    await store.setEnabled(s.slug, !s.enabled)
    if (selected.value?.slug === s.slug) {
      const refreshed = await api.getSkill(s.slug)
      selected.value = refreshed
    }
  } catch (e) {
    setStatus("error", e instanceof Error ? e.message : String(e))
  } finally {
    toggling.value = null
  }
}

async function deleteSkill(s: Skill) {
  const isMarket = s.origin === "market"
  const ok = await ask({
    title: t("skills.confirmDeleteTitle"),
    message: isMarket
      ? t("skills.confirmDeleteCloud", { name: s.name })
      : t("skills.confirmDelete", { name: s.name }),
    tone: "danger",
    confirmLabel: t("common.delete"),
  })
  if (!ok) return
  try {
    await store.remove(s.slug)
    // Mirror semantics: a local removal also drops the user's cloud
    // copy, otherwise the next sync would push it right back.
    if (isMarket) {
      try {
        await api.deleteMarketCopy(s.slug)
      } catch {
        /* 404 / offline — local removal still stands */
      }
    }
    if (selected.value?.slug === s.slug) clearSelection()
    setStatus("success", t("skills.deleted", { name: s.name }))
    await refreshSync()
  } catch (e) {
    setStatus("error", e instanceof Error ? e.message : String(e))
  }
}

function exportSkill(s: Skill) {
  // Direct anchor download — backend sets Content-Disposition.
  const a = document.createElement("a")
  a.href = api.exportSkillUrl(s.slug)
  a.download = `${s.slug}.skill.zip`
  a.click()
}

function startEdit() {
  if (!selected.value) return
  editDescription.value = selected.value.description
  editBody.value = selected.value.body
  editing.value = true
}

function cancelEdit() {
  editing.value = false
  if (selected.value) {
    editDescription.value = selected.value.description
    editBody.value = selected.value.body
  }
}

async function saveEdit() {
  if (!selected.value) return
  saving.value = true
  try {
    await api.updateSkill(selected.value.slug, {
      description: editDescription.value,
      body: editBody.value,
    })
    await store.refresh()
    const detail = await api.getSkill(selected.value.slug)
    selected.value = detail
    editing.value = false
  } catch (e) {
    setStatus("error", e instanceof Error ? e.message : String(e))
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="mine-pane">
    <header class="head">
      <div class="actions">
        <button class="btn-secondary" :disabled="syncing" @click="runSync">
          <Icon name="refresh" />
          {{ t("skills.sync") }}
        </button>
        <button class="btn-secondary" :disabled="importing" @click="pickFolder">
          <Icon name="folder" />
          {{ t("skills.importFolder") }}
        </button>
        <button class="btn-secondary" :disabled="importing" @click="pickZip">
          <Icon name="upload" />
          {{ t("skills.importZip") }}
        </button>
        <button class="btn-secondary" :disabled="importing" @click="pickBulkFolder">
          <Icon name="package" />
          {{ t("skills.importBulkFolder") }}
        </button>
        <button class="btn-secondary" :disabled="importing" @click="pickBulkZip">
          <Icon name="package" />
          {{ t("skills.importBulkZip") }}
        </button>
      </div>
    </header>

    <p v-if="conflictSlugs.length" class="status-banner status-error">
      {{ t("skills.syncConflicts", { n: conflictSlugs.length }) }}
      <span v-for="slug in conflictSlugs" :key="slug" class="conflict-row">
        <code>{{ slug }}</code>
        <button
          class="btn-secondary"
          :disabled="resolving === slug"
          @click="resolveConflict(slug, 'local')"
        >
          {{ t("skills.conflictUseLocal") }}
        </button>
        <button
          class="btn-secondary"
          :disabled="resolving === slug"
          @click="resolveConflict(slug, 'remote')"
        >
          {{ t("skills.conflictUseCloud") }}
        </button>
      </span>
    </p>

    <div v-if="reminder" class="sync-reminder">
      <span>{{ t("market.syncReminder", { n: reminderIssues }) }}<template v-if="syncTarget"> · {{ t("market.syncTarget", { user: syncTarget }) }}</template></span>
      <button class="btn-primary sm" @click="runSync">
        {{ t("market.syncRun") }}
      </button>
      <button class="btn-secondary sm" @click="reminder = null">
        {{ t("common.cancel") }}
      </button>
    </div>

    <p v-if="status" :class="['status-banner', `status-${status.level}`]">
      {{ status.message }}
    </p>
    <p
      v-if="store.loading && store.items.length === 0"
      class="loading"
    >
      {{ t("common.loading") }}
    </p>

    <div class="split">
      <ul v-if="filteredSkills.length > 0" class="list grid">
        <li
          v-for="s in filteredSkills"
          :key="s.slug"
          class="card skill-card"
          :class="{
            off: !s.enabled,
            selected: selected?.slug === s.slug,
          }"
          @click="selectBySlug(s.slug)"
        >
          <div class="sc-top">
            <MarketIcon :icon="s.icon" :name="displayName(s)" :size="48" />
            <div class="sc-badges">
              <span v-if="authorOf(s.slug)" class="origin-badge author-badge">{{ authorOf(s.slug) }}</span>
              <span class="origin-badge">{{ t(`skills.${s.origin}`) }}</span>
            </div>
          </div>
          <div class="title">
            {{ displayName(s) }}
            <span v-if="authorOf(s.slug)" class="title-author">{{ t("skills.marketBy", { author: authorOf(s.slug) }) }}</span>
          </div>
          <div class="desc">{{ s.description || t("skills.noDescription") }}</div>
          <div class="meta">
            <span class="slug">/{{ s.slug }}</span>
            <span v-if="s.files.length > 0" class="files">+ {{ s.files.length }} files</span>
          </div>
          <div class="sc-foot">
            <label
              class="switch"
              :title="s.enabled ? t('skills.disable') : t('skills.enable')"
              @click.stop
            >
              <input
                type="checkbox"
                :checked="s.enabled"
                :disabled="toggling === s.slug"
                @change="toggleEnabled(s)"
              />
              <span class="slider" />
            </label>
            <div class="sc-tools">
              <span class="sc-hint">{{ s.enabled ? t("skills.enable") : t("skills.disable") }}</span>
              <button
                v-if="s.origin !== 'bundled'"
                class="sc-remove"
                :title="t('skills.remove')"
                @click.stop="deleteSkill(s)"
              >
                <Icon name="trash" />
              </button>
            </div>
          </div>
        </li>
      </ul>
      <p v-else-if="!store.loading" class="muted" v-html="t('skills.empty')" />
    </div>

    <!-- Detail pane (slides over on the right when a skill is selected) -->
    <Transition name="fade">
      <div v-if="selected" class="detail-mask" @click="dismissDetail" />
    </Transition>
    <Transition name="pane">
      <aside v-if="selected" class="detail" @click.stop>
        <header class="detail-head">
          <div class="grow">
            <h3>
              {{ selected.name }}
              <span class="origin-badge small">
                {{ t(`skills.${selected.origin}`) }}
              </span>
            </h3>
            <div class="detail-sub">
              /{{ selected.slug }} · {{ selected.files.length }} {{ t("skills.files") }}
              <span v-if="authorOf(selected.slug)" class="detail-author">
                · {{ t("skills.marketBy", { author: authorOf(selected.slug) }) }}
              </span>
            </div>
          </div>
          <button class="close" :title="t('common.cancel')" @click="clearSelection">
            <Icon name="x" />
          </button>
        </header>

        <div class="detail-actions">
          <button class="btn-secondary" @click="exportSkill(selected)">
            <Icon name="download" />
            {{ t("skills.export") }}
          </button>
          <button class="btn-secondary" @click="startEdit" :disabled="editing">
            <Icon name="edit" />
            {{ t("skills.edit") }}
          </button>
          <button class="btn-danger" @click="deleteSkill(selected)">
            <Icon name="trash" />
            {{ t("common.delete") }}
          </button>
        </div>
        <div
          v-if="selected.origin !== 'bundled'"
          class="detail-actions publish-row"
        >
          <select v-model="publishCategory" class="market-cat">
            <option v-for="c in CATEGORIES" :key="c" :value="c">{{ c }}</option>
          </select>
          <select v-model="publishSource" class="market-cat" :title="t('skills.publishSource')">
            <option value="local">{{ t("skills.sourceLocal") }}</option>
            <option value="repost">{{ t("skills.sourceRepost") }}</option>
          </select>
          <input
            v-if="publishSource === 'repost'"
            v-model="publishSourceRef"
            class="market-cat source-ref"
            :placeholder="t('skills.sourceRef')"
          />
          <button class="btn-secondary" :disabled="publishing" @click="publishSelected">
            <Icon name="upload" />
            {{ t("skills.publish") }}
          </button>
          <button
            v-if="canDelist(selected.slug)"
            class="btn-danger"
            :disabled="delisting"
            @click="delistSelected"
          >
            {{ t("skills.delist") }}
          </button>
        </div>

        <section class="detail-body">
          <template v-if="!editing">
            <div class="detail-desc">{{ selected.description || t("skills.noDescription") }}</div>
            <h4>SKILL.md</h4>
            <pre class="md">{{ selected.body || t("skills.emptyBody") }}</pre>
            <template v-if="selected.files.length > 0">
              <h4>{{ t("skills.files") }}</h4>
              <ul class="filelist">
                <li v-for="f in selected.files" :key="f">
                  <Icon name="file" />
                  <span>{{ f }}</span>
                </li>
              </ul>
            </template>
          </template>

          <template v-else>
            <label class="field">
              <span>{{ t("skills.editDescription") }}</span>
              <textarea
                v-model="editDescription"
                rows="2"
                class="ta"
              />
            </label>
            <label class="field">
              <span>{{ t("skills.editBody") }}</span>
              <textarea
                v-model="editBody"
                rows="20"
                class="ta mono"
              />
            </label>
            <div class="edit-actions">
              <button class="btn-secondary" :disabled="saving" @click="cancelEdit">
                {{ t("common.cancel") }}
              </button>
              <button class="btn-primary" :disabled="saving" @click="saveEdit">
                {{ saving ? t("common.saving") : t("common.save") }}
              </button>
            </div>
          </template>
        </section>
      </aside>
    </Transition>
  </div>
</template>

<style scoped>
.mine-pane {
  display: flex;
  flex-direction: column;
  color: var(--text);
}
.head {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 12px;
  margin-bottom: 16px;
}
.head h2 {
  margin: 0 0 4px;
  font-size: 22px;
  letter-spacing: -0.01em;
}
.hint {
  margin: 0;
  color: var(--text-mid);
  font-size: 13px;
  max-width: 540px;
  line-height: 1.5;
}
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  flex-shrink: 0;
}
.split {
  flex: 1;
  min-height: 0;
}
.status-banner {
  border-radius: 8px;
  padding: 8px 12px;
  font-size: 13px;
  border: 1px solid transparent;
}
.status-banner.status-error {
  color: var(--danger);
  background: var(--danger-bg);
  border-color: var(--danger-border);
}
.status-banner.status-success {
  color: var(--success, #15803d);
  background: var(--success-bg, rgba(34, 197, 94, 0.10));
  border-color: var(--success-border, rgba(34, 197, 94, 0.30));
}
.status-banner.status-info {
  color: var(--text-mid);
  background: var(--bg-subtle, var(--bg));
  border-color: var(--border);
}
.loading,
.muted {
  color: var(--text-mid);
  margin: 12px 0;
}
.list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: grid;
  gap: 8px;
}
.list.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 16px;
}
.card {
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 14px 16px;
  background: var(--bg);
  cursor: pointer;
  transition: border-color 120ms ease, background 120ms ease, opacity 120ms ease;
}
.card.skill-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 18px;
  transition: transform 120ms ease, box-shadow 120ms ease, border-color 120ms ease;
}
.card.skill-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 18px rgba(0,0,0,.08);
}
.sc-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.sc-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: auto;
  padding-top: 10px;
  border-top: 1px solid var(--border-faint);
}
.sc-hint {
  font-size: 12px;
  color: var(--text-mid);
}
.sc-tools {
  display: flex;
  align-items: center;
  gap: 8px;
}
.sc-remove {
  border: 0;
  background: transparent;
  color: var(--text-faint);
  cursor: pointer;
  padding: 4px;
  border-radius: 6px;
  display: grid;
  place-items: center;
  transition: color 120ms ease, background 120ms ease;
}
.sc-remove:hover {
  color: var(--danger);
  background: var(--danger-bg, rgba(220,38,38,.08));
}

.card:hover {
  border-color: var(--border-mid);
}
.card.selected {
  border-color: var(--accent);
  box-shadow: 0 0 0 2px var(--accent-soft);
}
.card.off {
  opacity: 0.55;
  background: var(--bg-panel);
}
.row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}
.grow {
  min-width: 0;
  flex: 1;
}
.title {
  font-weight: 600;
  font-size: var(--app-font-size, 14.5px);
  display: flex;
  align-items: center;
  gap: 8px;
}
.origin-badge {
  font-size: 10px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  background: var(--bg-hover);
  padding: 2px 6px;
  border-radius: 4px;
  font-weight: 600;
  color: var(--text-mid);
}
.origin-badge.small {
  font-size: 9px;
  padding: 1px 6px;
}
.desc {
  font-size: var(--app-font-size, 14px);
  color: var(--text-mid);
  margin-top: 4px;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.meta {
  margin-top: 6px;
  font-size: 11.5px;
  color: var(--text-faint);
  display: flex;
  gap: 12px;
}
.slug {
  font-family: ui-monospace, "JetBrains Mono", monospace;
}
.author-badge {
  background: var(--accent-soft, rgba(37, 99, 235, 0.12));
  color: var(--accent, #2563eb);
}
.sc-badges {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
  justify-content: flex-end;
}
.title-author {
  font-size: 11px;
  font-weight: 500;
  color: var(--text-faint);
  margin-left: 6px;
  white-space: nowrap;
}
.detail-author {
  color: var(--text-mid);
}

/* Toggle switch */
.switch {
  position: relative;
  display: inline-block;
  width: 32px;
  height: 18px;
  flex-shrink: 0;
  cursor: pointer;
  margin-top: 2px;
}
.switch input {
  opacity: 0;
  width: 0;
  height: 0;
  position: absolute;
}
.slider {
  position: absolute;
  inset: 0;
  background: var(--border);
  border-radius: 999px;
  transition: background 140ms ease;
}
.slider::before {
  content: "";
  position: absolute;
  width: 14px;
  height: 14px;
  left: 2px;
  top: 2px;
  background: var(--bg);
  border-radius: 50%;
  box-shadow: var(--shadow-toggle);
  transition: transform 140ms ease;
}
.switch input:checked + .slider {
  background: var(--accent);
}
.switch input:checked + .slider::before {
  transform: translateX(14px);
}
.switch input:disabled + .slider {
  opacity: 0.5;
}

/* Detail pane */
/* 详情侧栏背后的半透明遮罩：点击空白关闭 */
.detail-mask {
  position: fixed;
  inset: 0;
  top: 36px;
  background: rgba(0, 0, 0, 0.28);
  z-index: 19;
}
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.18s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
.detail {
  position: fixed;
  top: 36px;  /* below TitleBar */
  right: 0;
  bottom: 0;
  width: min(560px, 60vw);
  background: var(--bg);
  border-left: 1px solid var(--border);
  box-shadow: -8px 0 24px rgba(0, 0, 0, 0.08);
  z-index: 20;
  display: flex;
  flex-direction: column;
}
.detail-head {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 16px 20px;
  border-bottom: 1px solid var(--border-faint);
}
.detail-head h3 {
  margin: 0;
  font-size: 16px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.detail-sub {
  margin-top: 4px;
  font-size: 12px;
  color: var(--text-mid);
}
.close {
  background: transparent;
  border: 0;
  cursor: pointer;
  color: var(--text-mid);
  padding: 4px;
  border-radius: 6px;
  transition: background 120ms ease, color 120ms ease;
}
.close:hover {
  background: var(--bg-hover);
  color: var(--text);
}
.detail-actions {
  display: flex;
  gap: 6px;
  padding: 10px 20px;
  border-bottom: 1px solid var(--border-faint);
}
.detail-body {
  padding: 16px 20px 24px;
  overflow-y: auto;
  flex: 1;
}
.detail-body h4 {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--text-faint);
  font-weight: 600;
  margin: 18px 0 8px;
}
.detail-desc {
  font-size: 13px;
  color: var(--text-mid);
  line-height: 1.6;
}
.md {
  background: var(--bg-subtle);
  border: 1px solid var(--border-faint);
  border-radius: 8px;
  padding: 12px 14px;
  font-size: 12.5px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0;
  max-height: 50vh;
  overflow-y: auto;
}
.filelist {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 4px;
}
.filelist li {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 8px;
  font-size: 12px;
  color: var(--text-mid);
  background: var(--bg-subtle);
  border-radius: 4px;
  font-family: ui-monospace, "JetBrains Mono", monospace;
}

/* Edit form */
.field {
  display: block;
  margin-bottom: 14px;
}
.field > span {
  display: block;
  font-size: 12px;
  color: var(--text-mid);
  margin-bottom: 4px;
  font-weight: 500;
}
.ta {
  width: 100%;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 8px 10px;
  font: inherit;
  font-size: var(--app-font-size, 14px);
  resize: vertical;
  background: var(--bg);
  color: var(--text);
}
.ta:focus {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 2px var(--accent-soft);
}
.ta.mono {
  font-family: ui-monospace, "JetBrains Mono", monospace;
  font-size: 12.5px;
  line-height: 1.55;
}
.edit-actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
}

/* Buttons */
.btn-primary,
.btn-secondary,
.btn-danger {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font: inherit;
  font-size: 13px;
  padding: 6px 12px;
  border-radius: 6px;
  cursor: pointer;
  border: 1px solid transparent;
  transition: background 120ms ease, border-color 120ms ease, color 120ms ease;
}
.btn-primary {
  background: var(--accent);
  color: var(--accent-fg);
  border-color: var(--accent);
}
.btn-primary:hover:not(:disabled) {
  background: var(--accent-hover);
}
.btn-primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.btn-secondary {
  background: var(--bg);
  color: var(--text-mid);
  border-color: var(--border);
}
.btn-secondary:hover:not(:disabled) {
  background: var(--bg-hover);
  color: var(--text);
}
.btn-secondary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.btn-danger {
  background: var(--bg);
  color: var(--danger);
  border-color: var(--danger-border);
}
.btn-danger:hover {
  background: var(--danger-bg);
}

/* Sync reminder + verification panel */
.sync-reminder {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  margin-bottom: 12px;
  border-radius: 8px;
  font-size: 13px;
  background: var(--bg-subtle, #f6f7f9);
  border: 1px solid var(--border);
}
.btn-primary.sm,
.btn-secondary.sm {
  padding: 3px 10px;
  font-size: 12px;
}
/* Slide-in transition for the detail pane */
.pane-enter-active,
.pane-leave-active {
  transition: transform 220ms cubic-bezier(0.4, 0, 0.2, 1), opacity 220ms ease;
}
.pane-enter-from,
.pane-leave-to {
  transform: translateX(20px);
  opacity: 0;
}

.conflict-row {
  display: inline-flex;
  gap: 6px;
  margin-left: 12px;
  align-items: center;
}
.publish-row {
  margin-top: 8px;
}

/* Promo bar: user stories */
.promo-bar {
  margin-bottom: 16px;
}
.promo-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.share-btn.solo {
  margin-bottom: 12px;
}
.promo-row {
  display: flex;
  gap: 12px;
  overflow-x: auto;
  padding-bottom: 4px;
}
.promo-card {
  min-width: 220px;
  max-width: 260px;
  text-align: left;
  cursor: pointer;
  font: inherit;
  padding: 14px;
  border-radius: 10px;
  border: 1px solid var(--border);
  background: linear-gradient(
    135deg,
    hsl(var(--ph, 220) 60% 96%),
    hsl(calc(var(--ph, 220) + 40) 60% 92%)
  );
}
.promo-card:hover {
  border-color: var(--text);
}
.promo-skill {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  font-size: 13px;
  margin-bottom: 8px;
}
.promo-title {
  font-weight: 600;
  margin-bottom: 6px;
}
.promo-meta {
  font-size: 12px;
  color: var(--text-muted, #888);
}

/* Story article */
.story-article {
  max-width: 760px;
  margin: 0 auto;
}
.story-article .back {
  margin-bottom: 16px;
}
.chev-back {
  font-size: 16px;
  margin-right: 4px;
}
.story-article header h2 {
  margin: 0 0 4px;
}
.story-meta {
  font-size: 13px;
  color: var(--text-muted, #888);
  margin-bottom: 20px;
}
.story-body {
  line-height: 1.7;
  margin-bottom: 24px;
}
.story-skill {
  display: flex;
  gap: 12px;
  align-items: center;
  padding: 14px;
}
.story-skill .desc {
  font-size: 13px;
}
.story-form {
  margin-bottom: 12px;
  padding: 14px;
  display: grid;
  gap: 10px;
}
</style>
