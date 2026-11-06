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
const apiUrl = moduleUrl(await readFile(new URL("../lib/api.ts", import.meta.url), "utf8"));
const source = await readFile(new URL("../lib/bookings-api.ts", import.meta.url), "utf8");
const bookings = await import(moduleUrl(source.replace('"./api"', JSON.stringify(apiUrl))));
const { ApiError } = await import(apiUrl);

const ID = "33333333-3333-4333-8333-333333333333";

async function capture(run, reply = () => Response.json({})) {
  const original = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (path, init) => {
    if (path === "/console/v1/auth/csrf") return Response.json({ csrf_token: "synthetic-csrf" });
    requests.push([path, init]);
    return reply(path, init);
  };
  try { return [await run(), requests]; } finally { globalThis.fetch = original; }
}
const shape = (requests) => requests.map(([path, init]) => [init.method ?? "GET", path]);

test("tenant members send no client_id; empty values are left out", async () => {
  const [, requests] = await capture(async () => {
    await bookings.listPeople(null);
    await bookings.addPerson(null, { display_name: "Aiman" });
    await bookings.changePerson(null, ID, { version: 2, is_active: false });
    await bookings.addPeopleFromTeam(null);
    await bookings.listResources(null);
    await bookings.addResource(null, { name: "Room 1", capacity: 6 });
    await bookings.changeResource(null, ID, { version: 1, capacity: null });
    await bookings.listTypes(null);
    await bookings.addType(null, { name: "Visit", duration_minutes: 30, location_kind: "phone" });
    await bookings.changeType(null, ID, { version: 4, name: "Visit" });
    await bookings.createStarterTypes(null);
    await bookings.getHours(null);
    await bookings.getHours(null, { kind: "person", id: ID });
    await bookings.replaceHours(null, { periods: [] });
    await bookings.listClosedDays(null);
    await bookings.listClosedDays(null, { from: "2026-11-01", to: "" });
    await bookings.addClosedDay(null, { day: "2026-12-25", note: "Holiday" });
    await bookings.removeClosedDay(null, "2026-12-25");
    await bookings.listBlocks(null, { from: "2026-11-01T00:00:00Z" });
    await bookings.addBlock(null, {
      subject_kind: "resource", subject_id: ID, starts_at: "2026-11-05T01:00:00Z", ends_at: "2026-11-05T03:00:00Z",
    });
    await bookings.removeBlock(null, ID);
  });
  assert.deepEqual(shape(requests), [
    ["GET", "/console/v1/bookings/people"],
    ["POST", "/console/v1/bookings/people"],
    ["PATCH", `/console/v1/bookings/people/${ID}`],
    ["POST", "/console/v1/bookings/people/from-team"],
    ["GET", "/console/v1/bookings/resources"],
    ["POST", "/console/v1/bookings/resources"],
    ["PATCH", `/console/v1/bookings/resources/${ID}`],
    ["GET", "/console/v1/bookings/types"],
    ["POST", "/console/v1/bookings/types"],
    ["PATCH", `/console/v1/bookings/types/${ID}`],
    ["POST", "/console/v1/bookings/types/starters"],
    ["GET", "/console/v1/bookings/hours"],
    ["GET", `/console/v1/bookings/hours?subject_kind=person&subject_id=${ID}`],
    ["PUT", "/console/v1/bookings/hours"],
    ["GET", "/console/v1/bookings/closed-days"],
    ["GET", "/console/v1/bookings/closed-days?from=2026-11-01"],
    ["POST", "/console/v1/bookings/closed-days"],
    ["DELETE", "/console/v1/bookings/closed-days/2026-12-25"],
    ["GET", "/console/v1/bookings/blocks?from=2026-11-01T00%3A00%3A00Z"],
    ["POST", "/console/v1/bookings/blocks"],
    ["DELETE", `/console/v1/bookings/blocks/${ID}`],
  ]);
  assert.deepEqual(JSON.parse(requests[2][1].body), { version: 2, is_active: false });
  assert.deepEqual(JSON.parse(requests[6][1].body), { version: 1, capacity: null });
  assert.deepEqual(JSON.parse(requests[13][1].body), { periods: [] });
  for (const [, init] of requests) {
    assert.equal(init.credentials, "same-origin");
    if ((init.method ?? "GET") === "GET") assert.equal(init.headers.has("X-CSRF-Token"), false);
    else assert.equal(init.headers.get("X-CSRF-Token"), "synthetic-csrf");
  }
});

