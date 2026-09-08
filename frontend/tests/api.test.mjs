import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { beforeEach, test } from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../src/lib/api.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } });
const { api, getIdentity, invalidateIdentity, resetSession, anonymousTokenKey, identityInvalidatedEvent, ApiError } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);
const identity = { id: "github:alice", authenticated: true, login: "alice", auth_enabled: true, anonymous_has_data: true };
const token = "12345678-1234-4234-8234-123456789abc";
let storage;

beforeEach(() => {
  invalidateIdentity();
  storage = new Map([[anonymousTokenKey, token]]);
  globalThis.localStorage = {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value),
  };
  globalThis.window = new EventTarget();
});

async function identify(t, value = identity) {
  t.mock.method(globalThis, "fetch", async () => Response.json(value));
  return getIdentity();
}

test("requires /me before account requests and never sends an unbound mutation", async (t) => {
  const fetch = t.mock.method(globalThis, "fetch", async () => Response.json({}));
  await assert.rejects(api("/sources", { owner: null, method: "POST", body: {} }), ApiError);
  await assert.rejects(api("/sources", { owner: identity.id, method: "POST", body: {} }), ApiError);
  assert.equal(fetch.mock.callCount(), 0);
});

test("session reset uses same-origin cookies and CSRF without identity or browser storage", async (t) => {
  t.mock.method(localStorage, "getItem", () => { throw new Error("storage unavailable"); });
  t.mock.method(localStorage, "setItem", () => { throw new Error("must not write browser data"); });
  const fetch = t.mock.method(globalThis, "fetch", async () => new Response(null, { status: 204 }));
  assert.equal(await resetSession(), undefined);
  assert.equal(fetch.mock.callCount(), 1);
  const [path, options] = fetch.mock.calls[0].arguments;
  assert.equal(path, "/session/reset");
  assert.equal(options.method, "POST");
  assert.deepEqual(options.headers, { "X-Changelorg-Request": "1" });
  assert.equal(options.credentials, "include");
  assert.equal(options.cache, "no-store");
  assert.equal(options.redirect, "error");
  assert.ok(options.signal instanceof AbortSignal);
  assert.equal(options.body, undefined);
  assert.equal(localStorage.getItem.mock.callCount(), 0);
  assert.equal(localStorage.setItem.mock.callCount(), 0);
  assert.equal(storage.get(anonymousTokenKey), token);
});

test("session reset preserves absent or invalid anonymous tokens and browser data", async (t) => {
  t.mock.method(globalThis, "fetch", async () => new Response(null, { status: 204 }));
  for (const value of [null, "invalid-token", token]) {
    storage.clear();
    storage.set("changelorg:user-state:v1", "private browser data");
    if (value !== null) storage.set(anonymousTokenKey, value);
    const before = new Map(storage);
    await resetSession();
    assert.deepEqual(storage, before);
  }
});

test("session reset rejects unexpected responses without private details or automatic retries", async (t) => {
  for (const status of [200, 401, 403, 404, 500]) {
    const fetch = t.mock.method(globalThis, "fetch", async () => new Response("private proxy credentials", { status }));
    await assert.rejects(resetSession(), (error) => {
      assert.ok(error instanceof ApiError);
      assert.equal(error.status, status);
      assert.ok(!error.message.includes("private proxy credentials"));
      return true;
    });
    assert.equal(fetch.mock.callCount(), 1);
    assert.equal(storage.get(anonymousTokenKey), token);
  }
});

test("session reset reports network failures without an automatic retry", async (t) => {
  const fetch = t.mock.method(globalThis, "fetch", async () => { throw new TypeError("private connection details"); });
  await assert.rejects(resetSession(), (error) => {
    assert.ok(error instanceof ApiError);
    assert.ok(!error.message.includes("private connection details"));
    return true;
  });
  assert.equal(fetch.mock.callCount(), 1);
  assert.equal(storage.get(anonymousTokenKey), token);
});

