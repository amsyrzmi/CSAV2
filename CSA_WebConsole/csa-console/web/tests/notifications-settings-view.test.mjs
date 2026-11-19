import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

async function load(path) {
  const source = await readFile(new URL(path, import.meta.url), "utf8");
  const code = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
  }).outputText;
  return { source, module: await import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`) };
}

const { source, module: view } = await load("../lib/notifications-settings-view.ts");
const { module: inbox } = await load("../lib/inbox-api.ts");
const {
  eventLabel,
  kindLabel,
  priorityLabel,
  failureLine,
  quietHoursText,
  validateQuietHours,
  validateEmailAddress,
  timingFields,
  validateTiming,
  EVENT_KEYS,
  KIND_KEYS,
} = view;

test("the production source holds no runtime imports", () => {
  assert.equal(/^import\s+(?!type\b)/m.test(source), false, "only `import type` is allowed");
});

test("every event the server offers has a plain label", () => {
  const expected = {
    escalation: "Customer needs a person",
    approval_needed: "Order needs approval",
    order_submitted: "Order submitted",
    dlq: "Failed task",
    circuit_opened: "Assistant paused itself after errors",
    booking_pending: "Booking waiting for confirmation",
    "inbox.overdue": "Customer waiting too long",
  };
  assert.deepEqual(EVENT_KEYS, Object.keys(expected));
  for (const [key, label] of Object.entries(expected)) assert.equal(eventLabel(key), label, key);
});

test("every personal kind has a plain label", () => {
  const expected = {
    "inbox.assigned": "Assigned to me",
    "inbox.overdue": "Overdue customer",
    "inbox.snooze_due": "Snooze ended",
    "inbox.stale_claim": "Claimed but quiet",
    "inbox.follow_up": "Follow up with customer",
    "inbox.handback": "Hand back to the assistant",
  };
  assert.deepEqual(KIND_KEYS, Object.keys(expected));
  for (const [key, label] of Object.entries(expected)) assert.equal(kindLabel(key), label, key);
});

test("an unknown event, kind or priority returns the raw key and never throws", () => {
  for (const key of ["brand_new", "", "inbox.nope", "constructor", "__proto__", "toString", "hasOwnProperty"]) {
    assert.equal(eventLabel(key), key);
    assert.equal(kindLabel(key), key);
    assert.equal(priorityLabel(key), key);
  }
  for (const bad of [undefined, null, 7, {}, []]) {
    assert.doesNotThrow(() => eventLabel(bad));
    assert.doesNotThrow(() => kindLabel(bad));
    assert.doesNotThrow(() => priorityLabel(bad));
  }
});

test("priority labels", () => {
  assert.equal(priorityLabel("low"), "Low");
  assert.equal(priorityLabel("normal"), "Normal");
  assert.equal(priorityLabel("high"), "High");
  assert.equal(priorityLabel("urgent"), "Urgent");
});

const target = (last_failure) => ({
  id: "0b9c1d52-3f4e-4a6b-8c7d-1e2f3a4b5c6d",
  channel: "email",
  address_masked: "a***@example.test",
  events: [],
  min_priority: "normal",
  quiet_hours: null,
  language: "en",
  is_enabled: true,
  verified: true,
  version: 1,
  last_failure,
});

test("failureLine is null without a failure", () => {
  assert.equal(failureLine(target(null)), null);
});

test("failureLine names the attempts and the time, in UTC unless told otherwise", () => {
  const failure = { at: "2026-11-18T14:02:09Z", attempts: 5 };
  assert.equal(failureLine(target(failure)), "Last delivery failed, 5 attempts, 18 Nov 14:02");
  assert.equal(
    failureLine(target({ at: "2026-11-18T14:02:09Z", attempts: 1 })),
    "Last delivery failed, 1 attempt, 18 Nov 14:02",
  );
  assert.equal(
    failureLine(target({ at: "2026-11-18T01:05:00Z", attempts: 3 })),
    "Last delivery failed, 3 attempts, 18 Nov 01:05",
    "hours and minutes are zero-padded, never 24:xx",
  );
  assert.equal(
    failureLine(target({ at: "2026-11-18T00:05:00Z", attempts: 3 })),
    "Last delivery failed, 3 attempts, 18 Nov 00:05",
  );
});

test("failureLine can write the time in a named zone, and survives bad input", () => {
  const failure = { at: "2026-11-18T14:02:00Z", attempts: 5 };
  assert.equal(
    failureLine(target(failure), "Asia/Kuala_Lumpur"),
    "Last delivery failed, 5 attempts, 18 Nov 22:02",
  );
  assert.equal(
    failureLine(target(failure), "Not/AZone"),
    "Last delivery failed, 5 attempts, 18 Nov 14:02",
    "an unknown zone falls back to UTC",
  );
  assert.equal(
    failureLine(target({ at: "not a date", attempts: 5 })),
    "Last delivery failed, 5 attempts",
    "an unreadable time is left out",
  );
  assert.equal(
    failureLine(target({ at: "2026-11-18T14:02:00Z", attempts: 0 })),
    "Last delivery failed, 18 Nov 14:02",
    "a missing attempt count is left out",
  );
  assert.doesNotMatch(failureLine(target({ at: "x", attempts: Number.NaN })), /NaN|undefined/);
});

test("quietHoursText", () => {
  assert.equal(quietHoursText(null), "No quiet hours");
  assert.equal(
    quietHoursText({ start: "22:00", end: "07:00", tz: "Asia/Kuala_Lumpur" }),
    "22:00 to 07:00 (Asia/Kuala_Lumpur), only urgent alerts",
  );
});

test("validateQuietHours accepts a window and rejects the rest in plain words", () => {
  assert.equal(validateQuietHours("22:00", "07:00", "Asia/Kuala_Lumpur"), null, "may cross midnight");
  assert.equal(validateQuietHours("09:00", "17:30", "UTC"), null);
  assert.equal(validateQuietHours("00:00", "23:59", "UTC"), null);
  assert.match(validateQuietHours("22:00", "22:00", "UTC"), /different/i, "same start and end");
  for (const bad of ["", "7:00", "24:00", "12:60", "12:5", "ab:cd", "12:00:00", " 12:00", "12:00\n"]) {
    assert.ok(validateQuietHours(bad, "07:00", "UTC"), `start ${JSON.stringify(bad)}`);
    assert.ok(validateQuietHours("22:00", bad, "UTC"), `end ${JSON.stringify(bad)}`);
  }
  for (const bad of ["", "   ", "Not/AZone", "Mars/Olympus"]) {
    assert.match(validateQuietHours("22:00", "07:00", bad), /time zone/i, JSON.stringify(bad));
  }
  for (const bad of [undefined, null, 7, {}]) {
    assert.ok(validateQuietHours(bad, bad, bad));
  }
});

test("validateEmailAddress returns null for a good address and a sentence for a bad one", () => {
  for (const good of ["a@b.co", "first.last@example.test", "ops+alerts@mail.example.com", "x@sub.domain.example"]) {
    assert.equal(validateEmailAddress(good), null, good);
  }
  for (const bad of [
    "a@b",
    "",
    "plain",
    "@example.test",
    "a@",
    "a b@example.test",
    "a@exa mple.test",
    " a@example.test",
    "a@example.test ",
    "a@example.test\n",
    "a\n@example.test",
    "a\r@example.test",
    "a@@example.test",
    "a,b@example.test",
    "a@example..test",
    "a@-example.test",
    "<a@example.test>",
    `${"a".repeat(65)}@example.test`,
    `a@${"b".repeat(250)}.test`,
  ]) {
    const problem = validateEmailAddress(bad);
    assert.equal(typeof problem, "string", JSON.stringify(bad));
    assert.ok(problem.length > 0);
  }
  for (const bad of [undefined, null, 7, {}, []]) assert.equal(typeof validateEmailAddress(bad), "string");
  const longest = `${"a".repeat(64)}@${"b".repeat(63)}.${"c".repeat(63)}.${"d".repeat(61)}`;
  assert.equal(longest.length, 254);
  assert.equal(validateEmailAddress(longest), null, "254 characters is allowed");
  assert.equal(typeof validateEmailAddress(`${longest}x`), "string", "255 is not");
});

test("the nine timing fields match the Inbox API ranges, in order", () => {
  assert.deepEqual(
    timingFields.map((f) => f.key),
    Object.keys(inbox.SETTINGS_RANGES),
  );
  for (const field of timingFields) {
    assert.deepEqual([field.min, field.max], [...inbox.SETTINGS_RANGES[field.key]], field.key);
    assert.ok(field.label.length > 0);
    assert.ok(["minutes", "hours", "count"].includes(field.unit), field.key);
  }
  assert.deepEqual(
    timingFields.filter((f) => f.zeroOff).map((f) => f.key),
    ["follow_up_hours", "stale_claim_hours", "handback_prompt_hours"],
    "only these three turn off at 0",
  );
});

const GOOD = {
  target_urgent_min: "15",
  target_high_min: "60",
  target_normal_min: "240",
  target_low_min: "1440",
  repeat_minutes: "60",
  max_repeats: "3",
  follow_up_hours: "24",
  stale_claim_hours: "4",
  handback_prompt_hours: "48",
};

test("validateTiming accepts good values, typed or numeric, and 0 where it means off", () => {
  assert.deepEqual(validateTiming(GOOD), {});
  const numeric = Object.fromEntries(Object.entries(GOOD).map(([k, v]) => [k, Number(v)]));
  assert.deepEqual(validateTiming(numeric), {});
  assert.deepEqual(
    validateTiming({ ...GOOD, follow_up_hours: "0", stale_claim_hours: "0", handback_prompt_hours: "0", max_repeats: "0" }),
    {},
  );
});

test("validateTiming enforces each range exactly, both edges", () => {
  const edges = {
    target_urgent_min: [5, 10080],
    target_high_min: [5, 10080],
    target_normal_min: [5, 10080],
    target_low_min: [5, 10080],
    repeat_minutes: [15, 1440],
    max_repeats: [0, 10],
    follow_up_hours: [0, 720],
    stale_claim_hours: [0, 168],
    handback_prompt_hours: [0, 720],
  };
  for (const [key, [low, high]] of Object.entries(edges)) {
    assert.deepEqual(validateTiming({ ...GOOD, [key]: String(low) }), {}, `${key} at ${low}`);
    assert.deepEqual(validateTiming({ ...GOOD, [key]: String(high) }), {}, `${key} at ${high}`);
    for (const out of [low - 1, high + 1]) {
      const errors = validateTiming({ ...GOOD, [key]: String(out) });
      assert.deepEqual(Object.keys(errors), [key], `${key} at ${out}`);
      assert.match(errors[key], new RegExp(`${low}.*${high}`), `${key} names its range`);
    }
  }
});

test("validateTiming rejects anything that is not a whole number and reports each bad field", () => {
  for (const bad of ["", "  ", "abc", "1.5", "-1", "+5", "1e2", "0x10", "10 minutes", null, undefined, Number.NaN, 7.5, {}]) {
    const errors = validateTiming({ ...GOOD, target_urgent_min: bad });
    assert.deepEqual(Object.keys(errors), ["target_urgent_min"], JSON.stringify(bad));
  }
  const many = validateTiming({ ...GOOD, repeat_minutes: "5", max_repeats: "11" });
  assert.deepEqual(Object.keys(many).sort(), ["max_repeats", "repeat_minutes"]);
  const missing = validateTiming({});
  assert.equal(Object.keys(missing).length, 9, "a missing field is an error, not a pass");
});
