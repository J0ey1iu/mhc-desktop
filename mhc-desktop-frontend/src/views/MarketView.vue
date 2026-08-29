<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue"
import {
  api,
  type MarketSkill,
  type MarketStory,
} from "../api/client"
import { useSkillsStore } from "../stores/skills"
import Icon from "../components/Icon.vue"
import MarketIcon from "../components/MarketIcon.vue"
import SkillsView from "./SkillsView.vue"
import { t } from "../i18n"
import { refreshSync } from "../lib/marketSync"
import { showToast, friendlyError } from "../lib/toast"

const store = useSkillsStore()
const tab = ref<"market" | "mine">("market")
// The market list only loads once at mount; publishing happens in the
// "my skills" tab, so reload whenever the user returns to the market tab.
watch(tab, (v) => {
  if (v === "market") loadMarket()
})

type StatusLevel = "info" | "success" | "error"
const status = ref<{ level: StatusLevel; message: string } | null>(null)
function setStatus(level: StatusLevel, message: string) {
  status.value = { level, message }
}
function clearStatus() {
  status.value = null
}

const CATEGORIES = ["efficiency", "writing", "coding", "office", "other"]
const CATEGORY_LABELS: Record<string, string> = {
  efficiency: t("category.efficiency"),
  writing: t("category.writing"),
  coding: t("category.coding"),
  office: t("category.office"),
  other: t("category.other"),
}
const SORT_OPTIONS = [
  { key: "downloads", label: t("market.sortDownloads") },
  { key: "newest", label: t("market.sortNewest") },
]

/** 发布来源徽标文案：local→原创，repost→转载，无 meta 则空。 */
function sourceLabel(m: { meta?: Record<string, string> }): string {
  const st = m.meta?.source_type
  if (!st) return ""
  return st === "repost" ? t("skills.originRepost") : t("skills.originOriginal")
}

const items = ref<MarketSkill[]>([])
const stories = ref<MarketStory[]>([])

// 市场统计（真实数据，client-side 聚合）
const stats = computed(() => {
  const authors = new Set(items.value.map((i) => i.author))
  const downloads = items.value.reduce((s, i) => s + (i.downloads || 0), 0)
  return { skills: items.value.length, authors: authors.size, downloads }
})
// Hero 右侧预览：按下载量 top 3
const topSkills = computed(() =>
  [...items.value].sort((a, b) => (b.downloads || 0) - (a.downloads || 0)).slice(0, 3),
)

const openStory = ref<MarketStory | null>(null)
const detail = ref<(MarketSkill & { body: string; files: { path: string; content: string }[] }) | null>(null)
const detailLoading = ref(false)
const query = ref("")
const mineQuery = ref("")
const category = ref("")
const sort = ref("downloads")
const loading = ref(false)
const adding = ref<string | null>(null)
const justAdded = ref<string | null>(null)
let justAddedTimer: ReturnType<typeof setTimeout> | null = null
// 添加成功/失败反馈：全局 toast + 按钮瞬时 ✓（约 1.6s 后恢复）。
function flashAdded(slug: string) {
  justAdded.value = slug
  if (justAddedTimer) clearTimeout(justAddedTimer)
  justAddedTimer = setTimeout(() => {
    justAdded.value = null
  }, 1600)
}

const GRADS = [
  "linear-gradient(135deg,#2563eb,#7c3aed)",
  "linear-gradient(135deg,#0ea5e9,#2563eb)",
  "linear-gradient(135deg,#f59e0b,#f97316)",
  "linear-gradient(135deg,#10b981,#34d399)",
  "linear-gradient(135deg,#ec4899,#f472b6)",
]
function grading(i: number): string {
  return GRADS[i % GRADS.length]
}

