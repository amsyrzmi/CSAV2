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
const inbox = await import(moduleUrl(
  await readFile(new URL("../lib/inbox-api.ts", import.meta.url), "utf8"),
));
const { approvalsPath, canConnectWhatsApp, mergeById, listTime, messagePictureId } = inbox;
const apiUrl = moduleUrl(await readFile(new URL("../lib/api.ts", import.meta.url), "utf8"));
const channels = await import(moduleUrl(
  (await readFile(new URL("../lib/channels-api.ts", import.meta.url), "utf8")).replace('"./api"', JSON.stringify(apiUrl)),
));

const BASE = "/console/v1/inbox/conversations";

test("the approvals path carries the conversation and the handled flag", () => {
  const id = "3f2c1a40-0000-4000-8000-000000000001";
  assert.equal(
    approvalsPath({ tenantQuery: "", conversationId: id, includeHandled: false }),
    `/console/v1/inbox/approvals?conversation_id=${id}&include_handled=false`,
  );
  assert.equal(
    approvalsPath({ tenantQuery: "client_id=abc", conversationId: id, includeHandled: true }),
    `/console/v1/inbox/approvals?client_id=abc&conversation_id=${id}&include_handled=true`,
  );
});

test("only platform and tenant administrators can connect WhatsApp", () => {
  assert.equal(canConnectWhatsApp("platform_admin"), true);
  assert.equal(canConnectWhatsApp("tenant_admin"), true);
  assert.equal(canConnectWhatsApp("tenant_staff"), false);
  assert.equal(canConnectWhatsApp("tenant_viewer"), false);
  assert.equal(canConnectWhatsApp(null), false);
});

test("merging keeps the first occurrence and the order", () => {
  assert.deepEqual(
    mergeById([{ id: "a", n: 1 }, { id: "b", n: 2 }, { id: "a", n: 3 }, { id: "c", n: 4 }, { id: "b", n: 5 }]),
    [{ id: "a", n: 1 }, { id: "b", n: 2 }, { id: "c", n: 4 }],
  );
  assert.deepEqual(mergeById([{ id: 2 }, { id: 1 }, { id: 2 }]), [{ id: 2 }, { id: 1 }]);
  assert.deepEqual(mergeById([]), []);
});

// Local-time construction keeps these independent of the machine time zone.
const NOW = new Date(2026, 9, 28, 15, 30, 0).getTime();
const at = (ms) => new Date(NOW - ms).toISOString();
const S = 1000, MIN = 60 * S, HOUR = 60 * MIN;

test("under a minute is Just now", () => {
  assert.equal(listTime(at(0), NOW), "Just now");
  assert.equal(listTime(at(59 * S), NOW), "Just now");
});

test("from a minute it counts minutes", () => {
  assert.equal(listTime(at(60 * S), NOW), "1 min");
  assert.equal(listTime(at(2 * MIN + 10 * S), NOW), "2 min");
  assert.equal(listTime(at(25 * MIN), NOW), "25 min");
  assert.equal(listTime(at(59 * MIN + 59 * S), NOW), "59 min");
});

test("from an hour it counts hours up to a day", () => {
  assert.equal(listTime(at(60 * MIN), NOW), "1 h");
  assert.equal(listTime(at(3 * HOUR + 5 * MIN), NOW), "3 h");
  assert.equal(listTime(at(23 * HOUR), NOW), "23 h");
});

test("past 24 hours it uses the calendar day in local time", () => {
  // NOW is 28 Oct 15:30 local.
  const yesterdayMidnight = new Date(2026, 9, 27, 0, 0, 0).toISOString();
  const yesterdayNoon = new Date(2026, 9, 27, 12, 0, 0).toISOString();
  const dayBefore = new Date(2026, 9, 26, 23, 59, 59).toISOString();
  assert.equal(listTime(yesterdayNoon, NOW), "Yesterday");
  assert.equal(listTime(yesterdayMidnight, NOW), "Yesterday");
  assert.equal(listTime(dayBefore, NOW), "26 Oct");
});

test("just after midnight, 24 hours back is Yesterday and 25 hours back is the day before", () => {
  const early = new Date(2026, 9, 28, 0, 20, 0).getTime();
  // 24 hours earlier is 27 Oct 00:20 local; 25 hours earlier is 26 Oct 23:20.
  assert.equal(listTime(new Date(early - 24 * HOUR).toISOString(), early), "Yesterday");
  assert.equal(listTime(new Date(early - 25 * HOUR).toISOString(), early), "26 Oct");
  // 23 hours earlier is still an hour count even though it crosses midnight.
  assert.equal(listTime(new Date(early - 23 * HOUR).toISOString(), early), "23 h");
});