test("sends credentials, UUIDv4, owner, and mutation marker with no cache", async (t) => {
  const calls = [];
  t.mock.method(globalThis, "fetch", async (path, options) => {
    calls.push({ path, options });
    return path === "/me" ? Response.json(identity) : new Response(null, { status: 204 });
  });
  await getIdentity();
  for (const method of ["GET", "POST", "PATCH", "DELETE"]) {
    await api("/sources", { owner: identity.id, method, ...(method === "PATCH" ? { body: { enabled: false } } : {}) });
  }
  assert.equal(calls[0].options.headers["X-Changelorg-Owner"], undefined);
  for (const { options } of calls) {
    assert.equal(options.headers["X-Anonymous-Token"], token);
    assert.equal(options.credentials, "include");
    assert.equal(options.cache, "no-store");
    assert.equal(options.headers["X-Changelorg-Request"], options.method === "GET" ? undefined : "1");
  }
  for (const { options } of calls.slice(1)) assert.equal(options.headers["X-Changelorg-Owner"], identity.id);
  assert.deepEqual(JSON.parse(calls[3].options.body), { enabled: false });
  assert.equal(calls[3].options.headers["Content-Type"], "application/json");
});

test("discovery and profileless preview use owner-bound POST requests with CSRF", async (t) => {
  await identify(t);
  const draft = { name: "Releases", plugin: "rss-atom", config: { url: "https://github.com/owner/repo/releases.atom" }, enabled: true };
  const preview = { changes: [], window: { start: "2026-08-09T00:00:00Z", end: "2026-09-08T00:00:00Z" } };
  const fetch = t.mock.method(globalThis, "fetch", async (path) => Response.json(path === "/sources/discover" ? [draft] : preview));
  const controller = new AbortController();
  const candidates = await api("/sources/discover", { owner: identity.id, method: "POST", body: { url: "owner/repo" }, signal: controller.signal });
  assert.equal(candidates[0].config.url, draft.config.url);
  assert.ok(!("profile" in candidates[0].config));
  const result = await api("/sources/preview", { owner: identity.id, method: "POST", body: candidates[0], signal: controller.signal });
  assert.deepEqual(result.changes, []);
  assert.equal(fetch.mock.callCount(), 2);
  for (const { arguments: [, options] } of fetch.mock.calls) {
    assert.equal(options.method, "POST");
    assert.equal(options.headers["X-Changelorg-Owner"], identity.id);
    assert.equal(options.headers["X-Changelorg-Request"], "1");
    assert.equal(options.credentials, "include");
    assert.equal(options.cache, "no-store");
    assert.ok(options.signal instanceof AbortSignal);
  }
  assert.deepEqual(JSON.parse(fetch.mock.calls[0].arguments[1].body), { url: "owner/repo" });
  assert.ok(!("profile" in JSON.parse(fetch.mock.calls[1].arguments[1].body).config));
  controller.abort();
  assert.ok(fetch.mock.calls.every(({ arguments: [, options] }) => options.signal.aborted));
});

test("discovery distinguishes no feeds from safe 422 and 502 failures", async (t) => {
  await identify(t);
  const options = { owner: identity.id, method: "POST", body: { url: "https://example.com" } };
  t.mock.method(globalThis, "fetch", async () => Response.json([]));
  assert.deepEqual(await api("/sources/discover", options), []);
  for (const status of [422, 502]) {
    const fetch = t.mock.method(globalThis, "fetch", async () => Response.json({ detail: "private upstream credentials" }, { status }));
    await assert.rejects(api("/sources/discover", options), (error) => {
      assert.ok(error instanceof ApiError);
      assert.equal(error.status, status);
      assert.ok(!error.message.includes("private upstream credentials"));
      return true;
    });
    assert.equal(fetch.mock.callCount(), 1);
  }
});

test("creates a persistent UUIDv4 but never replaces an invalid existing token", async (t) => {
  storage.clear();
  await identify(t);
  const created = storage.get(anonymousTokenKey);
  const parts = created.split("-");
  assert.deepEqual(parts.map((part) => part.length), [8, 4, 4, 4, 12]);
  assert.equal(parts[2][0], "4");
  assert.ok("89ab".includes(parts[3][0]));
  await getIdentity();
  assert.equal(storage.get(anonymousTokenKey), created);
  for (const invalid of ["", "not-a-token", token.replace("-4234-", "-1234-"), token.replace("-8234-", "-7234-")]) {
    storage.set(anonymousTokenKey, invalid);
    await assert.rejects(getIdentity(), ApiError);
    assert.equal(storage.get(anonymousTokenKey), invalid);
  }
});

