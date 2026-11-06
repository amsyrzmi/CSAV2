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
  weekDays, layoutDay, findingCopy, emailNote, addDays, timeInZone, dayInZone, shiftWeek, weekTitle,
  bookingTitle, mapsLink, wazeLink, safeMeetingLink, canChangeBooking,
  emptyBookingForm, bookingProblem, formToBookingIn, isPhone, entriesOnDay,
  inactiveNotes, customerNote, switchedOffPeople, severalSwitchedOff,
  settingsToForm, settingsProblem, formToSettingsIn, momentText, askedText, sourceNote, isChangeRequest,
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

const KL = "Asia/Kuala_Lumpur";
const NY = "America/New_York";

test("the week and its days follow the company's zone, not the browser's", () => {
  // Sunday 8 Nov 2026, 17:00 UTC: already Monday 9 Nov in Kuala Lumpur, still Sunday in New York.
  const anchor = new Date("2026-11-08T17:00:00Z");
  const kl = weekDays(anchor, KL);
  const ny = weekDays(anchor, NY);
  assert.deepEqual(kl.map((d) => d.day), [
    "2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13", "2026-11-14", "2026-11-15",
  ]);
  assert.equal(ny[0].day, "2026-11-02");
  assert.equal(ny[6].day, "2026-11-08");
  assert.equal(kl[0].label, "Mon 9 Nov");
  // Each day runs from its own midnight, so the instants differ by the zone's offset.
  assert.equal(kl[0].start, "2026-11-08T16:00:00.000Z");
  assert.equal(kl[0].end, "2026-11-09T16:00:00.000Z");
  assert.equal(ny[0].start, "2026-11-02T05:00:00.000Z");
  assert.equal(ny[0].end, "2026-11-03T05:00:00.000Z");
});

test("a day with a clock change still ends at the next midnight", () => {
  // New York leaves summer time on 1 Nov 2026: that Sunday has 25 hours.
  const sunday = weekDays(new Date("2026-11-01T12:00:00Z"), NY)[6];
  assert.equal(sunday.day, "2026-11-01");
  assert.equal(sunday.start, "2026-11-01T04:00:00.000Z");
  assert.equal(sunday.end, "2026-11-02T05:00:00.000Z");
});

test("moving the week keeps to whole weeks on the company's calendar", () => {
  const anchor = new Date("2026-11-08T17:00:00Z");
  assert.equal(weekDays(shiftWeek(anchor, 1, KL), KL)[0].day, "2026-11-16");
  assert.equal(weekDays(shiftWeek(anchor, -1, KL), KL)[0].day, "2026-11-02");
  assert.equal(addDays("2026-12-31", 1), "2027-01-01");
  assert.equal(addDays("2026-03-01", -1), "2026-02-28");
  assert.equal(weekTitle(weekDays(anchor, KL)), "9 Nov to 15 Nov 2026");
  assert.equal(weekTitle(weekDays(new Date("2026-12-30T04:00:00Z"), KL)), "28 Dec 2026 to 3 Jan 2027");
});

test("times and days of a booking are read on the company's clock", () => {
  assert.equal(timeInZone("2026-11-05T01:30:00Z", KL), "09:30");
  assert.equal(timeInZone("2026-11-05T01:30:00Z", NY), "20:30");
  assert.equal(dayInZone("2026-11-05T01:30:00Z", KL), "2026-11-05");
  assert.equal(dayInZone("2026-11-05T01:30:00Z", NY), "2026-11-04");
});

const at = (start, end, extra = {}) => ({
  starts_at: `2026-11-05T${start}:00Z`, ends_at: `2026-11-05T${end}:00Z`, ...extra,
});

