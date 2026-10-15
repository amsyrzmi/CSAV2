# Phase 07 — Hermes integration and evidence-based bug investigation

Prerequisites: phases 04 and 06. Assumption: Nous Research Hermes Agent, subject to phase 00 verification. Do not invent native Hermes API paths or CLI flags.

## IDE AI work

1. Build the typed adapter in CONTRACTS.md against the installed runtime's documented headless/SDK interface. Record runtime version and capabilities. The adapter may be a private service or an in-process wrapper; its contract is your new code.
2. Start with one Investigator assignment and read-only project evidence tools. Move to sandbox tests/code changes only when runner capabilities pass. LangGraph retains orchestration; constrain Hermes's autonomous tools, nested delegation and memory writes.
3. Create an isolated runtime home per project assignment or per run, with single-writer locking for persistent homes. Copy only reviewed SOUL/profile/skills. Never import the personal agent's memories, sessions, cron jobs or environment wholesale.
4. Bind the runtime to a server-selected workspace and base commit. Route every consequential tool through enforceable policy/runner limits. A textual instruction saying “do not touch production” is not enforcement. If native Hermes cannot restrict a tool adequately, disable it or restrict the enclosing sandbox and report reduced capabilities.
5. Bug investigation outputs: symptom, environment/commit, evidence locations, reproduction commands, observed vs expected result, confidence, suspected causes, proposed fix and missing information. Distinguish suspected from reproduced bugs. Do not claim exhaustive bug discovery.
6. For approved implementation create a run worktree, inspect baseline tests, create a patch artifact and run relevant verification. A failed reproduction or ambiguous output yields a draft finding, not a fabricated pass. Repository code/tests execute only under the sandbox policy.
7. Propagate aggregate budget and deadline to Hermes; meter nested calls when available. Enforce a process timeout even if the runtime lacks internal limits. Validate structured results; record unknown telemetry explicitly.
8. Persist external job IDs and reconcile timeout/crash before retry. Cancel is only complete after process/service acknowledgment. Display offline/unsupported capabilities honestly in the workroom.

## Acceptance

Use the same global investigator definition for two projects with different canary notes; prove no cross-project memory appears. Run two instances and verify distinct homes/workspaces. Reproduce a known fixture bug and provide file/commit/test evidence. Restart the worker during external execution and reconcile the original job rather than launching a duplicate. Cancel and verify termination. Confirm private Hermes state and production credentials cannot be read from a PM workspace.

## Operator work

Verify Hermes identity/version/CLI capabilities first. Moving the personal agent and adding PM instances are separate operations; see MANUAL_RUNBOOK.md for a stopped-writer, encrypted-transfer approach. Register a private adapter URL/credential if using a service. A disabled Hermes adapter must not block manual PM or the LangGraph planner.
