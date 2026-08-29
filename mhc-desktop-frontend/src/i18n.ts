import { computed, ref } from "vue"

export type Locale = "en" | "zh"
const LS_KEY = "mhc.locale"

// ---------------------------------------------------------------------------
// Dictionaries
// ---------------------------------------------------------------------------

const en: Record<string, string> = {
  // common
  "common.brand": "mhc-desktop",
  "common.cancel": "Cancel",
  "common.tryAgain": "please try again",
  "common.edit": "Edit",
  "common.confirm": "Confirm",
  "common.save": "Save",
  "common.saving": "Saving…",
  "common.delete": "Delete",
  "common.loading": "loading…",
  "common.confirmDeleteSession": "Delete this session?",
  "common.confirmDeleteProvider": 'Delete provider "{name}"?',

  // onboarding
  "onboarding.gotIt": "Got it",
  "onboarding.start": "Start",
  "onboarding.next": "Next",
  "onboarding.skip": "Skip",
  "onboarding.stepOf": "{current} of {total}",
  "onboarding.progressAria": "Onboarding progress",

  // nav
  "nav.chat": "Chat",
  "nav.models": "Models",
  "nav.skills": "My Skills",
  "nav.market": "Skill Market",

  // splash + lifecycle overlays
  "splash.loading": "Loading…",
  "splash.starting": "Starting backend… first launch can take a minute",
  "splash.exiting": "Shutting down…",
  "nav.mcp": "MCP",
  "nav.mcpHint": "More MCPs can be turned on in the MCP settings page.",
  "nav.tools": "Tools",
  "nav.settings": "Settings",
  "nav.metrics": "Usage",
  "nav.workspace": "Workspace",
  "nav.workspaceMCPsHint": "No MCPs enabled.",

  // title bar
  "titleBar.minimize": "Minimize",
  "titleBar.maximize": "Maximize",
  "titleBar.restore": "Restore",
  "titleBar.close": "Close",

  // side panel toggles
  "panel.expand": "Expand",
  "panel.collapse": "Collapse",

  // chat
  "chat.emptyTitle": "Ask anything",
  "chat.emptySub": "Messages stream back as the model generates.",
  "chat.placeholder":
    "Message mhc-desktop…  (Enter to send · Shift+Enter for newline)",
  "chat.placeholderExpanded": "Write freely — Esc to exit, ⏎ to send",
  "chat.pickModel": "Pick a model",
  "chat.fullscreen": "Fullscreen",
  "chat.exitFullscreen": "Exit fullscreen (Esc)",
  "chat.send": "Send (⏎)",
  "chat.stop": "Stop generating",
  "chat.noModelSelected":
    "No model selected. Enable a provider and pick a model.",
  "chat.noProvidersHint":
    "No providers enabled. Open the Models page to add one.",

  "chat.attachedFiles": "{count, plural, one{# file} other{# files}} attached",
  "chat.attachedFilesTitle":
    "Files attached when this message was sent. The model receives only the absolute paths and reads each file via its tools — the binary stays on your disk.",
  "chat.attachedFilesMax": "Maximum {count} files attached — remove one to add another.",
  "chat.attachFiles": "Attach files (up to 5)",
  "chat.fileNoPath":
    "No absolute path available for this file — the model cannot read it.",
  "cap.skills": "Skills",
  "cap.mcp": "MCP",
  "cap.tools": "Tools",
  "cap.files": "Files",
  "cap.title": "Skills · MCP · Tools",
  "cap.none": "None",
  "chat.toolCalls": "Tool calls",
  "chat.thinking": "Thinking",
  "chat.contextTitle":
    "Context: {prompt} prompt + {completion} completion tokens of {max} max",
  "chat.cancelled": "Response cancelled",
  "chat.cancelledTitle": "This reply was stopped mid-generation; the partial content is kept.",
  "chat.streaming": "Generating…",
  "chat.voiceInput": "Voice input — local speech recognition (sherpa-onnx)",
  "chat.voiceStop": "Stop recording",
  "chat.voiceLoading": "Loading speech model…",
  "chat.voiceError": "Voice input unavailable: {message}",
  "chat.voiceStageMic": "Opening mic",
  "chat.voiceStageModel": "Loading model",
  "chat.voiceStageRecord": "Listening",

  // mcp
  "mcp.title": "MCP",
  "mcp.hint":
    "MCP (Model Context Protocol) servers expose tools the model can call inside an agent run. Bundled dummy server ships with three trivial tools (add / echo / uppercase) so you can verify the wiring without depending on an external service.",
  "mcp.add": "Add MCP",
  "mcp.addTitle": "Add MCP server",
  "mcp.addHint":
    "Paste a command + args that starts an MCP server speaking JSON-RPC 2.0 over stdin/stdout. Real MCP servers usually come from npm (``npx -y @some/server``) or PyPI.",
  "mcp.name": "Display name",
  "mcp.namePlaceholder": "weather",
  "mcp.description": "Description",
  "mcp.command": "Command",
  "mcp.commandPlaceholder": "npx -y @modelcontextprotocol/server-everything",
  "mcp.commandRequired": "Command is required.",
  "mcp.args": "Args",
  "mcp.argsPlaceholder": "-y @modelcontextprotocol/server-everything",
  "mcp.env": "Environment (one KEY=VALUE per line)",
  "mcp.envPlaceholder": "API_KEY=sk-...",
  "mcp.envLabel": "Environment",
  "mcp.bundled": "bundled",
  "mcp.imported": "imported",
  "mcp.importBulkFolder": "Import pack (folder)…",
  "mcp.importBulkZip": "Import pack (zip)…",
  "mcp.bulkInstalled": "Installed",
  "mcp.bulkSkipped": "Skipped",
  "mcp.bulkErrors": "Errors",
  "mcp.noPicker": "Folder / file picker is unavailable in this build.",
  "mcp.confirmDelete": "Delete MCP \"{name}\"?",
  "mcp.confirmDeleteTitle": "Delete MCP",
  "mcp.deleted": "Deleted \"{name}\".",
  "mcp.noDescription": "(no description)",
  "mcp.noToolsYet": "No tools discovered yet. Click Refresh to spawn the subprocess and call tools/list.",
  "mcp.noToolsReturned": "Subprocess started but reported no tools. Check stderr / make sure it's an MCP server.",
  "mcp.refreshTools": "Refresh tools",
  "mcp.refreshing": "Refreshing…",
  "mcp.export": "Export",
  "mcp.editTitle": "Edit MCP server",
  "mcp.tools": "Tools",
  "mcp.spawn": "Spawn command",
  "mcp.empty":
    "No MCPs installed yet. The bundled <em>dummy</em> server should have been auto-installed at first launch — try restarting the backend if it's missing.",
  "mcp.disable": "Disable",
  "mcp.enable": "Enable",

  // sessions
  "sessions.title": "Sessions",
  "sessions.edit": "Edit",
  "sessions.editTitle": "Edit sessions",
  "sessions.done": "Done",
  "sessions.new": "New session",
  "sessions.delete": "Delete",
  "sessions.renameTitle": "Double-click to rename",
  "sessions.deleteSelected": "Delete selected",
  "sessions.clearAll": "Clear all sessions",
  "sessions.selectAll": "Select all",
  "sessions.selectedCount": "{count, plural, one{# selected} other{# selected}}",
  "sessions.confirmDeleteTitle": "Delete session",
  "sessions.confirmDeleteMany":
    "Delete {count, plural, one{# session} other{# sessions}}? This cannot be undone.",
  "sessions.confirmClearAll":
    "Delete all {count, plural, one{# session} other{# sessions}}? This cannot be undone.",
  "sessions.empty": "No sessions yet. Hit + to start.",
  "sessions.running": "Running…",
  "sessions.toastDone": "Finished",
  "sessions.toastJump": "Open this session",

  // models (page-level; the data underneath is providers)
  "models.title": "Models",
  "models.hint":
    "Models are configured through service providers. Disabled providers keep their config but don't appear in the chat model selector.",
  "models.providerSection": "Service providers",
  "models.addProvider": "+ Add provider",
  "models.addProviderTitle": "Add provider",
  "models.editProvider": "Edit",
  "models.providersEmpty":
    "No providers yet. Click <em>Add provider</em> to start.",
  "models.providerOff": "off",
  "models.providerDelete": "Delete",
  "models.providerDisable": "Disable",
  "models.providerEnable": "Enable",
  "models.noDefaultModel": "(no default model)",
  "models.confirmDeleteProviderTitle": "Delete provider",

  // provider form
  "providerForm.name": "Name",
  "providerForm.namePlaceholder": "my-openai",
  "providerForm.apiKey": "API key",
  "providerForm.apiKeyPlaceholder": "sk-...",
  "providerForm.apiKeyKeepHint":
    "Leave blank to keep the saved key; type a new value to replace it.",
  "providerForm.providerType": "Provider type",
  "providerForm.openai": "openai (also OpenAI-compatible vendors)",
  "providerForm.anthropic": "anthropic",
  "providerForm.baseUrl": "Base URL",
  "providerForm.baseUrlPlaceholder": "https://api.openai.com/v1",
  "providerForm.defaultModel": "Default model",
  "providerForm.defaultModelPlaceholder": "gpt-4o-mini",
  "providerForm.description": "Description",
  "providerForm.models": "Models",
  "providerForm.addModel": "Add model",
  "providerForm.removeModel": "Remove",
  "providerForm.moveUp": "Move up",
  "providerForm.moveDown": "Move down",
  "providerForm.modelsHint":
    "Each row is one model. Model ID is what we send to the LLM API; display name is shown in the UI; max context drives the context-usage meter.",
  "providerForm.modelsEmpty": "No models yet. Click + Add model to define one.",
  "providerForm.modelCodePlaceholder": "model-id",
  "providerForm.modelDisplayPlaceholder": "Display name",
  "providerForm.modelCtxPlaceholder": "Max context",
  "providerForm.modelParams": "Model params (JSON)",

  // settings
  "settings.title": "Settings",
  "settings.subtitle": "Local preferences for this app",
  "settings.appearance": "Appearance",
  "settings.theme": "Theme",
  "settings.themeDesc":
    "Switch the whole interface between light and dark.",
  "settings.themeLight": "Light",
  "settings.themeDark": "Dark",
  "settings.language": "Language",
  "settings.languageDesc": "Switch the interface language.",
  "settings.languageEn": "English",
  "settings.languageZh": "中文",
  "settings.about": "About",
  "settings.aboutDesc":
    "Self-contained Electron client for talking to LLM agents via your own provider keys. Independent of mh-gateway and mh-local.",

  "settings.updates": "Updates",
  "settings.updateStatus": "Update status",
  "settings.updateIdle": "Up to date",
  "settings.updateChecking": "Checking…",
  "settings.updateAvailable": "Update available",
  "settings.updateDownloading": "Downloading…",
  "settings.updateFailed": "Update failed",
  "settings.updateStaged": "Ready to install on next restart",
  "settings.updateApplying": "Installing…",
  "settings.updateCommitted": "Installed successfully",
  "settings.updateRolledBack": "Rolled back to previous version",
  "settings.updateCheck": "Check now",
  "settings.updateInstall": "Download",
  "settings.updateApply": "Restart to install",

  "settings.tour": "Show welcome tour",
  "settings.tourDesc":
    "Replay the first-run guide with skills and MCP walkthroughs.",
  "settings.appTitle": "App title",
  "settings.appTitleDesc":
    "Name shown in the title bar (English renders in caps) and the sidebar top. Each user can rename the app.",
  "settings.fontSize": "Font size",
  "settings.fontSizeDesc":
    "Adjusts body text across the app, including chat messages and the input box.",

  "settings.aiBehavior": "AI behavior",
  "settings.systemPromptAddition": "System prompt addition",
  "settings.systemPromptAdditionDesc":
    "Free-form instructions sent as the system prompt on every chat. The server always prepends a tiny base (just the skill folder location) — edit this to define the assistant's identity, tone, and any business rules. For example: “You are a senior code reviewer. Always reply in 中文 and be concise.”",
  "settings.systemPromptAdditionPlaceholder":
    "e.g. You are a senior code reviewer. Always reply in 中文.",
  "settings.save": "Save",
  "settings.saving": "Saving…",
  "settings.savedAt": "Saved at {time}",

  // backend startup status
  "backend.starting":
    "Backend starting… first Windows launch can take ~2 minutes while the antivirus scans the bundled Python.",

  // skills
  "skills.title": "Skill Market",
  "skills.hint":
    "Skills are folders of markdown instructions that the chat can carry into a message. Compatible with the Anthropic skill format (SKILL.md + YAML frontmatter). Import a folder or a .skill.zip bundle to get started.",
  "skills.mineTitle": "My Skills",
  "skills.marketSearch": "Search skills…",
  "skills.marketEmpty": "No skills published yet.",
  "skills.marketAdd": "Add Skill",
  "skills.marketAdding": "Adding…",
  "skills.marketAddedShort": "Added",
  "skills.marketAdded": "Added \"{name}\" to My Skills.",
  "skills.marketAddFailed": "Add failed: {detail}",
  "skills.marketDownloads": "{n} downloads",
  "skills.marketBy": "by {author}",
  "skills.publish": "Publish to Market",
  "skills.published": "Published \"{name}\" to the market.",
  "skills.publishFailed": "Publish failed: {detail}",
  "skills.publishCategory": "Category",
  "skills.publishSource": "Source",
  "skills.sourceLocal": "Original",
  "skills.sourceRepost": "Repost",
  "skills.sourceRef": "Source URL (for repost)",
  "skills.originOriginal": "Original",
  "skills.originRepost": "Repost",
  "skills.sourceLink": "Source",
  "skills.delist": "Delist from Market",
  "skills.delistTitle": "Delist skill",
  "skills.delistConfirm": "Delisting removes \"{name}\" from the public market. Your local copy stays. Delist it?",
  "skills.delisted": "Delisted \"{name}\" from the market.",
  "skills.delistFailed": "Delist failed: {detail}",
  "skills.sync": "Sync",
  "skills.synced": "Synced: {pushed} pushed · {pulled} pulled.",
  "skills.syncConflicts": "{n} conflict(s) need a decision:",
  "skills.conflictUseLocal": "Use local",
  "skills.conflictUseCloud": "Use cloud",
  "skills.conflictResolved": "Resolved \"{name}\".",
  "skills.actionPush": "local edits → push",
  "skills.actionPull": "cloud changed → pull",
  "skills.actionUpToDate": "up to date",
  "skills.actionCloudDeleted": "cloud copy deleted",
  "skills.actionConflict": "conflict",
  "skills.originMarket": "market",
  "skills.marketUnavailable": "Skill market is not available (not configured or unreachable).",
  "skills.marketAll": "All categories",
  "skills.promoTitle": "User stories",
  "skills.shareStory": "Share my story",
  "skills.storyTitle": "Title",
  "skills.storySkill": "Related skill",
  "skills.storyContent": "Content (markdown)",
  "skills.storyHint": "How did you use this skill? What did it help you do?",
  "skills.storyCreated": "Story published.",
  "skills.storyBack": "Back to market",
  "skills.importFolder": "Import folder…",
  "skills.importZip": "Import zip…",
  "skills.importBulkFolder": "Import pack (folder)…",
  "skills.importBulkZip": "Import pack (zip)…",
  "skills.bulkInstalled": "Installed",
  "skills.bulkSkipped": "Skipped",
  "skills.bulkErrors": "Errors",
  "skills.export": "Export",
  "skills.edit": "Edit",
  "skills.delete": "Delete",
  "skills.bundled": "bundled",
  "skills.imported": "imported",
  "skills.local": "local",
  "skills.confirmDelete": 'Delete skill "{name}"?',
  "skills.confirmDeleteTitle": "Delete skill",
  "skills.confirmDeleteCloud": 'Remove "{name}"?\n\nThe local skill will be deleted and its cloud copy will be removed too.',
  "skills.deleted": "Deleted \"{name}\".",
  "skills.noDescription": "(no description)",
  "skills.emptyBody": "(empty)",
  "skills.empty":
    "No skills installed yet. Click <em>Import folder</em> or <em>Import zip</em>, or install the bundled sample skills from the package.",
  "skills.noPicker":
    "Folder / file picker is unavailable in this build of the app.",
  "skills.disable": "Disable",
  "skills.remove": "Remove",
  "skills.enable": "Enable",
  "skills.files": "Files",
  "skills.editDescription": "Description (frontmatter)",
  "skills.editCancelled": "Edit discarded — changes were not saved.",
  "skills.editBody": "Body (markdown)",

  // market page
  "market.title": "Skill Market",
  "market.hint":
    "Browse the cloud skill registry, read user stories, and verify the local ↔ cloud sync mechanism per skill.",
  "market.tabMarket": "Market",
  "market.tabMine": "My Skills",
  "market.syncTitle": "Sync status (local ↔ cloud)",
  "market.syncCheck": "Check",
  "market.syncRun": "Sync now",
  "market.syncSlug": "Skill",
  "market.syncLocal": "Local",
  "market.syncCloud": "Cloud",
  "market.syncBase": "Base",
  "market.syncState": "State",
  "market.syncOps": "Actions",
  "market.autoOnSync": "runs on Sync now",
  "market.reupload": "Re-upload to cloud",
  "market.syncReminder":
    "Sync check: {n} skill(s) differ between local and cloud. Sync now?",
  "market.syncTarget": "Syncing to {user}'s cloud space",
  "market.syncEmpty":
    "Nothing to sync yet — add a skill from the market or create one locally.",
  "market.heroTitle": "Discover Skills",
  "market.hotSection": "Trending",
  "market.statSkills": "skills",
  "market.statAuthors": "authors",
  "market.statDownloads": "downloads",
  "market.heroSub":
    "Curated by the community · safe-reviewed · one-click add.",
  "market.sortDownloads": "By downloads",
  "market.sortNewest": "Newest first",
  // categories
  "category.efficiency": "Efficiency",
  "category.writing": "Writing",
  "category.coding": "Coding",
  "category.office": "Office",
  "category.other": "Other",


  // tools (third concept — local / script / remote)
  "tools.title": "Tools",
  "tools.hint":
    "Tools are executable units the model can call. Local tools are in-process Python callables; script and remote kinds are stubs in this build.",
  "tools.add": "Add Tool",
  "tools.addTitle": "Add tool",
  "tools.importTitle": "Import tool from Python source",
  "tools.importHint":
    "Paste a Python module that defines ``async def tool_run(**kwargs)``. The module is exec'd in this backend process when the tool runs.",
  "tools.importSourceLabel": "Python source",
  "tools.importSlugLabel": "Slug (optional, derived from name)",
  "tools.importNameLabel": "Display name",
  "tools.importDescriptionLabel": "Description",
  "tools.importSubmit": "Import & enable",
  "tools.importBulkFolder": "Import pack (folder)…",
  "tools.importBulkZip": "Import pack (zip)…",
  "tools.bulkInstalled": "Installed",
  "tools.bulkSkipped": "Skipped",
  "tools.bulkErrors": "Errors",
  "tools.importOverwrite": "Overwrite if slug exists",

  "tools.exportTitle": "Export tool",
  "tools.exportHint":
    "Download this tool's manifest as JSON. Use it to share the config or import on another machine.",
  "tools.download": "Download manifest",
  "tools.editNameLabel": "Display name",
  "tools.editModelNameLabel": "Name passed to the model",
  "tools.empty": "No tools installed yet.",
  "tools.bundled": "Bundled",
  "tools.kind.local": "Local",
  "tools.kind.script": "Script",
  "tools.kind.remote": "Remote",
  "tools.kindMcp": "MCP",
  "tools.kindTool": "Tool",
  "tools.imported": "Imported",
  "tools.toggle": "Toggle",
  "tools.remove": "Remove tool",
  "tools.confirmDelete": 'Delete tool "{name}"?',
  "tools.confirmDeleteTitle": "Delete tool",
  "tools.deleted": "Deleted \"{name}\".",
  "tools.parameters": "Parameters",
  "tools.source": "Source",
  "tools.noPicker": "File picker is not available in this environment.",

  // metrics dashboard
  "metrics.title": "Usage dashboard",
  "metrics.subtitle": "Local usage stats — raw events in ~/.mhc-desktop/metrics.jsonl",
  "metrics.range7": "Last 7 days",
  "metrics.range30": "Last 30 days",
  "metrics.rangeAll": "All time",
  "metrics.refresh": "Refresh",
  "metrics.refreshed": "Updated {time}",
  "metrics.empty": "No data yet. Start a chat to populate the dashboard.",
  "metrics.error": "Failed to load: {message}",

  "metrics.cards.todayConversations": "Today's conversations",
  "metrics.cards.totalConversations": "Historical conversations",
  "metrics.cards.todayTokens": "Today's tokens",
  "metrics.cards.totalTokens": "Historical tokens",
  "metrics.cards.todayToolCalls": "Today's tool calls",
  "metrics.cards.totalToolCalls": "Historical tool calls",
  "metrics.cards.todaySkillCalls": "Today's skill uses",
  "metrics.cards.totalSkillCalls": "Historical skill uses",
  "metrics.cards.errorRate": "LLM error rate",
  "metrics.cards.avgDuration": "Avg duration",
  "metrics.cards.avgTokensPerCall": "Avg tokens / call",
  "metrics.cards.totalTokensTotal": "Historical tokens",

  "metrics.trend.title": "Daily trend",
  "metrics.trend.llmCalls": "LLM calls",
  "metrics.trend.tokens": "Tokens consumed",
  "metrics.trend.toolCalls": "Tool calls",
  "metrics.trend.conversations": "Conversations",

  "metrics.rankings.tools": "Tool usage ranking",
  "metrics.rankings.toolErrors": "Tool error rate ranking",
  "metrics.rankings.skills": "Skill usage ranking",
  "metrics.rankings.mcps": "MCP usage ranking",
  "metrics.rankings.models": "Model usage stats",

  "metrics.col.name": "Name",
  "metrics.col.count": "Count",
  "metrics.col.errors": "Errors",
  "metrics.col.errorRate": "Error rate",
  "metrics.col.share": "Share",
  "metrics.col.avg": "Avg",
  "metrics.col.p50": "P50",
  "metrics.col.p95": "P95",
  "metrics.col.p99": "P99",
  "metrics.col.avgTokens": "Avg tokens",
  "metrics.col.calls": "Calls",
  "metrics.col.total": "Total",

  "metrics.models.empty": "No models called yet.",
  "metrics.rankings.empty": "No data in this range.",

  "metrics.page.prev": "Previous",
  "metrics.page.next": "Next",
  "metrics.page.of": "Page {page} / {total}",
  "metrics.page.total": "{total} total",

  // login
  "login.title": "Sign in",
  "login.username": "Username",
  "login.password": "Password",
  "login.submit": "Sign in",
  "login.submitting": "Signing in…",
  "login.failed": "Invalid username or password.",
  "login.errorMissing": "Please enter both username and password.",
  "login.brand": "mhc-desktop",
  "login.toggleTheme": "Toggle light/dark theme",

  // user card (sidebar footer)
  "sidebar.signOut": "Sign out",
  "sidebar.signedInAs": "Signed in as {name}",
}

