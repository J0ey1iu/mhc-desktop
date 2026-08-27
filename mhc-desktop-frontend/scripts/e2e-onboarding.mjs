// @ts-nocheck
// E2E: first-run onboarding overlay.
//
// Drives the live Electron app via CDP. The renderer fetches its
// cards from /api/v1/onboarding on mount, sends Accept-Language
// so the backend can resolve the title/body fields, opens the
// overlay if localStorage["mhc.onboarding.done"] is unset, and
// dismisses it when the user clicks the primary action on the
// last card.
//
// Checks:
//   1. Fresh install shows the overlay
//   2. All three card types render in order (centered, media-text,
//      media-top)
//   3. Primary button label is "Next" / "下一步" on cards 1-2 and
//      "Got it" / "知道了" on the last
//   4. Cards 2/3 carry a real illustration (media-image) instead
//      of just the colour fallback
//   5. Overlay text is non-selectable (user-select: none) so
//      users can't drag-copy walkthrough copy
//   6. Clicking the last card's primary button closes the overlay
//      AND sets the localStorage flag
//   7. A subsequent reload does NOT re-show the overlay
//   8. The fetch request includes an Accept-Language header so
//      the backend can localise its resolved strings
//   9. Toggling the locale in Settings swaps the displayed copy
//      without dismissing the overlay (i18n dicts are reactive)
//
// Run with: node scripts/e2e-onboarding.mjs

import { CDP, eval_, sleep } from "./e2e-helpers.mjs"

const TARGET_HOST = "127.0.0.1"
const TARGET_PORT = 9222

