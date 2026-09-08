import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../src/lib/sourceDiscovery.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } });
const { discoveryUrl } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);

test("names stay local search queries rather than invented web or GitHub searches", () => {
  for (const name of ["", "  ", "react", "React releases", "GitHub", "OpenAI news", "release notes/feed", "owner/repo/extra"]) {
    assert.equal(discoveryUrl(name), null, name);
  }
});

test("accepts absolute HTTP links and bare domains with paths and query strings", () => {
  for (const [input, expected] of [
    [" https://example.com/feed.xml ", "https://example.com/feed.xml"],
    ["http://example.com/rss", "http://example.com/rss"],
    ["HTTPS://EXAMPLE.COM/feed", "https://example.com/feed"],
    ["example.com", "https://example.com/"],
    ["news.example.com/updates?format=atom&lang=en", "https://news.example.com/updates?format=atom&lang=en"],
    ["example.com?feed=1", "https://example.com/?feed=1"],
    ["github.com/astral-sh/uv", "https://github.com/astral-sh/uv"],
  ]) assert.equal(discoveryUrl(input), expected, input);
});

test("recognizes GitHub owner/repo shorthand without a preset", () => {
  for (const repo of ["astral-sh/uv", "facebook/react", "Owner/repo.name_1-2", "owner/repo.git"]) {
    assert.equal(discoveryUrl(repo), `https://github.com/${repo}`);
    assert.equal(discoveryUrl(` ${repo}/ `), `https://github.com/${repo}`);
  }
  for (const invalid of ["/repo", "owner/", "-owner/repo", "owner-/repo", "own_er/repo", "owner/.", "owner/..", "owner/repo?query", "owner/repo#fragment"]) {
    assert.equal(discoveryUrl(invalid), null, invalid);
  }
});

test("leaves GitHub stream selection and feed normalization to discovery", () => {
  for (const path of ["", ".git", "/releases", "/releases/latest", "/releases/tag/v1.0", "/tags", "/tags.atom", "/releases.atom"]) {
    const url = `https://github.com/owner/repo${path}`;
    assert.equal(discoveryUrl(url), url);
  }
});

test("rejects credentials, non-HTTP schemes, malformed links, and embedded whitespace", () => {
  for (const input of [
    "ftp://example.com/feed", "javascript:alert(1)", "file:///tmp/feed.xml", "//example.com/feed",
    "https://user:secret@example.com/feed", "user@example.com/feed", "https://", "https://[invalid]/feed",
    "example..com/feed", "https://exam\nple.com/feed", "example.com/feed notes", "owner/repo\tname", "example.com\\feed",
  ]) assert.equal(discoveryUrl(input), null, input);
});