function excerpt(content: string): string {
  const t = content
    .replace(/#{1,4}\s+/g, "")
    .replace(/\*\*/g, "")
    .replace(/\n+/g, " ")
    .trim()
  return t.slice(0, 80) + (t.length > 80 ? "…" : "")
}

async function loadMarket() {
  loading.value = true
  try {
    const [all, storyList, local] = await Promise.all([
      api.listMarketSkills(query.value, category.value, sort.value),
      api.listMarketStories(),
      api.listSkills(), // already-owned local skills
    ])
    items.value = all
    stories.value = storyList
  } catch (e) {
    setStatus("error", friendlyError(e instanceof Error ? e.message : String(e)))
  } finally {
    loading.value = false
  }
}

function byCategory(c: string) {
  category.value = c
  loadMarket()
}
function bySort(k: string) {
  sort.value = k
  loadMarket()
}

function skillOf(slug: string): MarketSkill | undefined {
  return items.value.find((m) => m.slug === slug)
}

async function openDetail(m: MarketSkill) {
  detailLoading.value = true
  try {
    const files = await api.getMarketSkillFiles(m.slug)
    const skillMd = files.find((f) => f.path.endsWith("SKILL.md"))
    detail.value = {
      ...m,
      body: skillMd?.content ?? "",
      files,
    }
  } catch (e) {
    setStatus("error", friendlyError(e instanceof Error ? e.message : String(e)))
  } finally {
    detailLoading.value = false
  }
}
function closeDetail() {
  detail.value = null
}

function openStoryModal(id: string) {
  const s = stories.value.find((x) => x.id === id)
  if (s) openStory.value = s
}
function closeStory() {
  openStory.value = null
}

function fmtDate(ts: number): string {
  return new Date(ts * 1000).toLocaleDateString()
}

async function addSkill(m: MarketSkill) {
  adding.value = m.slug
  clearStatus()
  try {
    // 添加是无状态拉取：本地以市场 key 为 slug（唯一），同内容去重。
    const { skill } = await api.addMarketSkill(m.slug)
    await store.refresh()
    await refreshSync()  // 让「我的技能」立即带上作者
    flashAdded(m.slug)
    showToast(
      t("skills.marketAdded", { name: skill?.name ?? m.display_name }),
      "success",
    )
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e)
    showToast(t("skills.marketAddFailed", { detail: friendlyError(msg) }), "error")
  } finally {
    adding.value = null
  }
}

function addButtonText(m: MarketSkill): string {
  if (justAdded.value === m.slug) return "✓ " + t("skills.marketAddedShort")
  return adding.value === m.slug ? t("skills.marketAdding") : t("skills.marketAdd")
}
function addButtonDone(m: MarketSkill): boolean {
  return justAdded.value === m.slug
}

let searchTimer: ReturnType<typeof setTimeout> | null = null
function watchMarketQuery() {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => loadMarket(), 300)
}

onMounted(() => {
  loadMarket().catch((e) =>
    setStatus("error", friendlyError(e instanceof Error ? e.message : String(e))),
  )
  window.addEventListener("keydown", onKeydown)
})

onUnmounted(() => {
  window.removeEventListener("keydown", onKeydown)
})

// ESC 依次关闭：详情 → 推荐故事 → 无。
function onKeydown(e: KeyboardEvent) {
  if (e.key !== "Escape") return
  if (detail.value) closeDetail()
  else if (openStory.value) closeStory()
}
</script>