test("platform admins name the tenant first on every call", async () => {
  const tenant = "a b/1";
  const [, requests] = await capture(async () => {
    await bookings.listPeople(tenant);
    await bookings.addPeopleFromTeam(tenant);
    await bookings.changeType(tenant, ID, { version: 1 });
    await bookings.getHours(tenant, { kind: "resource", id: ID });
    await bookings.replaceHours(tenant, { subject_kind: "company", periods: [] });
    await bookings.removeClosedDay(tenant, "2026-12-25");
    await bookings.removeBlock(tenant, ID);
  });
  const named = "client_id=a+b%2F1";
  assert.deepEqual(shape(requests), [
    ["GET", `/console/v1/bookings/people?${named}`],
    ["POST", `/console/v1/bookings/people/from-team?${named}`],
    ["PATCH", `/console/v1/bookings/types/${ID}?${named}`],
    ["GET", `/console/v1/bookings/hours?${named}&subject_kind=resource&subject_id=${ID}`],
    ["PUT", `/console/v1/bookings/hours?${named}`],
    ["DELETE", `/console/v1/bookings/closed-days/2026-12-25?${named}`],
    ["DELETE", `/console/v1/bookings/blocks/${ID}?${named}`],
  ]);
});

test("booking, calendar, availability and customer calls", async () => {
  const [, requests] = await capture(async () => {
    await bookings.getCalendar(null, { from: "2026-11-02T00:00:00Z", to: "2026-11-09T00:00:00Z" });
    await bookings.getCalendar("t1", { from: "a", to: "b" }, { person: ID });
    await bookings.getCalendar(null, { from: "a", to: "b" }, { resource: ID });
    await bookings.getAvailability(null, { from: "a", to: "b", typeId: ID });
    await bookings.getAvailability(null, { from: "a", to: "b", people: [ID, "p2"], resources: ["r1"], minutes: 45 });
    await bookings.searchCustomers(null, "  aina ");
    await bookings.searchCustomers(null, "");
    await bookings.createBooking(null, { starts_at: "2026-11-05T02:00:00Z", type_id: ID });
    await bookings.moveBooking(null, ID, { version: 2, starts_at: "2026-11-06T02:00:00Z", acknowledged: ["closed_day"] });
    await bookings.changeBooking(null, ID, "cancel", 3);
    await bookings.changeBooking(null, ID, "no-show", 3);
  });
  assert.deepEqual(shape(requests), [
    ["GET", "/console/v1/bookings/calendar?from=2026-11-02T00%3A00%3A00Z&to=2026-11-09T00%3A00%3A00Z"],
    ["GET", `/console/v1/bookings/calendar?client_id=t1&from=a&to=b&person=${ID}`],
    ["GET", `/console/v1/bookings/calendar?from=a&to=b&resource=${ID}`],
    ["GET", `/console/v1/bookings/availability?from=a&to=b&type_id=${ID}`],
    ["GET", `/console/v1/bookings/availability?from=a&to=b&minutes=45&people=${ID}&people=p2&resources=r1`],
    ["GET", "/console/v1/bookings/customers?q=aina"],
    ["GET", "/console/v1/bookings/customers"],
    ["POST", "/console/v1/bookings/bookings"],
    ["PATCH", `/console/v1/bookings/bookings/${ID}`],
    ["POST", `/console/v1/bookings/bookings/${ID}/cancel`],
    ["POST", `/console/v1/bookings/bookings/${ID}/no-show`],
  ]);
  assert.deepEqual(JSON.parse(requests[8][1].body), {
    version: 2, starts_at: "2026-11-06T02:00:00Z", acknowledged: ["closed_day"],
  });
  assert.deepEqual(JSON.parse(requests[9][1].body), { version: 3 });
});

test("settings are read and replaced as a whole, naming the tenant for a platform admin", async () => {
  const saved = { reminder_minutes: 90, pending_hours: 12, customer_limit: 3, version: 2 };
  const [read, requests] = await capture(async () => {
    const found = await bookings.getSettings(null);
    await bookings.saveSettings(null, saved);
    await bookings.getSettings("a b/1");
    await bookings.saveSettings("a b/1", saved);
    return found;
  }, () => Response.json({ ...saved, replies_off: true }));
  assert.equal(read.replies_off, true);
  assert.deepEqual(shape(requests), [
    ["GET", "/console/v1/bookings/settings"],
    ["PUT", "/console/v1/bookings/settings"],
    ["GET", "/console/v1/bookings/settings?client_id=a+b%2F1"],
    ["PUT", "/console/v1/bookings/settings?client_id=a+b%2F1"],
  ]);
  assert.deepEqual(JSON.parse(requests[1][1].body), saved);
  assert.equal(requests[1][1].headers.get("X-CSRF-Token"), "synthetic-csrf");
});

