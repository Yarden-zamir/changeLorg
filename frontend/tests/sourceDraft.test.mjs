import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../src/lib/sourceDraft.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } });
const { draftSignature, fetchSignature, normalizedSource, toDraft } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);
const rss = { name: "Example", enabled: true, plugin: "rss-atom", config: { url: "https://example.com/feed", profile: "Work", include_any: ["release"], custom: { nested: [1, 2] } } };

test("X drafts retain filters but remove incompatible enrichment and HTML options", () => {
  const draft = toDraft({ name: "@example", enabled: true, plugin: "x", config: { url: "https://x.com/example", profile: "Work", include_any: ["release"], enrichment_profile: "release-notes", user_agent: "custom", article_path_prefix: "/news", limit: 10 } });
  assert.deepEqual(normalizedSource(draft).config, { url: "https://x.com/example", profile: "Work", include_any: ["release"], exclude_any: [] });
  assert.notEqual(fetchSignature(draft), fetchSignature({ ...draft, exclude: "repost" }));
});

test("fetch identity excludes subscription metadata but includes every fetch setting", () => {
  const draft = toDraft(rss);
  const signature = fetchSignature(draft);
  assert.equal(fetchSignature(toDraft({ ...rss, name: "Renamed", enabled: false, config: { ...rss.config, profile: "Research" } })), signature);
  for (const [key, value] of Object.entries({ url: "https://example.com/other", user_agent: "Custom agent", enrichment_profile: "release-notes", custom: { nested: [2, 1] }, future_option: true })) {
    assert.notEqual(fetchSignature(toDraft({ ...rss, config: { ...rss.config, [key]: value } })), signature, key);
  }
  assert.notEqual(fetchSignature({ ...draft, include: "other" }), signature);
  assert.notEqual(fetchSignature({ ...draft, exclude: "preview" }), signature);
  assert.notEqual(fetchSignature({ ...draft, source: { ...rss, plugin: "html-news" } }), signature);
});

test("fetch identity uses payload normalization and ignores object key order", () => {
  const draft = toDraft(rss);
  const reordered = toDraft({ ...rss, config: { custom: { nested: [1, 2] }, include_any: "release", url: " https://example.com/feed ", enrichment_profile: "" } });
  assert.equal(fetchSignature(draft), fetchSignature({ ...reordered, include: "\n release \n\n" }));
  const left = toDraft({ ...rss, config: { url: rss.config.url, extra: { a: 1, b: 2 } } });
  const right = toDraft({ ...rss, config: { extra: { b: 2, a: 1 }, url: rss.config.url } });
  assert.equal(fetchSignature(left), fetchSignature(right));
});

test("HTML fetch identity tracks limits, paths, exclusions, title cleanup, and unknown options", () => {
  const html = { ...rss, plugin: "html-news", config: { url: "https://example.com/news", article_path_prefix: "/news/", limit: 20, exclude_path_prefixes: ["/news/ads/"], title_suffixes: [" | News"], user_agent: "Agent", extra: true } };
  const signature = fetchSignature(toDraft(html));
  assert.equal(fetchSignature(toDraft({ ...html, config: { ...html.config, limit: "20", article_path_prefix: " /news/ " } })), signature);
  for (const [key, value] of Object.entries({ article_path_prefix: "/articles/", limit: 21, exclude_path_prefixes: ["/news/promos/"], title_suffixes: [], user_agent: "Other", extra: false })) {
    assert.notEqual(fetchSignature(toDraft({ ...html, config: { ...html.config, [key]: value } })), signature, key);
  }
  assert.equal(normalizedSource(toDraft({ ...html, config: { ...html.config, limit: "" } })).config.limit, undefined);
});

test("payloads preserve unknown fields and remove only incompatible plugin fields without mutation", () => {
  const mixed = toDraft({ ...rss, config: { ...rss.config, article_path_prefix: "/news/", exclude_path_prefixes: ["/ads/"], title_suffixes: [" | News"], limit: "12", user_agent: "Agent" } });
  const before = structuredClone(mixed);
  const feed = normalizedSource(mixed);
  for (const key of ["article_path_prefix", "exclude_path_prefixes", "title_suffixes", "limit"]) assert.equal(key in feed.config, false);
  assert.deepEqual(feed.config.custom, rss.config.custom);
  assert.equal(feed.config.user_agent, "Agent");
  assert.equal(feed.config.profile, "Work");
  const html = normalizedSource({ ...mixed, source: { ...mixed.source, plugin: "html-news" } });
  assert.equal("include_any" in html.config, false);
  assert.equal("exclude_any" in html.config, false);
  assert.equal(html.config.limit, 12);
  assert.deepEqual(html.config.exclude_path_prefixes, ["/ads/"]);
  assert.deepEqual(html.config.title_suffixes, [" | News"]);
  assert.deepEqual(html.config.custom, rss.config.custom);
  fetchSignature(mixed);
  assert.deepEqual(mixed, before);
});

test("draft identity tracks meaningful metadata and fetch edits independently of save eligibility", () => {
  const baseline = toDraft(rss);
  const signature = draftSignature(baseline);
  for (const source of [
    { ...rss, name: "Renamed" },
    { ...rss, enabled: false },
    { ...rss, config: { ...rss.config, profile: "Research" } },
    { ...rss, config: { ...rss.config, url: "https://example.com/other" } },
    { ...rss, config: { ...rss.config, custom: { nested: [3] } } },
  ]) assert.notEqual(draftSignature(toDraft(source)), signature);
  assert.notEqual(draftSignature({ ...baseline, include: "beta" }), signature);
  assert.notEqual(draftSignature({ ...baseline, exclude: "alpha" }), signature);
  const candidate = { ...rss, config: { ...rss.config, profile: "" } };
  assert.equal(draftSignature(toDraft(candidate)), draftSignature(toDraft(structuredClone(candidate))));
  assert.notEqual(draftSignature(toDraft(candidate)), signature);
});

test("draft identity treats normalized whitespace and reverted fields as unchanged", () => {
  const baseline = toDraft(rss);
  const signature = draftSignature(baseline);
  const equivalent = toDraft({ ...rss, name: " Example ", config: { custom: rss.config.custom, profile: "Work", url: ` ${rss.config.url} `, include_any: " release \n\n" } });
  assert.equal(draftSignature(equivalent), signature);
  const edited = { ...baseline, source: { ...baseline.source, name: "Other" } };
  assert.notEqual(draftSignature(edited), signature);
  assert.equal(draftSignature({ ...edited, source: { ...edited.source, name: " Example " } }), signature);
  const html = { ...rss, plugin: "html-news", config: { url: rss.config.url, profile: "Work", article_path_prefix: "/news/", limit: 20 } };
  assert.equal(draftSignature(toDraft(html)), draftSignature(toDraft({ ...html, config: { ...html.config, article_path_prefix: " /news/ ", limit: "20" } })));
});