test("overlapping bookings lay out side by side, the rest take the full width", () => {
  const placed = layoutDay(
    [
      at("01:00", "02:00", { id: "a" }),
      at("01:30", "02:30", { id: "b" }),
      at("01:45", "02:15", { id: "c" }),
      at("04:00", "05:00", { id: "d" }),
    ],
    KL,
  );
  const by = Object.fromEntries(placed.map((p) => [p.item.id, p]));
  assert.equal(by.a.startMinute, 9 * 60);
  assert.equal(by.a.endMinute, 10 * 60);
  assert.deepEqual([by.a.lane, by.b.lane, by.c.lane], [0, 1, 2]);
  assert.deepEqual([by.a.lanes, by.b.lanes, by.c.lanes], [3, 3, 3]);
  assert.deepEqual([by.d.lane, by.d.lanes], [0, 1]);
});

test("a lane is reused once its booking has ended", () => {
  const placed = layoutDay(
    [at("01:00", "02:00", { id: "a" }), at("01:30", "03:00", { id: "b" }), at("02:00", "02:30", { id: "c" })],
    KL,
  );
  const by = Object.fromEntries(placed.map((p) => [p.item.id, p]));
  assert.deepEqual([by.a.lane, by.b.lane, by.c.lane], [0, 1, 0]);
  assert.equal(by.c.lanes, 2);
});

test("a booking that runs past midnight stops at midnight, and a busy row lays out too", () => {
  const placed = layoutDay(
    [{ starts_at: "2026-11-05T14:00:00Z", ends_at: "2026-11-05T18:00:00Z", label: "Busy" }],
    KL,
  );
  assert.equal(placed[0].startMinute, 22 * 60);
  assert.equal(placed[0].endMinute, 24 * 60);
});

test("the finding copy is exact and never names leave", () => {
  assert.equal(findingCopy("outside_hours"), "Outside working hours.");
  assert.equal(findingCopy("closed_day"), "This is a closed day.");
  assert.equal(findingCopy("unavailable"), "Not available that day.");
  assert.equal(findingCopy("short_notice"), "Shorter notice than this booking type allows.");
  assert.equal(findingCopy("something_new"), "This time needs a second look.");
  for (const code of ["outside_hours", "closed_day", "unavailable", "short_notice", "x"]) {
    assert.doesNotMatch(findingCopy(code), /leave/i);
  }
});

test("the mail note says what happened, and nothing when all went well", () => {
  assert.equal(emailNote("off"), "Mail is off, so nobody was emailed.");
  assert.equal(emailNote("failed"), "We couldn't email everyone. Some may not have got it.");
  assert.equal(emailNote("none"), "Nobody on this booking has an email address.");
  assert.equal(emailNote("sent"), null);
  assert.equal(emailNote(null), null);
  assert.equal(emailNote(undefined), null);
});

const APPOINTMENT = {
  id: "t1", name: "Consultation", duration_minutes: 60, audience: "customers", delivery: "named",
  resource_rule: "none", person_ids: ["p1"], resource_ids: [],
};

test("a booking of a type asks for its people and a customer, then sends the type's way", () => {
  const form = { ...emptyBookingForm("2026-11-05"), typeId: "t1", time: "10:00" };
  assert.equal(bookingProblem(form, APPOINTMENT, KL), "Choose who this booking is with.");
  form.personIds = ["p1"];
  assert.equal(bookingProblem(form, APPOINTMENT, KL), "Choose a customer, or enter a new one with a phone number or an email.");
  form.walkName = "Aina Synthetic";
  assert.equal(bookingProblem(form, APPOINTMENT, KL), "Enter the customer's phone number or email.");
  form.walkPhone = "012-345 6789";
  assert.equal(bookingProblem(form, APPOINTMENT, KL), null);
  form.guests = [{ name: "", email: " guest@example.test " }, { name: "", email: "" }];
  const body = formToBookingIn(form, APPOINTMENT, KL, ["outside_hours"]);
  assert.deepEqual(body, {
    starts_at: "2026-11-05T02:00:00.000Z",
    type_id: "t1",
    people: ["p1"],
    resources: [],
    walk_in: { name: "Aina Synthetic", phone: "012-345 6789", email: null },
    guests: [{ name: null, email: "guest@example.test" }],
    acknowledged: ["outside_hours"],
  });
});