const zh: Record<string, string> = {
  // common
  "common.brand": "mhc-desktop",
  "common.cancel": "取消",
  "common.tryAgain": "请稍后重试",
  "common.confirm": "确认",
  "common.save": "保存",
  "common.saving": "保存中…",
  "common.delete": "删除",
  "common.edit": "编辑",
  "common.export": "导出",
  "common.loading": "加载中…",
  "common.confirmDeleteSession": "确认删除此会话？",
  "common.confirmDeleteProvider": '确认删除服务商 "{name}"？',

  // onboarding
  "onboarding.gotIt": "知道了",
  "onboarding.start": "开始",
  "onboarding.next": "下一步",
  "onboarding.skip": "跳过",
  "onboarding.stepOf": "第 {current} / {total} 步",
  "onboarding.progressAria": "指引进度",

  // nav
  "nav.chat": "对话",
  "nav.models": "模型配置",
  "nav.skills": "我的技能",
  "nav.market": "技能市场",

  // splash + lifecycle overlays
  "splash.loading": "加载中…",
  "splash.starting": "正在启动后端… 首次运行可能需要 1 分钟",
  "splash.exiting": "正在退出…",
  "nav.mcp": "MCP",
  "nav.mcpHint": "更多的 MCP 可以在“MCP”设置页面打开。",
  "nav.tools": "工具",
  "nav.settings": "设置",
  "nav.metrics": "用量",
  "nav.workspace": "工作区",
  "nav.workspaceMCPsHint": "未启用任何 MCP。",

  // title bar
  "titleBar.minimize": "最小化",
  "titleBar.maximize": "最大化",
  "titleBar.restore": "还原",
  "titleBar.close": "关闭",

  // side panel toggles
  "panel.expand": "展开",
  "panel.collapse": "收起",

  // chat
  "chat.emptyTitle": "随便问",
  "chat.emptySub": "模型生成时消息会实时流式返回。",
  "chat.placeholder": "给 mhc-desktop 发消息…（Enter 发送 · Shift+Enter 换行）",
  "chat.placeholderExpanded": "自由书写 — Esc 退出，⏎ 发送",
  "chat.pickModel": "选择模型",
  "chat.fullscreen": "全屏",
  "chat.exitFullscreen": "退出全屏 (Esc)",
  "chat.send": "发送 (⏎)",
  "chat.stop": "停止生成",
  "chat.noModelSelected": "未选择模型。请启用服务商并选择一个模型。",
  "chat.noProvidersHint": "没有启用的服务商。打开“模型配置”页面来添加一个。",
  "chat.attachedFiles": "{count, plural, other{# 个文件}}已附上",
  "chat.attachedFilesTitle":
    "本条消息发送时附带的文件。只会上传绝对路径，模型通过本地工具读取原文，原始内容始终保留在你的磁盘上。",
  "chat.attachedFilesMax": "已达到 {count} 个文件上限 — 移除一个后再添加新的。",
  "chat.attachFiles": "附加文件（最多 5 个）",
  "chat.fileNoPath": "该文件没有可用的绝对路径，模型无法读取。",
  "cap.skills": "技能",
  "cap.mcp": "MCP",
  "cap.tools": "工具",
  "cap.files": "文件",
  "cap.title": "技能 · MCP · 工具",
  "cap.none": "无",
  "chat.toolCalls": "工具调用",
  "chat.thinking": "思考",
  "chat.contextTitle":
    "上下文：{prompt} 输入 + {completion} 输出，上限 {max} tokens",
  "chat.cancelled": "已取消",
  "chat.cancelledTitle": "该回复在生成中途被停止，已保留已输出的部分内容。",
  "chat.streaming": "正在生成…",
  "chat.voiceInput": "语音输入 — 本地语音识别（sherpa-onnx）",
  "chat.voiceStop": "停止录音",
  "chat.voiceLoading": "正在加载语音模型…",
  "chat.voiceError": "语音输入不可用：{message}",
  "chat.voiceStageMic": "打开麦克风",
  "chat.voiceStageModel": "加载模型",
  "chat.voiceStageRecord": "收音中",

  // mcp
  "mcp.title": "MCP",
  "mcp.hint":
    "MCP（Model Context Protocol）服务器把工具开放给模型，让模型在一次 agent run 内调用。内置的 dummy 服务器提供了 add / echo / uppercase 三个简单工具，用来端到端验证整套 MCP 链路，不需要依赖外部服务。",
  "mcp.add": "添加 MCP",
  "mcp.importBulkFolder": "批量导入(文件夹)…",
  "mcp.importBulkZip": "批量导入(zip)…",
  "mcp.bulkInstalled": "已安装",
  "mcp.bulkSkipped": "已跳过",
  "mcp.bulkErrors": "错误",
  "mcp.addTitle": "添加 MCP 服务器",
  "mcp.addHint":
    "填入一条能启动 MCP 服务器的命令 + 参数，要求通过 stdin/stdout 使用 JSON-RPC 2.0 协议。常见的 MCP 服务器一般来自 npm（``npx -y @some/server``）或 PyPI。",
  "mcp.name": "显示名称",
  "mcp.namePlaceholder": "weather",
  "mcp.description": "描述",
  "mcp.command": "命令",
  "mcp.commandPlaceholder": "npx -y @modelcontextprotocol/server-everything",
  "mcp.commandRequired": "命令必填。",
  "mcp.args": "参数",
  "mcp.argsPlaceholder": "-y @modelcontextprotocol/server-everything",
  "mcp.env": "环境变量（每行 KEY=VALUE）",
  "mcp.envPlaceholder": "API_KEY=sk-...",
  "mcp.envLabel": "环境变量",
  "mcp.bundled": "内置",
  "mcp.imported": "导入",
  "mcp.confirmDelete": "确认删除 MCP \"{name}\"？",
  "mcp.confirmDeleteTitle": "删除 MCP",
  "mcp.deleted": "已删除 \"{name}\"。",
  "mcp.noPicker": "当前构建不支持文件夹 / 文件选择器。",
  "mcp.noDescription": "（无描述）",
  "mcp.noToolsYet": "尚未发现任何工具。点击“刷新工具”启动子进程并调用 tools/list。",
  "mcp.noToolsReturned": "子进程已启动但没有返回工具。请检查 stderr / 确认它是一个 MCP 服务器。",
  "mcp.refreshTools": "刷新工具",
  "mcp.refreshing": "正在刷新…",
  "mcp.export": "导出",
  "mcp.editTitle": "编辑 MCP 服务器",
  "mcp.tools": "工具",
  "mcp.spawn": "启动命令",
  "mcp.empty":
    "尚未安装任何 MCP。内置的 <em>dummy</em> 服务器应该已自动安装，缺失的话可重启后端试试。",
  "mcp.disable": "停用",
  "mcp.enable": "启用",

  // 后端启动状态
  "backend.starting":
    "后端正在启动…首次在 Windows 上运行可能需要 2 分钟，杀毒软件会扫描捆绑的 Python。",

  // sessions
  "sessions.title": "会话",
  "sessions.edit": "编辑",
  "sessions.editTitle": "编辑会话",
  "sessions.done": "完成",
  "sessions.new": "新建会话",
  "sessions.delete": "删除",
  "sessions.renameTitle": "双击重命名",
  "sessions.deleteSelected": "删除所选",
  "sessions.clearAll": "清空所有会话",
  "sessions.selectAll": "全选",
  "sessions.selectedCount": "已选 {count} 项",
  "sessions.confirmDeleteTitle": "删除会话",
  "sessions.confirmDeleteMany": "确定删除 {count} 个会话？该操作不可恢复。",
  "sessions.confirmClearAll": "确定删除全部 {count} 个会话？该操作不可恢复。",
  "sessions.empty": "暂无会话。点击 + 开始。",
  "sessions.running": "执行中…",
  "sessions.toastDone": "已完成",
  "sessions.toastJump": "打开该会话",

  // models（页面级；实际数据是服务商）
  "models.title": "模型配置",
  "models.hint": "模型通过服务商进行配置。禁用的服务商仍保留配置，但不会出现在对话的模型选择中。",
  "models.providerSection": "服务商",
  "models.addProvider": "+ 添加服务商",
  "models.addProviderTitle": "添加服务商",
  "models.editProvider": "编辑",
  "models.providersEmpty": "暂无服务商。点击 <em>添加服务商</em> 开始配置。",
  "models.providerOff": "已停用",
  "models.providerDelete": "删除",
  "models.providerDisable": "停用",
  "models.providerEnable": "启用",
  "models.noDefaultModel": "（无默认模型）",
  "models.confirmDeleteProviderTitle": "删除服务商",

  // provider form
  "providerForm.name": "名称",
  "providerForm.namePlaceholder": "my-openai",
  "providerForm.apiKey": "API 密钥",
  "providerForm.apiKeyPlaceholder": "sk-...",
  "providerForm.apiKeyKeepHint": "留空保留已保存的密钥；输入新值即替换。",
  "providerForm.providerType": "服务商类型",
  "providerForm.openai": "openai（也支持 OpenAI 兼容的服务商）",
  "providerForm.anthropic": "anthropic",
  "providerForm.baseUrl": "基础 URL",
  "providerForm.baseUrlPlaceholder": "https://api.openai.com/v1",
  "providerForm.defaultModel": "默认模型",
  "providerForm.defaultModelPlaceholder": "gpt-4o-mini",
  "providerForm.description": "描述",
  "providerForm.models": "模型",
  "providerForm.addModel": "添加模型",
  "providerForm.removeModel": "删除",
  "providerForm.moveUp": "上移",
  "providerForm.moveDown": "下移",
  "providerForm.modelsHint":
    "每行一个模型。模型 ID 是真正传给 LLM API 的参数；展示名称用于 UI；最大上下文用于上下文占用比计算。",
  "providerForm.modelsEmpty": "暂无模型。点击 + 添加模型。",
  "providerForm.modelCodePlaceholder": "model-id",
  "providerForm.modelDisplayPlaceholder": "展示名称",
  "providerForm.modelCtxPlaceholder": "最大上下文",
  "providerForm.modelParams": "模型参数 (JSON)",

  // settings
  "settings.title": "设置",
  "settings.subtitle": "本应用的本地偏好设置",
  "settings.appearance": "外观",
  "settings.theme": "主题",
  "settings.themeDesc": "在浅色和深色之间切换整个界面。",
  "settings.themeLight": "浅色",
  "settings.themeDark": "深色",
  "settings.language": "语言",
  "settings.languageDesc": "切换界面语言。",
  "settings.languageEn": "English",
  "settings.languageZh": "中文",
  "settings.about": "关于",
  "settings.aboutDesc":
    "独立的 Electron 客户端，通过您自己的服务商密钥与 LLM Agent 对话。独立于 mh-gateway 和 mh-local。",

  "settings.updates": "更新",
  "settings.updateStatus": "更新状态",
  "settings.updateIdle": "已是最新",
  "settings.updateChecking": "检查中…",
  "settings.updateAvailable": "有新版本可用",
  "settings.updateDownloading": "下载中…",
  "settings.updateFailed": "更新失败",
  "settings.updateStaged": "已下载，下次启动时自动安装",
  "settings.updateApplying": "安装中…",
  "settings.updateCommitted": "安装成功",
  "settings.updateRolledBack": "已回滚到上一版本",
  "settings.updateCheck": "立即检查",
  "settings.updateInstall": "下载更新",
  "settings.updateApply": "重启安装",

  "settings.tour": "再次显示欢迎指引",
  "settings.appTitle": "应用名称",
  "settings.appTitleDesc":
    "在标题栏（英文会显示为大写）和侧边栏顶部展示的名字。每个用户可以各自重命名本应用。",
  "settings.fontSize": "字体大小",
  "settings.fontSizeDesc":
    "调整全应用的正文字号，包括对话消息和输入框。",
  "settings.aiBehavior": "AI 行为",
  "settings.systemPromptAddition": "系统提示词",
  "settings.systemPromptAdditionDesc":
    "每次对话都作为系统提示词发送的自定义内容。服务端始终会在前面加上一小段基础信息（仅含技能目录位置）。您可以在此处定义助手的身份、语气以及业务规则。例如：“你是一位资深代码审阅者。请始终用中文回复，保持言简意赅。”",
  "settings.systemPromptAdditionPlaceholder":
    "例如：你是一位资深代码审阅者。请始终用中文回复。",
  "settings.save": "保存",
  "settings.saving": "保存中…",
  "settings.savedAt": "已于 {time} 保存",
  "settings.tourDesc": "重新播放首次启动时的指引卡片，包括技能与 MCP 的演示。",

  // skills
  "skills.title": "技能市场",
  "skills.hint":
    "技能是一组 markdown 指令，对话可将其带入下一条消息。兼容 Anthropic 技能格式（SKILL.md + YAML frontmatter）。导入一个文件夹或 .skill.zip 包即可开始。",
  "skills.mineTitle": "我的技能",
  "skills.marketSearch": "搜索技能…",
  "skills.marketEmpty": "还没有已发布的技能。",
  "skills.marketAdd": "添加技能",
  "skills.marketAdding": "添加中…",
  "skills.marketAddedShort": "已添加",
  "skills.marketAdded": "已将 “{name}” 添加到我的技能。",
  "skills.marketAddFailed": "添加失败：{detail}",
  "skills.marketDownloads": "{n} 次下载",
  "skills.marketBy": "作者 {author}",
  "skills.publish": "发布到市场",
  "skills.published": "已将 “{name}” 发布到市场。",
  "skills.publishFailed": "发布失败：{detail}",
  "skills.publishCategory": "分类",
  "skills.publishSource": "来源",
  "skills.sourceLocal": "本机原创",
  "skills.sourceRepost": "转载",
  "skills.sourceRef": "来源链接（转载时填写）",
  "skills.originOriginal": "原创",
  "skills.originRepost": "转载",
  "skills.sourceLink": "来源",
  "skills.delist": "下架",
  "skills.delistTitle": "下架技能",
  "skills.delistConfirm": "下架后，\"{name}\" 将从公共市场移除，你的本地副本保留。确定下架吗？",
  "skills.delisted": "已将 “{name}” 下架。",
  "skills.delistFailed": "下架失败：{detail}",
  "skills.sync": "同步",
  "skills.synced": "同步完成：上传 {pushed} 个 · 下载 {pulled} 个。",
  "skills.syncConflicts": "{n} 个冲突需要选择：",
  "skills.conflictUseLocal": "保留本地",
  "skills.conflictUseCloud": "使用云端",
  "skills.conflictResolved": "已处理 “{name}”。",
  "skills.actionPush": "本地有修改 → 上传",
  "skills.actionPull": "云端有更新 → 下载",
  "skills.actionUpToDate": "已是最新",
  "skills.actionCloudDeleted": "云端副本已删除",
  "skills.actionConflict": "冲突",
  "skills.originMarket": "市场",
  "skills.marketUnavailable": "技能市场不可用（未配置或无法访问）。",
  "skills.marketAll": "全部分类",
  "skills.promoTitle": "用户故事",
  "skills.shareStory": "分享我的使用故事",
  "skills.storyTitle": "标题",
  "skills.storySkill": "关联技能",
  "skills.storyContent": "内容（markdown）",
  "skills.storyHint": "你用这个技能做了什么？它帮到了你什么？",
  "skills.storyCreated": "故事已发布。",
  "skills.storyBack": "返回市场",
  "skills.importFolder": "导入文件夹…",
  "skills.importZip": "导入 zip…",
  "skills.importBulkFolder": "批量导入(文件夹)…",
  "skills.importBulkZip": "批量导入(zip)…",
  "skills.bulkInstalled": "已安装",
  "skills.bulkSkipped": "已跳过",
  "skills.bulkErrors": "错误",
  "skills.export": "导出",
  "skills.edit": "编辑",
  "skills.delete": "删除",
  "skills.bundled": "内置",
  "skills.imported": "导入",
  "skills.local": "本地",
  "skills.confirmDelete": '确认删除技能 "{name}"？',
  "skills.confirmDeleteTitle": "删除技能",
  "skills.confirmDeleteCloud": `移除 "{name}"？

本地技能将被删除，云端个人空间里的副本也会一并删除。`,
  "skills.deleted": "已删除 \"{name}\"。",
  "skills.noDescription": "（无描述）",
  "skills.emptyBody": "（空）",
  "skills.empty":
    "尚未安装任何技能。点击 <em>导入文件夹</em> 或 <em>导入 zip</em>，也可以安装包内的内置示例技能。",
  "skills.noPicker": "当前构建不支持文件夹 / 文件选择器。",
  "skills.disable": "停用",
  "skills.remove": "移除",
  "skills.enable": "启用",
  "skills.files": "文件",
  "skills.editDescription": "描述（frontmatter）",
  "skills.editCancelled": "已取消编辑，未保存的修改已丢弃。",
  "skills.editBody": "正文（markdown）",

  // market page
  "market.title": "技能市场",
  "market.hint":
    "浏览云端技能目录、阅读用户故事，并逐个验证本地与云端的同步机制。",
  "market.tabMarket": "市场广场",
  "market.tabMine": "我的技能",
  "market.syncTitle": "同步状态（本地 ↔ 云端）",
  "market.syncCheck": "检查",
  "market.syncRun": "立即同步",
  "market.syncSlug": "技能",
  "market.syncLocal": "本地",
  "market.syncCloud": "云端",
  "market.syncBase": "基准",
  "market.syncState": "状态",
  "market.syncOps": "操作",
  "market.autoOnSync": "点「立即同步」执行",
  "market.reupload": "重新上传到云端",
  "market.syncReminder":
    "同步检测：{n} 个技能本地与云端不一致，是否立即同步？",
  "market.syncTarget": "同步到 {user} 的云端空间",
  "market.syncEmpty":
    "暂无需要同步的内容 —— 从市场添加技能，或在本地新建一个。",
  "market.heroTitle": "发现技能",
  "market.hotSection": "热门",
  "market.statSkills": "个技能",
  "market.statAuthors": "位作者",
  "market.statDownloads": "次下载",
  "market.heroSub":
    "社区甄选 · 安全审核 · 一键添加。",
  "market.sortDownloads": "按下载量",
  "market.sortNewest": "最新上架",
  "category.efficiency": "效率",
  "category.writing": "写作",
  "category.coding": "编程",
  "category.office": "办公",
  "category.other": "其他",

  "tools.title": "工具",
  "tools.hint":
    "工具是可被模型调用的可执行单元。本地/内置工具为进程内 Python 调用；脚本与远程类型在该构建中为存根。",
  "tools.add": "添加工具",
  "tools.addTitle": "添加工具",
  "tools.importTitle": "从 Python 源码导入工具",
  "tools.importHint":
    "粘贴一个定义了 ``async def tool_run(**kwargs)`` 的 Python 模块。模块会在后端进程中执行。",
  "tools.importSourceLabel": "Python 源码",
  "tools.importSlugLabel": "Slug（可选，自动从名称生成）",
  "tools.importNameLabel": "显示名称",
  "tools.importDescriptionLabel": "描述",
  "tools.importSubmit": "导入并启用",
  "tools.importBulkFolder": "批量导入(文件夹)…",
  "tools.importBulkZip": "批量导入(zip)…",
  "tools.bulkInstalled": "已安装",
  "tools.bulkSkipped": "已跳过",
  "tools.bulkErrors": "错误",
  "tools.importOverwrite": "如果 slug 已存在则覆盖",
  "tools.exportTitle": "导出工具",
  "tools.exportHint": "以 JSON 形式下载该工具的清单。",
  "tools.download": "下载清单",
  "tools.editNameLabel": "展示名称",
  "tools.editModelNameLabel": "传给模型的名字",
  "tools.empty": "尚未安装任何工具。",
  "tools.bundled": "内置",
  "tools.kind.local": "本地",
  "tools.kind.script": "脚本",
  "tools.kind.remote": "远程",
  "tools.kindMcp": "MCP",
  "tools.kindTool": "工具",
  "tools.imported": "已导入",
  "tools.toggle": "切换",
  "tools.remove": "删除工具",
  "tools.confirmDelete": '确认删除工具 "{name}"？',
  "tools.confirmDeleteTitle": "删除工具",
  "tools.deleted": "已删除 \"{name}\"。",
  "tools.parameters": "参数",
  "tools.source": "源码位置",
  "tools.noPicker": "当前环境无法使用文件选择器。",

  // metrics dashboard
  "metrics.title": "用量看板",
  "metrics.subtitle": "本地收集的调用统计，明细落在 ~/.mhc-desktop/metrics.jsonl",
  "metrics.range7": "近 7 天",
  "metrics.range30": "近 30 天",
  "metrics.rangeAll": "全部",
  "metrics.refresh": "刷新",
  "metrics.refreshed": "已于 {time} 更新",
  "metrics.empty": "还没有数据。发起一次对话即可。",
  "metrics.error": "加载失败：{message}",

  "metrics.cards.todayConversations": "今日对话",
  "metrics.cards.totalConversations": "历史对话",
  "metrics.cards.todayTokens": "今日 token",
  "metrics.cards.totalTokens": "历史 token",
  "metrics.cards.todayToolCalls": "今日工具调用",
  "metrics.cards.totalToolCalls": "历史工具调用",
  "metrics.cards.todaySkillCalls": "今日技能使用",
  "metrics.cards.totalSkillCalls": "历史技能使用",
  "metrics.cards.errorRate": "LLM 错误率",
  "metrics.cards.avgDuration": "平均耗时",
  "metrics.cards.avgTokensPerCall": "平均每次调用 token",
  "metrics.cards.totalTokensTotal": "历史 token 总量",

  "metrics.trend.title": "按天趋势",
  "metrics.trend.llmCalls": "LLM 调用",
  "metrics.trend.tokens": "Token 消耗",
  "metrics.trend.toolCalls": "工具调用",
  "metrics.trend.conversations": "对话",

  "metrics.rankings.tools": "工具使用排名",
  "metrics.rankings.toolErrors": "工具错误率排名",
  "metrics.rankings.skills": "Skill 使用排名",
  "metrics.rankings.mcps": "MCP 使用排名",
  "metrics.rankings.models": "模型使用情况",

  "metrics.col.name": "名称",
  "metrics.col.count": "次数",
  "metrics.col.errors": "错误数",
  "metrics.col.errorRate": "错误率",
  "metrics.col.share": "占比",
  "metrics.col.avg": "平均",
  "metrics.col.p50": "P50",
  "metrics.col.p95": "P95",
  "metrics.col.p99": "P99",
  "metrics.col.avgTokens": "平均 token",
  "metrics.col.calls": "调用",
  "metrics.col.total": "合计",

  "metrics.models.empty": "还没有调用过任何模型。",
  "metrics.rankings.empty": "该范围内无数据。",

  "metrics.page.prev": "上一页",
  "metrics.page.next": "下一页",
  "metrics.page.of": "第 {page} / {total} 页",
  "metrics.page.total": "共 {total} 条",

  // login
  "login.title": "登录",
  "login.username": "用户名",
  "login.password": "密码",
  "login.submit": "登录",
  "login.submitting": "登录中…",
  "login.failed": "用户名或密码错误。",
  "login.errorMissing": "请输入用户名和密码。",
  "login.brand": "mhc-desktop",
  "login.toggleTheme": "切换亮色/暗色主题",

  // user card (sidebar footer)
  "sidebar.signOut": "退出登录",
  "sidebar.signedInAs": "当前登录：{name}",
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

function readSaved(): Locale {
  try {
    const v = localStorage.getItem(LS_KEY)
    if (v === "en" || v === "zh") return v
  } catch {
    /* ignore */
  }
  // Fall back to browser language; default to English.
  if (typeof navigator !== "undefined") {
    const lang = navigator.language?.toLowerCase() ?? ""
    if (lang.startsWith("zh")) return "zh"
  }
  return "en"
}

export const locale = ref<Locale>(readSaved())
export const dict = computed<Record<string, string>>(() =>
  locale.value === "zh" ? zh : en,
)

/** Resolve a localised display name from an item carrying a
 *  ``display_name_i18n: Record<string, string>`` field. Falls
 *  back through current locale → English → ``fallback`` so
 *  an item that ships only an English display name still
 *  renders correctly in a Chinese UI. */
export function pickI18n(
  item: { display_name_i18n?: Record<string, string> } | null | undefined,
  fallback: string,
): string {
  const m = item?.display_name_i18n
  if (!m) return fallback
  return m[locale.value] ?? m.en ?? fallback
}

/**
 * Look up a translation by key. Falls back to the key itself when missing,
 * which keeps templates readable while exposing typos as visible strings
 * during development. Supports:
 *   - simple `{name}` substitution
 *   - CLDR-lite plurals: `{count, plural, one {# skill} other {# skills}}`
 *     where `#` inside a branch refers to the count value. Only `one`
 *     and `other` are recognized — those cover everything we need.
 *
 * We don't pull in the full MessageFormat runtime (50+ KiB) just for
 * `one`/`other`.
 */
export function t(
  key: string,
  params?: Record<string, string | number>,
): string {
  let s = dict.value[key] ?? key
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      const re = new RegExp(`\\{${k}\\}`, "g")
      s = s.replace(re, String(v))
    }
    s = applyPlurals(s, params)
  }
  return s
}

