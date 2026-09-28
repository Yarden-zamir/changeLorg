import assert from "node:assert/strict";
import { test } from "node:test";
import { createServer } from "vite";

// Use an existing Playwright installation: PLAYWRIGHT_MODULE=/absolute/path/to/playwright/index.mjs node --test tests/sourceEditor.browser.mjs
test("source editor browser contracts", { skip: !process.env.PLAYWRIGHT_MODULE, timeout: 120_000 }, async (t) => {
  const { chromium } = await import(process.env.PLAYWRIGHT_MODULE);
  const entry = "\0editor-test.tsx";
  const server = await createServer({
    server: { host: "127.0.0.1", port: 0 },
    plugins: [{
      name: "editor-test",
      resolveId(id) { if (id === "virtual:editor-test.tsx" || id === "/virtual:editor-test.tsx") return entry; },
      load(id) {
        if (id !== entry) return;
        return `import React, { useState } from "react";
          import { createRoot } from "react-dom/client";
          import { SourceEditor } from "/src/components/SourceEditor.tsx";
          import { api, getIdentity } from "/src/lib/api.ts";
          import "/src/index.css";
          const identity = await getIdentity();
          const initial = await api("/profiles", { owner: identity.id });
          function Harness() {
            const [profiles, setProfiles] = useState(initial);
            const [editorOwner, setEditorOwner] = useState(identity.id);
            window.changeEditorOwner = () => setEditorOwner("github:456");
            return React.createElement(SourceEditor, { owner: editorOwner, profiles,
              onChanged: async () => {
                if (window.failSync) throw new Error("sync failed");
                setProfiles(await api("/profiles", { owner: identity.id }));
              },
              onClose: () => { window.closedEditor = true; window.editorCloseCount = (window.editorCloseCount ?? 0) + 1; },
              onViewSource: (source) => { window.viewedSource = source; } });
          }
          const root = createRoot(document.getElementById("root"));
          window.unmountEditor = () => root.unmount();
          root.render(React.createElement(Harness));`;
      },
      configureServer(vite) {
        vite.middlewares.use(async (request, response, next) => {
          if (request.url !== "/editor-test") return next();
          response.setHeader("Content-Type", "text/html");
          response.end(await vite.transformIndexHtml("/editor-test", '<html><head></head><body><div id="root"></div><script type="module" src="/virtual:editor-test.tsx"></script></body></html>'));
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
  const owner = "github:123";
  const window = { start: "2026-08-01T00:00:00Z", end: "2026-08-31T00:00:00Z" };
  const candidate = { name: "Discovered feed", plugin: "rss-atom", enabled: true, config: { url: "https://example.com/feed", custom: { keep: true } } };
  const source = (id, profile, config = {}) => ({ ...candidate, id, name: `Saved ${id}`, owner_id: owner, created_at: window.start, updated_at: window.end, config: { ...candidate.config, profile, ...config } });
  const plugins = ["rss-atom", "html-news", "x"].map((key) => ({ key, name: key, description: "", config_schema: { properties: { enrichment_profile: { enum: ["custom"] } } } }));

  async function setup(width = 1200) {
    const page = await browser.newPage({ viewport: { width, height: width === 320 ? 640 : width === 390 ? 844 : 900 } });
    page.setDefaultTimeout(5000);
    const state = { sources: [source(1, "Work"), source(2, "Research", { include_any: ["stable"] })], profiles: [{ name: "Work", source_count: 1 }, { name: "Research", source_count: 1 }], calls: [], refreshFailure: false, refreshErrors: false, saveFailure: false, loadFailure: false, candidates: [candidate], prompts: [], holdPath: "", release: null };
    page.on("dialog", async (dialog) => { state.prompts.push(dialog.message()); await dialog.dismiss(); });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.route("http://127.0.0.1:8000/**", async (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      const method = request.method();
      const body = request.postDataJSON();
      if (method !== "OPTIONS") state.calls.push({ path, method, body, headers: request.headers() });
      if (state.holdPath === path) await new Promise((resolve) => { state.release = resolve; });
      const send = (value, status = 200) => route.fulfill({ status, contentType: "application/json", headers: { "Access-Control-Allow-Origin": origin, "Access-Control-Allow-Credentials": "true", "Access-Control-Allow-Headers": "*" }, body: status === 204 ? "" : JSON.stringify(value) });
      if (method === "OPTIONS") return send({}, 204);
      if (path === "/me") return send({ id: owner, authenticated: true, login: "tester", auth_enabled: true, anonymous_has_data: false });
      assert.equal(request.headers()["x-changelorg-owner"], owner);
      if (method !== "GET") assert.equal(request.headers()["x-changelorg-request"], "1");
      if (path === "/plugins") return send(plugins);
      if (path === "/catalog") return send([candidate]);
      if (path === "/sources/discover") return send(state.candidates);
      if (path === "/sources/preview") return state.previewUnavailable ? send({}, 424) : send({ window, changes: [{ title: "Release preview", url: "https://example.com/release", published_at: window.end, external_id: "release", summary: "A useful release", content: "<p>Safe preview</p><script>window.unsafePreview = true</script>" }] });
      if (path === "/sources" && method === "GET") return send(state.loadFailure ? {} : state.sources, state.loadFailure ? 500 : 200);
      if (path === "/sources" && method === "POST") {
        if (state.saveFailure) return send({}, 500);
        const saved = { ...source(state.sources.length + 1, body.config.profile), ...body };
        state.sources.push(saved);
        return send(saved, 201);
      }
      if (path === "/profiles" && method === "GET") return send(state.profiles);
      if (path === "/profiles" && method === "POST") { state.profiles.push({ name: body.name, source_count: 0 }); return send({}, 204); }
      if (path.startsWith("/profiles/")) {
        const name = decodeURIComponent(path.slice("/profiles/".length));
        if (method === "PATCH") {
          state.profiles = state.profiles.map((profile) => profile.name === name ? { ...profile, name: body.name } : profile);
          state.sources = state.sources.map((saved) => saved.config.profile === name ? { ...saved, config: { ...saved.config, profile: body.name } } : saved);
        } else {
          state.profiles = state.profiles.filter((profile) => profile.name !== name);
          state.sources = state.sources.filter((saved) => saved.config.profile !== name);
        }
        return send({}, 204);
      }
      const id = Number(path.split("/")[2]);
      if (path.endsWith("/refresh")) return send(state.refreshFailure ? {} : { window, changes: [], errors: state.refreshErrors ? [{ source_id: id, source_name: "Private", plugin: "rss-atom", message: "secret error" }] : [] }, state.refreshFailure ? 502 : 200);
      if (method === "PATCH") {
        const index = state.sources.findIndex((saved) => saved.id === id);
        state.sources[index] = { ...state.sources[index], ...body };
        return send(state.sources[index]);
      }
      if (method === "DELETE") { state.sources = state.sources.filter((saved) => saved.id !== id); return send({}, 204); }
      throw new Error(`Unexpected request: ${method} ${path}`);
    });
    await page.goto(`${origin}/editor-test`);
    await page.getByRole("button", { name: "Close", exact: true }).waitFor();
    await page.waitForFunction(() => !document.querySelector("#source-discovery input").readOnly);
    assert.deepEqual(errors, []);
    t.after(async () => { state.release?.(); assert.deepEqual(state.prompts, [], "Editor actions must not open browser dialogs"); await page.close(); });
    return { page, state, errors };
  }
  const count = (state, path, method = "POST") => state.calls.filter((call) => call.path === path && call.method === method).length;
  async function discover(page) {
    await page.getByLabel("Find a source").fill("owner/repository");
    await page.getByRole("button", { name: "Discover owner/repository", exact: true }).click();
    await page.getByRole("region", { name: "Source preview", exact: true }).waitFor();
  }
  async function idle(page) { await page.waitForFunction(() => !document.querySelector("#source-discovery input").readOnly); }
  const confirmation = (page) => page.getByRole("dialog", { name: "Discard unsaved changes?", exact: true });
  const sourceEditor = (page) => page.getByRole("dialog", { name: "Sources & profiles", exact: true });
  async function unloadProtected(page) {
    return page.evaluate(() => {
      const event = new Event("beforeunload", { cancelable: true });
      window.dispatchEvent(event);
      return event.defaultPrevented;
    });
  }

  await t.test("X discovery selects the keyless handler and saves a subscription", async () => {
    const { page, state } = await setup();
    state.candidates = [{ name: "@example on X", plugin: "x", enabled: true, config: { url: "https://x.com/example" } }];
    await page.getByLabel("Find a source").fill("https://x.com/example/status/123");
    await page.getByLabel("Find a source").press("Enter");
    await idle(page);
    assert.equal(state.calls.find((call) => call.path === "/sources/preview").body.plugin, "x");
    await page.getByLabel("Save in profile").selectOption("Work");
    await page.getByRole("button", { name: "Save only", exact: true }).click();
    await idle(page);
    assert.equal(state.sources.at(-1).plugin, "x");
    assert.equal(state.sources.at(-1).config.url, "https://x.com/example");
    assert.equal(state.sources.at(-1).config.profile, "Work");
  });

  await t.test("unavailable keyless X feed reports an error without saving or requesting keys", async () => {
    const { page, state } = await setup();
    state.previewUnavailable = true;
    state.candidates = [{ name: "@example on X", plugin: "x", enabled: true, config: { url: "https://x.com/example" } }];
    await page.getByLabel("Find a source").fill("https://x.com/example");
    await page.getByLabel("Find a source").press("Enter");
    await idle(page);
    assert.match(await page.getByRole('alert').first().textContent(), /keyless public timeline.*unavailable/);
    assert.equal(await page.getByRole('button', { name: 'Save only', exact: true }).isDisabled(), true);
    assert.equal(count(state, '/sources'), 0);
  });

  await t.test("profile-free discovery, metadata retention, inline profile, Save only, and fresh draft focus", async () => {
    const { page, state } = await setup();
    assert.equal(await page.getByLabel("Find a source").evaluate((input) => input === document.activeElement), true);
    await discover(page);
    assert.equal("profile" in state.calls.find((call) => call.path === "/sources/preview").body.config, false);
    await page.getByLabel("Name", { exact: true }).fill("My feed");
    await page.getByLabel("Save in profile").selectOption("Work");
    await page.getByRole("button", { name: "Discovered feed", exact: false }).click();
    assert.equal(await page.getByLabel("Name", { exact: true }).inputValue(), "My feed");
    assert.equal(state.prompts.length, 0);
    await page.getByRole("button", { name: "New profile", exact: true }).click();
    await page.getByLabel("New profile name", { exact: true }).fill("Inline");
    await page.getByRole("button", { name: "Create and select", exact: true }).click();
    await idle(page);
    assert.equal(await page.getByLabel("Save in profile").inputValue(), "Inline");
    assert.equal(count(state, "/sources/preview"), 1);
    assert.equal(await page.getByRole("region", { name: "Source preview", exact: true }).count(), 1);
    assert.equal(await page.evaluate(() => window.unsafePreview), undefined);
    await page.getByRole("button", { name: "Save only", exact: true }).click();
    await idle(page);
    assert.equal(count(state, "/sources"), 1);
    assert.equal(state.calls.filter((call) => call.path.endsWith("/refresh")).length, 0);
    assert.deepEqual(state.sources[2].config.custom, { keep: true });
    await page.getByRole("button", { name: "Add another", exact: true }).click();
    assert.equal(await page.getByLabel("Find a source").inputValue(), "");
    assert.equal(await page.getByLabel("Find a source").evaluate((input) => input === document.activeElement), true);
    assert.equal(state.prompts.length, 0);
    await discover(page);
    assert.equal(await page.getByLabel("Save in profile").inputValue(), "");
  });

  await t.test("save and fetch writes once; refresh failures only retry refresh, and sync failure stays separate", async () => {
    const { page, state } = await setup();
    await discover(page);
    await page.getByLabel("Save in profile").selectOption("Work");
    state.refreshFailure = true;
    await page.getByRole("button", { name: "Save and fetch", exact: true }).click();
    await idle(page);
    assert.equal(count(state, "/sources"), 1);
    assert.equal(count(state, "/sources/3/refresh"), 1);
    assert.equal(await page.getByRole("alert").first().textContent().then((text) => text.includes("Saved; fetch failed")), true);
    assert.equal(await page.getByRole("button", { name: "Save and fetch", exact: true }).isDisabled(), true);
    state.refreshFailure = false;
    await page.getByRole("button", { name: "Retry Fetch latest", exact: true }).click();
    await idle(page);
    assert.equal(count(state, "/sources"), 1);
    assert.equal(count(state, "/sources/3/refresh"), 2);
    await page.getByRole("button", { name: "View source", exact: true }).click();
    assert.equal(await page.evaluate(() => window.viewedSource.id), 3);
    await page.getByLabel("Name", { exact: true }).fill("Renamed");
    await page.evaluate(() => { window.failSync = true; });
    await page.getByRole("button", { name: "Save and fetch", exact: true }).click();
    await idle(page);
    assert.equal(count(state, "/sources/3", "PATCH"), 1);
    assert.equal(count(state, "/sources/3/refresh"), 3);
    assert.equal(await page.getByRole("button", { name: "Retry sync only", exact: true }).count(), 1);
    await page.evaluate(() => { window.failSync = false; });
    await page.getByRole("button", { name: "Retry sync only", exact: true }).click();
    await idle(page);
    assert.equal(count(state, "/sources/3", "PATCH"), 1);
    assert.equal(count(state, "/sources/3/refresh"), 3);
  });

  await t.test("configuration invalidates preview; same selection retains edits and other sources require discard", async () => {
    const { page, state } = await setup();
    await page.getByRole("button", { name: "Saved 2", exact: false }).click();
    assert.equal(await page.getByLabel("Include any term").isVisible(), true);
    await page.getByRole("button", { name: "Preview last 30 days", exact: true }).click();
    await idle(page);
    await page.getByLabel("Name", { exact: true }).fill("Unsaved label");
    await page.getByRole("button", { name: "Saved 2", exact: false }).click();
    assert.equal(state.prompts.length, 0);
    assert.equal(await page.getByLabel("Name", { exact: true }).inputValue(), "Unsaved label");
    await page.getByRole("button", { name: "Saved 1", exact: false }).click();
    assert.equal(await confirmation(page).isVisible(), true);
    await confirmation(page).getByRole("button", { name: "Keep editing", exact: true }).click();
    assert.equal(state.prompts.length, 0);
    assert.equal(await page.getByLabel("Name", { exact: true }).inputValue(), "Unsaved label");
    await page.getByLabel("Include any term").fill("beta");
    assert.equal(await page.getByRole("region", { name: "Source preview", exact: true }).count(), 0);
    assert.equal(await page.getByRole("button", { name: "Save only", exact: true }).isDisabled(), true);
    await page.getByRole("button", { name: "Preview last 30 days", exact: true }).click();
    await idle(page);
    await page.getByText("Advanced / manual handler, URL & enrichment", { exact: true }).click();
    await page.getByLabel("Enrichment profile").selectOption("custom");
    assert.equal(await page.getByRole("region", { name: "Source preview", exact: true }).count(), 0);
  });

  await t.test("ambiguous save failures never auto-replay; source errors and failed loads preserve saved identity", async () => {
    const { page, state } = await setup();
    await discover(page);
    await page.getByLabel("Save in profile").selectOption("Work");
    state.saveFailure = true;
    await page.getByRole("button", { name: "Save and fetch", exact: true }).click();
    await idle(page);
    assert.equal(count(state, "/sources"), 1);
    assert.equal(state.calls.filter((call) => call.path.endsWith("/refresh")).length, 0);
    assert.equal(await page.getByRole("button", { name: "Add another", exact: true }).count(), 0);
    state.saveFailure = false;
    state.refreshErrors = true;
    state.loadFailure = true;
    await page.getByRole("button", { name: "Save and fetch", exact: true }).click();
    await idle(page);
    assert.equal(count(state, "/sources"), 2);
    assert.equal(count(state, "/sources/3/refresh"), 1);
    assert.equal(await page.getByRole("button", { name: "Retry Fetch latest", exact: true }).isEnabled(), true);
    assert.equal(await page.getByRole("button", { name: "Save and fetch", exact: true }).isDisabled(), true);
    assert.equal((await sourceEditor(page).textContent()).includes("secret error"), false);
    state.loadFailure = false;
    await page.getByRole("button", { name: "Retry sync only", exact: true }).click();
    await idle(page);
    assert.equal(count(state, "/sources"), 2);
    assert.equal(count(state, "/sources/3/refresh"), 1);
  });

  await t.test("profile rename preserves clean state, preview, and unrelated unsaved changes", async () => {
    const { page, state } = await setup();
    await page.getByRole("button", { name: "Saved 1", exact: false }).click();
    await page.getByRole("button", { name: "Preview last 30 days", exact: true }).click();
    await idle(page);
    await page.getByText("Manage profiles", { exact: true }).click();
    await page.getByLabel("Existing profile").selectOption("Work");
    await page.getByLabel("New name", { exact: true }).fill("Renamed profile");
    await page.getByRole("button", { name: "Rename", exact: true }).click();
    await idle(page);
    assert.equal(await page.getByLabel("Save in profile").inputValue(), "Renamed profile");
    assert.equal(await page.getByRole("button", { name: "Fetch latest", exact: true }).isEnabled(), true);
    assert.equal(await page.getByRole("region", { name: "Source preview", exact: true }).count(), 1);
    await page.getByLabel("Name", { exact: true }).fill("Unrelated unsaved name");
    await page.getByLabel("New name", { exact: true }).fill("Renamed again");
    await page.getByRole("button", { name: "Rename", exact: true }).click();
    await idle(page);
    assert.equal(await page.getByLabel("Name", { exact: true }).inputValue(), "Unrelated unsaved name");
    assert.equal(await page.getByLabel("Save in profile").inputValue(), "Renamed again");
    assert.equal(await page.getByRole("button", { name: "Save only", exact: true }).isEnabled(), true);
    assert.equal(count(state, "/sources/preview"), 1);
  });

  await t.test("disable, duplicate, and cascade delete remain explicit", async () => {
    const { page, state } = await setup();
    await page.getByRole("button", { name: "Saved 1", exact: false }).click();
    await page.getByRole("button", { name: "Preview last 30 days", exact: true }).click();
    await idle(page);
    assert.equal(await page.getByLabel("Enabled", { exact: true }).count(), 0);
    await page.getByRole("button", { name: "Disable source", exact: true }).click();
    await page.getByRole("dialog", { name: "Disable source?", exact: true }).getByRole("button", { name: "Disable source", exact: true }).click();
    await idle(page);
    assert.equal(await page.getByRole("region", { name: "Source preview", exact: true }).count(), 1);
    assert.equal(await page.getByRole("button", { name: "Fetch latest", exact: true }).isDisabled(), true);
    await page.getByRole("button", { name: "Duplicate draft", exact: true }).click();
    assert.equal(await page.getByLabel("Enabled", { exact: true }).isChecked(), false);
    await page.getByRole("button", { name: "Save only (disabled)", exact: true }).click();
    await idle(page);
    assert.equal(count(state, "/sources"), 1);
    assert.equal(state.sources.length, 3);
    assert.equal(state.calls.filter((call) => call.path.endsWith("/refresh")).length, 0);
    await page.getByRole("button", { name: "Delete source", exact: true }).click();
    const deletion = page.getByRole("dialog", { name: "Delete source?", exact: true });
    assert.equal((await deletion.textContent()).includes("shelf items, and notes"), true);
    await deletion.getByRole("button", { name: "Delete source", exact: true }).click();
    await idle(page);
    assert.equal(state.sources.length, 2);
    assert.equal(state.prompts.length, 0);
  });

  await t.test("independent profile create and delete preserve an unrelated source draft", async () => {
    const { page, state } = await setup();
    await discover(page);
    await page.getByLabel("Name", { exact: true }).fill("Unsaved name");
    await page.getByText("Manage profiles", { exact: true }).click();
    await page.getByLabel("New profile", { exact: true }).fill("Independent");
    await page.getByRole("button", { name: "Create profile", exact: true }).click();
    await idle(page);
    assert.equal(await page.getByLabel("Save in profile").inputValue(), "");
    await page.getByLabel("Existing profile").selectOption("Independent");
    await page.getByRole("button", { name: "Delete", exact: true }).click();
    await page.getByRole("dialog", { name: "Delete profile?", exact: true }).getByRole("button", { name: "Delete profile", exact: true }).click();
    await idle(page);
    assert.equal(state.profiles.some((profile) => profile.name === "Independent"), false);
    assert.equal(await page.getByLabel("Name", { exact: true }).inputValue(), "Unsaved name");
    assert.equal(await page.getByRole("region", { name: "Source preview", exact: true }).count(), 1);
  });

  await t.test("manual HTML fallback retains explicit prefix and limit validation", async () => {
    const { page, state } = await setup();
    state.candidates = [];
    await page.getByLabel("Find a source").fill("example.com/news");
    await page.getByRole("button", { name: "Discover example.com/news", exact: true }).click();
    await idle(page);
    await page.getByRole("button", { name: "Use manual HTML news", exact: false }).click();
    await page.getByLabel("Name", { exact: true }).fill("Manual news");
    await page.getByRole("button", { name: "Use manual HTML news", exact: false }).click();
    assert.equal(state.prompts.length, 0);
    assert.equal(await page.getByLabel("Name", { exact: true }).inputValue(), "Manual news");
    await page.getByRole("button", { name: "Preview last 30 days", exact: true }).click();
    await idle(page);
    assert.equal(count(state, "/sources/preview"), 0);
    await page.getByLabel("Article path prefix", { exact: true }).fill("/news/");
    await page.getByLabel("Article limit", { exact: true }).fill("101");
    await page.getByRole("button", { name: "Preview last 30 days", exact: true }).click();
    await idle(page);
    assert.equal(count(state, "/sources/preview"), 0);
    await page.getByLabel("Article limit", { exact: true }).fill("12");
    await page.getByRole("button", { name: "Preview last 30 days", exact: true }).click();
    await idle(page);
    const body = state.calls.find((call) => call.path === "/sources/preview").body;
    assert.equal(body.plugin, "html-news");
    assert.equal(body.config.article_path_prefix, "/news/");
    assert.equal(body.config.limit, 12);
    assert.equal("include_any" in body.config, false);
    await page.getByLabel("Source URL", { exact: true }).fill("https://example.com/other");
    assert.equal(await page.getByRole("region", { name: "Source preview", exact: true }).count(), 0);
  });

  await t.test("untouched candidates can switch and close without discard or unload warnings", async () => {
    const { page } = await setup();
    await page.getByRole("button", { name: "Discovered feed", exact: false }).click();
    await idle(page);
    assert.equal(await unloadProtected(page), false);
    await page.getByRole("button", { name: "Saved 1", exact: false }).click();
    assert.equal(await confirmation(page).count(), 0);
    assert.equal(await page.getByLabel("Name", { exact: true }).inputValue(), "Saved 1");
    await discover(page);
    assert.equal(await unloadProtected(page), false);
    await page.getByRole("button", { name: "Close", exact: true }).click();
    assert.equal(await confirmation(page).count(), 0);
    assert.equal(await page.evaluate(() => window.editorCloseCount), 1);
  });

  await t.test("custom discard defaults to safety, traps focus, restores focus, and consumes its action once", async () => {
    const { page, state } = await setup();
    await page.getByRole("button", { name: "Saved 2", exact: false }).click();
    await page.getByRole("button", { name: "Preview last 30 days", exact: true }).click();
    await idle(page);
    await page.getByLabel("Name", { exact: true }).fill("Uncommitted source");
    const otherSource = page.getByRole("button", { name: "Saved 1", exact: false });
    const callsBefore = state.calls.filter((call) => call.method !== "GET").length;
    await otherSource.click();
    const keep = confirmation(page).getByRole("button", { name: "Keep editing", exact: true });
    assert.equal(await keep.evaluate((button) => document.activeElement === button), true);
    assert.equal((await confirmation(page).textContent()).includes("Uncommitted source"), true);
    for (let step = 0; step < 3; step++) {
      await page.keyboard.press("Tab");
      assert.equal(await confirmation(page).evaluate((dialog) => dialog.contains(document.activeElement)), true);
    }
    await page.keyboard.press("Escape");
    assert.equal(await confirmation(page).count(), 0);
    assert.equal(await page.evaluate(() => window.closedEditor), undefined);
    assert.equal(await otherSource.evaluate((button) => document.activeElement === button), true);
    assert.equal(await page.getByRole("region", { name: "Source preview", exact: true }).count(), 1);
    await otherSource.click();
    await keep.click();
    assert.equal(await otherSource.evaluate((button) => document.activeElement === button), true);
    assert.equal(await page.getByLabel("Name", { exact: true }).inputValue(), "Uncommitted source");
    assert.equal(state.calls.filter((call) => call.method !== "GET").length, callsBefore);
    await page.getByRole("button", { name: "Close", exact: true }).click();
    await confirmation(page).getByRole("button", { name: "Discard changes", exact: true }).evaluate((button) => { button.click(); button.click(); });
    assert.equal(await page.evaluate(() => window.editorCloseCount), 1);
    assert.equal(state.calls.filter((call) => call.method !== "GET").length, callsBefore);
  });

  await t.test("reverted metadata, filters, URL whitespace, and enabled state need no discard", async () => {
    const { page } = await setup();
    await discover(page);
    await page.getByLabel("Name", { exact: true }).fill("Changed");
    assert.equal(await unloadProtected(page), true);
    await page.getByLabel("Name", { exact: true }).fill(" Discovered feed ");
    await page.getByLabel("Save in profile").selectOption("Work");
    assert.equal(await unloadProtected(page), true);
    await page.getByLabel("Save in profile").selectOption("");
    await page.getByLabel("Enabled", { exact: true }).uncheck();
    assert.equal(await unloadProtected(page), true);
    await page.getByLabel("Enabled", { exact: true }).check();
    await page.getByText("Filters / None", { exact: true }).click();
    await page.getByLabel("Include any term").fill("beta");
    assert.equal(await unloadProtected(page), true);
    await page.getByLabel("Include any term").fill(" \n ");
    await page.getByText("Advanced / manual handler, URL & enrichment", { exact: true }).click();
    await page.getByLabel("Source URL", { exact: true }).fill(" https://example.com/feed ");
    assert.equal(await unloadProtected(page), false);
    await page.getByRole("button", { name: "Close", exact: true }).click();
    assert.equal(await confirmation(page).count(), 0);
    assert.equal(await page.evaluate(() => window.editorCloseCount), 1);
  });

  await t.test("explicit profile selection warns about the source draft, not loss of the saved profile", async () => {
    const { page, state } = await setup();
    await discover(page);
    await page.getByRole("button", { name: "New profile", exact: true }).click();
    await page.getByLabel("New profile name", { exact: true }).fill("Persistent profile");
    await page.getByRole("button", { name: "Create and select", exact: true }).click();
    await idle(page);
    await page.getByRole("button", { name: "Close", exact: true }).click();
    assert.equal((await confirmation(page).textContent()).includes("profiles that you created stay on the server"), true);
    await confirmation(page).getByRole("button", { name: "Discard changes", exact: true }).click();
    assert.equal(state.profiles.some((profile) => profile.name === "Persistent profile"), true);
    assert.equal(count(state, "/sources"), 0);
  });

  await t.test("saved sources stay clean after fetch or sync failure and profile rename", async () => {
    const { page, state } = await setup();
    await discover(page);
    await page.getByLabel("Save in profile").selectOption("Work");
    state.refreshFailure = true;
    await page.evaluate(() => { window.failSync = true; });
    await page.getByRole("button", { name: "Save and fetch", exact: true }).click();
    await idle(page);
    assert.equal(await unloadProtected(page), false);
    await page.getByRole("button", { name: "Close", exact: true }).click();
    assert.equal(await confirmation(page).count(), 0);
    assert.equal(await page.evaluate(() => window.editorCloseCount), 1);
    await page.evaluate(() => { window.failSync = false; });
    await page.getByText("Manage profiles", { exact: true }).click();
    await page.getByLabel("Existing profile").selectOption("Work");
    await page.getByLabel("New name", { exact: true }).fill("Renamed work");
    await page.getByRole("button", { name: "Rename", exact: true }).click();
    await idle(page);
    assert.equal(await unloadProtected(page), false);
    await page.getByRole("button", { name: "Close", exact: true }).click();
    assert.equal(await confirmation(page).count(), 0);
    assert.equal(await page.evaluate(() => window.editorCloseCount), 2);
  });

  await t.test("duplicate preserves edited content and preview without a discard prompt", async () => {
    const { page, state } = await setup();
    await page.getByRole("button", { name: "Saved 1", exact: false }).click();
    await page.getByRole("button", { name: "Preview last 30 days", exact: true }).click();
    await idle(page);
    await page.getByLabel("Name", { exact: true }).fill("Duplicate with edits");
    await page.getByRole("button", { name: "Duplicate draft", exact: true }).click();
    assert.equal(await confirmation(page).count(), 0);
    assert.equal(await page.getByLabel("Name", { exact: true }).inputValue(), "Duplicate with edits");
    assert.equal(await page.getByRole("region", { name: "Source preview", exact: true }).count(), 1);
    assert.equal(await unloadProtected(page), true);
    await page.getByRole("button", { name: "Save only", exact: true }).click();
    await idle(page);
    assert.equal(state.sources[0].name, "Saved 1");
    assert.equal(state.sources[2].name, "Duplicate with edits");
    assert.equal(count(state, "/sources"), 1);
  });

  await t.test("disable and source delete cancel without requests and confirm exactly once", async () => {
    const { page, state } = await setup();
    await page.getByRole("button", { name: "Saved 1", exact: false }).click();
    await page.getByRole("button", { name: "Disable source", exact: true }).click();
    const disabling = page.getByRole("dialog", { name: "Disable source?", exact: true });
    assert.equal(await disabling.getByRole("button", { name: "Cancel", exact: true }).evaluate((button) => document.activeElement === button), true);
    await page.keyboard.press("Enter");
    assert.equal(count(state, "/sources/1", "PATCH"), 0);
    await page.getByRole("button", { name: "Disable source", exact: true }).click();
    await disabling.getByRole("button", { name: "Disable source", exact: true }).evaluate((button) => { button.click(); button.click(); });
    await idle(page);
    assert.equal(count(state, "/sources/1", "PATCH"), 1);
    assert.equal(await unloadProtected(page), false);
    await page.getByRole("button", { name: "Delete source", exact: true }).click();
    const deletion = page.getByRole("dialog", { name: "Delete source?", exact: true });
    await deletion.getByRole("button", { name: "Cancel", exact: true }).click();
    assert.equal(count(state, "/sources/1", "DELETE"), 0);
    await page.getByRole("button", { name: "Delete source", exact: true }).click();
    await deletion.getByRole("button", { name: "Delete source", exact: true }).evaluate((button) => { button.click(); button.click(); });
    await idle(page);
    assert.equal(count(state, "/sources/1", "DELETE"), 1);
  });

  await t.test("profile deletion preserves manual edits and updates the baseline for committed membership changes", async () => {
    const { page, state } = await setup();
    await page.getByRole("button", { name: "Saved 1", exact: false }).click();
    await page.getByRole("button", { name: "Preview last 30 days", exact: true }).click();
    await idle(page);
    await page.getByLabel("Name", { exact: true }).fill("Retain this name");
    await page.getByText("Manage profiles", { exact: true }).click();
    await page.getByLabel("Existing profile").selectOption("Work");
    await page.getByRole("button", { name: "Delete", exact: true }).click();
    const deletion = page.getByRole("dialog", { name: "Delete profile?", exact: true });
    await deletion.getByRole("button", { name: "Cancel", exact: true }).click();
    assert.equal(count(state, "/profiles/Work", "DELETE"), 0);
    await page.getByRole("button", { name: "Delete", exact: true }).click();
    await deletion.getByRole("button", { name: "Delete profile", exact: true }).evaluate((button) => { button.click(); button.click(); });
    await idle(page);
    assert.equal(count(state, "/profiles/Work", "DELETE"), 1);
    assert.equal(await page.getByLabel("Name", { exact: true }).inputValue(), "Retain this name");
    assert.equal(await page.getByLabel("Save in profile").inputValue(), "");
    assert.equal(await page.getByRole("region", { name: "Source preview", exact: true }).count(), 1);
    assert.equal(await unloadProtected(page), true);
    await page.getByLabel("Name", { exact: true }).fill("Saved 1");
    assert.equal(await unloadProtected(page), false);
    await page.getByRole("button", { name: "Close", exact: true }).click();
    assert.equal(await confirmation(page).count(), 0);
  });

  await t.test("unsubmitted profile text survives source switches and has a close guard", async () => {
    const { page } = await setup();
    await page.getByText("Manage profiles", { exact: true }).click();
    await page.getByLabel("New profile", { exact: true }).fill("Unsubmitted profile");
    await page.getByRole("button", { name: "Saved 1", exact: false }).click();
    assert.equal(await confirmation(page).count(), 0);
    assert.equal(await page.getByLabel("New profile", { exact: true }).inputValue(), "Unsubmitted profile");
    await page.getByRole("button", { name: "Close", exact: true }).click();
    await confirmation(page).getByRole("button", { name: "Keep editing", exact: true }).click();
    assert.equal(await page.getByLabel("New profile", { exact: true }).inputValue(), "Unsubmitted profile");
  });

  await t.test("unload protection covers pending mutations but not read-only preview", async () => {
    const { page, state } = await setup();
    await page.getByRole("button", { name: "Saved 1", exact: false }).click();
    for (const [path, label, protectedDuringRequest] of [["/sources/preview", "Preview last 30 days", false], ["/sources/1/refresh", "Fetch latest", true]]) {
      state.holdPath = path;
      const request = page.waitForRequest((request) => new URL(request.url()).pathname === path);
      await page.getByRole("button", { name: label, exact: true }).click();
      await request;
      assert.equal(await unloadProtected(page), protectedDuringRequest);
      state.holdPath = "";
      state.release();
      await idle(page);
      assert.equal(await unloadProtected(page), false);
    }
  });

  for (const transition of ["owner change", "unmount"]) await t.test(`${transition} cancels pending confirmation without a queued mutation`, async () => {
    const { page, state } = await setup();
    await page.getByRole("button", { name: "Saved 1", exact: false }).click();
    await page.getByRole("button", { name: "Delete source", exact: true }).click();
    const deletion = page.getByRole("dialog", { name: "Delete source?", exact: true });
    await deletion.getByRole("button", { name: "Delete source", exact: true }).evaluate((button) => { window.staleConfirmButton = button; });
    await page.evaluate((transition) => { if (transition === "owner change") window.changeEditorOwner(); else window.unmountEditor(); }, transition);
    await deletion.waitFor({ state: "hidden" });
    await page.evaluate(() => window.staleConfirmButton.click());
    assert.equal(count(state, "/sources/1", "DELETE"), 0);
  });

  for (const width of [320, 390, 1200]) await t.test(`layout and reachable actions at ${width}px`, async () => {
    const { page, errors } = await setup(width);
    await discover(page);
    await page.getByLabel("Save in profile").selectOption("Work");
    const dimensions = await sourceEditor(page).evaluate((dialog) => ({ client: dialog.clientWidth, scroll: dialog.scrollWidth }));
    assert.ok(dimensions.scroll <= dimensions.client + 1, JSON.stringify(dimensions));
    const button = page.getByRole("button", { name: "Save and fetch", exact: true });
    const bounds = await button.boundingBox();
    assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= width);
    assert.ok(bounds.y >= 0 && bounds.y + bounds.height <= page.viewportSize().height);
    await page.getByRole("button", { name: "New profile", exact: true }).click();
    assert.equal(await page.getByLabel("New profile name", { exact: true }).evaluate((input) => input === document.activeElement), true);
    await page.getByRole("button", { name: "Close", exact: true }).click();
    const modalBounds = await confirmation(page).boundingBox();
    assert.ok(modalBounds.x >= 0 && modalBounds.x + modalBounds.width <= width);
    assert.ok(modalBounds.y >= 0 && modalBounds.y + modalBounds.height <= page.viewportSize().height);
    await confirmation(page).getByRole("button", { name: "Keep editing", exact: true }).click();
    assert.deepEqual(errors, []);
  });
});
