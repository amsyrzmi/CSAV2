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
async function load(name, replacements = []) {
  let source = await readFile(new URL(`../lib/${name}.ts`, import.meta.url), "utf8");
  for (const [from, to] of replacements) source = source.replaceAll(from, to);
  return import(moduleUrl(source));
}
const { filterOrgChart, orgFilterChoices, orgMarker, departmentTint, orgChartCsv } = await load(
  "hr-org-chart",
  [['from "./hr-people"', `from "${moduleUrl(await readFile(new URL("../lib/hr-people.ts", import.meta.url), "utf8"))}"`]],
);

const ENG = "11111111-1111-4111-8111-111111111111";
const OPS = "22222222-2222-4222-8222-222222222222";

function node(id, name, manager = null, extra = {}) {
  return {
    employee_id: id, display_name: name, job_title: null, department: null, department_id: null,
    manager_id: manager, status: "active", start_date: null, last_day: null, legal_entity_reference: null,
    ...extra,
  };
}
const chart = {
  nodes: [
    node("a", "Aina Tan", null, { job_title: "Director", department: "Engineering", department_id: ENG, legal_entity_reference: "Contoh Sdn Bhd" }),
    node("b", "Bala Raj", "a", { job_title: "Lead Engineer", department: "Engineering", department_id: ENG, legal_entity_reference: " contoh sdn bhd " }),
    node("c", "Chen Wei", "b", { job_title: "Engineer", department: "Engineering", department_id: ENG }),
    node("d", "Dev Kaur", "a", { job_title: "Operations Manager", department: "Operations", department_id: OPS, legal_entity_reference: "Ujian Enterprise" }),
    node("z", "Zul Hamid"),
  ],
  looped: [],
};
const ids = (c) => c.nodes.map((item) => item.employee_id).sort();

test("an empty filter returns the same nodes", () => {
  assert.deepEqual(ids(filterOrgChart(chart, {})), ["a", "b", "c", "d", "z"]);
  assert.deepEqual(ids(filterOrgChart(chart, { query: "   ", department_id: "", legal_entity: " " })), ["a", "b", "c", "d", "z"]);
  assert.deepEqual(filterOrgChart(chart, {}).nodes, chart.nodes);
});

test("a match keeps the whole manager chain so it is never orphaned", () => {
  const out = filterOrgChart(chart, { query: "chen" });
  assert.deepEqual(ids(out), ["a", "b", "c"]);
});

test("search matches the name or the job title, trimmed and case-insensitive", () => {
  assert.deepEqual(ids(filterOrgChart(chart, { query: "  OPERATIONS man " })), ["a", "d"]);
  assert.deepEqual(ids(filterOrgChart(chart, { query: "zul" })), ["z"]);
  assert.deepEqual(ids(filterOrgChart(chart, { query: "nobody" })), []);
});

test("the department filter matches by id and keeps ancestors", () => {
  assert.deepEqual(ids(filterOrgChart(chart, { department_id: OPS })), ["a", "d"]);
  assert.deepEqual(ids(filterOrgChart(chart, { department_id: ENG })), ["a", "b", "c"]);
});

test("the legal entity filter ignores case and surrounding spaces", () => {
  assert.deepEqual(ids(filterOrgChart(chart, { legal_entity: "CONTOH SDN BHD" })), ["a", "b"]);
  assert.deepEqual(ids(filterOrgChart(chart, { legal_entity: "  ujian enterprise  " })), ["a", "d"]);
});

test("filters combine with AND", () => {
  assert.deepEqual(ids(filterOrgChart(chart, { department_id: ENG, query: "lead" })), ["a", "b"]);
  assert.deepEqual(ids(filterOrgChart(chart, { department_id: OPS, query: "lead" })), []);
});

test("filtering keeps only loop ids that remain and survives a reporting loop", () => {
  const looped = { nodes: [node("p", "Pia", "q"), node("q", "Qi", "p"), node("r", "Rina")], looped: ["p", "q"] };
  const out = filterOrgChart(looped, { query: "pia" });
  assert.deepEqual(ids(out), ["p", "q"]);
  assert.deepEqual(out.looped, ["p", "q"]);
  assert.deepEqual(filterOrgChart(looped, { query: "rina" }).looped, []);
});

test("a manager outside the chart ends the chain without failing", () => {
  const out = filterOrgChart({ nodes: [node("a", "Aina", "gone")], looped: [] }, { query: "aina" });
  assert.deepEqual(ids(out), ["a"]);
});

