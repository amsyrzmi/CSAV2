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
const view = await import(moduleUrl(
  (await readFile(new URL("../lib/bookings-view.ts", import.meta.url), "utf8"))
    .replace(/import type [^;]+;/g, ""),
));
const {
  hoursLines, hoursLabel, periodsToDays, daysToPeriods, hoursProblem, LATEST_CLOSE,
  typeToForm, emptyTypeForm, formToTypeIn, formToTypePatch, typeProblem,
  audienceLabel, deliveryLabel, locationLabel, durationText, typeSummary, isEmail, zonedToUtc,
  blockWhen, emailForViewer, todayInZone,
} = view;

const week = (opens, closes, days = [0, 1, 2, 3, 4]) => days.map((weekday) => ({ weekday, opens, closes }));

test("the company's default week reads as Monday to Friday, marked default", () => {
  const hours = { subject_kind: "company", subject_id: null, is_default: true, periods: week("09:00", "18:00") };
  assert.deepEqual(hoursLines(hours), ["Monday to Friday, 09:00 to 18:00 (default)"]);
  assert.equal(hoursLabel(hours), "Monday to Friday, 09:00 to 18:00 (default)");
});

test("a person or room with nothing stored follows the company, whatever is_default says", () => {
  for (const kind of ["person", "resource"]) {
    for (const isDefault of [true, false]) {
      assert.equal(
        hoursLabel({ subject_kind: kind, subject_id: "x", is_default: isDefault, periods: [] }),
        "Same as the company",
      );
    }
  }
});

test("a person's own hours are not marked default", () => {
  const hours = { subject_kind: "person", subject_id: "x", is_default: false, periods: week("10:00", "16:00", [1, 2, 3]) };
  assert.deepEqual(hoursLines(hours), ["Tuesday to Thursday, 10:00 to 16:00"]);
});

test("hours group by day, join runs and list split shifts", () => {
  const periods = [
    ...week("09:00", "12:00", [0, 1]), ...week("13:00", "17:00", [0, 1]),
    ...week("09:00", "13:00", [5]), ...week("09:00", "13:00", [2]),
  ];
  const lines = hoursLines({ subject_kind: "company", subject_id: null, is_default: false, periods });
  assert.deepEqual(lines, [
    "Monday and Tuesday, 09:00 to 12:00 and 13:00 to 17:00",
    "Wednesday, Saturday, 09:00 to 13:00",
  ]);
});

test("a company with no periods and no default is closed every day", () => {
  assert.deepEqual(
    hoursLines({ subject_kind: "company", subject_id: null, is_default: false, periods: [] }),
    ["Closed every day"],
  );
});

test("periods round-trip through the seven-day editor, in time order", () => {
  const periods = [
    { weekday: 2, opens: "13:00", closes: "17:00" },
    { weekday: 2, opens: "09:00", closes: "12:00" },
    { weekday: 6, opens: "10:00", closes: "11:00" },
  ];
  const days = periodsToDays(periods);
  assert.equal(days.length, 7);
  assert.deepEqual(days[0], []);
  assert.deepEqual(days[2], [{ opens: "09:00", closes: "12:00" }, { opens: "13:00", closes: "17:00" }]);
  assert.deepEqual(daysToPeriods(days), [
    { weekday: 2, opens: "09:00", closes: "12:00" },
    { weekday: 2, opens: "13:00", closes: "17:00" },
    { weekday: 6, opens: "10:00", closes: "11:00" },
  ]);
});

test("hours cannot close at midnight: the latest close is 23:59", () => {
  assert.equal(LATEST_CLOSE, "23:59");
  const one = (opens, closes) => periodsToDays([{ weekday: 0, opens, closes }]);
  assert.equal(hoursProblem(one("09:00", "23:59")), null);
  assert.match(hoursProblem(one("09:00", "00:00")), /23:59/);
  assert.match(hoursProblem(one("18:00", "09:00")), /before it closes/);
  assert.match(hoursProblem(one("", "09:00")), /choose/i);
});

test("periods on the same day may not overlap, touching is fine", () => {
  const overlap = periodsToDays([
    { weekday: 1, opens: "09:00", closes: "13:00" }, { weekday: 1, opens: "12:00", closes: "17:00" },
  ]);
  assert.match(hoursProblem(overlap), /overlap/);
  const touching = periodsToDays([
    { weekday: 1, opens: "09:00", closes: "13:00" }, { weekday: 1, opens: "13:00", closes: "17:00" },
  ]);
  assert.equal(hoursProblem(touching), null);
});

const type = {
  id: "t1", name: "Consultation", duration_minutes: 60, buffer_minutes: 15, audience: "both",
  delivery: "named", resource_rule: "any", location_kind: "in_person", address: "1 Jalan Contoh",
  meeting_link: null, needs_confirmation: true, min_notice_minutes: 120, max_advance_days: 30,
  change_cutoff_minutes: 1440, is_active: true, version: 3, person_ids: ["p1", "p2"], resource_ids: ["r1"],
};

