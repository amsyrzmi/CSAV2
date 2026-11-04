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
const transition = await import(moduleUrl(
  await readFile(new URL("../lib/avatar-transition.ts", import.meta.url), "utf8"),
));
const { nextPhase, avatarStateForPhase } = transition;

const PHASES = ["idle", "still", "pending", "wake", "settling"];
const EVENTS = {
  start: { type: "start" },
  confirmedActive: { type: "confirmed", to: "active" },
  confirmedInactive: { type: "confirmed", to: "inactive" },
  failed: { type: "failed" },
  done: { type: "done" },
};

// Expected result for every (phase, event) pair, motion allowed and not allowed.
// Only a request in flight (pending) reacts to confirmed and failed.
const TABLE = {
  idle: {
    start: ["pending", "pending"], confirmedActive: ["idle", "idle"],
    confirmedInactive: ["idle", "idle"], failed: ["idle", "idle"], done: ["idle", "idle"],
  },
  still: {
    start: ["pending", "pending"], confirmedActive: ["still", "still"],
    confirmedInactive: ["still", "still"], failed: ["still", "still"], done: ["still", "still"],
  },
  pending: {
    start: ["pending", "pending"], confirmedActive: ["wake", "idle"],
    confirmedInactive: ["settling", "still"], failed: ["still", "still"],
    done: ["pending", "pending"],
  },
  wake: {
    start: ["pending", "pending"], confirmedActive: ["wake", "wake"],
    confirmedInactive: ["wake", "wake"], failed: ["wake", "wake"], done: ["idle", "idle"],
  },
  settling: {
    start: ["pending", "pending"], confirmedActive: ["settling", "settling"],
    confirmedInactive: ["settling", "settling"], failed: ["settling", "settling"],
    done: ["still", "still"],
  },
};

for (const phase of PHASES) {
  for (const [name, event] of Object.entries(EVENTS)) {
    for (const [index, motion] of [true, false].entries()) {
      test(`${phase} + ${name} (motion ${motion})`, () => {
        assert.equal(nextPhase(phase, event, motion), TABLE[phase][name][index]);
      });
    }
  }
}

test("the table covers every phase and event", () => {
  assert.deepEqual(Object.keys(TABLE), PHASES);
  for (const row of Object.values(TABLE)) {
    assert.deepEqual(Object.keys(row), Object.keys(EVENTS));
  }
});

test("failed returns to the phase before start", () => {
  for (const original of ["idle", "still"]) {
    for (const motion of [true, false]) {
      const pending = nextPhase(original, EVENTS.start, motion);
      assert.equal(pending, "pending");
      assert.equal(nextPhase(pending, EVENTS.failed, motion, original), original);
    }
  }
});

test("failed never returns to an animated or in-flight phase", () => {
  assert.equal(nextPhase("pending", EVENTS.failed, true, "wake"), "idle");
  assert.equal(nextPhase("pending", EVENTS.failed, true, "settling"), "still");
  assert.equal(nextPhase("pending", EVENTS.failed, true, "pending"), "still");
  assert.equal(nextPhase("pending", EVENTS.failed, true), "still");
});

test("wake is unreachable without a confirmed activation", () => {
  for (const phase of PHASES) {
    if (phase === "wake") continue;
    for (const [name, event] of Object.entries(EVENTS)) {
      if (name === "confirmedActive") continue;
      for (const motion of [true, false]) {
        for (const before of [undefined, ...PHASES]) {
          assert.notEqual(
            nextPhase(phase, event, motion, before), "wake",
            `${phase} + ${name} (motion ${motion}, before ${before})`,
          );
        }
      }
    }
  }
});

test("a confirmed activation only wakes from pending", () => {
  for (const phase of PHASES) {
    const result = nextPhase(phase, EVENTS.confirmedActive, true);
    assert.equal(result === "wake" && phase !== "pending", phase === "wake");
  }
});

test("without motion a confirmed activation lands in idle and a suspension in still", () => {
  assert.equal(nextPhase("pending", EVENTS.confirmedActive, false), "idle");
  assert.equal(nextPhase("pending", EVENTS.confirmedInactive, false), "still");
});

test("a full activation walks pending, wake, idle", () => {
  let phase = nextPhase("still", EVENTS.start, true);
  phase = nextPhase(phase, EVENTS.confirmedActive, true);
  assert.equal(phase, "wake");
  assert.equal(nextPhase(phase, EVENTS.done, true), "idle");
});

test("avatarStateForPhase maps each phase", () => {
  assert.equal(avatarStateForPhase("idle"), "idle");
  assert.equal(avatarStateForPhase("still"), "still");
  assert.equal(avatarStateForPhase("pending"), "pending");
  assert.equal(avatarStateForPhase("wake"), "wake");
  assert.equal(avatarStateForPhase("settling"), "still");
});
