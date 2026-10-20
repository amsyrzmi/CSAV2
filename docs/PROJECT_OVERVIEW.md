# Project overview: what each file does

See [ARCHITECTURE.md](./ARCHITECTURE.md), [RUNBOOK.md](./RUNBOOK.md), [PROGRESS.md](./PROGRESS.md).
Current continuation: [domain/release handoff and prompt](./handoffs/2026-10-19-domain-release-next-session.md).
Source schema is **0360**; recorded last approved persistent dev is **0300**, not a
fresh availability or schema probe. M6 remains incomplete and offline. The selected
public layout preserves the `fourstacks.my`/`www` landing page and places the future
console at `console.fourstacks.my`; no domain or deployment change has been performed.

## Dated implementation history

The following chronology retains earlier results and superseded next-step statements.
Use current progress and the handoff for release decisions, not historical service probes.

M4 is functionally closed with accepted laptop-dev performance debt. M5's Telegram
staff/reply-approval slice was reviewed, validated (692 full tests pass), and rolled out
on explicitly approved guarded dev0300 with platform-admin/synthetic Telegram acceptance.
Live reader denial and representative tenant-admin/staff browser replay pass.
M6's expanded multi-platform seller roadmap and offline local-read foundation are in
the [console migration plan](./handoffs/2026-10-15-console-source-migration-plan.md#m6--business-tabs-integrations-and-staff-preparation).
Local catalogue/stock are not synchronized marketplace listings or inventory authority.
M6A lint/targeted/frontend checks pass, but its full validation was **695 passed / 1 TCP
latency failure**; see progress for the unchanged threshold and acceptance limits.
The recorded probe at that stage found the dev gateway/frontend unavailable; see current progress before
assuming services are running. No restoration was performed during offline M6 work.
M6B adds internal typed TikTok order pages and explicit safe read failures, not console
provider wiring. Current API specification and tenant/store credential binding are pending.
M6B adapter-groundwork checks: **41 passed**; its full tests were **732 passed / 1 TCP
latency failure**.
Local order source/store filters now constrain list/detail/cursors; local products and
stock remain explicitly tenant-wide. Filters do not establish authorized store connections.
Filter validation: backend/contract **10 passed**, frontend **14 transport + 2 state
tests** plus lint/types/build pass. Full lint passes; full tests **736 passed / 1 TCP
latency failure**.
Local store-record metadata and exact tenant/record/platform/store identity binding are
implemented offline. No token columns or arbitrary metadata are selected; permissions
and authorization remain unknown/unverified. This is not provider credential resolution.
Store-slice validation: frontend **15 transport + 2 state tests** and lint/types/
build pass; full lint passes, full tests **742 passed / 1 TCP latency failure** (p95
**200.161 ms**, unchanged **<200 ms**). That historical full run remains not green.
Official TikTok v202309 request/envelope corrections are implemented and synthetic-tested.
Line-item quantity/price normalization and verified scope/revocation evidence remain gates;
no real provider connection has been verified.
Latest request-correction lint/full suite passes: **749 tests**, with unchanged standalone
ASGI/TCP ingress limits. Earlier failures remain evidence; one pass is not a capacity guarantee.
The next offline slice adds immutable provider-native order/line snapshots, separate
from normalized orders and console projections. **82 targeted tests** and lint pass;
full validation passes **784 tests**, including unchanged standalone ingress limits and
disposable reset checks. No quantity/price interpretation or authorization is inferred.
Per-operation authorization verification was explicitly selected next. Internal immutable
evidence contracts and fail-closed metadata checks pass **121 targeted tests** and lint.
Full validation: **819 passed / 1 TCP latency failure**, p95 **643.091 ms** against
unchanged **<200 ms**. Disposable reset checks pass; full validation is not green.
Durable state, live verification and dispatch-race enforcement
are still unimplemented; no credential resolver or provider connection is enabled.
The subsequent offline slice adds durable grant/lifecycle metadata and terminal
compare-and-swap invalidation with atomic audit in migration0310. **154 targeted tests**
and lint pass. Initial full run **828 passed / 4 schema-readiness failures**; gateway
expectation corrected to0310, with **23 readiness/migration/grant tests passing**.
Corrected full validation: **831 passed / 1 ASGI latency failure**, p95 **681.146 ms**
against unchanged **<200 ms**; lint and disposable reset checks pass. Full validation
is not green. Persistent dev stays at approved0300;
the new gateway build requires0310 and is not deployed.
Current-credential generation fencing and trusted per-operation dispatch remain pending.
Subsequent0320 source adds generation/invalidation fences and durable single-attempt
dispatch with conservative race ordering and no replay. Internal synthetic callbacks only;
trusted verification/credential resolution/provider integration remain unavailable.
The new gateway expects0320; persistent dev remains0300. **183 targeted tests** and
full lint pass, including disposable reset/readiness. Latest full lint/test:
**857 passed**, with unchanged standalone ingress checks passing. Earlier latency
failures remain historical evidence, not a resolved cause or stable capacity guarantee.
The following offline slice adds exact fresh authorized-shop ID/cipher checking against
officially inspected v202309 fields. **138 targeted tests**, full lint and **877 full
tests pass**, including unchanged standalone ingress limits and disposable reset checks.
Earlier failures remain evidence. Revised user policy combines fresh shop matching with stored verified scopes
and actual order-endpoint permission enforcement. Trusted verifier/adapter binding and
fenced integration remain open, not established by this shop primitive.
The subsequent server-only coordinator composes an injected trusted bound reader,
fresh shop verification and fenced native order dispatch. **184 targeted tests**
and lint pass; full validation **887 tests passed**, including unchanged standalone
ingress limits and disposable reset checks. Historical failures remain recorded.
Real credential resolution/decryption and
authenticated app mapping remain absent; no console/agent or live-provider wiring is enabled.
The next offline slice supplies a callable resolver composition with injected trusted
app/credential loaders, decryptor and transport, secret-masked contracts and exact
binding/expiry/AAD checks. There is no default DB/key lookup or runtime registration;
actual authenticated app/cipher storage and live eligibility remain open.
Resolver selection **122 tests** and lint pass, including disposable reset/migration
checks; full lint/test validation **907 passed** (136 warnings).
Subsequent0330 adds immutable encrypted shop-binding evidence and same-transaction
loading with explicit external app/crypto dependencies. Storage tests and lint pass.
Full validation: **932 passed / 1 TCP latency failure**, ack p95 **259.251 ms**
against unchanged **<200 ms**; full validation is not green. See progress for evidence.
No real registry/key lookup/runtime registration exists; external app rotation/disable
must coordinate grant invalidation. Gateway source now requires0330, persistent dev0300.
Subsequent0340 adds global non-secret app revision/audit metadata and shared read fences.
Operator-only disable/activate CAS invalidates old grant revisions across tenants;
external app material must match revision, with immutable external snapshots and
disable committed before secret replacement. Lint and **150 targeted tests** pass;
expanded lifecycle **9 tests** pass; full lint/test validation **945 passed** (136 warnings),
including disposable reset and unchanged standalone ingress limits.
Gateway source now requires0340, persistent dev0300. The user made the prior latency
failure non-blocking for development; home-server causality remains unverified.
Subsequent0350 adds hashed one-use authorization attempts and atomic reconnection through
an injected trusted verifier. Pending/failed reconnects preserve existing credentials;
successful credential rotation/new grant/shop evidence commits together, retaining history.
No real exchange/callback route or default verifier exists. **116 targeted tests** and
expanded **39 connection/grant tests** pass; full lint passes, full tests
**971 passed / 1 TCP latency failure** (213.232 ms, unchanged <200 ms).
Latency is non-blocking for development by user decision, not a green full result.
At that slice gateway source required0350, with persistent dev recorded0300; incomplete Partner onboarding remains
a live gate, not a synthetic-development blocker.
Staff-only Business store list/detail now add advisory lifecycle status and explicit
unavailable Connect/Reconnect UI. Locally recorded authorization is not verified live;
tokens, cipher/scope values and attempt secrets remain excluded. **30 targeted backend
tests** and isolated **16 transport + 2 state tests**, lint/types/build pass; full backend
lint/tests **975 passed** (136 warnings), including disposable reset and unchanged
standalone ingress limits. No schema change, provider access, rollout or UI-guide edit.
Subsequent offline selected token-response parser validates absolute Unix expirations,
strict standard-seller type and exact scope keys with frozen masked output; app/shop
binding and grant authority are not inferred. The isolated single-attempt exchange
transport passes **105 focused transport/parser/reconnect tests** and full lint. It
bypasses client hooks/instrumentation, disables environment proxy lookup, retries zero
times, never follows redirects and bounds the response. One full suite had **1052 passed /
1 TCP latency failure**; the later full run had **1065 passed**, 136 warnings. Latest
standalone ASGI/TCP ack p95 **74.585/174.885 ms** pass unchanged **<200 ms** acceptance.
The timestamp-guard false positive is fixed; the earlier latency failure remains
historical and one passing run does not establish stable capacity. A trusted verifier
composition now binds parsed tokens to an injected
immutable app revision and fresh authorized-shop match for the exact selected shop,
returning the existing verified-result contract. **173 focused tests** and full lint
pass. The full run preceded the final refresh-expiry boundary change, which the focused
selection covers. It is not connected to a callback/runtime or actual app registry; no
live request/connection is enabled. The subsequent concrete verifier/completion test
passes offline; M6D now has frozen count/audit contracts and disposable transaction/
role evidence, not writable runtime persistence. See progress for exact evidence.
The gateway separately mounts legacy marketplace OAuth routes; those are not this
M6B verifier flow and require containment before public hosting.

## Source map

| Path | Role |
|---|---|
| `apps/gateway/main.py` | FastAPI webhook gateway, health, schema-version check |
| `apps/worker/main.py` | Claims ingress events, runs the turn engine |
| `apps/delivery/main.py` | Sends outbox messages (renders buttons), runs staff sender |
| `apps/telegram_poller/` | Polling alternative to webhooks |
| `csa/channels/` | `base.py` channel contracts, `telegram.py` adapter, `ingress.py` webhook handling and button clearing |
| `csa/turn/` | `deps.py` wires DB, LLM and agents into the graph |
| `csa/graph/` | `coordinator.py` graph (plan → spokes → bounded observe loop → gate), `policy/` routing, plan and handoff rules, `saver.py` checkpoints, `retention.py` |
| `csa/decide/` | `groq.py` client (retry), `llm.py` classify/plan/next_step/compose/semantic review, `jev.py` typed classifier, `providers.py` shadow/cascade selection, `grounding.py` fact checking |
| `csa/evals/` | Offline scoring helpers, 60 synthetic decision cases, explicit live LLM/Jev comparison CLI |
| `csa/agents/` | Spokes: `orders`, `order_approval`, `escalation`, `tickets`; `runner`, `breaker`, `recorder` |
| `csa/notify/` | `notifier.py` queues staff alerts; `sender.py` delivers them with action buttons |
| `csa/graph/events.py` | Redacted per-step timeline events written by the graph |
| `csa/console/` | Session identity/RBAC, onboarding, audited capability/KB administration, runtime reads, private KB/product-information sandbox, Telegram inbox and staff-only local Business reads ([CONSOLE_API.md](./CONSOLE_API.md)) |
| `csa/approval/service.py` | Hold AI replies for staff review; decide and expire them |
| `csa/graph/policy/gate.py` | Pure send/hold/suppress policy |
| `csa/staff/callbacks.py` | Handles staff approve / reject / resume taps |
| `csa/contracts/` | Pydantic contracts: envelope, plan, state, callbacks, approval, registry |
| `csa/core/` | Settings, DB pool, crypto, logging, PII, tenancy, tracing, clock |
| `db/migrations/` | Ordered additive migrations through 0360 (empty inventory warehouse registry and tenant-composite identity anchors, not ledger writes); 0350 one-use authorization attempts, 0340 app lifecycle, 0330 encrypted shop bindings, 0320 generation/read fences, 0310 grants, 0270 identity, 0280 KB uploads, 0290 sandbox, 0300 inbox. Later migrations are disposable-tested, not applied by this task to recorded dev0300; staged RLS, no FORCE. |
| `db/sql/` | All parameterised SQL, by area |
| `db/seed/` | Persistent sandbox tenants |
| `scripts/` | `migrate.py`, `setup_dev_telegram.py` (dev tenant, endpoint, demo products, staff target), `lg_setup.py` |
| `tests/` | Pytest synthetic unit/integration/golden suites; `test_policy_edges.py` policy boundaries; `test_load_acceptance.py` ASGI/TCP ingress and simultaneous-worker benchmarks; `test_worker_multiprocess.py` ordering/crash recovery. Exact current results and accepted performance debt live in [PROGRESS.md](./PROGRESS.md), not a duplicated stale count. |
| `Makefile` | Dev lifecycle, lint/tests; `coverage` enforces 90% policy branch coverage, `load-test` reproduces isolated benchmarks |
| `docs/handoffs/` | Current domain/release continuation, M6 roadmap and retained M4/M5/M6B, recovery and agent-preparation history |
| `docs/langgraph-rebuild/` | LG00–LG15 phase plans and acceptance criteria |
| `AGENTS.md` | Repository rules and isolated-dev safety |