test("older dates show day and short month, with the year when it differs", () => {
  assert.equal(listTime(new Date(2026, 9, 20, 9, 0, 0).toISOString(), NOW), "20 Oct");
  assert.equal(listTime(new Date(2026, 0, 3, 9, 0, 0).toISOString(), NOW), "3 Jan");
  assert.equal(listTime(new Date(2025, 9, 27, 9, 0, 0).toISOString(), NOW), "27 Oct 2025");
  assert.equal(listTime(new Date(2025, 11, 31, 9, 0, 0).toISOString(), NOW), "31 Dec 2025");
});

test("a future timestamp is clamped to Just now", () => {
  assert.equal(listTime(new Date(NOW + 5 * MIN).toISOString(), NOW), "Just now");
  assert.equal(listTime(new Date(NOW + 5 * 24 * HOUR).toISOString(), NOW), "Just now");
});

test("an invalid date gives an empty string", () => {
  assert.equal(listTime("", NOW), "");
  assert.equal(listTime("not a date", NOW), "");
});

test("a message shows a picture only when the server sent one", () => {
  const asset = "6f1c2d3e-0000-4000-8000-000000000002";
  assert.equal(messagePictureId({ picture: { asset_id: asset } }), asset);
  assert.equal(messagePictureId({ picture: null }), null);
  // A server that predates the field sends none at all.
  assert.equal(messagePictureId({}), null);
});

test("a picture that was not sent is explained in plain words and leaves the text out of it", () => {
  for (const code of ["picture_unavailable", "picture_rejected", "picture_failed"]) {
    const notice = channels.deliveryNotice({
      delivery_status: "skipped", delivery_error: code, provider_status: null, provider_error: null,
    });
    assert.ok(notice && notice.startsWith("The picture was not sent."), code);
    assert.ok(notice.endsWith("The text message is not affected by this."), notice);
    // Never a claim that the text reached the customer, nor that the picture is simply gone.
    assert.ok(!notice.includes("got the text"), notice);
    assert.ok(!notice.includes("no longer in your pictures"), notice);
    assert.ok(!notice.includes("_"), notice);
  }
  const [unavailable, rejected, failed] = ["picture_unavailable", "picture_rejected", "picture_failed"].map((code) =>
    channels.deliveryNotice({ delivery_status: "skipped", delivery_error: code, provider_status: null, provider_error: null }));
  assert.equal(new Set([unavailable, rejected, failed]).size, 3);
});

// ---- queue routes: paths and bodies ----

const {
  queueActionPath, bulkPath, queueActionBody, bulkBody, assigneesPath, settingsPath, viewsPath,
} = inbox;
const apiModule = await import(apiUrl);
const ROOT = "/console/v1/inbox";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const row = { id: "c1", inbox_version: 7, last_customer_at: "2026-11-17T03:00:00Z" };

test("queue paths carry the tenant query when there is one and no stray question mark when not", () => {
  assert.equal(queueActionPath(`${BASE}/c1`, "claim", ""), `${BASE}/c1/queue/claim`);
  assert.equal(queueActionPath(`${BASE}/c1`, "snooze", "client_id=abc"), `${BASE}/c1/queue/snooze?client_id=abc`);
  assert.equal(bulkPath(""), `${ROOT}/bulk`);
  assert.equal(bulkPath("client_id=abc"), `${ROOT}/bulk?client_id=abc`);
  assert.equal(assigneesPath(""), `${ROOT}/assignees`);
  assert.equal(assigneesPath("client_id=abc"), `${ROOT}/assignees?client_id=abc`);
  assert.equal(settingsPath(""), `${ROOT}/settings`);
  assert.equal(settingsPath("client_id=abc"), `${ROOT}/settings?client_id=abc`);
  assert.equal(viewsPath(""), `${ROOT}/views`);
  assert.equal(viewsPath("client_id=abc"), `${ROOT}/views?client_id=abc`);
  assert.equal(viewsPath("client_id=abc", "v 1"), `${ROOT}/views/v%201?client_id=abc`);
  assert.equal(viewsPath("", "v1"), `${ROOT}/views/v1`);
});

test("every action body carries the request key and the version the person read", () => {
  for (const action of ["claim", "unclaim"]) {
    assert.deepEqual(queueActionBody(action, row, {}, "k1"), { request_key: "k1", expected_version: 7 });
  }
  assert.deepEqual(queueActionBody("priority", row, { priority: "urgent" }, "k1"),
    { request_key: "k1", expected_version: 7, priority: "urgent" });
  assert.deepEqual(queueActionBody("topic", row, { topic: "payment" }, "k1"),
    { request_key: "k1", expected_version: 7, topic: "payment" });
});