<template>
  <section class="page">
    <header class="head">
      <div>
        <h2>{{ t("market.title") }}</h2>
        <p class="hint">{{ t("market.hint") }}</p>
      </div>
    </header>

    <div class="tabs-row">
      <nav class="tabs">
        <button :class="['tab', { active: tab === 'market' }]" @click="tab = 'market'">
          {{ t("market.tabMarket") }}
        </button>
        <button :class="['tab', { active: tab === 'mine' }]" @click="tab = 'mine'">
          {{ t("market.tabMine") }}
        </button>
      </nav>
      <div class="nav-search">
        <Icon name="search" />
        <input
          v-if="tab === 'market'"
          v-model="query"
          :placeholder="t('skills.marketSearch')"
          @input="watchMarketQuery"
        />
        <input
          v-else
          v-model="mineQuery"
          :placeholder="t('skills.marketSearch')"
        />
      </div>
    </div>

    <SkillsView v-if="tab === 'mine'" :filter-query="mineQuery" />

    <template v-else>
      <p v-if="status" :class="['status-banner', `status-${status.level}`]">
        {{ status.message }}
      </p>

      <!-- Story carousel — top, App-Store style -->
      <div class="section-title"><em>今日推荐</em> · {{ t("skills.promoTitle") }}</div>
      <div v-if="stories.length" class="stories">
        <div
          v-for="(s, i) in stories"
          :key="s.id"
          class="story"
          :style="{ background: grading(i) }"
          @click="openStoryModal(s.id)"
        >
          <div class="eyebrow">{{ t("skills.promoTitle") }} · {{ skillOf(s.skill_slug)?.display_name ?? s.skill_slug }}</div>
          <h3>{{ s.title }}</h3>
          <p>{{ excerpt(s.content) }}</p>
          <div class="meta">{{ t("skills.marketBy", { author: s.author }) }} · {{ fmtDate(s.created_at) }}</div>
        </div>
      </div>

      <!-- Hero：左文案+统计 / 右热门前三预览 -->
      <div class="market-hero">
        <div class="hero-copy">
          <h1>{{ t("market.heroTitle") }}</h1>
          <p class="hero-sub">{{ t("market.heroSub") }}</p>
          <div class="hero-stats">
            <div class="stat">
              <span class="stat-n">{{ stats.skills }}</span>
              <span class="stat-l">{{ t("market.statSkills") }}</span>
            </div>
            <div class="stat">
              <span class="stat-n">{{ stats.authors }}</span>
              <span class="stat-l">{{ t("market.statAuthors") }}</span>
            </div>
            <div class="stat">
              <span class="stat-n">{{ stats.downloads.toLocaleString() }}</span>
              <span class="stat-l">{{ t("market.statDownloads") }}</span>
            </div>
          </div>
        </div>
        <div class="hero-preview">
          <div class="preview-title">{{ t("market.hotSection") }}</div>
          <div
            v-for="(s, i) in topSkills"
            :key="s.slug"
            class="preview-card"
            @click="openDetail(s)"
          >
            <MarketIcon :icon="s.icon" :name="s.display_name" :size="36" />
            <div class="preview-main">
              <b>{{ s.display_name }}</b>
              <span class="preview-meta">{{ t("skills.marketBy", { author: s.author }) }} · ⬇ {{ s.downloads }}</span>
            </div>
            <button
              class="preview-add"
              :class="{ done: addButtonDone(s) }"
              :disabled="adding === s.slug"
              @click.stop="addSkill(s)"
            >{{ addButtonText(s) }}</button>
          </div>
        </div>
      </div>

      <!-- Filters -->
      <div class="filters">
        <div class="chips">
          <span class="chip" :class="{ active: !category }" @click="byCategory('')">
            {{ t("skills.marketAll") }}
          </span>
          <span
            v-for="c in CATEGORIES"
            :key="c"
            class="chip"
            :class="{ active: category === c }"
            @click="byCategory(c)"
          >{{ CATEGORY_LABELS[c] }}</span>
        </div>
        <div class="seg">
          <button
            v-for="o in SORT_OPTIONS"
            :key="o.key"
            :class="{ active: sort === o.key }"
            @click="bySort(o.key)"
          >{{ o.label }}</button>
        </div>
      </div>

      <p v-if="loading && !items.length" class="loading">{{ t("common.loading") }}</p>
      <div v-else-if="items.length" class="grid">
        <div v-for="m in items" :key="m.slug" class="skill-card" @click="openDetail(m)">
          <div class="top">
            <MarketIcon :icon="m.icon" :name="m.display_name" :size="52" />
          </div>
          <h3>{{ m.display_name }}</h3>
          <div class="desc">{{ m.description || t("skills.noDescription") }}</div>
          <div class="foot">
            <div class="stats">
              <span class="cat-tag">{{ CATEGORY_LABELS[m.category] ?? m.category }}</span>
              <span v-if="sourceLabel(m)" class="cat-tag source-tag">{{ sourceLabel(m) }}</span>
              <span class="author">{{ t("skills.marketBy", { author: m.author }) }} · ⬇ {{ m.downloads }}</span>
            </div>
            <button
              class="btn-add"
              :class="{ done: addButtonDone(m) }"
              :disabled="adding === m.slug"
              @click.stop="addSkill(m)"
            >
              {{ addButtonText(m) }}
            </button>
          </div>
        </div>
      </div>
      <p v-else class="muted">{{ t("skills.marketEmpty") }}</p>

      <!-- Market skill detail modal -->
      <div v-if="detail" class="dialog-mask" @click.self="closeDetail">
        <div class="story-modal detail-modal">
          <div class="detail-hero">
            <div class="detail-head">
              <div class="dh-main">
                <div class="dh-top">
                  <MarketIcon :icon="detail.icon" :name="detail.display_name" :size="56" />
                  <div class="grow">
                    <h3>{{ detail.display_name }}</h3>
                    <div class="detail-meta">
                      <span class="meta-pill">{{ CATEGORY_LABELS[detail.category] ?? detail.category }}</span>
                      <span v-if="sourceLabel(detail)" class="meta-pill source-tag">{{ sourceLabel(detail) }}</span>
                      <a
                        v-if="detail.meta?.source_ref"
                        class="meta-pill source-link"
                        :href="detail.meta.source_ref"
                        target="_blank"
                        rel="noopener"
                      >
                        {{ t("skills.sourceLink") }}
                      </a>
                      <span>{{ t("skills.marketBy", { author: detail.author }) }}</span>
                      <span>⬇ {{ detail.downloads }}</span>
                      <span v-if="detail.updated_at">{{ fmtDate(detail.updated_at) }}</span>
                    </div>
                  </div>
                </div>
                <p class="detail-desc">{{ detail.description }}</p>
              </div>
              <button class="btn-secondary detail-close" @click="closeDetail">✕</button>
            </div>
          </div>
          <div class="detail-body">
            <div class="section-title" style="margin-top:0">SKILL.md</div>
            <pre class="md">{{ detailLoading ? t("common.loading") : detail.body || t("skills.emptyBody") }}</pre>
            <div v-if="detail.files.length > 1" class="section-title">Files</div>
            <ul v-if="detail.files.length > 1" class="filelist">
              <li v-for="f in detail.files" :key="f.path">{{ f.path }}</li>
            </ul>
          </div>
          <div class="detail-foot">
            <button
              class="btn-primary detail-add"
              :class="{ done: addButtonDone(detail) }"
              :disabled="adding === detail.slug"
              @click="addSkill(detail)"
            >
              {{ addButtonText(detail) }}
            </button>
          </div>
        </div>
      </div>

      <!-- Story modal -->
      <div v-if="openStory" class="dialog-mask" @click.self="closeStory">
        <div class="story-modal">
          <div class="story-head">
            <div>
              <div class="hint story-eyebrow">
                {{ t("skills.promoTitle") }} · {{ skillOf(openStory.skill_slug)?.display_name ?? openStory.skill_slug }}
              </div>
              <h3>{{ openStory.title }}</h3>
              <div class="hint">{{ t("skills.marketBy", { author: openStory.author }) }} · {{ fmtDate(openStory.created_at) }}</div>
            </div>
            <button class="btn-secondary" @click="closeStory">✕</button>
          </div>
          <div class="content">{{ openStory.content }}</div>
          <div v-if="skillOf(openStory.skill_slug)" class="linked">
            <MarketIcon
              :icon="skillOf(openStory.skill_slug)!.icon"
              :name="skillOf(openStory.skill_slug)!.display_name"
              :size="40"
            />
            <div class="grow">
              <b>{{ skillOf(openStory.skill_slug)!.display_name }}</b>
              <div class="hint">{{ CATEGORY_LABELS[skillOf(openStory.skill_slug)!.category] ?? skillOf(openStory.skill_slug)!.category }} · {{ skillOf(openStory.skill_slug)!.downloads }} ↓</div>
            </div>
            <button
              class="btn-primary"
              :class="{ done: addButtonDone(skillOf(openStory.skill_slug)!) }"
              :disabled="adding === openStory.skill_slug"
              @click="addSkill(skillOf(openStory.skill_slug)!)"
            >{{ addButtonText(skillOf(openStory.skill_slug)!) }}</button>
          </div>
        </div>
      </div>
    </template>
  </section>
