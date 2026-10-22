# M5 Telegram customer inbox

Continuation of [M4 closeout](2026-10-16-m4-closeout-m5-start.md). Isolated synthetic
development only. Post-review validation passes. The user subsequently approved
forward-only 0300, coordinated guarded dev replacement and authenticated/synthetic
Telegram acceptance; rollout and platform-admin acceptance are verified below.
Live reader denial replay now passes after the approved private dev password recovery.
Tenant-admin/staff representative browser replay also passes, with exact limits below.

## Packaged customer-service browser acceptance (2026-10-22)

Current base: PR #7 merged as `650bc08`, with PR/branch/main CI success freshly
verified. The `test/customer-service-browser` branch in
`/home/amsyrzmi/CSAV2-customer-service` adds
[`test_packaged_customer_inbox_browser_acceptance`](../../tests/test_packaged_console.py)
to the existing packaged test harness. This is test/documentation-only work, not
a runtime rollout or a change to the previously approved Telegram-only scope.

The scenario uses a JavaScript-enabled Chromium browser, packaged gateway/frontend,
synthetic TLS, normal invite/login cookie sessions and disposable migrated PostgreSQL:

- Anonymous page redirects/API denial; staff and tenant-admin transcript access;
  reader content/mutation denial; foreign-tenant resource denial and staff cross-tenant
  selection denial. Platform users select a tenant explicitly, and switching tenants
  removes the previous transcript.
- Common-pattern email redaction; private sandbox exclusion even after its channel
  is deliberately mislabeled Telegram; missing-CSRF mutation denial.
- Staff takeover pauses AI; a human reply commits, then a route interceptor drops
  its acknowledgement. Polling finds the saved message. Explicit retry sends the
  exact same payload/key and produces one message, delivery, receipt and reply audit.
  Release and reload preserve AI ownership, and queued is never labeled delivered.
- Real browser approve/edit/reject and expired-approval handling. A different-key
  decision on each settled approval returns `already_handled` with no new delivery.
  Handled history is visible on request.
- Final state: AI ownership/version 2; one human pending delivery plus four approval
  pending deliveries; exactly one approved, edited, expired and rejected approval;
  four approval-domain audit entries; 11 console action receipts/audits (three
  conversation operations, four decisions and four already-handled attempts).
  Browser external-origin requests are blocked and asserted absent.

No delivery process runs. Synthetic ingress metadata and held drafts are seeded;
the coordinator/model/customer-channel ingress is not exercised by this scenario.
An inert credential placeholder is never decrypted or sent. Five durable pending
deliveries establish queuing, not Telegram acceptance or exactly-once remote delivery.
This does not complete KB/order/ticket routing, grounding, language quality, SLA,
customer-web, subscription execution, interactive voice or public deployment.

Commands use `NO_LOCAL_ENV=1 ENV=ci`, existing pinned dependencies and the isolated
task worktree:

```sh
uv run pytest tests/test_console_inbox.py -q
uv run pytest tests/test_packaged_console.py::test_packaged_customer_inbox_browser_acceptance -q
make lint
```

Evidence:

- Existing inbox permission/replay/approval/race suite: **14 passed, 1 existing
  Testcontainers deprecation warning in 102.20 s**.
- Final packaged browser: **1 passed, 1 existing warning in 215.34 s**.
- Repository Ruff/formatting/Pyright/import-boundary checks passed. No frontend
  source changed; the browser fixture builds the current packaged frontend.