test("assign sends the person's id and never the display name", () => {
  const body = queueActionBody("assign", row, { assignee_id: "u1", assigneeName: "Aina Demo" }, "k1");
  assert.deepEqual(body, { request_key: "k1", expected_version: 7, assignee_id: "u1" });
});

test("snooze and resolve carry what the customer last wrote, null when they never did", () => {
  assert.deepEqual(queueActionBody("snooze", row, { until: "2026-11-18T09:00:00+08:00" }, "k1"), {
    request_key: "k1", expected_version: 7, until: "2026-11-18T09:00:00+08:00",
    seen_customer_at: "2026-11-17T03:00:00Z",
  });
  assert.deepEqual(queueActionBody("resolve", row, {}, "k1"),
    { request_key: "k1", expected_version: 7, seen_customer_at: "2026-11-17T03:00:00Z" });
  assert.equal(queueActionBody("resolve", { ...row, last_customer_at: null }, {}, "k1").seen_customer_at, null);
});

test("a field the action does not take is left out of its body", () => {
  const body = queueActionBody("claim", row, { priority: "high", until: "x", assignee_id: "u1" }, "k1");
  assert.deepEqual(body, { request_key: "k1", expected_version: 7 });
});

test("each body gets a fresh request key unless one is passed", () => {
  const a = queueActionBody("claim", row, {});
  const b = queueActionBody("claim", row, {});
  assert.match(a.request_key, UUID);
  assert.match(b.request_key, UUID);
  assert.notEqual(a.request_key, b.request_key);
  assert.match(bulkBody("claim", ["c1"], {}, "2026-11-17T03:00:00Z").request_key, UUID);
});

test("a bulk body carries the action, the ids, when the list was read and the action's field", () => {
  const seen = "2026-11-17T03:00:00Z";
  assert.deepEqual(bulkBody("resolve", ["c1", "c2"], {}, seen, "k1"),
    { request_key: "k1", action: "resolve", ids: ["c1", "c2"], seen_at: seen });
  assert.deepEqual(bulkBody("snooze", ["c1"], { until: "2026-11-18T09:00:00+08:00" }, seen, "k1"),
    { request_key: "k1", action: "snooze", ids: ["c1"], seen_at: seen, until: "2026-11-18T09:00:00+08:00" });
  assert.deepEqual(bulkBody("assign", ["c1"], { assignee_id: "u1", assigneeName: "Aina Demo" }, seen, "k1"),
    { request_key: "k1", action: "assign", ids: ["c1"], seen_at: seen, assignee_id: "u1" });
  assert.deepEqual(bulkBody("priority", ["c1"], { priority: "low" }, seen, "k1"),
    { request_key: "k1", action: "priority", ids: ["c1"], seen_at: seen, priority: "low" });
  assert.deepEqual(bulkBody("topic", ["c1"], { topic: "other" }, seen, "k1"),
    { request_key: "k1", action: "topic", ids: ["c1"], seen_at: seen, topic: "other" });
  // The server rejects a field the action does not take, so it is never sent.
  assert.deepEqual(bulkBody("claim", ["c1"], { priority: "low" }, seen, "k1"),
    { request_key: "k1", action: "claim", ids: ["c1"], seen_at: seen });
});

test("a coded error body keeps its code and body on the error; a plain one is unchanged", async () => {
  const json = (status, body) => new Response(JSON.stringify(body), { status });
  const coded = await apiModule.decode(json(409, { detail: { code: "held_reply_pending" } })).catch((e) => e);
  assert.equal(coded.status, 409);
  assert.equal(coded.code, "held_reply_pending");
  assert.deepEqual(coded.body, { detail: { code: "held_reply_pending" } });
  assert.equal(coded.message, "Request failed (409)");
  const plain = await apiModule.decode(json(409, { detail: "conversation changed; refresh before acting" })).catch((e) => e);
  assert.equal(plain.code, "conversation changed; refresh before acting");
  assert.deepEqual(plain.body, { detail: "conversation changed; refresh before acting" });
  // A validation error (a list) and a detail object with no code stay as they were.
  const listed = await apiModule.decode(json(422, { detail: [{ msg: "x" }] })).catch((e) => e);
  assert.equal(listed.code, "Request failed (422)");
  const bare = await apiModule.decode(json(422, { detail: { other: 1 } })).catch((e) => e);
  assert.equal(bare.code, "Request failed (422)");
});

test("errorCode reads a coded refusal and is null for plain-text details and anything else", async () => {
  const { errorCode } = inbox;
  const json = (status, body) => new Response(JSON.stringify(body), { status });
  const coded = await apiModule.decode(json(409, { detail: { code: "customer_wrote" } })).catch((e) => e);
  assert.equal(errorCode(coded), "customer_wrote");
  const plain = await apiModule.decode(json(409, { detail: "conversation changed; refresh before acting" })).catch((e) => e);
  assert.equal(errorCode(plain), null);
  assert.equal(errorCode(new Error("boom")), null);
  assert.equal(errorCode(null), null);
  assert.equal(errorCode({ body: { detail: { code: 5 } } }), null);
});