test("filter choices are distinct and sorted, legal entities empty when none carry one", () => {
  const choices = orgFilterChoices(chart);
  assert.deepEqual(choices.departments, [{ id: ENG, name: "Engineering" }, { id: OPS, name: "Operations" }]);
  assert.deepEqual(choices.legalEntities, ["Contoh Sdn Bhd", "Ujian Enterprise"]);
  const bare = orgFilterChoices({ nodes: [node("a", "Aina")], looped: [] });
  assert.deepEqual(bare, { departments: [], legalEntities: [] });
});

test("orgMarker marks starting and leaving people only", () => {
  assert.equal(orgMarker(node("a", "A", null, { status: "starting" })), "starting");
  assert.equal(orgMarker(node("a", "A", null, { status: "leaving" })), "leaving");
  assert.equal(orgMarker(node("a", "A")), null);
});

test("departmentTint is stable, within 1 to 6, and null without a department", () => {
  assert.equal(departmentTint(null), null);
  assert.equal(departmentTint(ENG), departmentTint(ENG));
  for (const id of [ENG, OPS, "x", "y", "z", "abc"]) {
    const tint = departmentTint(id);
    assert.ok(Number.isInteger(tint) && tint >= 1 && tint <= 6, String(tint));
  }
  const seen = new Set(Array.from({ length: 60 }, (_, i) => departmentTint(`dept-${i}`)));
  assert.ok(seen.size >= 4);
});

const parse = (text) => {
  // Minimal RFC 4180 reader so the test proves a round trip.
  const rows = [];
  let row = [], field = "", quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') { field += '"'; i += 1; }
      else if (ch === '"') quoted = false;
      else field += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === ",") { row.push(field); field = ""; }
    else if (ch === "\r" && text[i + 1] === "\n") { row.push(field); rows.push(row); row = []; field = ""; i += 1; }
    else field += ch;
  }
  row.push(field); rows.push(row);
  return rows;
};

test("the CSV has a header, CRLF line ends and one row per person", () => {
  const text = orgChartCsv(chart, { includeLegalEntity: true });
  assert.ok(!text.startsWith("﻿"));
  assert.ok(!/[^\r]\n/.test(text.replace(/"[^"]*"/g, "")));
  const rows = parse(text);
  assert.deepEqual(rows[0], ["Name", "Job title", "Department", "Manager", "Status", "Start date", "Last day", "Legal entity"]);
  assert.equal(rows.length, 6);
  const byName = Object.fromEntries(rows.slice(1).map((r) => [r[0], r]));
  assert.deepEqual(byName["Bala Raj"], ["Bala Raj", "Lead Engineer", "Engineering", "Aina Tan", "active", "", "", " contoh sdn bhd "]);
  assert.deepEqual(byName["Zul Hamid"].slice(0, 4), ["Zul Hamid", "", "", ""]);
});

test("the CSV omits the legal entity column when asked", () => {
  const rows = parse(orgChartCsv(chart, { includeLegalEntity: false }));
  assert.ok(rows.every((r) => r.length === 7));
  assert.ok(!rows[0].includes("Legal entity"));
});

test("the CSV neutralises formula starts and round-trips commas, quotes and newlines", () => {
  const hostile = {
    nodes: [
      node("a", "=cmd|' /C calc'!A0", null, { job_title: 'Head of "Ops", North' }),
      node("b", "Line one\nLine two", "a", { job_title: "+1 title", department: "@dept", start_date: "2026-12-01", status: "starting" }),
      node("c", "-minus", "a", { job_title: "\tTab" }),
      node("d", "\rCarriage", "a"),
    ],
    looped: [],
  };
  const text = orgChartCsv(hostile, { includeLegalEntity: false });
  const rows = parse(text);
  const byId = Object.fromEntries(rows.slice(1).map((r) => [r[0].replace(/\n/g, "|"), r]));
  assert.equal(byId["'=cmd|' /C calc'!A0"][0], "'=cmd|' /C calc'!A0");
  assert.equal(byId["'=cmd|' /C calc'!A0"][1], 'Head of "Ops", North');
  assert.deepEqual(byId["Line one|Line two"].slice(0, 7), ["Line one\nLine two", "'+1 title", "'@dept", "'=cmd|' /C calc'!A0", "starting", "2026-12-01", ""]);
  assert.equal(byId["'-minus"][1], "'\tTab");
  assert.equal(byId["'\rCarriage"][0], "'\rCarriage");
  // The manager name is a field too and must be neutralised.
  assert.equal(rows.length, 5);
  for (const row of rows.slice(1)) for (const field of row) assert.ok(!/^[=+\-@\t\r]/.test(field), field);
});