- Initial browser attempts failed: **440.38 s** on a fixture using unsupported
  ingress status `done` (corrected to `completed`); **202.86 s** on an ambiguous
  reply-text locator matching both saved message and retained textarea; **329.73 s**
  on an exact edit-label locator (corrected to the approval card's textbox role).
  Each was **1 failed / 1 existing warning**, not a passing acceptance result.
  Initial lint also caught a 101-character fixture SQL line, corrected without
  suppressing checks. Final lint and browser checks passed afterward.

Per the user's current validation policy, full local pytest/load/disposable-reset
checks were not rerun; full hosted CI remains the merge gate. No migration, API,
runtime/frontend behavior, dependency, worker, provider or persistent service changed.

## Confirmed authority

The user explicitly selected **Telegram only, admin/staff access; defer customer-web
and order decisions**. Verified `platform_admin` (with explicit tenant selection),
`tenant_admin` and `tenant_staff` sessions may read customer transcripts and outbound
reply approvals, take over/release, queue human replies and approve/reject/edit reply
drafts. `read_only`, machine and anonymous principals cannot use the inbox. Existing
session, membership, tenant, Origin and CSRF checks remain authoritative.

Customer web and stock-changing order decisions are deferred acceptance, not passed
using the sandbox. M5 does not add an order decision endpoint or a public web participant
model. Existing Telegram order callbacks still use their own domain handler.
M6 and public deployment are outside this slice.

## Separation and contracts

- `/inbox` and `/portal/inbox` are separate from `/chat` and `/portal/chat`.
- `/console/v1/inbox` exposes Telegram conversation/detail/message reads and the reply
  approval queue. All queries exclude sandbox thread IDs and sandbox participant contacts,
  including archived threads and a mislabeled channel. Private held turn fields stay private.
- Content projections are bounded and apply common-pattern redaction. They omit credentials,
  raw envelopes, attachment payloads and arbitrary source URLs. Approval source navigation
  uses the scoped conversation ID. This is not comprehensive personal-information removal.
- Reads use bounded keyset pages; messages/conversations use `(created/opened time, UUID)`
  and approvals use bigint IDs. Default approval pages show pending rows; `include_handled`
  adds settled history. Expired pending rows are settled through the shared domain service.
- Mutations require a UUID `request_key`; conversation actions also require the observed
  `expected_version`. Conflicting content or stale versions return 409. The immutable,
  tenant-scoped replay receipt, console audit, domain change and outbox commit together.
  An explicit same-key retry returns the committed receipt after response loss.
- Human and edited replies use the shared 2,000-character validation. JSON requests are
  capped at 32 KiB. The frontend retains uncertain requests in memory for explicit retry;
  reconnect only refreshes. After navigation/reload inspect persisted history before resending.

## Takeover and delivery semantics

Takeover establishes indefinite human ownership until deliberate release. A current mute
is `owned_by='human_support'` with null or future expiry, matching LG04 and the existing
status trigger. Muted new turns bypass classification, planning, spokes and composition.
Already-running computation can finish; takeover does not undo business work already
executed. Escalation/egress/holds/fallback recheck current ownership under the conversation
lock. Taking over a temporarily escalated conversation explicitly clears its expiry;
the UI exposes **Take over indefinitely**. A late escalation cannot shorten indefinite
ownership to a 24-hour mute or re-mute an explicitly released stale turn.

Takeover skips pending automatic deliveries and records a timestamp boundary. Release
advances that boundary too: old queued/in-flight/retried turns cannot start sending or
holding stale drafts after release. Only subsequently received ingress becomes eligible.
Explicit staff-approved/edited and human replies carry staff authority; automatic rejection/
expiry fallback remains subject to takeover suppression. The approval decision is still
audited when that fallback is suppressed. Normal unmuted rejection/expiry behavior remains.

An intentional code-selected handoff acknowledgement has separate provenance, requires a
matching persisted escalation, and remains subject to the takeover timestamp boundary.
It does not authorize model text or error fallbacks while a human owns the conversation.

Telegram delivery holds the conversation lock through provider send and local acknowledgement.
Takeover waits for a send already in progress; an acknowledged takeover cannot recall a
provider request already accepted. Claims order pending/delivering rows per conversation
by `(created_at,id)` and retain head-of-line blocking during retry. Channel, tenant,
message/conversation/trace and ingress endpoint bindings are checked before transport.
Queued means queued; only the delivery record establishes delivered status. Telegram remains
at least once: remote acceptance followed by crash before local commit can duplicate.

## Shared approvals and schema

`csa.approval.service.transition_reply` returns typed applied/already-handled/expired/
not-found results with queue state and edit warnings. Console and Telegram adapters use
the same row locks, audit, action closure, edit validation and outbox settlement. Console
actors are `console_user:<verified UUID>`; no Telegram action or identity is fabricated.
Telegram edit binding remains in its existing callback adapter. Its response wording now
truthfully says queued instead of sent.
Existing Telegram order callback replies also record a metadata-only completed ingress
trace/endpoint receipt atomically with their customer message/outbox. Without that
provenance the new delivery binding rejected them despite successful order transitions.
Order decisions remain deferred in the console; the existing Telegram domain handler
and strict delivery checks remain authoritative.

Additive **0300** introduces the suppression timestamp, inbox version, message send
authority, replay receipts and supporting indexes. Existing committed reply approvals are
classified as reviewed. Tenant RLS is enabled without FORCE. Applied 0290 is unchanged.
After separate explicit approval, persistent isolated dev was migrated **0290 → 0300**
atomically and the guarded gateway, worker, delivery and frontend were replaced together.
No reset or role-password provisioning was performed. Future replacements/migrations
still require scoped approval; older processes do not implement these ownership guarantees.

## Evidence and remaining acceptance

| Check | Current result |
|---|---|
| Final inbox + coordinator selection | **32 passed**, 1 warning, 201.40 s |
| Migration/reset + sandbox binding + contract selection | **10 passed**, 1 warning, 91.23 s; includes actual guarded disposable-only `make db-reset` through 0300 and staged RLS |
| Final frontend isolated validation | **12 transport/routing/cancellation + 2 state tests passed**; ESLint, route types, TypeScript, production webpack build and build-trace boundary pass |
| Pre-review readiness/load follow-up after version correction | **7 passed / 1 failed**, 4 warnings, 290.42 s; only the unchanged ASGI latency check fails |
| Review takeover/escalation + inbox/staff/coordinator selection | **59 passed**, 23 warnings, 142.23 s |
| Review existing order callback through strict delivery binding | **2 passed**, 3 warnings, 22.56 s; approve/reject, mocked Telegram, one delivered reply each |
| Review frontend isolated validation | **12 transport/routing/cancellation + 2 state tests passed**, lint/types/production build and trace boundary pass |
| Post-review `NO_LOCAL_ENV=1 ENV=ci PYTEST_ADDOPTS=-s make lint test` | **692 passed**, 136 warnings, 1645.86 s; Ruff/format/Pyright/import-linter pass |
| Same full run, unchanged 50-rps ingress benchmarks | ASGI ack/scheduled p95 **108.284 / 109.555 ms**; TCP **174.623 / 176.324 ms**, both `<200 ms`; 150 completed turns and pending deliveries each |

Selections overlap the full suite; counts are not additive. The initial focused run
had 63 passed / 2 new-fixture failures (wrong test URL and duplicate synthetic thread),
then an expanded run had 71 passed / 2 failures (the strengthened Telegram join changed
an existing sandbox error code, and the API snapshot changed during that run). The
fixtures/error-code behavior were corrected; the final focused/reset/contract evidence
above supersedes those failures. No failed latency check was rerun to manufacture green.

The first full run finished **683 passed / 4 failed**, 134 warnings, 1329.21 s.
All four failures were the missed gateway readiness expected-version constant (0290
versus migrated 0300): two health tests and two TCP load cases rejected readiness
before measurements. The constant is corrected to 0300; the pre-review readiness/load
follow-up above confirms those readiness failures are fixed. The post-review full
run now passes all 692 tests, including the five new regression cases and actual
disposable-only reset through 0300. The earlier version mismatch was an implementation
error, not the accepted latency exception.
Canonical evidence is also in
[progress](../PROGRESS.md).

Review found and fixed three coupled gaps: temporary mutes were incorrectly treated as
completed indefinite takeover; late escalation could overwrite ownership after takeover/
release; existing Telegram order callbacks lacked the ingress provenance now required
by delivery. Added five regressions covering those paths, without weakening the binding,
changing applied migrations, or expanding console authority. Focused selections overlap
the full suite and must not be added to it. The final frontend rerun includes the
temporary-pause takeover control. Subsequent authenticated acceptance is recorded below;
the temporary-escalation and crash races themselves retain automated-test evidence.
M4 remains functionally closed under the user's laptop-dev performance exception;
historical full results remain 674 passed / 2 failed latency checks, with final targeted
TCP p95 261.451 ms against unchanged <200 ms. No laptop cause has been established.
The later user-owned architecture review records another non-green run (675 passed /
1 TCP latency failure). None of these is a performance waiver for M7/M8 release.
The new passing full run is current evidence, not a guarantee of consistent performance
on the laptop or a proven explanation for historical failures. No latency thresholds,
pool sizes, tracing or durability settings were changed to obtain it. The simultaneous
capacity report has 150 unique replies, worker-turn p95 **2690.479 ms** and ack p95
**382.791 ms** under its distinct capacity criteria; do not confuse it with the
separate ingress-only `<200 ms` checks. Graph-only overhead p95 **88.396 ms** passes.

No production, n8n, infrastructure, webhook or environment-file changes are part of
this implementation or the subsequently approved dev rollout.
No commit was requested. The checkout was clean at `d8f7149` on entry, superseding the
older handoff's dirty-worktree observation; existing reviews/UI-guide material were preserved.

## Approved guarded-dev rollout and live acceptance (2026-10-16 UTC)

The user approved **forward-only migration 0300, coordinated dev replacement, and
authenticated UI + synthetic Telegram acceptance**. Exact-target preflight required
`127.0.0.1:5434/csa`, owner `csa`, app `csa_app`, identity `csa_identity`, with
`hostaddr`/service overrides rejected. Only pending 0300 was applied in one transaction.
The exact old dev process UID/cwd/role bindings were verified before termination;
no duplicate worker or poller was started. No persistent reset, upload cleanup,
secret inspection/printing, production connection or UI-guide change occurred.

- Gateway: loopback **18081**, detached handle `m5-dev-gateway`.
- Worker: `m5-dev-worker`, dedicated `/tmp/csa-m5-dev-worker.alive`.
- Delivery: `m5-dev-delivery`, dedicated `/tmp/csa-m5-dev-delivery.alive`.
- Frontend: loopback **13000**, `m5-dev-frontend-correct-origin`, isolated reviewed
  source/build at `/tmp/csa-console-check-fAmvT1`. **Keep this stage while it serves
  the dev console.** The active guarded launcher is session-local, not a repo change.
- Initial isolated build used the test script's 18080 rewrite despite the runtime
  18081 setting, producing frontend 500/connection-refused. Rebuilt the same isolated
  source with **18081 at build time**, verified the compiled rewrite and safe traces,
  and replaced only that frontend. Corrected frontend login/CSRF return 200;
  anonymous identity/inbox reads return 401. Gateway health/readiness return 200;
  worker/delivery dedicated heartbeats are fresh.

All acceptance data belongs to existing **Local Telegram Development**
`76946f31-2b21-4e48-9383-65d1a78029c2`, conversation
`ace54869-c5cc-4a43-ac35-c91ef622bff1`. The user signed in privately as Synthetic
Admin; passwords/cookies/CSRF values were not inspected or printed.

| Live check | Correlated evidence |
|---|---|
| Admin navigation/scoping | Separate Customer inbox; explicit tenant selection required (unscoped list 400); conversation and message reads 200 |
| Indefinite takeover | Human-owned, null mute expiry, version 3. Three actual Telegram “What is the return window?” turns completed with zero outbounds before fixture creation; user confirmed no automatic reply |
| Synthetic approvals | Shared `hold_reply` created clearly labeled dev-only fixtures #1/#2/#3, bound respectively to real traces `2ec4c4d0-4670-4c74-9107-ae40ea612049`, `9ca46d6b-cc5c-40aa-9312-cf975d832e2b`, `59b9b43b-7ca6-4b76-99ad-f654da06e3a9`; tenant policy unchanged |
| Human reply + response loss | Browser committed reply, deliberately dropped its response, exposed uncertain same-action retry; explicit retry recovered **one message, one delivery, one receipt, one transport attempt**, message `fbc06a08-fdbc-4de3-8321-a7198fd53cca` |
| Approve/edit/reject | UI settled #1 approved, #2 edited, #3 rejected; verified console actor and exactly one domain audit each. Approved/edited messages delivered with reviewed authority, one attempt each. `appr:3:rejected` has **zero fallback messages** while paused |
| User receipt | User confirmed exactly one labeled human, approved and edited reply in the existing dev Telegram chat, and no rejected fallback |
| Conflict/isolation | Stale ownership version 409; same conversation under other tenant 404; owned private sandbox `1e2140a9-4274-4fc5-a0d7-f98a50e5d19c` returns 404 through inbox |
| Pagination/reload | Two bounded message pages return 2 + 2 distinct IDs. Reload/reselect recovers one replay-test human reply and all three settled cards, without resending |
| Deliberate release | UI removes human-reply form; ownership AI, version 4. Fresh trace `b9b49cba-5e4d-4fd6-9576-2c050fdad52b` completed after the suppression boundary, yielding the seventeen-day KB answer, delivered once; user confirmed receipt |
| No stale automatic release | Since takeover, the only new automatic delivery is that fresh post-release trace; no automatic output from the paused old turns |
| Reader live replay | Initially blocked by forgotten password/throttling; after separately approved recovery and normal private login, **passed at 2026-10-16 21:02 UTC**: verified `read_only`, inbox navigation absent, direct `/portal/inbox` shows permission denial; all four inbox read probes and all four mutation probes return403 |

The requested inbound fixture labels were not the actual Telegram content: the user
sent three return-window questions instead. Their exact trace IDs, not a claimed marker,
bind the fixtures. Earlier user-operated console replies and release/takeover were
observed separately; they are not counted as the controlled response-loss test.
The fresh post-release inbound text was “What is the sample”; its actual persisted
answer, not the suggested full question, is the acceptance evidence.

### Remaining scope

- Live reader denial and representative tenant-admin/staff browser replay pass.
  Do not repeat password recovery or bypass throttling without new approval.
- Timed escalation, expiry, crash races, Telegram order callback compatibility and
  broader policy paths retain the passing full-suite evidence, not new live claims.
- Customer-web, console order decisions, M3 tenant-role replay and later M6–M8 scope
  remain deferred. Historical laptop performance debt and release thresholds are unchanged.
- After subsequent tenant-role replay, the synthetic conversation is deliberately left
  **AI-owned, version 8**, not muted.
  Fixture approvals are settled; retained messages/uploads/history are not purged.
- No new runtime-code changes followed the 692-pass full run; this continuation changed
  dev state and evidence only. No commit or public deployment was performed.

### Subsequently approved private dev login recovery

The user explicitly requested resetting **all synthetic console-user login passwords**
and clearing relevant dev throttling, excluding database-role passwords, bot tokens,
API/encryption keys and production. Inventory was exactly Synthetic Admin
`admin@example.test` and Synthetic M4 Reader `m4-reader-20261016@example.test`.
The user ran a session-local interactive helper with hidden `getpass` prompts;
no password entered chat, command arguments, source or logs.

The helper reused existing identity password hashing/SQL under exact isolated-dev
guards, committed both updates atomically, revoked sessions and unused invite/reset
links, cleared targeted email/loopback throttling and wrote one recovery audit per
account. Metadata verification found both enabled, no forced-password flag, zero
active sessions/links immediately after recovery, and matching recovery audits at
**2026-10-16 20:54:24 UTC**. Subsequent throttle rows were newly created low-count
attempts after recovery, not retained pre-recovery lockouts. Subsequent normal private
reader login and denial replay pass; no lockout policy was disabled.

### Reader denial replay (2026-10-16 21:02 UTC)

Verified session identity200, role `read_only`, active Local Telegram Development tenant.
Customer inbox navigation is absent. Direct `/portal/inbox` renders only
“Staff inbox access requires an administrator or staff role,” without transcript or
approval content. Authenticated requests return **403** for conversation list, existing
conversation detail, messages and approvals. With a valid privately consumed CSRF token,
takeover, release, human reply and approval-decision requests also return **403**.
No token/cookie/password was printed. No accepted mutation or send occurred.
The existing reader role was not elevated. This closes the reader-specific live gap,
not tenant-admin/staff or machine-session browser acceptance.

### Tenant-admin/staff representative replay (2026-10-16 21:08–21:13 UTC)

User explicitly approved separate synthetic accounts through normal invitations,
preserving the existing reader. User privately generated/accepted invitation links
and set passwords; no token/password was inspected. Platform admin invited
`m5-tenant-admin@example.test`; that tenant admin invited
`m5-tenant-staff@example.test`, both in Local Telegram Development.

Both normal authenticated sessions return identity200 with their expected role and
active tenant. Customer inbox navigation is present; conversation list/detail/messages
and handled-approval reads return200. Explicit foreign tenant selection returns403;
the known private sandbox conversation returns404. Each role performed UI takeover,
queued a clearly labeled synthetic human reply, then deliberately released. Persisted
delivery evidence is:

| Role | Human reply message | Result |
|---|---|---|
| Tenant admin | `9509ad2b-7169-4cf5-b3d3-b7dfefcf4320` | One human-authority message, one delivered outbox, one attempt, one receipt |
| Tenant staff | `0b039449-8f4d-4832-80ae-0cb651b7ee4e` | One human-authority message, one delivered outbox, one attempt, one receipt |

The user confirmed both labeled replies arrived once each in the existing dev Telegram
chat. Staff decision replay against already-approved #1 returns200/already_handled,
queued=false; no duplicate send. Fresh approve/edit/reject transitions were exercised
live as platform admin, not repeated for every tenant role; their full role matrix
remains automated-test evidence. Likewise this replay does not close the separate
M3 KB tenant-role acceptance work.

Read-only membership is unchanged and enabled. Both newly accepted synthetic memberships
remain enabled for reuse; their passwords are known only to the user. Final conversation
ownership is AI, null mute expiry, version8. No migration, restart, production/webhook
change or runtime-source edit was needed for these checks.
