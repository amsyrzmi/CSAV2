import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

// proxy.ts lets every request under /fourstacks/ skip the session check, so
// public/fourstacks/ may only ever hold the public agent avatars. These tests
// keep that folder and the matcher exclusion from growing quietly.

function moduleUrl(source) {
  const code = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022,
  } }).outputText;
  return `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
}
const { AGENT_IDS } = await import(moduleUrl(
  await readFile(new URL("../lib/agent-avatars.ts", import.meta.url), "utf8"),
));

const publicRoot = new URL("../public/fourstacks/", import.meta.url);
const MOVE_IT =
  "public/fourstacks/ is served without a session check (proxy.ts matcher). " +
  "Only agents/<id>.svg for the ids in lib/agent-avatars.ts AGENT_IDS belongs there; " +
  "put anything else under a path the proxy checks.";

async function entriesOf(url) {
  return (await readdir(url, { withFileTypes: true }))
    .map((entry) => ({
      name: entry.name,
      kind: entry.isSymbolicLink() ? "symlink"
        : entry.isDirectory() ? "directory"
        : entry.isFile() ? "file"
        : "other",
    }))
    .sort((a, b) => a.name.localeCompare(b.name));
}

test("public/fourstacks/ holds only the agents folder", async () => {
  const entries = await entriesOf(publicRoot);
  assert.deepEqual(
    entries,
    [{ name: "agents", kind: "directory" }],
    `public/fourstacks/ must contain only the agents/ directory (no symlinks, no other files or folders). ${MOVE_IT}`,
  );
});

test("public/fourstacks/agents/ holds exactly one SVG per agent avatar id", async () => {
  const entries = await entriesOf(new URL("agents/", publicRoot));
  for (const entry of entries) {
    assert.equal(
      entry.kind,
      "file",
      `public/fourstacks/agents/${entry.name} is a ${entry.kind}; only regular SVG files belong there. ${MOVE_IT}`,
    );
    assert.match(
      entry.name,
      /^[a-z]+\.svg$/,
      `public/fourstacks/agents/${entry.name} is not an <id>.svg file. ${MOVE_IT}`,
    );
  }
  const expected = AGENT_IDS.map((id) => `${id}.svg`).sort();
  const actual = entries.map((entry) => entry.name).sort();
  const extra = actual.filter((name) => !expected.includes(name));
  const missing = expected.filter((name) => !actual.includes(name));
  assert.deepEqual(
    extra,
    [],
    `Unexpected files in public/fourstacks/agents/: ${extra.join(", ")}. ${MOVE_IT}`,
  );
  assert.deepEqual(
    missing,
    [],
    `Missing avatars in public/fourstacks/agents/: ${missing.join(", ")}. ` +
      "Copy them from ui-guide/assets/agents/ (every AGENT_IDS entry needs one).",
  );
  assert.equal(AGENT_IDS.length, 11, "AGENT_IDS should list the eleven agent avatars");
});

const ALLOWED_EXCLUSIONS = [
  "_next/static",
  "_next/image",
  "favicon.ico",
  "brand/",
  "fourstacks/",
];

test("the proxy matcher skips only the reviewed public paths", async () => {
  const proxy = await readFile(new URL("../proxy.ts", import.meta.url), "utf8");
  const matchers = [...proxy.matchAll(/\bmatcher\s*:\s*\[([^\]]*)\]/g)];
  assert.equal(
    matchers.length,
    1,
    "proxy.ts must declare exactly one `matcher: [...]` in its config; keep the single negative-lookahead entry.",
  );
  const patterns = [...matchers[0][1].matchAll(/"([^"]*)"|'([^']*)'|`([^`]*)`/g)]
    .map((match) => match[1] ?? match[2] ?? match[3]);
  assert.equal(
    patterns.length,
    1,
    "proxy.ts matcher must hold one pattern, /((?!...).*), so every other path goes through the session check.",
  );
  const shape = /^\/\(\(\?!([^()]*)\)\.\*\)$/.exec(patterns[0]);
  assert.ok(
    shape,
    `proxy.ts matcher ${JSON.stringify(patterns[0])} is not of the form /((?!a|b|c).*); keep that form.`,
  );
  const exclusions = shape[1].split("|");
  const broaden = "Do not widen the unauthenticated paths: serve new public files from an existing excluded folder " +
    "only if they are public by design, and update this test with a review.";
  for (const exclusion of exclusions) {
    assert.ok(
      ALLOWED_EXCLUSIONS.includes(exclusion),
      `proxy.ts matcher excludes ${JSON.stringify(exclusion)}, which is not in the reviewed list ` +
        `${ALLOWED_EXCLUSIONS.join("|")}. ${broaden}`,
    );
    assert.ok(!/[*+?{}[\]\\^$]/.test(exclusion), `Exclusion ${JSON.stringify(exclusion)} uses a regex wildcard. ${broaden}`);
    assert.ok(!/public|\.svg/i.test(exclusion), `Exclusion ${JSON.stringify(exclusion)} names public or .svg. ${broaden}`);
  }
  assert.deepEqual(
    exclusions,
    ALLOWED_EXCLUSIONS,
    `proxy.ts matcher exclusions must be exactly ${ALLOWED_EXCLUSIONS.join("|")} in that order. ${broaden}`,
  );
  assert.ok(
    exclusions.includes("fourstacks/") && !exclusions.includes("fourstacks"),
    "The fourstacks exclusion must keep its trailing slash (\"fourstacks/\"); without it any path starting with " +
      "\"fourstacks\" would skip the session check.",
  );
  assert.equal(
    exclusions.indexOf("fourstacks/"),
    exclusions.indexOf("brand/") + 1,
    "Keep \"fourstacks/\" right after \"brand/\" in the matcher.",
  );
});
