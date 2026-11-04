import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

function moduleUrl(source) {
  const code = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022,
  } }).outputText;
  return `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
}
const theme = await import(moduleUrl(
  await readFile(new URL("../lib/theme.ts", import.meta.url), "utf8"),
));
const { THEMES, THEME_COOKIE, parseTheme, themeCookie, writeTheme } = theme;

test("the three themes and the cookie name are fixed", () => {
  assert.deepEqual([...THEMES], ["cobalt", "night", "bureau"]);
  assert.equal(THEME_COOKIE, "fs-theme");
});

test("parseTheme returns each valid name", () => {
  for (const name of THEMES) assert.equal(parseTheme(name), name);
});

test("parseTheme falls back to cobalt for anything else", () => {
  for (const raw of [
    null, undefined, "", "NIGHT", "Night", " night", "night ", "night; Path=/",
    "<script>", '"><script>alert(1)</script>', "night\r\nSet-Cookie: a=b",
    "cobalt,night", "constructor", "__proto__", "toString", "0", "bureau\u0000",
  ]) assert.equal(parseTheme(raw), "cobalt", JSON.stringify(raw));
});

test("themeCookie is a one-year Lax cookie for the whole site", () => {
  for (const name of THEMES) {
    const cookie = themeCookie(name);
    assert.equal(cookie, `fs-theme=${name}; Path=/; Max-Age=31536000; SameSite=Lax`);
  }
  assert.match(themeCookie("night"), /fs-theme=night/);
});

test("writeTheme sets the cookie, mirrors localStorage and tags the root element", () => {
  const written = [];
  const stored = {};
  globalThis.document = {
    documentElement: { dataset: {} },
    set cookie(value) { written.push(value); },
  };
  globalThis.localStorage = { setItem: (k, v) => { stored[k] = v; } };
  try {
    writeTheme("bureau");
    assert.deepEqual(written, [themeCookie("bureau")]);
    assert.deepEqual(stored, { "fs-theme": "bureau" });
    assert.equal(document.documentElement.dataset.theme, "bureau");
  } finally {
    delete globalThis.document;
    delete globalThis.localStorage;
  }
});

test("writeTheme still applies the theme when storage is blocked", () => {
  globalThis.document = {
    documentElement: { dataset: {} },
    set cookie(_value) {},
  };
  globalThis.localStorage = { setItem: () => { throw new Error("blocked"); } };
  try {
    writeTheme("night");
    assert.equal(document.documentElement.dataset.theme, "night");
  } finally {
    delete globalThis.document;
    delete globalThis.localStorage;
  }
});
