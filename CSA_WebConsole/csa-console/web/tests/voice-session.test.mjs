import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";
const code = ts.transpileModule(await readFile(new URL("../lib/voice-session.ts", import.meta.url), "utf8"), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
}).outputText;
const { SpeechBoundary, playLocalReply, captureUtterance } = await import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);

test("silence and a single noise spike never submit a question; sustained speech ends after pause", () => {
  const boundary = new SpeechBoundary();
  assert.equal(boundary.sample(0, 0), false);
  boundary.sample(0.1, 100);
  assert.equal(boundary.sample(0, 200), false);
  assert.equal(boundary.heardSpeech, false);
  boundary.sample(0.1, 300); boundary.sample(0.1, 400); boundary.sample(0.1, 500);
  assert.equal(boundary.heardSpeech, true);
  assert.equal(boundary.sample(0, 600), false);
  assert.equal(boundary.sample(0, 1799), false);
  assert.equal(boundary.sample(0, 1800), true);
});

test("playback uses the selected installed language and reads the entire reply", async () => {
  const original = globalThis.window; const utterance = globalThis.SpeechSynthesisUtterance;
  const spoken = []; const voice = { localService: true, lang: "ms-MY" };
  globalThis.SpeechSynthesisUtterance = class { constructor(text) { this.text = text; } };
  globalThis.window = { speechSynthesis: { getVoices: () => [voice], cancel() {}, speak(value) {
    assert.equal(value.voice, voice); spoken.push(value.text); queueMicrotask(() => value.onend());
  } } };
  try {
    const text = "Apakah waktu operasi? ".repeat(80);
    await playLocalReply(text, "ms", new AbortController().signal);
    assert.equal(spoken.join(""), text);
    assert.ok(spoken.length > 1);
    await assert.rejects(playLocalReply("Hello", "en", new AbortController().signal), /No installed English/);
  } finally { globalThis.window = original; globalThis.SpeechSynthesisUtterance = utterance; }
});

test("a cancelled permission request releases the microphone when permission eventually resolves", async () => {
  const originalWindow = globalThis.window;
  const originalNavigator = Object.getOwnPropertyDescriptor(globalThis, "navigator");
  const originalRecorder = globalThis.MediaRecorder;
  let resolve; let stops = 0;
  const permission = new Promise(value => { resolve = value; });
  globalThis.window = { isSecureContext: true };
  globalThis.MediaRecorder = class {};
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: { mediaDevices: { getUserMedia: () => permission } } });
  try {
    const controller = new AbortController();
    const capturing = captureUtterance(controller.signal);
    controller.abort();
    resolve({ getTracks: () => [{ stop() { stops++; } }] });
    await assert.rejects(capturing);
    assert.equal(stops, 1);
  } finally {
    globalThis.window = originalWindow; globalThis.MediaRecorder = originalRecorder;
    if (originalNavigator) Object.defineProperty(globalThis, "navigator", originalNavigator);
    else delete globalThis.navigator;
  }
});