test("a chosen customer is sent by id, and a type for staff has no customer at all", () => {
  const form = { ...emptyBookingForm("2026-11-05"), typeId: "t1", time: "10:00", personIds: ["p1"], contactId: "c1", walkName: "Ignored" };
  assert.equal(formToBookingIn(form, APPOINTMENT, KL).contact_id, "c1");
  assert.equal("walk_in" in formToBookingIn(form, APPOINTMENT, KL), false);
  const staffOnly = { ...APPOINTMENT, audience: "staff" };
  const body = formToBookingIn({ ...form, contactId: "" }, staffOnly, KL);
  assert.equal("walk_in" in body || "contact_id" in body, false);
  assert.equal(bookingProblem({ ...form, contactId: "" }, staffOnly, KL), null);
  // "any" delivery lets the server pick, so no people are sent.
  assert.deepEqual(formToBookingIn(form, { ...APPOINTMENT, delivery: "any" }, KL).people, []);
});

test("a meeting needs a title, a length and someone or somewhere, and keeps only what it uses", () => {
  const form = { ...emptyBookingForm("2026-11-05"), time: "15:00", title: "Planning", minutes: "45" };
  assert.equal(bookingProblem({ ...form, title: " " }, null, KL), "Give the meeting a title.");
  assert.equal(bookingProblem({ ...form, minutes: "0" }, null, KL), "Enter the length as a whole number of minutes, from 1 to 1,440 (24 hours).");
  assert.equal(bookingProblem(form, null, KL), "Choose at least one colleague or room.");
  form.personIds = ["p1", "p2"];
  form.locationKind = "online";
  form.link = "http://meet.example.test/x";
  assert.equal(bookingProblem(form, null, KL), "The meeting link must start with https://.");
  form.link = "https://meet.example.test/x";
  form.address = "Left over";
  form.isPrivate = true;
  assert.equal(bookingProblem(form, null, KL), null);
  assert.deepEqual(formToBookingIn(form, null, KL), {
    starts_at: "2026-11-05T07:00:00.000Z",
    title: "Planning",
    minutes: 45,
    people: ["p1", "p2"],
    resources: [],
    private: true,
    location_kind: "online",
    meeting_link: "https://meet.example.test/x",
    guests: [],
    acknowledged: [],
  });
});

test("the day and time are required, a guest needs an address, a phone is digits", () => {
  const form = { ...emptyBookingForm("2026-11-05"), time: "", title: "x", personIds: ["p1"] };
  assert.equal(bookingProblem(form, null, KL), "Choose the day and time.");
  form.time = "10:00";
  form.guests = [{ name: "Guest", email: "not-an-address" }];
  assert.equal(bookingProblem(form, null, KL), "Check the guest's email address: not-an-address.");
  assert.equal(isPhone("+60 12-345 6789"), true);
  assert.equal(isPhone("call me"), false);
  assert.equal(isPhone("12"), false);
});

