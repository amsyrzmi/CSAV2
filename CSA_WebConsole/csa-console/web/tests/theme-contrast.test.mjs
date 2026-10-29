import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const css = await readFile(new URL("../app/themes.css", import.meta.url), "utf8");
const THEMES = ["cobalt", "night", "bureau"];

/** Token map for one theme: every `--name: value;` in the rule blocks that select it. */
function tokens(theme) {
  const out = {};
  for (const [, selector, body] of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const selectors = selector.replace(/\/\*[\s\S]*?\*\//g, "").split(",").map((s) => s.trim());
    const applies = selectors.some((s) =>
      s === `[data-theme="${theme}"]` || (theme === "cobalt" && s === ":root"));
    if (!applies) continue;
    for (const [, name, value] of body.matchAll(/(--[\w-]+|color-scheme)\s*:\s*([^;]+);/g)) out[name] = value.trim();
  }
  return out;
}

function rgb(hex) {
  assert.match(hex, /^#[0-9a-f]{6}$/i, `not a #rrggbb colour: ${hex}`);
  return [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
}
function luminance(hex) {
  const [r, g, b] = rgb(hex).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
function ratio(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

const COLOUR_TOKENS = [
  "--fs-bg", "--fs-surface", "--fs-surface-raised", "--fs-surface-blue", "--fs-text",
  "--fs-text-secondary", "--fs-primary", "--fs-primary-hover", "--fs-border", "--fs-border-control",
  "--fs-focus", "--fs-disabled-bg", "--fs-disabled-text", "--fs-success", "--fs-success-bg",
  "--fs-warning", "--fs-warning-bg", "--fs-danger", "--fs-danger-bg", "--fs-info", "--fs-info-bg",
  "--fs-on-primary", "--fs-shell", "--fs-rail", "--fs-tile", "--fs-human", "--color-white",
  "--fs-ink-950", "--fs-ink-800", "--fs-ink-700", "--fs-ink-400", "--fs-ink-200", "--fs-ink-100",
  "--fs-ink-50", "--fs-brand-100", "--fs-brand-200", "--fs-brand-900",
  "--fs-border-strong", "--fs-neutral-bg", "--fs-neutral-text", "--fs-customer-bg", "--fs-customer-text",
  "--fs-team-bg", "--fs-team-text", "--fs-skeleton", "--fs-skeleton-shine", "--fs-auth-from", "--fs-auth-to", "--fs-auth-text",
  ...[1, 2, 3, 4].flatMap((n) => [`--fs-customer-${n}-bg`, `--fs-customer-${n}-text`]),
];

// [foreground token, background token, minimum ratio, what it protects]
const PAIRS = [
  ["--fs-text", "--fs-surface", 4.5, "body text on a card"],
  ["--fs-text-secondary", "--fs-surface", 4.5, "muted text on a card"],
  ["--fs-text", "--fs-shell", 4.5, "text on the shell tint"],
  ["--fs-text-secondary", "--fs-shell", 4.5, "muted text on the shell tint"],
  ["--fs-text", "--fs-rail", 4.5, "text on the rail"],
  ["--fs-text-secondary", "--fs-rail", 4.5, "muted text on the rail"],
  ["--fs-text", "--fs-tile", 4.5, "text on a tile"],
  ["--fs-text-secondary", "--fs-tile", 4.5, "muted text on a tile"],
  ["--fs-text", "--fs-surface-blue", 4.5, "text on the soft accent"],
  ["--fs-on-primary", "--fs-primary", 4.5, "label on the accent button"],
  ["--fs-on-primary", "--fs-primary-hover", 4.5, "label on the hovered accent button"],
  ["--fs-primary", "--fs-surface", 4.5, "accent link text on a card"],
  ["--fs-primary", "--fs-shell", 4.5, "accent link text on the shell tint"],
  ["--fs-primary", "--fs-surface-blue", 4.5, "accent text on the soft accent"],
  ["--fs-ink-700", "--fs-surface", 4.5, "table cell text on a card"],
  ["--fs-ink-950", "--fs-brand-100", 4.5, "selected text"],
  ["--fs-primary-hover", "--fs-surface-blue", 4.5, "current nav item text on the soft accent"],
  ["--fs-human", "--fs-surface", 4.5, "human actor text on a card"],
  ["--fs-border-control", "--fs-surface", 3, "input border on a card"],
  ["--fs-border-control", "--fs-shell", 3, "input border on the shell tint"],
  ["--fs-focus", "--fs-surface", 3, "focus ring on a card"],
  ["--fs-focus", "--fs-shell", 3, "focus ring on the shell tint"],
  ["--fs-success", "--fs-success-bg", 4.5, "Services status word: success"],
  ["--fs-warning", "--fs-warning-bg", 4.5, "Services status word: warning"],
  ["--fs-danger", "--fs-danger-bg", 4.5, "Services status word: danger"],
  ["--fs-info", "--fs-info-bg", 4.5, "Services status word: info"],
  ["--fs-success", "--fs-surface", 4.5, "success word on a card"],
  ["--fs-warning", "--fs-surface", 4.5, "warning word on a card"],
  ["--fs-danger", "--fs-surface", 4.5, "danger word on a card"],
  ["--fs-disabled-text", "--fs-disabled-bg", 4.5, "disabled control text"],
  ["--fs-text-secondary", "--fs-surface-blue", 4.5, "muted text on the soft accent"],
  ["--fs-text-secondary", "--fs-ink-100", 4.5, "inactive tab on the tab strip"],
  ["--fs-ink-700", "--fs-ink-50", 4.5, "table cell text on a hovered row"],
  ["--fs-border-control", "--fs-tile", 3, "input border on a tile"],
  ["--fs-border-control", "--fs-rail", 3, "input border on the rail"],
  ["--fs-neutral-text", "--fs-neutral-bg", 4.5, "status label: neutral"],
  ["--fs-customer-text", "--fs-customer-bg", 4.5, "status label: customer"],
  ["--fs-team-text", "--fs-team-bg", 4.5, "status label: team"],
  ...[1, 2, 3, 4].map((n) => [`--fs-customer-${n}-text`, `--fs-customer-${n}-bg`, 4.5, `customer avatar tint ${n}`]),
  ["--fs-auth-text", "--fs-auth-from", 4.5, "text on the sign-in panel, top"],
  ["--fs-auth-text", "--fs-auth-to", 4.5, "text on the sign-in panel, bottom"],
  ["--fs-text-secondary", "--fs-surface", 4.5, "unknown customer avatar mark"],
  ["--fs-border-control", "--fs-surface", 3, "unknown customer avatar dashed outline"],
  ["--color-white", "--fs-primary", 4.5, "text-white utility on bg-brand-600"],
  ["--color-white", "--fs-text", 4.5, "text-white utility on bg-ink-900"],
];

for (const theme of THEMES) {
  test(`${theme}: every colour token is defined as #rrggbb`, () => {
    const map = tokens(theme);
    for (const name of COLOUR_TOKENS) {
      assert.ok(map[name], `${theme} is missing ${name}`);
      assert.match(map[name], /^#[0-9a-f]{6}$/i, `${theme} ${name} = ${map[name]}`);
    }
    assert.match(map["color-scheme"] ?? "", /^(light|dark)$/, `${theme} color-scheme`);
  });

  test(`${theme}: text and control pairs meet WCAG contrast`, () => {
    const map = tokens(theme);
    const rows = [];
    const failures = [];
    for (const [fg, bg, min, what] of PAIRS) {
      const got = ratio(map[fg], map[bg]);
      rows.push(`${theme.padEnd(7)} ${what.padEnd(44)} ${got.toFixed(2)} (min ${min})`);
      if (got < min) failures.push(`${theme}: ${what}: ${fg} ${map[fg]} on ${bg} ${map[bg]} is ${got.toFixed(2)}, needs ${min}`);
    }
    if (process.env.THEME_CONTRAST_TABLE) console.log(rows.join("\n"));
    assert.deepEqual(failures, []);
  });
}

test("--color-white follows the card surface so bg-white utilities retheme", () => {
  for (const theme of THEMES) {
    const map = tokens(theme);
    assert.equal(map["--color-white"].toLowerCase(), map["--fs-surface"].toLowerCase(), theme);
  }
});

test("cobalt is the :root default and the other themes are selected by data-theme", () => {
  assert.match(css, /:root\s*,\s*\[data-theme="cobalt"\]/);
  assert.match(css, /\[data-theme="night"\]/);
  assert.match(css, /\[data-theme="bureau"\]/);
});

test("bureau uses a hard 3px offset shadow and square controls; the others stay soft and round", () => {
  const bureau = tokens("bureau");
  assert.equal(bureau["--fs-radius-control"], "0");
  assert.match(bureau["--fs-shadow-popover"], /^3px 3px 0 /);
  for (const theme of ["cobalt", "night"]) {
    const map = tokens(theme);
    assert.notEqual(map["--fs-radius-control"], "0");
    assert.doesNotMatch(map["--fs-shadow-popover"], /^3px 3px 0 /);
  }
});

test("the select chevron is drawn in the control border colour", () => {
  for (const theme of THEMES) {
    const map = tokens(theme);
    const hex = map["--fs-border-control"].slice(1).toLowerCase();
    assert.match(map["--fs-select-chevron"] ?? "", /^url\("data:image\/svg\+xml,/, theme);
    assert.ok(map["--fs-select-chevron"].toLowerCase().includes(`stroke='%23${hex}'`), `${theme} chevron stroke`);
  }
});

test("the scrim and shadows are translucent hex that themes.css owns", () => {
  for (const theme of THEMES) {
    const map = tokens(theme);
    assert.match(map["--fs-scrim"] ?? "", /^#[0-9a-f]{8}$/i, `${theme} scrim`);
  }
});

test("cobalt customer tints match the library's approved blues", async () => {
  const source = await readFile(new URL("../lib/customer-identity.ts", import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022,
  } }).outputText;
  const { CUSTOMER_TINTS } = await import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);
  const map = tokens("cobalt");
  CUSTOMER_TINTS.forEach((tint, i) => {
    assert.equal(map[`--fs-customer-${i + 1}-bg`].toLowerCase(), tint.background.toLowerCase());
    assert.equal(map[`--fs-customer-${i + 1}-text`].toLowerCase(), tint.color.toLowerCase());
  });
});