const PLURAL_RE =
  /\{\s*([a-zA-Z_][\w]*)\s*,\s*plural\s*,/g

function applyPlurals(
  s: string,
  params: Record<string, string | number>,
): string {
  PLURAL_RE.lastIndex = 0
  let out = ""
  let last = 0
  let m: RegExpExecArray | null
  while ((m = PLURAL_RE.exec(s)) !== null) {
    out += s.slice(last, m.index)
    const name = m[1]
    // Find the matching closing '}' by brace depth from this position.
    const start = PLURAL_RE.lastIndex
    let depth = 1
    let i = start
    while (i < s.length && depth > 0) {
      if (s[i] === "{") depth++
      else if (s[i] === "}") depth--
      if (depth === 0) break
      i++
    }
    const body = s.slice(start, i)
    PLURAL_RE.lastIndex = i + 1
    last = i + 1
    out += resolvePlural(name, body, params)
  }
  out += s.slice(last)
  return out
}

function resolvePlural(
  name: string,
  body: string,
  params: Record<string, string | number>,
): string {
  const count = Number(params[name] ?? 0)
  const branches: Record<string, string> = {}
  let i = 0
  let depth = 0
  while (i < body.length) {
    while (i < body.length && /[\s,]/.test(body[i])) i++
    const wStart = i
    while (i < body.length && /[a-zA-Z]/.test(body[i])) i++
    const keyword = body.slice(wStart, i)
    if (!keyword) break
    while (i < body.length && body[i] !== "{") i++
    if (body[i] !== "{") break
    i++  // step past the opening '{' so depth tracking starts at 0
    const mStart = i
    while (i < body.length) {
      if (body[i] === "{") depth++
      else if (body[i] === "}") {
        if (depth === 0) break
        depth--
      }
      i++
    }
    const message = body.slice(mStart, i).trim()
    i++  // step past the closing '}'
    branches[keyword] = message
  }
  const pick =
    (count === 1 && branches.one) ||
    branches.other ||
    branches.one ||
    Object.values(branches)[0] ||
    ""
  return pick.replace(/#/g, String(count))
}

export function setLocale(l: Locale) {
  locale.value = l
  try {
    localStorage.setItem(LS_KEY, l)
  } catch {
    /* ignore */
  }
  // Reflect on the root element so screen readers + browser features
  // (e.g. spellcheck) pick up the right BCP-47 tag.
  if (typeof document !== "undefined") {
    document.documentElement.setAttribute("lang", l === "zh" ? "zh-CN" : "en")
  }
}

// Set the initial <html lang="..."> on module load.
if (typeof document !== "undefined") {
  document.documentElement.setAttribute(
    "lang",
    locale.value === "zh" ? "zh-CN" : "en",
  )
}