test("titles, links and who may change a booking", () => {
  assert.equal(bookingTitle({ redacted: true, title: "Busy", kind: "meeting" }), "Busy");
  assert.equal(bookingTitle({ redacted: false, title: "Consultation", kind: "appointment" }), "Consultation");
  assert.equal(bookingTitle({ redacted: false, title: null, kind: "appointment" }), "Appointment");
  assert.equal(bookingTitle({ redacted: false, title: null, kind: "meeting" }), "Meeting");
  assert.equal(
    mapsLink("1 Jalan Contoh & Sons"),
    "https://www.google.com/maps/search/?api=1&query=1%20Jalan%20Contoh%20%26%20Sons",
  );
  assert.equal(wazeLink("1 Jalan Contoh"), "https://waze.com/ul?q=1%20Jalan%20Contoh&navigate=yes");
  assert.equal(safeMeetingLink("https://meet.example.test/abc"), "https://meet.example.test/abc");
  assert.equal(safeMeetingLink("http://meet.example.test/abc"), null);
  assert.equal(safeMeetingLink("javascript:alert(1)"), null);
  assert.equal(safeMeetingLink(null), null);

  // The server says who may change a booking; the page adds only that it is still live.
  const live = { can_change: true, status: "confirmed" };
  assert.equal(canChangeBooking(live), true);
  assert.equal(canChangeBooking({ can_change: true, status: "pending" }), true);
  assert.equal(canChangeBooking({ can_change: false, status: "confirmed" }), false);
  assert.equal(canChangeBooking({ can_change: true, status: "cancelled" }), false);
  assert.equal(canChangeBooking({ can_change: true, status: "no_show" }), false);
});

const day = (days, iso) => days.find((d) => d.day === iso);

test("a booking is split across each day it touches, in the company's zone", () => {
  // Sunday 8 Nov 22:00 to Monday 9 Nov 02:00 in Kuala Lumpur.
  const overnight = { id: "o", starts_at: "2026-11-08T14:00:00Z", ends_at: "2026-11-08T18:00:00Z" };
  const days = weekDays(new Date("2026-11-11T04:00:00Z"), KL);
  assert.equal(days[0].day, "2026-11-09");
  // It started last week, and still belongs on this week's Monday.
  const monday = entriesOnDay([overnight], day(days, "2026-11-09"));
  assert.deepEqual(monday.map((i) => i.id), ["o"]);
  const [piece] = layoutDay(monday, KL, day(days, "2026-11-09"));
  assert.deepEqual([piece.startMinute, piece.endMinute], [0, 120]);
  assert.deepEqual([piece.continuesBefore, piece.continuesAfter], [true, false]);
  assert.equal(piece.item, overnight);
  // Its first part, on the Sunday of the week before.
  const before = weekDays(new Date("2026-11-04T04:00:00Z"), KL);
  const sunday = day(before, "2026-11-08");
  const [first] = layoutDay(entriesOnDay([overnight], sunday), KL, sunday);
  assert.deepEqual([first.startMinute, first.endMinute], [22 * 60, 1440]);
  assert.deepEqual([first.continuesBefore, first.continuesAfter], [false, true]);
  // And on no other day.
  assert.deepEqual(entriesOnDay([overnight], day(before, "2026-11-07")), []);
  assert.deepEqual(entriesOnDay([overnight], day(days, "2026-11-10")), []);
});

test("a long booking fills the days between, and one ending at midnight stops there", () => {
  const days = weekDays(new Date("2026-11-11T04:00:00Z"), KL);
  // Monday 22:00 to Wednesday 02:00 (Tuesday at 18:00Z is Wednesday 02:00 in Kuala Lumpur).
  const long = { starts_at: "2026-11-09T14:00:00Z", ends_at: "2026-11-10T18:00:00Z" };
  const placed = days.map((d) => layoutDay(entriesOnDay([long], d), KL, d)[0]);
  assert.deepEqual(placed.slice(0, 4).map((p) => p && [p.startMinute, p.endMinute]), [
    [22 * 60, 1440], [0, 1440], [0, 120], undefined,
  ]);
  assert.deepEqual(placed[1] && [placed[1].continuesBefore, placed[1].continuesAfter], [true, true]);
  // Ends exactly at Tuesday 00:00: nothing on Tuesday.
  const ends = { starts_at: "2026-11-09T14:00:00Z", ends_at: "2026-11-09T16:00:00Z" };
  assert.equal(entriesOnDay([ends], days[1]).length, 0);
  const [piece] = layoutDay(entriesOnDay([ends], days[0]), KL, days[0]);
  assert.equal(piece.continuesAfter, false);
  // It is drawn to the end of the day, not shortened to half an hour.
  assert.deepEqual([piece.startMinute, piece.endMinute], [22 * 60, 1440]);
});

