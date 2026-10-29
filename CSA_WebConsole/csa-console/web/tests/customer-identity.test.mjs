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
const identity = await import(moduleUrl(
  await readFile(new URL("../lib/customer-identity.ts", import.meta.url), "utf8"),
));
const {
  CUSTOMER_TINTS, initialsFor, tintIndex, rowTitle, secondaryLine, senderLabel,
} = identity;

test("initials are the first code point of the first two words, upper-cased", () => {
  assert.equal(initialsFor("Nur Aisyah Rahman"), "NA");
  assert.equal(initialsFor("Tan Wei Jian"), "TW");
  assert.equal(initialsFor("Priya"), "P");
  assert.equal(initialsFor("  aisyah   rahman "), "AR");
  assert.equal(initialsFor("\u{1D4D0}li Baba"), "\u{1D4D0}B");
});

test("a blank or missing name has no initials", () => {
  assert.equal(initialsFor(null), null);
  assert.equal(initialsFor(""), null);
  assert.equal(initialsFor("   \t "), null);
});

test("tintIndex is stable and spreads ids over all four tints", () => {
  assert.equal(tintIndex("c0ffee"), tintIndex("c0ffee"));
  const counts = [0, 0, 0, 0];
  for (let i = 0; i < 200; i += 1) {
    const index = tintIndex(crypto.randomUUID());
    assert.ok([0, 1, 2, 3].includes(index));
    counts[index] += 1;
  }
  for (const count of counts) assert.ok(count >= 10, `spread ${counts}`);
});

test("tintIndex matches the FNV-1a reference value", () => {
  // FNV-1a 32-bit of "a" is 0xe40c292c; 0xe40c292c % 4 === 0.
  assert.equal(tintIndex("a"), 0xe40c292c % 4);
  // FNV-1a 32-bit of "b" is 0xe70c2de5; 0xe70c2de5 % 4 === 1.
  assert.equal(tintIndex("b"), 0xe70c2de5 % 4);
});

test("rowTitle prefers the name, then @username, then the masked number", () => {
  const none = { display_name: null, telegram_username: null, phone_masked: null };
  assert.equal(rowTitle({ ...none, display_name: "Nur Aisyah", telegram_username: "a" }), "Nur Aisyah");
  assert.equal(rowTitle({ ...none, telegram_username: "aisyah_r", phone_masked: "••••0148" }), "@aisyah_r");
  assert.equal(rowTitle({ ...none, phone_masked: "••••0148" }), "••••0148");
  assert.equal(rowTitle({ ...none, display_name: "  " }), "Unknown sender");
  assert.equal(rowTitle(none), "Unknown sender");
});

test("secondaryLine names the handle or number and the channel", () => {
  const none = { display_name: null, telegram_username: null, phone_masked: null };
  assert.equal(
    secondaryLine({ ...none, display_name: "Aisyah", telegram_username: "aisyah_r" }, "telegram"),
    "@aisyah_r · Telegram",
  );
  assert.equal(
    secondaryLine({ ...none, phone_masked: "••••0148" }, "whatsapp"),
    "••••0148 · WhatsApp",
  );
  assert.equal(secondaryLine(none, "whatsapp"), "WhatsApp");
});

test("senderLabel is text for known and new senders", () => {
  assert.equal(senderLabel(true), "Customer");
  assert.equal(senderLabel(false), "New sender");
});

function luminance(hex) {
  const channels = [1, 3, 5].map((i) => {
    const value = parseInt(hex.slice(i, i + 2), 16) / 255;
    return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
}
function contrast(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

test("the four tints are the approved blues and each text pair is at least 4.5:1", () => {
  assert.deepEqual(CUSTOMER_TINTS.map((t) => [t.background, t.color]), [
    ["#D9E5FF", "#183DA5"],
    ["#CFEBFF", "#0B5A8C"],
    ["#E0E6FB", "#2C3E9E"],
    ["#D2EEF6", "#164B8D"],
  ]);
  for (const tint of CUSTOMER_TINTS) {
    const ratio = contrast(tint.background, tint.color);
    console.log(`${tint.background} / ${tint.color}: ${ratio.toFixed(2)}:1`);
    assert.ok(ratio >= 4.5, `${tint.background}/${tint.color} ${ratio}`);
  }
  console.log(`unknown #FFFFFF / #566274: ${contrast("#FFFFFF", "#566274").toFixed(2)}:1`);
  assert.ok(contrast("#FFFFFF", "#566274") >= 4.5);
});
