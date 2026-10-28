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
const motion = await import(moduleUrl(
  await readFile(new URL("../lib/motion.ts", import.meta.url), "utf8"),
));
const { motionAllowed, parsePause, PAUSE_KEY } = motion;

test("decoration runs only when no input blocks it (all 16 combinations)", () => {
  for (let bits = 0; bits < 16; bits += 1) {
    const inputs = {
      reducedMotion: Boolean(bits & 1),
      documentHidden: Boolean(bits & 2),
      offscreen: Boolean(bits & 4),
      paused: Boolean(bits & 8),
    };
    assert.equal(motionAllowed(inputs), bits === 0, JSON.stringify(inputs));
  }
});

test("only the string 1 pauses decoration", () => {
  assert.equal(parsePause("1"), true);
  assert.equal(parsePause("0"), false);
  assert.equal(parsePause(null), false);
  assert.equal(parsePause("true"), false);
  assert.equal(parsePause(""), false);
});

test("the pause key is stable", () => {
  assert.equal(PAUSE_KEY, "fs-motion-paused");
});
