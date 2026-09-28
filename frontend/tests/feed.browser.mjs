import assert from "node:assert/strict";
import { test } from "node:test";
import { setTimeout as delay } from "node:timers/promises";
import { createServer } from "vite";

// Use PLAYWRIGHT_MODULE with an existing Playwright module and Chromium installation.
test("feed browser contracts", { skip: !process.env.PLAYWRIGHT_MODULE, timeout: 120_000 }, async (t) => {
  const { chromium } = await import(process.env.PLAYWRIGHT_MODULE);
  const entry = "\0feed-test.tsx";
  const server = await createServer({
    server: { host: "127.0.0.1", port: 0 },
    plugins: [{
      name: "feed-test",
      resolveId(id) { if (id === "virtual:feed-test.tsx" || id === "/virtual:feed-test.tsx") return entry; },
      load(id) {
        if (id !== entry) return;
        return `import React from "react";
          import { createRoot } from "react-dom/client";
          import App from "/src/App.tsx";
          import "/src/index.css";
          const root = createRoot(document.getElementById("root"));
          window.unmountFeed = () => root.unmount();
          root.render(React.createElement(React.StrictMode, null, React.createElement(App)));`;
      },
      configureServer(vite) {
        vite.middlewares.use(async (request, response, next) => {
          if (request.url !== "/feed-test") return next();
          response.setHeader("Content-Type", "text/html");
          response.end(await vite.transformIndexHtml("/feed-test", '<html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><div id="root"></div><script type="module" src="/virtual:feed-test.tsx"></script></body></html>'));
        });
      },
    }],
  });
  await server.listen();
  const browser = await chromium.launch({ headless: true });
  t.after(async () => { await browser.close(); await server.close(); });
  const address = server.httpServer.address();
  assert.equal(typeof address, "object");
  const origin = `http://127.0.0.1:${address.port}`;
  const card = (page, id) => page.locator(`[data-change-key="${id % 2 + 1}:release-${id}"]`);
  const desk = (page) => page.locator('[data-queue="desk"] article');
  const clear = (locator) => locator.getByRole("button", { name: "Clear from desk", exact: true }).filter({ visible: true });
  const idle = (page) => page.waitForFunction(() => ![...document.querySelectorAll('[role="status"]')].some((node) => node.textContent === "Save in progress..."));
  async function setup(mobile = false) {
    const page = await browser.newPage({ viewport: { width: mobile ? 390 : 1200, height: 900 }, isMobile: mobile, hasTouch: mobile });
    page.setDefaultTimeout(8000);
    const state = { owner: "github:123", calls: [], active: 0, maxActive: 0, failAt: 0, reads: 0, acknowledgements: 0,
      changes: Array.from({ length: 9 }, (_, index) => {
        const id = index + 1;
        return { id, external_id: `release-${id}`, source_id: id % 2 + 1, source_name: `Source ${id % 2 + 1}`, source_profile: "Work",
          plugin: "rss-atom", title: `Release ${id}`, url: `https://example.com/${id}`, summary: "Useful release details.", content: "",
          metadata: { retained: id }, published_at: `2026-09-${String(10 - id).padStart(2, "0")}T00:00:00Z`, fetched_at: "2026-09-09T00:00:00Z",
          dismissed: false, saved: false, note: id === 8 ? "Saved baseline" : "", state_updated_at: null };
      }) };
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.route("http://127.0.0.1:8000/**", async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      const method = request.method();
      const send = (value, status = 200) => route.fulfill({ status, contentType: "application/json", headers: {
        "Access-Control-Allow-Origin": origin, "Access-Control-Allow-Credentials": "true", "Access-Control-Allow-Headers": "*",
      }, body: status === 204 ? "" : JSON.stringify(value) });
      if (method === "OPTIONS") return send({}, 204);
      if (url.pathname === "/me") return send({ id: state.owner, authenticated: true, login: "tester", auth_enabled: true, anonymous_has_data: false });
      assert.equal(request.headers()["x-changelorg-owner"], state.owner);
      if (url.pathname === "/profiles") return send([{ name: "Work", source_count: 2 }, { name: "Other", source_count: 1 }]);
      if (["/sources", "/plugins", "/catalog"].includes(url.pathname) && method === "GET") return send([]);
      if (url.pathname === "/changes" && method === "GET") {
        state.reads++;
        return send(state.changes.filter((item) => item.source_profile === url.searchParams.get("profile") &&
          (!url.searchParams.has("source_id") || item.source_id === Number(url.searchParams.get("source_id")))));
      }
      assert.equal(method, "PATCH");
      assert.equal(request.headers()["x-changelorg-request"], "1");
      const id = Number(url.pathname.split("/")[2]);
      const body = request.postDataJSON();
      state.calls.push({ id, body, owner: state.owner });
      const number = state.calls.length;
      state.active++;
      state.maxActive = Math.max(state.maxActive, state.active);
      await delay(1500);
      state.active--;
      if (number === state.failAt) return send({ detail: "private error" }, 500);
      const index = state.changes.findIndex((item) => item.id === id);
      state.changes[index] = { ...state.changes[index], ...body, state_updated_at: "2026-09-09T01:00:00Z" };
      state.acknowledgements++;
      return send(state.changes[index]);
    });
    await page.goto(`${origin}/feed-test`);
    await card(page, 1).waitFor();
    t.after(async () => { await page.close(); assert.deepEqual(errors, []); });
    return { page, state };
  }

  async function position(page, id = 1) {
    await card(page, id).evaluate((element) => window.scrollBy(0, element.getBoundingClientRect().top - 120));
    return card(page, id).boundingBox();
  }

  await t.test("mobile controls use a full-width profile and readable window/order row", async () => {
    const { page } = await setup(true);
    for (const width of [320, 390]) {
      await page.setViewportSize({ width, height: 844 });
      const profile = await page.locator('#profile-select').boundingBox();
      const window = await page.locator('#window-select').boundingBox();
      const sort = await page.locator('#sort-select').boundingBox();
      assert.ok(profile.width > window.width * 1.8);
      assert.equal(window.y, sort.y);
      assert.ok(window.y > profile.y);
      assert.equal(await page.getByLabel('Current feed selection').count(), 0);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      const readingWidth = await card(page, 1).locator('.changelorg-rendered').evaluate((element) => element.getBoundingClientRect().width);
      assert.ok(readingWidth > width * 0.78, "Article text uses most of the mobile screen width");
      if (process.env.SCREENSHOT_DIR) await page.screenshot({ path: `${process.env.SCREENSHOT_DIR}/compact-panel-${width}.png` });
    }
  });

  await t.test("account actions stay in the GitHub menu and count buttons open management views", async () => {
    const { page } = await setup(true);
    await page.evaluate(() => { window.haptics = []; Object.defineProperty(navigator, 'vibrate', { configurable: true, value: (duration) => { window.haptics.push(duration); return true; } }); });
    assert.equal(await page.getByText("Your personal change newspaper", { exact: true }).count(), 0);
    assert.equal(await page.getByRole("link", { name: "Sign out" }).isVisible(), false);
    await page.getByLabel("GitHub account menu").click();
    assert.deepEqual(await page.evaluate(() => window.haptics), [10]);
    assert.equal(await page.getByRole("link", { name: "Sign out" }).isVisible(), true);
    await page.keyboard.press("Escape");
    assert.equal(await page.getByRole("link", { name: "Sign out" }).isVisible(), false);
    await page.getByRole("button", { name: "Manage sources and profiles" }).click();
    await page.getByRole("dialog", { name: "Sources & profiles" }).waitFor();
    await page.getByRole("dialog", { name: "Sources & profiles" }).getByRole("button", { name: "Close", exact: true }).click();
    await page.getByRole("button", { name: "View cleared items" }).click();
    await page.getByText("No cleared items in this view.", { exact: true }).waitFor();
    await page.getByRole("dialog", { name: "Cleared items" }).getByRole("button", { name: "Close", exact: true }).click();
  });

  await t.test("fixed mouse position advances three cards before acknowledgement; Undo waits for the last save", async () => {
    const { page, state } = await setup();
    const top = await position(page);
    const button = await clear(card(page, 1)).boundingBox();
    const metrics = [];
    for (let id = 1; id <= 3; id++) {
      const start = performance.now();
      const before = await card(page, id + 1).boundingBox();
      await page.mouse.click(button.x + button.width / 2, button.y + button.height / 2);
      await delay(80);
      const during = await card(page, id + 1).boundingBox();
      assert.ok(during.y < before.y - 10, "The next card moves during the outgoing animation");
      assert.ok(during.y > top.y + 5, "The next card approaches rather than jumping to its final position");
      await card(page, id).waitFor({ state: "detached", timeout: 340 });
      const next = await card(page, id + 1).boundingBox();
      metrics.push({ milliseconds: Math.round(performance.now() - start), topDelta: next.y - top.y, xDelta: next.x - top.x });
      assert.ok(Math.abs(next.y - top.y) <= 2, JSON.stringify(metrics));
      assert.equal(next.x, top.x);
      assert.equal(await clear(card(page, id + 1)).isEnabled(), true);
      assert.equal(await card(page, id + 1).evaluate((element) => element === document.activeElement), true);
    }
    assert.equal(state.acknowledgements, 0);
    assert.equal(state.calls.length, 1);
    await page.getByRole("button", { name: "View cleared items", exact: true }).click();
    assert.equal(await page.getByRole("button", { name: "Restore all shown", exact: true }).isDisabled(), true);
    await page.getByRole("dialog", { name: "Cleared items" }).getByRole("button", { name: "Close", exact: true }).click();
    await page.keyboard.press("z");
    assert.equal(state.calls.length, 1);
    await idle(page);
    assert.deepEqual(state.calls.map((call) => call.id), [1, 2, 3]);
    assert.equal(state.maxActive, 1);
    assert.ok(Math.abs((await card(page, 4).boundingBox()).y - top.y) <= 2);
    await page.getByRole("button", { name: "Undo", exact: true }).click();
    await idle(page);
    assert.deepEqual(state.calls.at(-1), { id: 3, body: { dismissed: false, saved: false, note: "" }, owner: "github:123" });
    assert.equal(await card(page, 1).count(), 0);
    assert.equal(await card(page, 3).count(), 1);
    t.diagnostic(`Desktop, 1500 ms PATCH delay: ${JSON.stringify(metrics)}; maximum active PATCH requests: ${state.maxActive}`);
  });

  for (const failAt of [1, 2]) await t.test(`failure ${failAt} rolls back only failed and unsent jobs; reload never replays`, async () => {
    const { page, state } = await setup();
    state.failAt = failAt;
    await card(page, 8).getByRole("button", { name: "Expand note editor" }).click();
    await card(page, 8).getByRole("textbox").fill("Keep this draft");
    await card(page, 1).focus();
    // Two synchronous events target the same render and must enqueue only one write.
    await page.evaluate(() => { for (let i = 0; i < 2; i++) window.dispatchEvent(new KeyboardEvent("keydown", { key: "x" })); });
    await card(page, 1).waitFor({ state: "detached" });
    await page.keyboard.press("x");
    await card(page, 2).waitFor({ state: "detached" });
    await page.keyboard.press("x");
    await card(page, 3).waitFor({ state: "detached" });
    await idle(page);
    assert.equal(state.calls.length, failAt);
    assert.equal(state.maxActive, 1);
    assert.equal(await desk(page).count(), failAt === 1 ? 9 : 8);
    assert.equal(await card(page, 1).count(), failAt === 1 ? 1 : 0);
    assert.equal(await card(page, 8).getByRole("textbox").inputValue(), "Keep this draft");
    assert.equal(await clear(card(page, 2)).isDisabled(), true);
    assert.equal((await page.getByRole("alert").textContent()).includes("private error"), false);
    const reads = state.reads;
    await page.getByRole("button", { name: "Reload desk from server" }).click();
    await clear(card(page, 2)).waitFor();
    assert.ok(state.reads > reads);
    assert.equal(state.calls.length, failAt);
    assert.equal(await clear(card(page, 2)).isEnabled(), true);
    assert.equal(await card(page, 8).getByRole("textbox").inputValue(), "Keep this draft");
  });

  await t.test("touch flicks advance before save and retain the next card position", async () => {
    const { page, state } = await setup(true);
    const top = await position(page);
    const session = await page.context().newCDPSession(page);
    const metrics = [];
    for (let id = 1; id <= 2; id++) {
      const point = { x: top.x + 250, y: top.y + 100 };
      await session.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [point] });
      await session.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ ...point, x: point.x - 70 }] });
      const start = performance.now();
      await session.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
      await card(page, id).waitFor({ state: "detached", timeout: 340 });
      const next = await card(page, id + 1).boundingBox();
      assert.ok(Math.abs(next.y - top.y) <= 2);
      metrics.push({ milliseconds: Math.round(performance.now() - start), topDelta: next.y - top.y });
    }
    assert.equal(state.acknowledgements, 0);
    await idle(page);
    assert.equal(state.maxActive, 1);
    t.diagnostic(`Touch, 1500 ms PATCH delay: ${JSON.stringify(metrics)}`);
  });

  await t.test("shelf, read, back to desk, and note-shelf anchor at the optimistic commit", async () => {
    const { page, state } = await setup();
    let top = await position(page);
    await card(page, 1).getByRole("button", { name: "Shelf", exact: true }).click();
    await page.locator('[data-queue="shelf"] article').waitFor();
    assert.ok(Math.abs((await card(page, 2).boundingBox()).y - top.y) <= 2);
    assert.equal(await card(page, 1).getAttribute("aria-busy"), "true");
    await card(page, 2).getByRole("button", { name: "Expand note editor" }).click();
    await card(page, 2).getByRole("textbox").fill("Submitted note");
    top = await card(page, 2).boundingBox();
    await card(page, 2).getByRole("textbox").press("Control+Enter");
    await page.waitForFunction(() => document.querySelectorAll('[data-queue="shelf"] article').length === 2);
    assert.ok(Math.abs((await card(page, 3).boundingBox()).y - top.y) <= 2);
    await idle(page);
    assert.deepEqual(state.calls.map((call) => call.body), [{ saved: true }, { saved: true, note: "Submitted note" }]);
    top = await position(page);
    await card(page, 1).getByRole("button", { name: "Mark read", exact: true }).filter({ visible: true }).click();
    await card(page, 1).waitFor({ state: "detached", timeout: 340 });
    assert.ok(Math.abs((await card(page, 2).boundingBox()).y - top.y) <= 2);
    top = await card(page, 2).boundingBox();
    await card(page, 2).getByRole("button", { name: "Back to desk", exact: true }).click();
    await page.waitForFunction(() => document.querySelectorAll('[data-queue="shelf"] article').length === 0);
    assert.ok(Math.abs((await card(page, 3).boundingBox()).y - top.y) <= 2);
    await idle(page);
    assert.deepEqual(state.calls.at(-1).body, { saved: false, note: "" });
  });

  await t.test("other-card notes serialize; new scope and manual overrides survive queued responses", async () => {
    const { page, state } = await setup();
    await card(page, 8).getByRole("button", { name: "Expand note editor" }).click();
    await card(page, 8).getByRole("textbox").fill("Temporary edit");
    await card(page, 8).getByRole("textbox").fill("Saved baseline");
    await card(page, 1).focus();
    await page.keyboard.press("x");
    await card(page, 2).getByRole("button", { name: "Expand note editor" }).click();
    await card(page, 2).getByRole("textbox").fill("Queued note");
    await card(page, 2).getByRole("textbox").press("Control+Enter");
    await card(page, 8).getByRole("button", { name: "Source 1", exact: true }).click();
    assert.equal(await card(page, 1).count(), 0);
    state.changes[7].note = "New server baseline";
    await idle(page);
    await card(page, 8).waitFor();
    assert.equal(await card(page, 8).getByRole("textbox").inputValue(), "Saved baseline");
    assert.equal(await card(page, 1).count(), 0);
    assert.deepEqual(state.calls.map((call) => call.id), [1, 2]);
    assert.equal(state.maxActive, 1);
    assert.equal(state.changes[1].note, "Queued note");
    await page.getByRole("button", { name: "Show all sources" }).click();
    await card(page, 3).waitFor();
    assert.equal(await card(page, 8).getByRole("textbox").inputValue(), "Saved baseline");
    await card(page, 2).getByRole("textbox").fill("Latest note");
    await card(page, 2).getByRole("textbox").press("Control+Enter");
    await card(page, 3).getByRole("button", { name: "Source 2", exact: true }).click();
    await card(page, 3).waitFor();
    await page.getByRole("button", { name: "Show all sources" }).click();
    await card(page, 2).waitFor();
    assert.equal(state.changes[1].note, "Latest note");
    assert.equal(await card(page, 2).getByRole("textbox").inputValue(), "Latest note");
  });

  for (const transition of ["account", "unmount", "profile", "window"]) await t.test(`${transition} during queued writes cannot inject an old view or send under a new owner`, async () => {
    const { page, state } = await setup();
    await card(page, 1).focus();
    await page.keyboard.press("x");
    await card(page, 1).waitFor({ state: "detached" });
    await page.keyboard.press("x");
    await card(page, 2).waitFor({ state: "detached" });
    if (transition === "account") {
      state.owner = "github:456";
      await page.evaluate(() => window.dispatchEvent(new Event("changelorg:identity-invalidated")));
      await card(page, 1).waitFor();
    } else if (transition === "unmount") await page.evaluate(() => window.unmountFeed());
    else if (transition === "profile") await page.locator("#profile-select").selectOption("Other");
    else await page.locator("#window-select").selectOption("30d");
    await delay(3300);
    assert.equal(state.calls.length, ["account", "unmount"].includes(transition) ? 1 : 2);
    assert.ok(state.calls.every((call) => call.owner === "github:123"));
    if (transition === "account") assert.equal(await card(page, 1).count(), 1);
    if (transition === "unmount" || transition === "profile") assert.equal(await page.locator("article").count(), 0);
    if (transition === "window") {
      assert.equal(await card(page, 1).count(), 0);
      assert.equal(await desk(page).count(), 7);
    }
  });
});