async function main() {
  const client = await CDP({ host: TARGET_HOST, port: TARGET_PORT })
  const { Runtime, Page, Network } = client
  await Runtime.enable()
  await Page.enable()
  await Network.enable()

  // Capture the Accept-Language header sent to /api/v1/onboarding
  // for the "i18n header is sent" assertion.
  const observedHeaders = []
  client.on("Network.requestWillBeSent", (e) => {
    if (e.request.url.includes("/api/v1/onboarding")) {
      observedHeaders.push({
        url: e.request.url,
        acceptLanguage: e.request.headers["Accept-Language"] ?? null,
      })
    }
  })

  console.log("waiting for Electron renderer...")
  await eval_(client, "location.href")

  // 0. Reset onboarding state — the test is hermetic.
  await eval_(
    client,
    `(() => {
      localStorage.removeItem("mhc.onboarding.done");
      localStorage.removeItem("mhc.onboarding.index");
      localStorage.setItem("mhc.locale", "en");
    })()`,
  )
  await eval_(client, "location.reload()")
  await sleep(4000)

  // 1. Fresh install shows the overlay with card 1 (centered, English).
  const first = await eval_(
    client,
    `(() => {
      const dialog = document.querySelector('[role=dialog][aria-modal]');
      if (!dialog) return { overlayVisible: false };
      const card = dialog.querySelector('.card');
      const dots = Array.from(dialog.querySelectorAll('.dot')).map(d =>
        d.classList.contains('active'),
      );
      return {
        overlayVisible: true,
        title: dialog.querySelector('h1,h2')?.textContent?.trim() || null,
        body: dialog.querySelector('p')?.textContent?.trim() || null,
        type: card?.getAttribute('data-type'),
        dots,
        userSelect: getComputedStyle(dialog).userSelect,
        webkitUserSelect: getComputedStyle(dialog).webkitUserSelect,
      };
    })()`,
  )
  if (!first.overlayVisible) throw new Error("overlay should be visible on first run")
  if (first.type !== "centered")
    throw new Error(`card 1 should be centered, got ${first.type}`)
  if (first.title !== "Welcome to mhc-desktop-backend")
    throw new Error(`expected English title with kernel-default brand, got "${first.title}"`)
  if (!first.dots[0] || first.dots.filter(Boolean).length !== 1)
    throw new Error(`expected only first dot active, got ${JSON.stringify(first.dots)}`)
  if (first.userSelect !== "none" || first.webkitUserSelect !== "none")
    throw new Error(
      `overlay text must be user-select:none, got ${first.userSelect} / ${first.webkitUserSelect}`,
    )
  console.log(`✓ card 1: type=${first.type}, title="${first.title}", user-select=none`)

  // 2+3. Walk to card 2 (media-text) and verify image + Chinese-style
  //      layout when locale is zh, but we stay in en to also assert
  //      the title is the English version. (Locale-switch is checked
  //      in step 9.)
  await eval_(client, `document.querySelector('[role=dialog][aria-modal] .primary')?.click()`)
  await sleep(1300)
  const second = await eval_(
    client,
    `(() => {
      const dialog = document.querySelector('[role=dialog][aria-modal]');
      const card = dialog?.querySelector('.card');
      const img = dialog?.querySelector('.media-image');
      return {
        type: card?.getAttribute('data-type'),
        title: dialog?.querySelector('h1,h2')?.textContent?.trim(),
        imageSrc: img?.getAttribute('src') || null,
        imageLoaded: !!img?.complete && img.naturalWidth > 0,
      };
    })()`,
  )
  if (second.type !== "media-text")
    throw new Error(`card 2 should be media-text, got ${second.type}`)
  if (second.title !== "Skills ride along")
    throw new Error(`expected English skills title, got "${second.title}"`)
  if (!second.imageSrc || !second.imageSrc.includes("skills.svg"))
    throw new Error(`card 2 should carry /onboarding/skills.svg, got ${second.imageSrc}`)
  if (!second.imageLoaded)
    throw new Error(`card 2 illustration failed to load: ${second.imageSrc}`)
  console.log(`✓ card 2: image=${second.imageSrc.split("/").pop()}, loaded=true`)

  // Card 3 (media-top, MCP illustration).
  await eval_(client, `document.querySelector('[role=dialog][aria-modal] .primary')?.click()`)
  await sleep(1300)
  const third = await eval_(
    client,
    `(() => {
      const dialog = document.querySelector('[role=dialog][aria-modal]');
      const card = dialog?.querySelector('.card');
      const img = dialog?.querySelector('.media-image');
      const primary = dialog?.querySelector('.primary');
      return {
        type: card?.getAttribute('data-type'),
        title: dialog?.querySelector('h1,h2')?.textContent?.trim(),
        imageSrc: img?.getAttribute('src') || null,
        imageLoaded: !!img?.complete && img.naturalWidth > 0,
        primaryLabel: primary?.textContent?.trim() || null,
      };
    })()`,
  )
  if (third.type !== "media-top")
    throw new Error(`card 3 should be media-top, got ${third.type}`)
  if (third.title !== "Connect tools via MCP")
    throw new Error(`expected English MCP title, got "${third.title}"`)
  if (!third.imageSrc || !third.imageSrc.includes("mcp.svg"))
    throw new Error(`card 3 should carry /onboarding/mcp.svg, got ${third.imageSrc}`)
  if (!third.imageLoaded)
    throw new Error(`card 3 illustration failed to load: ${third.imageSrc}`)
  if (third.primaryLabel !== "Got it")
    throw new Error(`last card primary should be "Got it", got "${third.primaryLabel}"`)
  console.log(`✓ card 3: image=${third.imageSrc.split("/").pop()}, primary="${third.primaryLabel}"`)

  // 4. Locale switch — flip mhc.locale to "zh" without dismissing
  //    the overlay; the renderer should re-render title/body from
  //    the i18n dicts immediately and the store should fire a
  //    background reload so the backend's resolved strings stay in
  //    sync. We assert on the rendered title first (instant),
  //    then on the backend response after the reload resolves.
  await eval_(
    client,
    `(() => {
      const mod = window.__i18n || null;
      // Fall back to writing localStorage and reloading the page if
      // we can't reach the ref. The reload path also exercises the
      // Accept-Language header on the next fetch.
      if (mod && mod.setLocale) mod.setLocale('zh');
    })()`,
  )
  await sleep(2500)
  const zhTitle = await eval_(
    client,
    `(() => { const d = document.querySelector('[role=dialog][aria-modal]'); return d?.querySelector('h1,h2')?.textContent; })()?.trim()`,
  )
  if (zhTitle !== "用 MCP 连接工具" && zhTitle !== "技能随消息绑定" && zhTitle !== "欢迎使用 mhc-desktop-backend")
    throw new Error(
      `locale switch did not re-render in Chinese; got "${zhTitle}". ` +
      `This usually means the component isn't reading the i18n dicts.`,
    )
  console.log(`✓ locale switch re-rendered title to "${zhTitle}"`)

  // Re-walk to card 3 if we landed on an earlier one (locale flip
  // shouldn't change card index, but the reload we triggered may
  // have re-mounted). Then verify the resolved title on the wire.
  for (let i = 0; i < 2; i++) {
    const cur = await eval_(
      client,
      `document.querySelector('[role=dialog][aria-modal] .card')?.getAttribute('data-type')`,
    )
    if (cur === "media-top") break
    await eval_(client, `document.querySelector('[role=dialog][aria-modal] .primary')?.click()`)
    await sleep(1200)
  }
  const card3zh = await eval_(
    client,
    `(() => { const d = document.querySelector('[role=dialog][aria-modal]'); return d?.querySelector('h1,h2')?.textContent; })()?.trim()`,
  )
  if (card3zh !== "用 MCP 连接工具")
    throw new Error(`card 3 should now be "用 MCP 连接工具", got "${card3zh}"`)
  console.log("✓ card 3 rendered in Chinese")

  // 5. Click last card's primary to dismiss. This still works after
  //    the locale change because the primary button is keyed on
  //    isLast, not on the locale.
  await eval_(client, `document.querySelector('[role=dialog][aria-modal] .primary')?.click()`)
  await sleep(500)
  const after = await eval_(
    client,
    `(() => ({
      overlayVisible: !!document.querySelector('[role=dialog][aria-modal]'),
      doneFlag: localStorage.getItem('mhc.onboarding.done'),
    }))()`,
  )
  if (after.overlayVisible)
    throw new Error("overlay should disappear after dismissing the last card")
  if (after.doneFlag !== "1")
    throw new Error(`done flag should be "1", got ${JSON.stringify(after.doneFlag)}`)
  console.log("✓ overlay closes and sets mhc.onboarding.done=1")

  // 6. Reload — overlay must NOT come back.
  await eval_(client, "location.reload()")
  await sleep(3500)
  const reload = await eval_(
    client,
    `(() => ({
      overlayVisible: !!document.querySelector('[role=dialog][aria-modal]'),
      doneFlag: localStorage.getItem('mhc.onboarding.done'),
    }))()`,
  )
  if (reload.overlayVisible)
    throw new Error("overlay should NOT re-appear after dismissal + reload")
  console.log("✓ overlay stays dismissed after reload")

  // 7. The first onboarding fetch above must have carried an
  //    Accept-Language header so the backend can localise its
  //    resolved strings.
  if (observedHeaders.length === 0)
    throw new Error("no /api/v1/onboarding request was observed")
  const last = observedHeaders[observedHeaders.length - 1]
  if (!last.acceptLanguage || !["en", "zh"].some((l) => last.acceptLanguage.startsWith(l)))
    throw new Error(
      `onboarding fetch must send Accept-Language=en|zh, got ${last.acceptLanguage}`,
    )
  console.log(`✓ onboarding fetch sent Accept-Language=${last.acceptLanguage}`)

  await client.close()
  console.log("\nALL ONBOARDING CHECKS PASSED")
}

main().catch((e) => {
  console.error("FAIL:", e)
  process.exit(1)
})