test("a booking that ends at midnight is drawn to the end of a 23-hour and a 25-hour day", () => {
  // The grid counts wall-clock minutes, so a day always ends at 1440 whatever its real length.
  const cases = [
    // New York leaves summer time on 1 Nov 2026 (25 hours): 22:00 to 00:00 is 03:00Z to 05:00Z.
    [new Date("2026-11-01T12:00:00Z"), "2026-11-01", "2026-11-02T03:00:00Z", "2026-11-02T05:00:00Z"],
    // And starts it on 8 Mar 2026 (23 hours): 22:00 to 00:00 is 02:00Z to 04:00Z the next day.
    [new Date("2026-03-08T12:00:00Z"), "2026-03-08", "2026-03-09T02:00:00Z", "2026-03-09T04:00:00Z"],
  ];
  for (const [anchor, iso, starts_at, ends_at] of cases) {
    const target = day(weekDays(anchor, NY), iso);
    const hours = (new Date(target.end) - new Date(target.start)) / 3600000;
    assert.ok(hours === 25 || hours === 23, `${iso} is ${hours} hours`);
    const [piece] = layoutDay(entriesOnDay([{ starts_at, ends_at }], target), NY, target);
    assert.deepEqual([piece.startMinute, piece.endMinute, piece.continuesAfter], [22 * 60, 1440, false], iso);
  }
});

test("a busy row is split across days the same way", () => {
  const days = weekDays(new Date("2026-11-11T04:00:00Z"), KL);
  const busy = { starts_at: "2026-11-08T14:00:00Z", ends_at: "2026-11-08T18:00:00Z", label: "Busy" };
  const [piece] = layoutDay(entriesOnDay([busy], days[0]), KL, days[0]);
  assert.deepEqual([piece.startMinute, piece.endMinute, piece.continuesBefore], [0, 120, true]);
});

test("on a day with a clock change the minutes still read on the wall clock", () => {
  // New York, 1 Nov 2026 has 25 hours; a booking 23:00 to 01:00 next day.
  const days = weekDays(new Date("2026-11-01T12:00:00Z"), NY);
  const sunday = day(days, "2026-11-01");
  const late = { starts_at: "2026-11-02T03:00:00Z", ends_at: "2026-11-02T06:00:00Z" };
  const [piece] = layoutDay(entriesOnDay([late], sunday), NY, sunday);
  assert.deepEqual([piece.startMinute, piece.endMinute, piece.continuesAfter], [22 * 60, 1440, true]);
});

test("a booking says who on it is switched off, and names nobody on a redacted one", () => {
  const person = (name, is_active) => ({ id: name, display_name: name, is_active });
  const room = (name, is_active) => ({ id: name, name, is_active });
  const base = { redacted: false, people: [], resources: [] };
  assert.deepEqual(inactiveNotes(base), []);
  assert.deepEqual(inactiveNotes({ ...base, people: [person("Aina", true)], resources: [room("Room A", true)] }), []);
  assert.deepEqual(inactiveNotes({ ...base, people: [person("Aina", false)] }), ["Aina isn't taking bookings any more."]);
  assert.deepEqual(
    inactiveNotes({ ...base, people: [person("Aina", false), person("Bakar", false), person("Chen", true)] }),
    ["Aina and Bakar aren't taking bookings any more."],
  );
  assert.deepEqual(
    inactiveNotes({ ...base, people: [person("Aina", false), person("Bakar", false), person("Chen", false)] }),
    ["Aina, Bakar and Chen aren't taking bookings any more."],
  );
  assert.deepEqual(inactiveNotes({ ...base, resources: [room("Room A", false)] }), ["Room A isn't available any more."]);
  assert.deepEqual(
    inactiveNotes({ ...base, people: [person("Aina", false)], resources: [room("Room A", false)] }),
    ["Aina isn't taking bookings any more.", "Room A isn't available any more."],
  );
  assert.deepEqual(inactiveNotes({ ...base, redacted: true, people: [person("Aina", false)] }), []);
});