test("blocks stale owner and token requests before fetch", async (t) => {
  await identify(t);
  const fetch = t.mock.method(globalThis, "fetch", async () => Response.json({}));
  await assert.rejects(api("/sources", { owner: "github:bob", method: "POST" }), { status: 412 });
  storage.set(anonymousTokenKey, crypto.randomUUID());
  let invalidated = false;
  window.addEventListener(identityInvalidatedEvent, () => { invalidated = true; });
  await assert.rejects(api("/me/import", { owner: identity.id, method: "POST" }), { status: 412 });
  assert.equal(fetch.mock.callCount(), 0);
  assert.equal(invalidated, true);
});

test("rejects a late mutation response after an account switch", async (t) => {
  await identify(t);
  const delayed = Promise.withResolvers();
  t.mock.method(globalThis, "fetch", (path) => path === "/me"
    ? Promise.resolve(Response.json({ ...identity, id: "github:bob", login: "bob" })) : delayed.promise);
  const save = api("/changes/1", { owner: identity.id, method: "PATCH", body: { note: "private" } });
  const rejected = assert.rejects(save, { status: 412 });
  await getIdentity();
  delayed.resolve(Response.json({ id: 1, note: "private" }));
  await rejected;
});

test("checks ownership again after a delayed JSON body", async (t) => {
  await identify(t);
  const body = Promise.withResolvers();
  const started = Promise.withResolvers();
  t.mock.method(globalThis, "fetch", async () => ({ ok: true, status: 200, json: () => { started.resolve(); return body.promise; } }));
  const load = api("/sources", { owner: identity.id });
  const rejected = assert.rejects(load, { status: 412 });
  await started.promise;
  invalidateIdentity();
  body.resolve([]);
  await rejected;
});

test("same-owner identity checks do not cancel an active save", async (t) => {
  await identify(t);
  const delayed = Promise.withResolvers();
  t.mock.method(globalThis, "fetch", (path) => path === "/me" ? Promise.resolve(Response.json(identity)) : delayed.promise);
  const save = api("/changes/1", { owner: identity.id, method: "PATCH", body: { saved: true } });
  await getIdentity();
  delayed.resolve(Response.json({ id: 1, saved: true }));
  assert.deepEqual(await save, { id: 1, saved: true });
});

test("ignores out-of-order /me results and preserves same-owner requests", async (t) => {
  await identify(t);
  const delayed = Promise.withResolvers();
  let calls = 0;
  t.mock.method(globalThis, "fetch", async () => ++calls === 1 ? delayed.promise : Response.json(identity));
  const old = getIdentity();
  const rejected = assert.rejects(old, { status: 412 });
  await getIdentity();
  delayed.resolve(Response.json({ ...identity, id: "github:bob" }));
  await rejected;
  await api("/sources", { owner: identity.id });
});

test("rejects invalid identities and blocks later writes", async (t) => {
  for (const value of [null, [], {}, { ...identity, id: "" }, { ...identity, authenticated: "true" }, { ...identity, login: 1 }]) {
    await assert.rejects(identify(t, value), ApiError);
    await assert.rejects(api("/sources", { owner: identity.id, method: "POST" }), ApiError);
  }
});

test("expired sessions invalidate access without an anonymous retry or private error details", async (t) => {
  for (const status of [401, 412]) {
    await identify(t);
    const fetch = t.mock.method(globalThis, "fetch", async () => Response.json({ detail: "private proxy credentials" }, { status }));
    let invalidated = false;
    window.addEventListener(identityInvalidatedEvent, () => { invalidated = true; }, { once: true });
    await assert.rejects(api("/changes/1", { owner: identity.id, method: "PATCH", body: { saved: true } }), (error) => {
      assert.equal(error.status, status);
      assert.ok(!error.message.includes("private proxy credentials"));
      return true;
    });
    await assert.rejects(api("/sources", { owner: identity.id, method: "POST" }), ApiError);
    assert.equal(fetch.mock.callCount(), 1);
    assert.equal(invalidated, true);
  }
});
