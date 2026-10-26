# LG15 — Console turnover (n8n → CSA)

## Current continuation (2026-10-19)

Current entry point: [domain/release handoff and prompt](../archive/handoffs/2026-10-19-domain-release-next-session.md).
Source expects 0360; persistent dev0300 is last recorded approval, not a fresh probe.
Repository-only CI/release packaging is implemented; hosted workflow/OCI integrity
and exact-artifact disposable runtime acceptance pass as of 2026-10-20 (see handoff).
Public deployment acceptance remains unverified, with promotion disabled. The isolated staging
manifest is a proposal only. Disposable packaged-console HTTPS identity/CSRF,
role/tenant denial, readiness refusal/recovery and legacy-path denial now pass;
this is HTTP-client/server-rendered evidence, not public-proxy or browser acceptance.
See the current handoff and
[release contract](../../deploy/RELEASE.md). Preserve the existing landing page and
show incomplete M6 features as local-only, unavailable or synthetic demo.

### Dated implementation and validation chronology

M5 evidence: [Telegram scope and evidence](../archive/handoffs/2026-10-17-m5-telegram-inbox.md).
Prior evidence: [M4 closeout](../archive/handoffs/2026-10-16-m4-closeout-m5-start.md).
M4 is functionally closed by explicit user decision with a laptop-dev performance
exception; M5's authorized Telegram-only staff inbox/reply-approval slice is implemented;
post-review full lint/tests **692 passed**, including unchanged ASGI/TCP ingress thresholds.
Explicitly approved guarded dev0300 rollout and platform-admin/synthetic Telegram
acceptance, live reader denial and representative tenant-admin/staff browser replay
are verified, with automated authority tests passing. Historical laptop latency failures are
retained context, not erased by one passing run or a production performance waiver.
M6 is now staged as a Business management seller workspace: local read foundation,
verified seller reads/connections, separately authorized TikTok operations, multi-platform
inventory authority/mapping, and business support/staff preparation. The local orders/
tracking/catalogue/stock read foundation is implemented offline, not live-rolled out.
See the [expanded M6 scope and gates](../archive/handoffs/2026-10-15-console-source-migration-plan.md#m6--business-tabs-integrations-and-staff-preparation).
M6D has a user-selected offline authority direction and a user-approved rule requiring a
second distinct tenant admin for every non-zero on-hand change, with no self-approval.
Count/adjustment contracts, prototype behavior and test-local audit enforcement now
exist, but no authoritative runtime ledger is implemented; authority cutover remains
proposed. The TikTok Products/Get Product references and public Postman Update Stock request
did not expose schemas in the available public retrieval. The user selected Malaysia and
provided the official Update Inventory v202309 URL, which also returned only the Partner
Center shell; no inventory endpoint or quantity semantics were verified. Provider inventory
remains report-only pending a sanitized official Malaysia v202309 read/write schema.
The user suggested `/product/202309/products/{product_id}/inventory/update` as a candidate
path; it is unverified. Until the rendered official contract is available, only
provider-agnostic synthetic conformance work is appropriate. The detailed M6 roadmap now
records a Stage 1 acceptance matrix for ledger/reservation invariants, tenant isolation,
admin approval, fixed allocations, conflict handling and ambiguous operations; provider-
order cases remain deferred. A separate immutable in-memory prototype, report-only
reconciliation and generated reservation/allocation sequences have **1,352 focused
synthetic cases passing**; repository lint passes. Full synthetic suite: **2,419 passed,
136 warnings** in **1,874.60 s**, using disposable Testcontainers Postgres; `git diff
--check` passes. No failures, including latency checks, occurred in that run; earlier
latency failures remain historical and are not waived or erased. The prototype is not
wired to persistence, APIs, UI, providers or runtime, and covers only part of the
acceptance matrix. Current stock remains local/read-only, with no provider synchronization
or stock-write authority. Next gate is agreeing a behavior-only persistence/concurrency
acceptance contract: competing reservations, cancel/fulfil races, return inspection
single-use, count approval versus concurrent changes, tenant-scoped replay and injected
transaction failures with exact audit/outbox assertions. This selects no schema,
isolation level or locking strategy and is not database execution evidence. The M6 roadmap
now has a local-only conceptual schema/transaction proposal keyed by explicitly enrolled
tenant-scoped `products.id`; it covers candidate account/warehouse, reservation, count,
event and return keys, atomic order/count/return boundaries, composite tenant-FK
prerequisites, and staged RLS/privileges. Review clarified the reservation product
relationship, active-claim source of truth, single initial warehouse, and fenced cutover
from legacy projections, including order-line quantity binding and verified holding
statuses. Before rollout/cutover, verify parent tenant integrity, trusted actor identities, configured
holding statuses, old-writer drain/fence and initial count evidence/approval. Source
confirms the current code default holds draft/pending-confirmation orders and approval
commits stock, but deployed settings/writers remain to verify. Current agent context lacks
verified staff identity; the approval flow's staff target is not proof of the approver, so
future count approval must use session-derived tenant-admin identity. Current local orders
use product rows as stock
units, but no typed selectable-variant contract proves external variant identity. New
storage should remain non-authoritative until a tested atomic cutover; provider mapping,
allocations and sync stay gated. No code, DDL, migration or write authority is approved.
The baseline FKs do not enforce that order item, order and product share a tenant; existing
rows were not inspected. No all-writer maintenance fence is implemented/verified, and no
physical initial count or approval exists. Those are blockers before rollout/cutover,
not further synthetic development or proposal review.
An isolated candidate-schema feasibility test now passes **4 synthetic disposable-
PostgreSQL cases**: composite tenant/product FK rejection, synchronized competing
reservations, stale-count rejection preserving a new claim, and transaction rollback on
an injected event constraint failure (`uv run pytest
tests/test_inventory_persistence_feasibility.py -q`, **4 passed in 10.47 s**, one
Testcontainers deprecation warning). `make lint` passes. This is not application-schema,
actual-row, RLS, trusted-admin, writer-fence, physical-count or full Stage 1 acceptance
evidence; it authorizes no DDL or cutover. Expanded 2026-10-19 focused evidence:
**21 passed in 21.74 s**, one Testcontainers warning; `make lint` passes. Tests now cover
eight cancel/fulfil races, both terminal outcomes, replay/opposite-action rejection and
rollback after each terminal account/claim/event write, plus concurrent reservation
replay and changed-quantity rejection. Assertions include exact durable event payloads.
Combined domain/persistence regression passes **1,373 tests in 32.92 s**, one warning;
`git diff --check` passes. Fixture order IDs now match baseline text types.
Return/replay continuation now has **36 candidate cases**; combined domain/persistence
regression: **1,388 passed in 49.21 s**, one Testcontainers warning; `make lint` and
`git diff --check` pass. Covers concurrent return caps and request replay, receipt-gated
zero/partial/full restocking, duplicate/conflicting inspection races, rollback/retry
at coupled writes, changed-payload rejection and independent synthetic tenant keys/events.
No application transaction, trusted physical receipt/inspection evidence, order-state,
refund/payment, RLS, outbox or migration-compatibility proof is claimed. The full suite was not
rerun; earlier suite/latency evidence remains historical. Repository schema is sufficient
for offline design; no old database/n8n dump is required. The documentation-only migration
review now recommends identity scaffolding first: necessary typed tenant-composite
anchors plus an empty warehouse registry, tenant-read RLS and no runtime mutation grants.
Existing global PKs and legacy writes stay intact; same-tenant parent FKs are separate
because NOT VALID still enforces new writes. Explicit default-grant revocation and later
trigger-owned append-only events are required; full actors/fingerprints, pending-count
freeze and atomic order integration remain unresolved. Disposable migration compatibility,
role/RLS, reapply and rollback acceptance is specified in the M6 roadmap.
The user subsequently approved Stage A: migration **0360** now adds only the required
composite unique anchors and empty warehouse registry, tenant-read RLS without FORCE,
explicit default-grant revocation and no runtime writes. No ledger/enrollment/backfill,
same-tenant parent FK or legacy stock trigger is added. Gateway source expects0360;
no persistent migration or service restart occurred. Disposable catalog/role/constraint,
synthetic upgrade/legacy stock, runner reapply/rollback and current-version readiness
checks are implemented. Combined inventory/migration/health/order regression:
**1,421 passed, 1 deselected, 1 warning in 119.07 s**; full lint passes.
The reset test was deliberately deselected; no reset was run. Full suite/latency checks
were not rerun, and historical evidence remains unchanged. Next: typed command/actor/
evidence contracts, fingerprints and pending-count freeze semantics before writable
ledger storage; parent-integrity enforcement remains a separate compatibility review.
Actual-row, deployed-writer/status and physical-count evidence is required for
persistent rollout/cutover, not to continue synthetic development.
Offline count-command continuation adds frozen Pydantic account/admin/request/review
contracts and canonical schema-v1 business-payload fingerprints; session renewal is
excluded from payload identity, not from future authentication checks. Strict quantity,
tenant and distinct-reviewer checks are synthetic validation, not verified authority or
durable outcomes. Pending-count prototype regression confirms new-claim freeze while
existing fulfilment can stale approval; rejection clears the pending freeze.
Full lint and **1,383 focused contracts/domain tests in 7.45 s** pass, no database use.
No migration/runtime transition was changed. Persisted outcomes, trusted actor loaders,
service/return evidence and reservation replay during freeze remain next design work.
Subsequent offline contracts add pending/recorded/approved/rejected outcome validation
and unchanged exact replay, plus an injected actor-loader boundary without a default
implementation or authentication claim. Prototype existing-reservation replay now
precedes freeze/shortfall checks after scope/authority validation; changed quantities
fail and new claims remain frozen. Full lint and **1,391 focused tests in 7.60 s** pass;
`git diff --check` passes. No database/migration/runtime integration or latency rerun.
Next: count transition/audit persistence and transaction review; trusted identity
adapter, concurrency enforcement and service/return evidence remain unfinished.
The subsequent documentation-only count-storage review finds writable persistence not
ready: models do not enforce database transitions, freeze changes need account-level
serialization beyond ledger version, actor reload needs a transaction-bound revocation
contract, and immutable event envelopes/mandatory append enforcement are missing.
Approval must fence stale revisions; authorized rejection must still clear stale pending
commands without posting stock. Decision replay binds reviewer/outcome/reason.
Next recommended bounded slice is a disposable synthetic count-transaction harness,
not a new migration/runtime grant. Prior 1,391-test/lint evidence is unchanged;
no database or service/provider access occurred during review.
The subsequent disposable count harness adds **20 synthetic cases**: freeze/reservation
and request races, competing decisions/replay, fulfilment versus approval, stale fences,
rejection recovery, zero counts, synthetic revocation ordering and event rollback. A
frozen [count audit envelope](../../csa/inventory/contracts.py) now validates typed
request/decision evidence, and the disposable harness reads back command, record and
audit JSON to check fingerprints, event IDs and terminal event/record agreement.
Focused count/contracts: **59 passed, 1 existing Testcontainers deprecation warning in
38.44 s**; combined count/contracts/domain/persistence: **1,448 passed, 1 warning in
92.68 s**. `NO_LOCAL_ENV=1 ENV=ci make lint` and `git diff --check` pass. No reset or
persistent/provider/service access; full suite/latency not rerun, source 0360 unchanged.
This remains a synthetic single-account model, not proof of deployed role grants, RLS or
protection from table-owner/superuser trigger bypass. Test-local deferred constraints
reject missing/mismatched command audit events, and triggers reject event update/delete/
truncate and terminal-command replacement. The harness still runs as disposable
superuser `csa`; its synthetic non-owner `count_trial_app` can commit only with the
matching event and cannot mutate events, disable triggers or set
`session_replication_role`. This does not validate deployed `csa_app` grants or protect
against the superuser/table owner. No application migration or runtime writer/grant was
added.

Identity source review confirms the current `csa_identity` session/membership resolution
commits on a separate pool transaction before a later `csa_app` tenant transaction, so
there is no transaction-bound tenant-admin revalidation for inventory writes. Existing
membership/user/session revocation paths also touch identity rows in differing orders.
Focused transaction tests: **29 passed, 1 existing Testcontainers deprecation warning
in 43.73 s**; combined count/contracts/domain/persistence: **1,457 passed, 1 warning in
114.59 s**. `NO_LOCAL_ENV=1 ENV=ci make lint` and `git diff --check` pass; full suite/
latency was not rerun. Next: disposable least-privilege role and trigger-ownership
checks for a chosen ownership model that addresses the legacy `csa`/n8n owner bypass,
then reconcile identity lock order before proposing a migration. Writable inventory
persistence remains gated.
M6A lint, targeted and frontend checks pass. Its full validation: **695 passed / 1 TCP latency
failure** (ack p95 **788.334 ms**, unchanged **<200 ms**). This is not green or covered
by the earlier M4 exception. See [current progress](../PROGRESS.md) for exact evidence.
M6B's offline order-read groundwork validates existing synthetic TikTok response assumptions,
preserves continuation tokens and sanitizes failures. It does not verify current provider
compatibility or bind credentials to tenants/stores; those gates precede console wiring.
M6B adapter-groundwork checks: **41 passed**; full lint passed, full tests **732 passed / 1 TCP
latency failure**, ack p95 **608.321 ms** against unchanged **<200 ms**. Disposable reset
checks pass; no live rollout, threshold relaxation or new performance waiver occurred.
The next offline M6B slice adds exact persisted source/store filters to local order
list/detail/cursors and an explicit-apply UI. Inventory stays tenant-wide; no provider
or credential binding is inferred. Latest evidence and remaining gates are in progress.
Filter-slice checks: backend/contract **10 passed**, frontend **14 transport + 2 state tests**
plus lint/types/build pass. Full lint passes; full tests **736 passed / 1 TCP latency
failure**, p95 **235.195 ms** against unchanged **<200 ms**. Full validation remains not green.
The next offline slice adds local store-record metadata and exact tenant/record/platform/
store binding. No credential content is selected or decrypted; permissions are unknown,
authorization unverified and provider reads unavailable. Provider credential resolution
and current API-spec verification remain gates, not completed by this metadata view.
Store-slice validation: frontend **15 transport + 2 state tests** and lint/types/build
pass; full lint passes, full tests **742 passed / 1 TCP latency failure** (ack p95
**200.161 ms**, unchanged **<200 ms**). Disposable reset checks pass; no rounding,
latency retry, threshold change, new performance waiver or live rollout is claimed.
Subsequent official-doc verification corrected TikTok v202309 detail path/query/envelope
and search pagination placement; **47 targeted tests pass**. Documented line-item price/
quantity semantics do not match the legacy normalizer; explicit failure is retained rather
than invented quantities. Grant/revocation/credential-generation evidence remains required
before any provider resolver or live read. See the M6 roadmap for authoritative source links.
Latest request-correction full lint/tests pass: **749 passed**, 136 warnings,2783.22 s;
standalone ASGI/TCP p95 **102.572/150.074 ms** pass unchanged **<200 ms**. Disposable reset
checks pass. Simultaneous TCP p95 **1184.752 ms** remains diagnostic evidence, not200 ms
acceptance. Earlier failures remain recorded; no stable laptop capacity or live rollout claim.
The subsequent offline slice adds immutable provider-native order/line snapshots and
detail/page reads, preserving line IDs and `sale_price` without inventing quantities.
Native/legacy synthetic checks **82 passed** and lint/types/import boundaries pass;
full validation passes **784 tests**, including disposable reset checks. Standalone
ASGI/TCP ack p95 **102.295/189.661 ms** pass unchanged **<200 ms**; simultaneous TCP
p95 **1256.362 ms** remains diagnostic, not sub-200 ms acceptance. Historical failures
remain. Normalization and grant/lifecycle binding remain open gates.
User subsequently selected per-operation authorization/shop verification, including a
new operation per search page. Offline immutable evidence contracts and a pure metadata
gate pass **121 targeted tests** and lint. Full validation: **819 passed / 1 TCP latency
failure**, ack p95 **643.091 ms** against unchanged **<200 ms**; disposable reset
checks pass. Full validation is not green; no retry, threshold change or new waiver.
Durable lifecycle
state, trusted live verification and atomic revoke/dispatch handling are not implemented.
The metadata gate alone must not be treated as authorization or provider wiring.
The next offline slice adds durable grant/lifecycle metadata in0310 and an internal
async repository: exact local-store creation matching, terminal revision-CAS invalidation
and database-triggered atomic audit. **154 targeted tests** and full lint pass; migration
idempotency and reset pass only on disposable containers. Initial full validation:
**828 passed / 4 schema-readiness failures**; gateway expectation corrected from0300
to0310, with **23 readiness/migration/grant tests passing**. Corrected full run:
**831 passed / 1 ASGI latency failure**, p95 **681.146 ms** against unchanged
**<200 ms**; lint and disposable reset checks pass. Readiness failures are resolved,
but full validation is not green. No further retry, threshold change or new waiver.
Persistent dev remains at approved0300. Current-credential fencing, trusted verification
and operation/dispatch race enforcement remain gates; no live provider access is enabled.
Subsequent0320 source implements credential-generation invalidation and single-use
read dispatch fences, using the user's conservative ordering/no-replay choice.
Internal callbacks only; no live verifier, credential resolver or adapter wiring.
Lint/types and **183 targeted tests** pass, including disposable reset and readiness;
full lint/test **857 passed**, including unchanged standalone ASGI/TCP limits.
Simultaneous TCP p95 **1418.025 ms** remains diagnostic, not sub-200 ms acceptance;
earlier failures remain recorded, with no stable capacity claim. Gateway source expects0320;
persistent dev stays0300. Existing latency evidence shows large request-start lag but
does not isolate a host/DB/handler cause or establish laptop causality.
Next offline slice: official authorized-shop request/selected response fields verified;
exact fresh shop/cipher match primitive passes **138 targeted tests**, full lint and
**877 full tests**, including disposable reset and unchanged standalone ingress limits.
Simultaneous TCP p95 **450.294 ms** remains diagnostic, not sub-200 ms acceptance;
historical failures remain recorded. User revised freshness semantics to shop match plus stored verified
scopes, with actual order endpoint enforcing current permission. No fresh-scope proof,
trusted adapter binding, evidence minting or fenced integration is claimed by that primitive.
Subsequent server-only composition integrates an injected bound reader and fresh shop
lookup with fenced persisted order dispatch, using truthful shop-plus-stored-scopes
provenance. **184 targeted tests** and lint pass; full lint/test **887 passed**,
including unchanged standalone ingress limits and disposable reset checks.
Simultaneous TCP p95 **535.413 ms** remains diagnostic; historical failures remain.
Resolver/
HTTP tests use synthetic data only; real credential decryption and authenticated app
mapping remain gated. No console/agent integration or live request is enabled.
The next offline resolver contract adds explicit injected app/credential loaders and
decryptor, exact binding/expiry checks and existing canonical row/field AAD. Synthetic
AES-GCM fixtures only; no environment key, stored credential or live request is used.
No default storage implementation/runtime registration exists. **122 targeted tests**
and lint/types pass, including disposable reset/migration; full lint/test validation:
**907 passed** (136 warnings). Performance evidence, historical caveats and actual
trusted storage/eligibility gates are recorded in progress.
Subsequent0330 adds immutable encrypted shop-binding evidence and same-transaction
credential loading, with external app secrets and crypto still explicitly injected.
Trusted writer/loader never infer app/cipher authority from legacy metadata.
Lint/types, storage and disposable reset/migration tests pass. Full validation:
**932 passed / 1 TCP latency failure**, ack p95 **259.251 ms**, unchanged **<200 ms**.
Full validation is not green; no latency retry, threshold change or new waiver.
See progress for exact evidence. External registry rotation/disable must coordinate
grant invalidation; automatic enforcement and verified exchange/reconnection remain gates.
Gateway source now expects0330; persistent dev stays0300. No real key/credential access,
runtime registration, live request, persistent migration/reset, service or UI-guide change.
Subsequent0340 adds a user-selected global non-secret app revision fence and operator-only
audited disable/activate CAS. Shared app locks precede credential/grant fences; old grants
never revive, including after reactivation. External app material must match revision;
disable must commit before changing immutable external snapshots. No default external
registry/key integration exists. Lint and **150 targeted tests** pass; expanded lifecycle
**9 tests** pass; full lint/test validation **945 passed** (136 warnings), including
disposable reset and unchanged standalone ingress limits. Historical failures and
simultaneous-load diagnostic limits remain recorded in progress.
Gateway source now expects0340, persistent dev0300. User made the prior TCP latency failure
non-blocking for continued development, not a benchmark pass or established home-server cause.
Subsequent0350 adds offline one-use authorization attempts and atomic reconnect completion
via an injected trusted verifier. State is hashed, binding/expiry exact, consumption commits
before exchange and cannot replay after uncertainty. Pending/failed attempts preserve
current credentials; success rotates generation and creates new grant/shop evidence
atomically while retaining terminal history. No actual TikTok exchange/callback or default
registry/key source is introduced. **116 targeted tests** and expanded **39 connection/grant
tests** pass; full lint passes, full tests **971 passed / 1 TCP latency failure**
(213.232 ms, unchanged <200 ms). Latency remains user-accepted as non-blocking for
development, not a green full result. Gateway source expects0350, persistent dev0300.
User cannot complete Partner onboarding: live authorization remains externally gated;
offline work neither bypasses that gate nor proves a Sandbox/custom-app exemption.
Subsequent staff Business reads add metadata-only lifecycle status and unavailable
Connect/Reconnect controls. Recorded authorization is explicitly not verified live;
existing staff/tenant authority and secret exclusions remain. **30 targeted backend
tests**, isolated **16 transport + 2 state tests**, lint/types/build pass; full backend
lint/tests **975 passed** (136 warnings), including disposable reset and unchanged
standalone ingress limits. No schema change, live callback/provider access, rollout or
UI-guide edit; source0350/persistent dev0300 unchanged.
Subsequent public-doc verification confirms token exchange query shape, explicit
`granted_scopes` and absolute Unix expiry timestamps despite `*_expire_in` names.
User identity is not shop identity. Query-secret logging isolation must precede real
transport; next offline slice is a strict selected-response parser with synthetic tests.
No runtime change or new validation run; latest975 full pass remains prior evidence.
See the [exchange contract](../archive/handoffs/2026-10-15-console-source-migration-plan.md#m6b-public-token-exchange-contract-verification-2026-10-18).
Subsequent offline selected-response parser validates absolute Unix expiry, standard
seller0 and exact distinct required scopes, returning only frozen secret-masked token
material/expiry/scopes. No app/shop binding or grant authority is inferred; no transport
or trusted live verifier exists. Lint and **103 parser/authorization tests** pass;
One full run had **1052 passed / 1 TCP latency failure**; the subsequent full run had
**1065 passed**, 136 warnings. Latest standalone ASGI/TCP ack p95 **74.585/174.885 ms**
pass unchanged **<200 ms** acceptance. The timestamp guard false positive is fixed.
The historical latency failure remains recorded; one pass is not a stable-capacity
claim. The isolated
fixed-endpoint exchange transport has **105 focused transport/parser/reconnect tests**
and full lint passing. It bypasses client hooks/instrumentation, uses zero retries/no
redirects, disables environment proxy lookup, bounds responses and returns fixed safe
errors. It is not integrated into attempt completion and does not prove app/shop
provenance; no live request or connection. Subsequent trusted verifier composition
binds selected tokens to injected immutable app revision and fresh exact authorized-shop
evidence, returning the existing verified-result contract. **173 focused tests** and
full lint pass. The full run preceded the final refresh-expiry boundary addition, which
the focused tests cover. It is not connected to a callback/runtime or real registry.
Eligible real seller acceptance remains gated; source0350/persistent0300 unchanged.

The console source is now available at `CSA_WebConsole/csa-console/`.
The user approved replacing its backend logic, retaining the Next.js UI and using
**one CSAV2 backend**, with project-management/terminal/Hermes/workroom features
disabled for a separate phase.

The authoritative implementation sequence and source-to-target route inventory are in
[the detailed migration plan](../archive/handoffs/2026-10-15-console-source-migration-plan.md).
It supersedes earlier assumptions about a separate console backend, actor headers,
unknown console endpoints and source availability in this document and older handoffs.
M0–M3 source adoption, identity/onboarding, controls/activity and KB lifecycle are
implemented. Guided M1/M2 isolated-dev acceptance and agent-operated platform-admin M3
browser checks pass; M3 tenant-role browser replay remains pending. Public deployment and
runtime cutover have not been performed. See the implementation record and review evidence
in the detailed plan; this phase as a whole is not complete.

### M4 read-only sandbox continuation (2026-10-16)

M4 is implemented under the user's explicit **Sandbox assistant** choice:
KB/product-information reads only, private per-account/tenant conversations, durable
web ingress/graph/outbox and atomic local availability, held-state retention, stable
polling/history and audited archive. Customer records, business mutations, staff
escalation, callbacks/confirmations, voice and attachments are rejected/unavailable.
The original M4 confirmation requirement is intentionally narrowed, not bypassed.

Migration 0290 has staged tenant RLS, dedicated sandbox contacts and disabled/token-free
internal routing metadata. Explicit risk approval authorized its forward-only isolated-dev
application and guarded service replacement, now complete without reset. Health/readiness
pass; user-operated KB/archive acceptance is metadata-corroborated. An invoice clarification
with no agent execution exposed a scope UX gap, fixed with **11 sandbox tests passing**.
The worker-only replacement is live after explicit approval despite the updated full
latency failures. Fresh user-operated refusal, sandbox KB and Telegram checks are
metadata-corroborated; audited archive, history reload and tab offline/online recovery
pass. Separately approved, privately registered synthetic-reader browser replay now
confirms disabled creation, absent send/archive, empty owned list, other-owner history
404 and cross-tenant 403; backend denial also passes on disposable fixtures.
The reader's own pre-existing history is not live-replayed because the new account has
none. No passwords, invite tokens or cookies were inspected.
No public deployment, Telegram webhook or infrastructure change is authorized or claimed.
Focused runtime/runner/Telegram/contract/migration validation **60 passed**, including
disposable-only `make db-reset`. See [current progress](../PROGRESS.md) and
[the API contract](../CONSOLE_API.md#private-sandbox-assistant-m4)
for validation and guarded live acceptance. Pre-rollout full run:
**674 passed / 1 TCP latency failure**, p95 **202.643 ms** against unchanged `<200 ms`.
The isolated repeat passes, but that full run remains **not green**. The latest scope-fix
full run is **674 passed / 2 latency failures**, ASGI **508.717 ms**, TCP **298.288 ms**
against unchanged `<200 ms`; lint and disposable-only reset pass. Explicit risk approvals
did not change the threshold. The user later explicitly deferred performance acceptance
for functional M4 closeout on the reported laptop server; no cause is established.
This is a local-dev exception, not green tests or a production waiver.
Latest uninstrumented focused reader/ASGI/TCP selection: **2 passed / 1 failed**;
ASGI p95 **93.234 ms** passes, TCP **261.451 ms** fails. Metadata-only profiling captured
passing TCP runs, not a proven cause for the failures; runtime/thresholds remain unchanged.

### Runtime follow-up (2026-10-16)

After the explicitly approved clean-dev recovery, fresh synthetic KB ingestion and
Telegram delivery passed. Subsequent isolated investigation reproduced a gateway
stdout-backpressure stall and implemented user-approved bounded background logging:
liveness remains responsive, readiness exposes stalls/overflow/writer errors and lost
event counts. A real Next.js proxy socket-reset test verifies HTTP 500 can follow a
committed upload and that an explicit same-key retry reuses the job.

Final offline lint/full suite: **655 passed**; final focused logging/gateway/poller
selection: **29 passed** (overlapping, including an additional exact threshold test
added after full-run collection). Both unchanged 50-rps latency benchmarks pass in the
full run. Frontend transport tests: **9 passed**, changed-test ESLint passes.
The historical hang/proxy-error cause is not established. No new live rollout, production
or infrastructure changes were made. Dev gateway/frontend were not listening at the
final check; guarded startup and fresh authenticated acceptance remain pending.
See [detailed investigation and recovery evidence](../PROGRESS.md).

## M3 KB lifecycle implementation (2026-10-15)

- Added authenticated bounded multipart uploads, server UUID storage, durable atomic
  publication, tenant idempotency, typed original-source metadata/download and audited
  document/source deletion. Public contracts omit raw storage paths/internal job metadata.
- Migration 0280 uses staged tenant RLS for batches/files/hash tombstones. Shared tenant
  locks and source/hash checks before deduplication and after embedding fence deletion
  races and retries, including legacy jobs. Explicit audited reconciliation removes only
  UUID-pattern orphan/partial/deleted files, preserving active and unknown legacy files.
- `/knowledge-base` and `/portal/faq` provide uploads, 5-second job/per-file progress,
  duplicates, versions/model labels, original downloads, confirmed deletion and gaps.
  Reader controls are absent; backend role/CSRF/tenant checks remain authoritative.
  An accepted queue job does not mean the worker is running or the source is searchable.
- Focused synthetic upload/worker/agent/streaming/contract checks **57 passed**.
  Final three-format retrieval/binary-contract/projection **3 passed** (overlap).
  Python lint, isolated frontend
  eight transport checks/lint/types/build and disposable-only `make db-reset` pass;
  disposable reset verifies 0280 and RLS on all three new tables.
- Authorized persistent-dev rollout forward-applied 0280 without reset, restarted only
  gateway/frontend and started a guarded KB-only loop for the explicit synthetic M1 tenant.
  Task-local storage uses `$HOME/.local/share/csav2-dev/kb-uploads`, not the configured
  repository-root value. No environment-file edit, full runtime or production access.
  Platform browser PDF/DOCX/CSV ingestion via loopback TEI, byte-verified downloads,
  duplicate suppression, confirmed source/document deletion, 404 after deletion and
  active-source reconciliation pass; audit events are corroborated. One synthetic DOCX
  remains. Both synthetic-tenant KB switches stay enabled for testing.
- Live acceptance found/fixed gap-list argument drift in console/agent callers; the
  rollout regression/upload/agent/launcher/contract selection **34 passed**, with final
  lint passing. M3 tenant-admin/reader browser replay, populated gap UI, live changed-
  content versions, crash races and customer chat retrieval remain unverified.
  No Gemini failover or public deployment claim.
- Full suite **607 passed / 2 ingress latency failures** (ASGI
  266.718 ms, TCP 887.834 ms against unchanged 200 ms). Final resource follow-ups pass
  focused tests/lint. Both unchanged benchmarks then pass in isolation (ASGI 123.840 ms,
  TCP 137.444 ms), but full acceptance is not green; no cause for the variability is
  claimed. Detailed evidence is recorded in [progress](../PROGRESS.md).

The review snapshots below predate M2 browser follow-up and M3. With explicit approval
the stale isolated gateway was restarted for M2; authenticated platform views loaded
and the user later confirmed the guided tenant-admin/reader checks and cleanup.
Populated-run/provider acceptance remains unverified.

## Implementation review (2026-10-15)

- Implemented: isolated identity pool and migration 0270, sessions/CSRF/RBAC, invite/reset/
  password lifecycle, audited tenant/member onboarding, disabled capability provisioning,
  adopted authentication/admin/portal shell, static OpenAPI contract and deferred-route gates.
- M2 now implemented locally: role-derived catalogue/readiness, independent subscriptions
  and tenant switches, safety guards, specialised inspection navigation, scoped run metadata/
  24-hour overview and ID-keyset pagination. Agents/activity screens reuse trace-event polling.
  Default-provider limitations and KB embedding compatibility are explicit, not healthy metrics.
  Production attempts freshly enforce switches across processes, callbacks and retries;
  console KB execution and background upload claims are gated too. In-flight work is not cancelled.
- M2 focused checks: catalogue/API/identity/registry/runner/contract selection **74 passed**;
  runtime/callback/KB-queue/timeline/worker selection **77 passed** (overlapping selections).
  Python lint and isolated frontend **6 transport checks**, ESLint, types and production
  build pass. No schema/dependency change or reset. Authenticated M2 browser checks remain
  pending; the existing loopback gateway needs a deliberate restart to load the new routes.
- M2 full run: **559 passed, 32 failed**. Lifecycle mocks and golden fixtures were
  corrected for the new settings/registry requirements; a focused gateway/golden/
  runtime/contract rerun **45 passed**, with final Python lint passing. Full-run ASGI
  ack p95 was **853.178 ms** (>200). Subsequent unchanged isolated load checks both
  pass: ASGI **92.637 ms**, TCP **126.684 ms**, 150 completed turns/pending deliveries
  each. No clean full-suite rerun or explanation for performance variability is claimed.
- Review fixes: UTF-8-safe password equality and rejection of password resets for pending
  invitations, preserving invitation membership creation. Synthetic regression tests pass.
- Final review lint passed. Session selection: 33 passed; fix/config/lifecycle/contract/
  password selection: 30 passed (overlapping selections, not additive).
- The full run before these two fixes completed with 564 passed and one failed TCP ingress
  benchmark (p95 1021.433 ms versus the unchanged 200 ms limit). Do not mark full validation
  green or infer a cause from that result alone. The exact standalone rerun also failed
  at p95 451.844 ms; diagnosis remains open. The five frontend transport/routing tests
  were rerun successfully during review.
- Next: authenticated M2 browser acceptance; M3 authenticated KB upload/lifecycle can follow
  once its storage/deletion semantics are tested. Provider validation and public
  deployment remain pending.

## Isolated dev smoke check (2026-10-15)

Applied missing 0250–0270 migrations forward-only to verified `csa_dev_db` on port
5434, with no reset. User provisioned identity credentials and the first admin
interactively. Gateway 18081 and frontend 13000 are running on loopback only.
Private VS Code forwarding exposes `http://localhost:13000` to the user's browser.
Gateway readiness, anonymous route gates and same-origin CSRF checks pass.
User confirms login, required password change, admin home, tenant list and team
screen all load; audit corroborates login/password change. Authenticated browser
checks are user-reported because the shared browser has a separate session.
Guided tenant creation and two invitation acceptances are corroborated by scoped
memberships, consumed tokens and disabled capabilities. User reports role gates and
used-invitation rejection pass. Reader logout subsequently returned HTTP 204 as
observed by the user; DB audit and revoked sessions corroborate it. Post-logout
portal access redirects to login according to the user. These guided lifecycle
checks pass; authenticated M2 browser acceptance and full-suite repeatability remain open.
No public proxy, worker, delivery, provider or runtime cutover changes.

## CONTEXT FOR CODING AGENT
The web console is currently built against n8n: its webhooks and workflows, and possibly direct
database reads. This phase moves every console feature onto the CSA console API (`/console/v1`,
`csa/console/`) and moves each client's runtime from n8n to LangGraph, using the LG09 strangler
flag. n8n core workflows are then retired. Read `AGENTS.md`, `LG09`, `LG13` (console and KB upload
contract), `LG07` (approval semantics), `docs/CONSOLE_API.md`, and
the [current source-based migration plan](../archive/handoffs/2026-10-15-console-source-migration-plan.md)
first.

Two separate turnovers must not be confused:
1. **Console turnover:** the console UI and backend stop calling n8n and call `/console/v1` only.
2. **Runtime turnover:** a client's customer traffic moves from n8n to LangGraph
   (`clients.config.runtime`). A client can be on the new console while still on the n8n runtime,
   as long as the console endpoints it uses read tables that both runtimes write.

## Boundaries (unchanged from LG13)
- The browser never sees the CSA token, provider secrets, or the database. Next.js forwards
  same-origin requests to the CSAV2 gateway; session identity and tenant memberships are
  verified in CSAV2. There is no separate permanent console business backend.
- Every request resolves to `client_id` + actor + role. Every database call runs under
  `tx(pool, client_id)` (RLS GUC) and filters by `client_id`.
- Staff decisions from the console must reuse the same approval and pending-action code paths as
  Telegram callbacks. These have single-use, expiry, tenant scoping, `approval_audit`, and
  idempotent outbox delivery (LG07). Do not build a second approval implementation.
- Keep SQL in `db/sql/`, use Pydantic in/out models with `extra="forbid"`, use psycopg 3 async,
  make migrations additive and ordered, and use no silent fallbacks.

## Step 0 — Discovery (core inventory and M0 adoption complete)
- Source: copied Next.js UI plus a separate synchronous SQLAlchemy/FastAPI API, n8n
  chat/file/poll calls, direct CSA DB reads/writes, and a large optional PM runtime.
- Reviewed core routes and feature mapping are in the detailed migration plan. No copied
  envs, setup scripts, applications or production n8n resources were executed.
- Any deployed workflow inventory or real user/file import is a later explicitly approved
  operation, not a prerequisite for rewriting the dev console.
- Inventory every console → n8n call: webhook path, method, payload, response, and the screen that
  uses it. Core inventory is recorded; implementation should keep it current as routes change.
- Re-export the **current** n8n workflow list read-only (name, active, trigger and webhook paths
  only; never node credentials). The 2026-09-20 backup inventory is in the handoff and may be
  stale.
- Produce a mapping table: console feature → n8n source → CSA endpoint (exists / to build) →
  tables read/written. Store it in `docs/handoffs/` and keep it updated.

## Step 1 — P0 fixes before any console traffic
1. **Invoice download link.** Remove the hard-coded fallback key and the `CREDENTIALS_KEY`
   fallback in `csa/console/router.py`. Require `INVOICE_SIGNING_SECRET` and raise a configuration
   error if it is missing. Implement the served route `GET /api/v1/invoices/{id}/download`: verify
   the signature and expiry with `verify_invoice_signature`, run under the tenant scope, and return
   404 when the signature is invalid or expired. Decide where invoice files come from
   (storage path or generator).
2. **RBAC and sessions.** Implement cookie-backed sessions and explicit tenant memberships
   in CSAV2. Derive actor identity/role from verified sessions, never browser-supplied
   `X-Actor-Id` or `X-Actor-Role`. Roles:
   `platform_admin | tenant_admin | tenant_staff | read_only`.
   Per-endpoint role checks:
   - `is_enabled` (subscription) is `platform_admin` only.
   - `client_enabled` is `tenant_admin`+.
   - Approvals and takeover are `tenant_staff`+.
   - Read endpoints accept every role.
   Separate existing service-token machine principals from human users, and scope them
   explicitly. Do not let machine auth bypass browser-role restrictions.
3. **Audit.** Record capability toggles (who, when, before → after). This probably needs an additive
`capability_audit` migration: `subscription_events` covers customer subscriptions and
requires a `subscription_id`. Add the actor to KB, approval and takeover actions.
4. **OpenAPI exposure.** Serve `/docs` and `/openapi.json` only in dev, or behind the console
   token. Publish a versioned `docs/console_openapi.json` snapshot for the console developer.

## Step 2 — Console API parity (build in this order unless discovery says otherwise)
| # | Feature | Endpoint(s) | Notes |
|---|---|---|---|
| 1 | Conversations inbox | `GET /conversations`, `GET /conversations/{id}/messages` | paginate by time; redact nothing for staff, but no secrets |
| 2 | Takeover / release / human reply | `POST /conversations/{id}/takeover`, `/release`, `/reply` | uses `owned_by` / `ai_muted_until`; reply goes through the outbox with an idempotency key |
| 3 | Approvals queue | `GET /approvals`, `POST /approvals/{id}/decide` (`approve`/`reject`/`edit`+text) | reuse the LG07 service; same bounds (edited text validation), expiry, single-use, `approval_audit` |
| 4 | KB upload | `POST /kb/uploads` (multipart, ≤3 files) → writes `KB_UPLOAD_ROOT/<client_id>/` + `kb_ingest_jobs` row | replaces console-side DB writes; reuse `extract.py` validators before accepting |
| 5 | KB gap answer | `POST /kb/gaps/{id}/answer` (text → FAQ doc + resolve) | needs approval policy decision |
| 6 | Capability config | `GET/PUT /capabilities/{name}/config` validated by `config_schema` | |
| 7 | Live updates | `GET /stream` (SSE: timeline events, new approvals) | polling remains supported |
| 8 | Channels & integrations | status, OAuth start/callback (TikTok exists under `/api/v1/channels`), revoke | secrets write-only |
| 9 | Notification targets | CRUD on `staff_notification_targets`, quiet hours | |
| 10 | Ops | `GET /runs`, `GET /dlq`, `POST /dlq/{id}/redrive`, breaker reset (`csa.ops.runbooks`) | `platform_admin` only |
| 11 | Domain reads | orders, tickets, bookings, invoices (exists), ecommerce orders | per specialised tab |

Each endpoint needs tests for: the authorised role, a forbidden role, the wrong tenant (empty or
404, never another tenant's data), validation errors, and idempotency where it writes.

## Step 3 — Agent parity (runtime)
A client moves to `runtime=langgraph` only after every n8n workflow it uses has a CSA equivalent,
or has been deliberately moved to the signed `n8n_webhook` agent kind (non-hot-path automations).
See the parity table in the handoff. Not ported yet: bookings, refund status, warranty,
subscription, store, quote, account, voice (LiveKit transcript → internal HMAC endpoint), and
KB sheet ingest.

Preparation update (2026-10-15): booking/subscription now have local customer reads and
unexecuted request agents; staff email/research/invoice-write preparation libraries also
exist. These do not satisfy provider-execution parity. See
`docs/handoffs/2026-10-15-wave23-preparation.md` before estimating the remaining work.

## Step 4 — Per-client cutover (LG09 procedure)
`shadow` (compare for at least a week) → `suggest` → `auto`, one client at a time, with the console
switched to `/console/v1` for that client. Rollback means flipping `clients.config.runtime`; no
webhook changes. Set `KB_INGEST_ENABLED=true`, `KB_UPLOAD_ROOT` and `TEI_ENDPOINT` on the server
at the console switch, and confirm the legacy `kb_documents.embed_model` label (re-index if it
differs).

## Step 5 — Retire n8n core
After 2 clean weeks per client: deactivate (do not delete) the n8n core workflows, revoke tokens
hard-coded in workflow JSON (LG09), migrate the Langfuse sink to OTLP before **2026-11-16**, then
enable `FORCE ROW LEVEL SECURITY` and move n8n off the owner role (LG11 M14).

## Acceptance criteria
- [x] Core copied-source discovery and proposed route mapping recorded in the detailed plan.
- [x] M0 reviewed frontend adoption and static contract baseline implemented.
- [x] M1 identity/RBAC/onboarding implemented with synthetic integration evidence.
- [x] Guided M1 browser lifecycle/RBAC/logout acceptance, user-operated and DB-corroborated.
- [x] M2 runtime/readiness/UI implementation with focused disposable integration evidence.
- [x] Authenticated synthetic packaged M2 Agents/Runtime browser acceptance; see the
      [2026-10-20 handoff](../archive/handoffs/2026-10-19-domain-release-next-session.md#latest-slice-synthetic-m2-browser-acceptance-2026-10-20).
- [ ] Full-suite performance acceptance.
- [ ] Every active frontend feature actually uses an implemented CSAV2 endpoint, not n8n;
      the old Python API and deferred PM/voice runtimes are not mounted or started.
- [ ] P0 fixes merged with tests (download route and strict signing secret, RBAC, audit, OpenAPI
      restriction).
- [ ] Parity endpoints implemented with role, tenant, validation and idempotency tests; LG07
      approval semantics proven through the console path (approve/reject/edit/expired/used).
- [ ] Console running against the dev stack (`csa-dev.nexusmandiri.com`) with synthetic tenants;
      end-to-end UI check recorded.
- [ ] Per-client shadow → auto completed with recorded rollback drill; n8n core deactivated.
- [ ] `make lint test` green; `make db-reset` verified on a disposable database for any migration.