// ---- response times (queue settings) ----

const SETTINGS = {
  target_urgent_min: 15, target_high_min: 60, target_normal_min: 240, target_low_min: 1440,
  repeat_minutes: 60, max_repeats: 3, follow_up_hours: 48, stale_claim_hours: 8, handback_prompt_hours: 24,
};

test("the client's ranges are the API's (csa/console/inbox_settings.py InboxSettingsIn)", () => {
  assert.deepEqual(inbox.SETTINGS_RANGES, {
    target_urgent_min: [5, 10080], target_high_min: [5, 10080], target_normal_min: [5, 10080],
    target_low_min: [5, 10080], repeat_minutes: [15, 1440], max_repeats: [0, 10],
    follow_up_hours: [0, 720], stale_claim_hours: [0, 168], handback_prompt_hours: [0, 720],
  });
  assert.deepEqual([...inbox.TARGET_KEYS], ["target_urgent_min", "target_high_min", "target_normal_min", "target_low_min"]);
});

test("a response target is refused in plain words unless it is a whole number of minutes in range", () => {
  const { settingProblem } = inbox;
  const words = "Enter a whole number of minutes, from 5 to 10080.";
  for (const ok of ["5", "30", " 30 ", "030", "10080"]) assert.equal(settingProblem("target_normal_min", ok), null, ok);
  for (const bad of ["", " ", "4", "0", "10081", "-5", "1.5", "30.0", "1e3", "abc", "3 0", "+30", "٣٠"]) {
    assert.equal(settingProblem("target_normal_min", bad), words, bad);
  }
  // The other fields carry their own unit and range.
  assert.equal(settingProblem("repeat_minutes", "14"), "Enter a whole number of minutes, from 15 to 1440.");
  assert.equal(settingProblem("max_repeats", "11"), "Enter a whole number, from 0 to 10.");
  assert.equal(settingProblem("stale_claim_hours", "169"), "Enter a whole number of hours, from 0 to 168.");
  assert.equal(settingProblem("follow_up_hours", "0"), null);
});

test("a draft becomes the nine values only when every field is valid", () => {
  const { settingsDraft, draftValues } = inbox;
  const draft = settingsDraft({ ...SETTINGS, version: 3 });
  assert.deepEqual(draft, Object.fromEntries(Object.entries(SETTINGS).map(([key, value]) => [key, String(value)])));
  assert.deepEqual(draftValues({ ...draft, target_normal_min: " 30 " }), { ...SETTINGS, target_normal_min: 30 });
  assert.equal(draftValues({ ...draft, target_normal_min: "3" }), null);
  assert.equal(draftValues({ ...draft, target_urgent_min: "" }), null);
});

test("the PUT body carries every field and the version read, and nothing else", () => {
  const { settingsBody } = inbox;
  const body = settingsBody({ ...SETTINGS, target_normal_min: 30, extra: "dropped" }, 4);
  assert.deepEqual(body, { ...SETTINGS, target_normal_min: 30, version: 4 });
  assert.deepEqual(Object.keys(body).sort(), [...Object.keys(SETTINGS), "version"].sort());
  assert.equal(settingsBody(SETTINGS, 0).version, 0);
});

test("minutes are shown as hours and minutes", () => {
  const { minutesText } = inbox;
  assert.equal(minutesText(5), "5 min");
  assert.equal(minutesText(45), "45 min");
  assert.equal(minutesText(60), "1 h");
  assert.equal(minutesText(90), "1 h 30 min");
  assert.equal(minutesText(240), "4 h");
  assert.equal(minutesText(1441), "24 h 1 min");
  assert.equal(minutesText(10080), "168 h");
  assert.equal(minutesText(0), "0 min");
  for (const odd of [-1, 1.5, Number.NaN, Infinity]) assert.equal(minutesText(odd), "", String(odd));
});

test("the reminder settings read as plain words, 0 being off where it turns one off", () => {
  const { reminderSettingText } = inbox;
  assert.equal(reminderSettingText("repeat_minutes", 60), "60 minutes (1 h)");
  assert.equal(reminderSettingText("max_repeats", 3), "3");
  assert.equal(reminderSettingText("max_repeats", 0), "0");
  assert.equal(reminderSettingText("follow_up_hours", 48), "48 hours");
  assert.equal(reminderSettingText("stale_claim_hours", 1), "1 hour");
  assert.equal(reminderSettingText("handback_prompt_hours", 0), "Off");
  assert.equal(reminderSettingText("follow_up_hours", 0), "Off");
});