</template>

<style scoped>
.page {
  position: relative;
  display: block;
  height: 100%;
  overflow-y: auto;
  padding: 32px 32px 60px;
  color: var(--text);
}
.head { display: flex; flex-direction: column; align-items: stretch; gap: 12px; margin-bottom: 16px; }
.head h2 { margin: 0 0 4px; font-size: 22px; letter-spacing: -0.01em; }
.hint { margin: 0; color: var(--text-mid); font-size: 13px; max-width: 540px; line-height: 1.5; }
.tabs-row { display: flex; align-items: flex-end; justify-content: space-between; gap: 12px; border-bottom: 1px solid var(--border); margin-bottom: 12px; }
.tabs { display: flex; gap: 4px; }
.tab { background: none; border: none; border-bottom: 2px solid transparent; color: var(--text-mid); padding: 8px 14px; cursor: pointer; font: inherit; }
.tab.active { color: var(--text); border-bottom-color: var(--text); }
.nav-search { display: flex; align-items: center; gap: 8px; background: var(--bg); border: 1px solid var(--border); border-radius: 999px; padding: 7px 14px; min-width: 220px; }
.nav-search input { border: 0; background: transparent; font: inherit; font-size: 13px; color: var(--text); width: 100%; }
.nav-search input:focus { outline: none; }