test("a booking refusal carries the server's sentence and its findings; other pages are unchanged", async () => {
  const refuse = (body, status = 409) =>
    assert.rejects(
      capture(() => bookings.createBooking(null, { starts_at: "x" }), () => Response.json(body, { status })),
      (error) => error instanceof ApiError,
    );
  await refuse({ detail: "slot_taken", message: "That time was just taken." });

  const caught = async (body, status = 409) => {
    try {
      await capture(() => bookings.createBooking(null, { starts_at: "x" }), () => Response.json(body, { status }));
    } catch (error) {
      return error;
    }
    throw new Error("expected a refusal");
  };
  const warn = await caught({
    detail: "needs_acknowledging",
    message: "Outside working hours. This is a closed day.",
    findings: ["closed_day", "outside_hours"],
  });
  assert.equal(warn.code, "needs_acknowledging");
  assert.equal(warn.message, "Outside working hours. This is a closed day.");
  assert.deepEqual(warn.body.findings, ["closed_day", "outside_hours"]);

  const refused = await caught({ detail: "refused", message: "Not available that day.", findings: ["unavailable"] });
  assert.equal(refused.message, "Not available that day.");
  assert.deepEqual(refused.body.findings, ["unavailable"]);

  const stale = await caught({ detail: "stale", message: "This booking was changed by someone else." });
  assert.equal(stale.message, "This booking was changed by someone else.");
  // Without a sentence from the server there is still one here, never the code.
  assert.match((await caught({ detail: "stale" })).message, /changed by someone else/);
  assert.match((await caught({ detail: "own_bookings_only" }, 403)).message, /only change bookings you made or are part of/);
  assert.match((await caught({ detail: "wrong_status" })).message, /current status/);

  // A message on any other code is ignored, so no other page changes.
  const other = await caught({ detail: "version_conflict", message: "Something else" });
  assert.match(other.message, /changed this while you were editing/);
  const unknown = await caught({ detail: "some_code", message: "A sentence" });
  assert.equal(unknown.message, "some_code");
});

test("a setup refusal reads as a plain sentence, never the code", async () => {
  const cases = {
    version_conflict: /changed this while you were editing/,
    person_exists: /already on the list/,
    type_needs_people: /Choose at least one person/,
    hours_invalid: /23:59/,
    slot_taken: /^That time was just taken\.$/,
    "bookings setup requires an administrator": /^Only an Admin can change/,
    person_inactive: /^That person isn't taking bookings any more\./,
    resource_inactive: /^That room isn't available any more\./,
    type_inactive: /^That booking type is switched off\./,
    times_invalid: /^The booking must end after it starts\./,
    minutes_required: /^Enter how long it lasts/,
    people_or_resources_required: /^Choose a person or a room/,
    window_invalid: /^The end of the period must be after its start\./,
    no_room_available: /^No room is free for this booking type\./,
    "bookings changes require staff or an administrator": /^Only staff and Admins can change bookings\./,
  };
  for (const [code, expected] of Object.entries(cases)) {
    const status = code.startsWith("version") ? 409 : 422;
    await assert.rejects(
      capture(() => bookings.changePerson(null, ID, { version: 1 }), () => Response.json({ detail: code }, { status })),
      (error) => error instanceof ApiError && error.code === code && expected.test(error.message),
      code,
    );
  }
});

test("the Google Calendar calls go to the calendar routes, and its refusals are sentences", async () => {
  const [, requests] = await capture(async () => {
    await bookings.getCalendarStatus(null);
    await bookings.startCalendarConnect(null);
    await bookings.disconnectCalendar(null);
    await bookings.getCalendarStatus(ID);
  });
  assert.deepEqual(shape(requests), [
    ["GET", "/console/v1/bookings/calendar/status"],
    ["POST", "/console/v1/bookings/calendar/connect"],
    ["POST", "/console/v1/bookings/calendar/disconnect"],
    ["GET", `/console/v1/bookings/calendar/status?client_id=${ID}`],
  ]);
  const cases = [
    ["person_required", /Ask an Admin to add you/],
    ["not_connected", /isn't connected/],
    ["calendar_unavailable", /switched on/],
  ];
  for (const [code, expected] of cases) {
    await assert.rejects(
      capture(() => bookings.startCalendarConnect(null), () => Response.json({ detail: code }, { status: 409 })),
      (error) => error instanceof ApiError && error.code === code && expected.test(error.message),
      code,
    );
  }
});