test("the people a type names who are switched off", () => {
  const people = [
    { id: "a", display_name: "Aina", is_active: false },
    { id: "b", display_name: "Bakar", is_active: true },
  ];
  assert.deepEqual(switchedOffPeople(["a", "b", "zzz"], people), ["Aina"]);
  assert.deepEqual(switchedOffPeople(["b"], people), []);
});

test("a several-people type with someone switched off says so in a sentence", () => {
  assert.equal(severalSwitchedOff([]), null);
  assert.equal(
    severalSwitchedOff(["Aina"]),
    "Aina isn't taking bookings any more, so this booking type can't be booked until an Admin changes it.",
  );
  assert.match(severalSwitchedOff(["Aina", "Bakar"]), /^Aina and Bakar aren't taking bookings any more/);
});

test("the customer note appears only when the customer was not told", () => {
  assert.equal(customerNote(false), "Customer wasn't told.");
  assert.equal(customerNote(true), null);
  assert.equal(customerNote(null), null);
  assert.equal(customerNote(undefined), null);
});

test("the reminder time shows in days when it is whole days, else in hours, and is stored as minutes", () => {
  const read = (minutes) => settingsToForm({ reminder_minutes: minutes, pending_hours: 24, customer_limit: 5 });
  assert.deepEqual(read(1440), { reminder: "1", reminderUnit: "days", pendingHours: "24", limit: "5" });
  assert.deepEqual(read(10080), { reminder: "7", reminderUnit: "days", pendingHours: "24", limit: "5" });
  assert.deepEqual(read(120), { reminder: "2", reminderUnit: "hours", pendingHours: "24", limit: "5" });
  assert.deepEqual(read(90), { reminder: "1.5", reminderUnit: "hours", pendingHours: "24", limit: "5" });
  assert.deepEqual(read(15), { reminder: "0.25", reminderUnit: "hours", pendingHours: "24", limit: "5" });
  // A value that hours cannot say exactly is kept in minutes rather than rounded.
  assert.deepEqual(read(100), { reminder: "100", reminderUnit: "minutes", pendingHours: "24", limit: "5" });

  const send = (form, version = 3) => formToSettingsIn({ ...read(1440), ...form }, version);
  assert.deepEqual(send({}), { reminder_minutes: 1440, pending_hours: 24, customer_limit: 5, version: 3 });
  assert.equal(send({ reminder: "2", reminderUnit: "days" }).reminder_minutes, 2880);
  assert.equal(send({ reminder: "1.5", reminderUnit: "hours" }).reminder_minutes, 90);
  assert.equal(send({ reminder: "0.25", reminderUnit: "hours" }).reminder_minutes, 15);
  assert.equal(send({ reminder: "1.1", reminderUnit: "hours" }).reminder_minutes, 66);
  assert.equal(send({ reminder: "100", reminderUnit: "minutes" }).reminder_minutes, 100);
  assert.deepEqual(send({ pendingHours: " 48 ", limit: "10" }, 0), {
    reminder_minutes: 1440, pending_hours: 48, customer_limit: 10, version: 0,
  });
});

test("the settings form names the first thing the server would refuse, in plain words", () => {
  const form = (change) => ({ reminder: "1", reminderUnit: "days", pendingHours: "24", limit: "5", ...change });
  assert.equal(settingsProblem(form({})), null);
  assert.equal(settingsProblem(form({ reminder: "0.25", reminderUnit: "hours" })), null);
  assert.equal(settingsProblem(form({ reminder: "7", reminderUnit: "days" })), null);
  assert.match(settingsProblem(form({ reminder: "" })), /^Enter how long before a booking to remind people/);
  assert.match(settingsProblem(form({ reminder: "abc" })), /^Enter how long before/);
  assert.match(settingsProblem(form({ reminder: "0.1", reminderUnit: "hours" })), /at least 15 minutes/);
  assert.match(settingsProblem(form({ reminder: "8", reminderUnit: "days" })), /at most 7 days/);
  assert.match(settingsProblem(form({ reminder: "1.333", reminderUnit: "hours" })), /^Enter how long before/);
  assert.match(settingsProblem(form({ pendingHours: "0" })), /whole number of hours, from 1 to 168/);
  assert.match(settingsProblem(form({ pendingHours: "169" })), /from 1 to 168/);
  assert.match(settingsProblem(form({ pendingHours: "1.5" })), /from 1 to 168/);
  assert.match(settingsProblem(form({ limit: "0" })), /whole number, from 1 to 50/);
  assert.match(settingsProblem(form({ limit: "51" })), /from 1 to 50/);
  assert.match(settingsProblem(form({ limit: "" })), /from 1 to 50/);
});

test("a moment reads on the company's clock, not the browser's", () => {
  assert.equal(momentText("2026-11-05T01:00:00Z", "Asia/Kuala_Lumpur"), "5 Nov 2026, 09:00");
  assert.equal(momentText("2026-11-05T01:00:00Z", "America/New_York"), "4 Nov 2026, 20:00");
});

const asked = (id, original, extra = {}) => ({
  id, status: "pending", replaces_booking_id: original, starts_at: "2026-11-09T02:00:00Z",
  ends_at: "2026-11-09T03:00:00Z", ...extra,
});
const live = (id, extra = {}) => ({
  id, status: "confirmed", replaces_booking_id: null, starts_at: "2026-11-05T01:00:00Z",
  ends_at: "2026-11-05T02:00:00Z", ...extra,
});

test("a pending change reads as 'Asked to move from … to …', from the times the server sent", () => {
  const zone = "Asia/Kuala_Lumpur";
  // The original need not be on the same week: its time comes with the request.
  const request = asked("r", "a", {
    replaces_starts_at: "2026-11-02T01:00:00Z", replaces_ends_at: "2026-11-02T02:00:00Z",
  });
  assert.equal(askedText(request, zone), "Asked to move from 2 Nov 2026, 09:00 to 9 Nov 2026, 10:00");
  assert.equal(
    askedText(request, "America/New_York"),
    "Asked to move from 1 Nov 2026, 20:00 to 8 Nov 2026, 21:00",
  );
});

test("only a live request that carries its original's time reads as a change", () => {
  const zone = "Asia/Kuala_Lumpur";
  assert.equal(askedText(live("a"), zone), null);
  assert.equal(askedText(asked("r", "a"), zone), null);
  assert.equal(askedText(asked("r", "a", {
    status: "cancelled", replaces_starts_at: "2026-11-02T01:00:00Z",
  }), zone), null);
  assert.equal(askedText({ ...live("a"), replaces_starts_at: "2026-11-02T01:00:00Z" }, zone), null);
});

test("only a pending booking with an original is a change request", () => {
  assert.equal(isChangeRequest(asked("r", "a")), true);
  assert.equal(isChangeRequest(asked("r", "a", { status: "cancelled" })), false);
  assert.equal(isChangeRequest(live("a")), false);
  assert.equal(isChangeRequest({ id: "x", status: "pending" }), false);
});

test("a booking the customer's bot made says so", () => {
  assert.equal(sourceNote({ source: "bot", redacted: false }), "Booked by the customer");
  assert.equal(sourceNote({ source: "staff", redacted: false }), null);
  assert.equal(sourceNote({ source: "assistant", redacted: false }), null);
  assert.equal(sourceNote({ source: null, redacted: true }), null);
  assert.equal(sourceNote({ redacted: false }), null);
  assert.equal(sourceNote({ source: "bot", redacted: true }), null);
});