.status-banner { border-radius: 8px; padding: 8px 12px; font-size: 13px; border: 1px solid transparent; margin-bottom: 12px; }
.status-banner.status-error { color: var(--danger); background: var(--danger-bg); border-color: var(--danger-border); }
.status-banner.status-success { color: var(--success, #15803d); background: var(--success-bg, rgba(34,197,94,.1)); border-color: var(--success-border, rgba(34,197,94,.3)); }
.status-banner.status-info { color: var(--text-mid); background: var(--bg-subtle, var(--bg)); border-color: var(--border); }

.section-title { font-size: 13px; color: var(--text-faint); text-transform: uppercase; letter-spacing: 0.06em; margin: 22px 0 12px; font-weight: 600; }
.section-title em { color: var(--accent); font-style: normal; text-transform: none; }
.loading, .muted { color: var(--text-mid); margin: 12px 0; }

/* stories */
.stories { display: flex; gap: 16px; overflow-x: auto; padding: 4px 0 10px; scroll-snap-type: x mandatory; flex-shrink: 0; }
.story { scroll-snap-align: start; flex: 0 0 380px; width: 380px; border-radius: 18px; padding: 22px; color: #fff; cursor: pointer; box-shadow: 0 1px 2px rgba(0,0,0,.04), 0 4px 12px rgba(0,0,0,.06); }
.story:hover { transform: translateY(-3px); box-shadow: 0 8px 30px rgba(0,0,0,.12); }
.story .eyebrow { font-size: 12px; opacity: 0.9; font-weight: 600; letter-spacing: 0.04em; margin-bottom: 10px; }
.story h3 { font-size: 20px; margin: 0 0 8px; line-height: 1.3; }
.story p { font-size: 13px; opacity: 0.92; margin: 0 0 14px; line-height: 1.5; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.story .meta { display: flex; gap: 8px; font-size: 12px; opacity: 0.9; }

/* hero: 左文案+统计 / 右热门前三预览 */
.market-hero { display: grid; grid-template-columns: minmax(0, 1fr) minmax(300px, 0.85fr); gap: 32px; align-items: center; padding: 18px 0 22px; }
.hero-copy h1 { font-size: 26px; margin: 0 0 6px; letter-spacing: -0.02em; }
.hero-sub { color: var(--text-mid); margin: 0 0 16px; font-size: 13.5px; max-width: 46ch; line-height: 1.6; }
.hero-stats { display: flex; gap: 26px; }
.stat { display: flex; flex-direction: column; gap: 1px; }
.stat-n { font-size: 22px; font-weight: 700; letter-spacing: -0.02em; font-variant-numeric: tabular-nums; }
.stat-l { font-size: 11.5px; color: var(--text-faint); }
.hero-preview { display: flex; flex-direction: column; gap: 8px; }
.preview-title { font-size: 11px; color: var(--text-faint); text-transform: uppercase; letter-spacing: 0.07em; font-weight: 600; margin-bottom: 2px; }
.preview-card { display: flex; align-items: center; gap: 10px; background: var(--bg); border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px; box-shadow: 0 1px 2px rgba(0,0,0,.04); cursor: pointer; transition: transform .15s, box-shadow .15s, border-color .15s; }
.preview-card:hover { transform: translateX(3px); box-shadow: 0 4px 14px rgba(0,0,0,.08); border-color: var(--border-mid); }
.preview-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
.preview-main b { font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.preview-meta { font-size: 11.5px; color: var(--text-faint); }
.preview-add { border: 0; border-radius: 7px; background: var(--accent); color: var(--accent-fg); font: inherit; font-size: 12px; font-weight: 600; padding: 6px 12px; cursor: pointer; white-space: nowrap; transition: background 120ms ease, transform 120ms ease; }
.preview-add:hover { background: var(--accent-hover); }
.preview-add:active { transform: scale(.97); }
.preview-add.done { background: var(--bg); color: var(--success, #16a34a); border: 1px solid var(--border); }

/* filters */
.filters { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin: 6px 0 18px; flex-wrap: wrap; }
.chips { display: flex; gap: 8px; flex-wrap: wrap; }
.chip { padding: 7px 15px; font-size: 13px; border: 1px solid var(--border); border-radius: 999px; background: var(--bg); color: var(--text-mid); cursor: pointer; font-weight: 500; }
.chip:hover { border-color: var(--border-mid); color: var(--text); }
.chip.active { background: var(--accent); border-color: var(--accent); color: var(--accent-fg); }
.seg { display: flex; border: 1px solid var(--border); border-radius: 8px; overflow: hidden; background: var(--bg); }
.seg button { border: 0; background: transparent; padding: 7px 14px; font-size: 13px; color: var(--text-mid); cursor: pointer; font-weight: 500; }
.seg button.active { background: var(--bg-hover); color: var(--accent); font-weight: 600; }

/* cards */
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 16px; }
.skill-card { background: linear-gradient(160deg, var(--bg-subtle, #f7f8fa), var(--bg)); border: 1px solid var(--border); border-radius: 12px; padding: 18px; display: flex; flex-direction: column; gap: 12px; transition: transform .15s, box-shadow .15s, border-color .15s; cursor: pointer; }
.skill-card:hover { transform: translateY(-3px); box-shadow: 0 8px 30px rgba(0,0,0,.12); border-color: var(--border-mid); }
.skill-card .top { display: flex; align-items: flex-start; }
.skill-card h3 { margin: 0; font-size: 15px; }
.skill-card .desc { font-size: 13px; color: var(--text-mid); line-height: 1.55; min-height: 40px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.skill-card .foot { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-top: auto; padding-top: 10px; border-top: 1px solid var(--border-faint); }
.stats { display: flex; align-items: center; gap: 8px; font-size: 11.5px; color: var(--text-faint); min-width: 0; }
.cat-tag { font-size: 10.5px; background: var(--bg-hover); color: var(--text-mid); padding: 2px 8px; border-radius: 999px; font-weight: 600; white-space: nowrap; }
.source-tag { background: var(--accent-soft, rgba(37,99,235,.12)); color: var(--accent, #2563eb); }
.source-link { text-decoration: none; }
.source-link:hover { text-decoration: underline; }
.stats .author { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.btn-add { background: var(--accent); color: var(--accent-fg); border: 0; border-radius: 8px; padding: 7px 13px; font-size: 13px; cursor: pointer; font-weight: 600; flex-shrink: 0; }
.btn-add:hover { background: var(--accent-hover); }
.btn-add.done { background: var(--bg); color: var(--success, #16a34a); border: 1px solid var(--border); }
.btn-add:disabled { cursor: default; opacity: 0.7; }
.btn-add:disabled:hover { background: var(--accent); }
.btn-primary.done { background: var(--bg); color: var(--success, #16a34a); border: 1px solid var(--border); }

/* story modal */
.dialog-mask { position: fixed; inset: 0; background: rgba(0,0,0,.4); z-index: 60; display: grid; place-items: center; padding: 24px; }
.story-modal { background: var(--bg); border-radius: 16px; width: 680px; max-width: 92vw; max-height: 88vh; overflow-y: auto; padding: 24px; box-shadow: 0 8px 30px rgba(0,0,0,.18); }
.story-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; }
.story-head h3 { margin: 0 0 4px; }
.story-eyebrow { color: var(--accent); font-weight: 600; margin-bottom: 4px; }
.content { font-size: 14px; line-height: 1.8; max-height: 46vh; overflow-y: auto; margin: 14px 0; white-space: pre-wrap; }
.linked { display: flex; gap: 12px; align-items: center; background: var(--bg-subtle, var(--bg)); border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; }
.grow { flex: 1; min-width: 0; }
.btn-primary,
.btn-secondary {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font: inherit;
  font-size: 13px;
  padding: 7px 14px;
  border-radius: 8px;
  cursor: pointer;
  border: 1px solid transparent;
  transition: background 120ms ease, border-color 120ms ease, color 120ms ease;
}
.detail-modal { display: flex; flex-direction: column; max-height: 88vh; }
.detail-hero { background: var(--bg-subtle, var(--bg-hover)); border-bottom: 1px solid var(--border-faint); margin: -24px -24px 0; padding: 22px 24px 18px; }
.detail-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; margin-bottom: 8px; }
.dh-main { flex: 1; min-width: 0; }
.dh-top { display: flex; align-items: center; gap: 14px; margin-bottom: 8px; }
.dh-top h3 { margin: 0; font-size: 21px; }
.detail-meta { display: flex; align-items: center; gap: 10px; font-size: 12px; color: var(--text-mid); margin-top: 4px; flex-wrap: wrap; }
.meta-pill { background: var(--accent-soft, rgba(37,99,235,.12)); color: var(--accent, #2563eb); font-weight: 600; padding: 2px 10px; border-radius: 999px; }
.detail-desc { font-size: 13.5px; color: var(--text-mid); line-height: 1.6; margin: 0; }
.detail-close { align-self: flex-start; }
.detail-body { overflow-y: auto; flex: 1; padding-top: 16px; }
.md { background: var(--bg-subtle, var(--bg)); border: 1px solid var(--border-faint); border-radius: 8px; padding: 12px 14px; font-size: 12.5px; line-height: 1.55; white-space: pre-wrap; word-break: break-word; margin: 0 0 12px; max-height: 46vh; overflow-y: auto; }
.filelist { list-style: none; margin: 0; padding: 0; display: grid; gap: 4px; }
.filelist li { padding: 5px 8px; font-size: 12px; color: var(--text-mid); background: var(--bg-subtle, var(--bg)); border-radius: 4px; font-family: ui-monospace, "JetBrains Mono", monospace; }
.detail-foot { display: flex; justify-content: flex-end; padding-top: 12px; border-top: 1px solid var(--border-faint); margin-top: 8px; }
.btn-primary { background: var(--accent); color: var(--accent-fg); border-color: var(--accent); }
.btn-primary:hover:not(:disabled) { background: var(--accent-hover); }
.btn-secondary { background: var(--bg); color: var(--text-mid); border-color: var(--border); }
.btn-secondary:hover:not(:disabled) { background: var(--bg-hover); color: var(--text); }
.detail-add { min-width: 110px; justify-content: center; }

@media (max-width: 860px) {
  .market-hero { grid-template-columns: 1fr; gap: 18px; }
}
</style>