test("a booking type's form values round-trip", () => {
  const form = typeToForm(type);
  assert.equal(form.duration, "60");
  assert.deepEqual(formToTypeIn(form), {
    name: "Consultation", duration_minutes: 60, buffer_minutes: 15, audience: "both", delivery: "named",
    resource_rule: "any", location_kind: "in_person", address: "1 Jalan Contoh", meeting_link: null,
    needs_confirmation: true, min_notice_minutes: 120, max_advance_days: 30, change_cutoff_minutes: 1440,
    person_ids: ["p1", "p2"], resource_ids: ["r1"],
  });
  assert.deepEqual(formToTypePatch(form, 3), { ...formToTypeIn(form), version: 3, is_active: true });
});

test("a type keeps only what its rules use", () => {
  const online = { ...typeToForm(type), locationKind: "online", link: " https://meet.example.test/x ", resourceRule: "none" };
  const body = formToTypeIn(online);
  assert.equal(body.address, null);
  assert.equal(body.meeting_link, "https://meet.example.test/x");
  assert.deepEqual(body.resource_ids, []);
  assert.equal(formToTypeIn({ ...typeToForm(type), locationKind: "phone" }).address, null);
});

test("the new type form starts with the server's defaults and asks for what is missing", () => {
  const form = emptyTypeForm();
  assert.equal(form.name, "");
  assert.equal(form.minNotice, "60");
  assert.equal(form.maxAdvance, "60");
  assert.equal(form.cutoff, "120");
  assert.equal(form.delivery, "any");
  assert.match(typeProblem(form), /name/i);
  assert.equal(typeProblem({ ...form, name: "Haircut", duration: "30" }), null);
});

test("a type form says what is missing in plain words", () => {
  const ok = { ...emptyTypeForm(), name: "Visit", duration: "30" };
  assert.match(typeProblem({ ...ok, duration: "0" }), /length/i);
  assert.match(typeProblem({ ...ok, duration: "1441" }), /24 hours/);
  assert.match(typeProblem({ ...ok, delivery: "named", personIds: [] }), /who/i);
  assert.match(typeProblem({ ...ok, resourceRule: "specific", resourceIds: [] }), /room/i);
  assert.match(typeProblem({ ...ok, locationKind: "in_person", address: " " }), /address/i);
  assert.match(typeProblem({ ...ok, locationKind: "online", link: "http://x.test" }), /https/);
  assert.match(typeProblem({ ...ok, buffer: "-1" }), /whole number/i);
});

test("labels and summaries are plain language", () => {
  assert.equal(audienceLabel("both"), "Customers and staff");
  assert.equal(audienceLabel("staff"), "Staff only");
  assert.equal(deliveryLabel("any"), "Any free person");
  assert.equal(locationLabel("online"), "Online");
  assert.equal(durationText(30), "30 minutes");
  assert.equal(durationText(60), "1 hour");
  assert.equal(durationText(90), "1 hour 30 minutes");
  assert.equal(durationText(1440), "1 day");
  assert.equal(durationText(0), "None");
  assert.equal(typeSummary(type), "1 hour · Customers and staff · A person you choose · In person");
});

test("emails are one plain address", () => {
  assert.ok(isEmail("aiman@example.test"));
  for (const bad of ["", "aiman", "a b@example.test", "a@b", "<a@example.test>", "a,b@example.test"]) {
    assert.ok(!isEmail(bad), bad);
  }
});

test("only an Admin is shown a person's email", () => {
  assert.equal(emailForViewer("a@example.test", true), "a@example.test");
  assert.equal(emailForViewer("a@example.test", false), null);
  assert.equal(emailForViewer(null, true), null);
});

test("a wall-clock time in the company's zone becomes the right UTC instant", () => {
  assert.equal(zonedToUtc("2026-11-05", "09:00", "Asia/Kuala_Lumpur"), "2026-11-05T01:00:00.000Z");
  assert.equal(zonedToUtc("2026-11-05", "09:00", "UTC"), "2026-11-05T09:00:00.000Z");
  // New York leaves summer time on 1 Nov 2026: EDT before, EST after.
  assert.equal(zonedToUtc("2026-10-31", "12:00", "America/New_York"), "2026-10-31T16:00:00.000Z");
  assert.equal(zonedToUtc("2026-11-02", "12:00", "America/New_York"), "2026-11-02T17:00:00.000Z");
  assert.equal(zonedToUtc("2026-11-05", "09:00", "Not/AZone"), null);
  assert.equal(zonedToUtc("nope", "09:00", "UTC"), null);
});

test("a block reads in the company's zone", () => {
  assert.equal(
    blockWhen("2026-11-05T01:00:00Z", "2026-11-05T04:00:00Z", "Asia/Kuala_Lumpur"),
    "5 Nov 2026, 09:00 to 12:00",
  );
  assert.equal(
    blockWhen("2026-11-05T01:00:00Z", "2026-11-07T04:00:00Z", "Asia/Kuala_Lumpur"),
    "5 Nov 2026, 09:00 to 7 Nov 2026, 12:00",
  );
});

test("today is read on the company's calendar", () => {
  const instant = new Date("2026-11-04T20:00:00Z");
  assert.equal(todayInZone("Asia/Kuala_Lumpur", instant), "2026-11-05");
  assert.equal(todayInZone("UTC", instant), "2026-11-04");
  assert.equal(todayInZone(null, instant), null);
  assert.equal(todayInZone("Not/AZone", instant), null);
});
