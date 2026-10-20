# CSA — LangGraph rebuild

Customer-service automation core. See [the phase plans](docs/langgraph-rebuild/)
and the authoritative [dependency pins](pyproject.toml) / [resolved lockfile](uv.lock).

Docs: [Architecture](docs/ARCHITECTURE.md) · [Runbook](docs/RUNBOOK.md) ·
[Progress](docs/PROGRESS.md) · [Project overview](docs/PROJECT_OVERVIEW.md)

## Continuing development

Start with the [domain/release handoff and continuation prompt](docs/handoffs/2026-10-19-domain-release-next-session.md)
and [current progress and verification gaps](docs/PROGRESS.md), then the
[console migration plan](docs/handoffs/2026-10-15-console-source-migration-plan.md).
Source expects schema **0360**; last approved persistent dev is recorded as **0300**,
not re-probed here. M6 remains incomplete: local reads and synthetic authorization/
inventory evidence do not enable live provider operations or stock authority.
The user selected `console.fourstacks.my` for the future console, preserving the
existing apex/`www` landing page. Repository-only CI/image packaging is implemented;
hosting, promotion and deployment remain disabled/unverified. See the
[release contract and isolated staging proposal](deploy/RELEASE.md).
The historical production Compose file must not be run for this release.

### Dated implementation history

The following chronology preserves earlier evidence and superseded next-step language.
It is not a statement of current service availability or deployment authorization.

M4 is functionally closed with an explicitly accepted laptop-dev performance exception.
M5's Telegram staff inbox/reply-approval slice was reviewed and rolled out on approved
guarded dev at **0300**, with platform-admin UI and synthetic Telegram acceptance.
M5 validation: **692 full tests passed** plus lint/frontend checks; live reader denial replay passes.
Representative tenant-admin/staff browser replay also passes. Future rollouts require scoped
approval. M6 now has an [expanded seller-workspace roadmap](docs/handoffs/2026-10-15-console-source-migration-plan.md#m6--business-tabs-integrations-and-staff-preparation)
and an offline read-only local orders/catalogue/stock foundation; it has not replaced
the last approved M5 build. A probe at that stage found the dev gateway/frontend unavailable;
restoration requires separate approval. Historical failures remain recorded. The
[runtime recovery handoff](docs/handoffs/2026-10-16-runtime-stabilization.md)
retains incident history and environment safety details.
M6A lint, targeted backend and frontend checks pass; its full run was **695 passed /
1 TCP latency failure**, not green. Exact unchanged-threshold evidence is in
[current progress](docs/PROGRESS.md).
M6B continues offline with validated TikTok order pages and sanitized failures;
current provider compatibility and tenant/store binding remain explicit gates.
M6B adapter-groundwork checks: **41 passed**; full lint passed, full tests **732 passed /
1 TCP latency failure** (p95 **608.321 ms**, unchanged **<200 ms**). No rollout or
performance waiver is implied.
The next offline M6B slice adds exact source/platform and store filters to local orders;
local catalogue/inventory remains tenant-wide, not synchronized store stock.
Filter-slice validation: backend/contract **10 passed**, frontend **14 transport +
2 state tests** and lint/types/build pass; full lint passes, full tests **736 passed /
1 TCP latency failure** (p95 **235.195 ms**, unchanged **<200 ms**). No live rollout.
The next offline slice adds safe local store-record metadata and exact identity binding.
It does not read secrets, verify connections or resolve credentials for provider calls.
Store-slice validation: frontend **15 transport + 2 state tests** and lint/types/build
pass; full lint passes, full tests **742 passed / 1 TCP latency failure** (p95 **200.161 ms**,
unchanged **<200 ms**). That historical run remains not green.
Official TikTok v202309 documentation now verifies corrected order request paths,
pagination placement and detail envelope. Line-item normalization and trustworthy
authorization evidence still block live provider wiring; see the M6 roadmap.
Latest request-correction validation: **47 targeted tests passed**; full lint and
**749 tests pass**, including unchanged standalone ASGI/TCP limits. Prior failures
remain recorded; one pass is not a laptop performance guarantee or live-integration approval.
The following offline slice adds provider-native typed order/line snapshots without
inventing quantity or unit-price semantics: **82 targeted tests** and full lint pass.
Its full validation passes **784 tests**, including unchanged standalone ingress
limits and disposable reset checks; see [progress](docs/PROGRESS.md) for evidence
and remaining live/normalization gates.
The user selected per-operation authorization/shop verification. Offline evidence
contracts and metadata checks now pass **121 targeted tests** plus lint. Their full run
is **819 passed / 1 TCP latency failure** (p95 **643.091 ms**, unchanged **<200 ms**);
disposable reset checks pass, but full validation is not green. They do not yet supply
durable lifecycle state, trusted live verification or
atomic dispatch, and do not enable provider reads.
The next offline slice adds empty grant/lifecycle storage in migration0310, an internal
repository and atomic terminal invalidation/audit: **154 targeted tests** and lint pass.
Initial full validation: **828 passed / 4 schema-readiness failures**. Gateway version
expectation is now corrected to0310; **23 readiness/migration/grant tests pass**.
Corrected full run: **831 passed / 1 ASGI latency failure** (p95 **681.146 ms**,
unchanged **<200 ms**); lint and disposable reset checks pass. Full validation is not
green. The migration is disposable-tested only, not applied to
persistent dev0300; current-credential fencing and trusted verification/dispatch remain gates.
The subsequent offline0320 slice adds credential-generation invalidation and single-use
read dispatch with the user's conservative race ordering/no-replay policy. It remains
unwired to real credentials/provider calls. **183 targeted tests** and full lint pass;
full lint and **857 tests pass**, including unchanged standalone ingress limits and
disposable reset checks. Historical latency failures remain, not a capacity guarantee.
See [progress](docs/PROGRESS.md) for evidence;
the new gateway expects0320 and is not deployed to persistent dev0300.
The next offline slice verifies official authorized-shop request/selected response fields
and adds fresh exact shop/cipher matching: **138 targeted tests**, full lint and
**877 full tests pass**, including unchanged standalone ingress limits and disposable
reset checks. Earlier latency failures remain recorded. User policy is now fresh shop match
plus stored verified scopes,
with current order permission enforced by the actual order endpoint. This primitive
does not mint evidence or complete trusted adapter/dispatch integration.
The subsequent server-only coordinator composes fresh shop verification with fenced
native order reads and truthful revised evidence provenance: **184 targeted tests**
and lint pass; full validation **887 tests passed**, including unchanged standalone
ingress limits and disposable reset checks. Historical failures remain recorded.
Resolver/HTTP are injected synthetic-only
dependencies in tests, not a real credential resolver or live integration.
The next offline slice defines injected credential/app loaders and decryptor contracts
with exact context/expiry checks and pinned row/field AAD. No real storage/key source
is registered or read; trusted app/cipher provenance and live eligibility remain gates.
See [progress](docs/PROGRESS.md) for current validation.
Resolver selection **122 tests** and repository lint pass, including disposable
reset/migration checks; full lint/test validation **907 passed** (136 warnings).
Subsequent0330 adds immutable encrypted shop-binding evidence and a same-transaction
stored loader with explicit external app/crypto dependencies; storage tests and lint pass.
Full validation: **932 passed / 1 TCP latency failure**, ack p95 **259.251 ms**
against unchanged **<200 ms**. Full validation is not green; see progress for evidence.
No real registry/key lookup or runtime registration exists. Gateway source now
requires0330; persistent dev stays0300. External app rotation/disable requires coordinated
grant invalidation and remains a live integration gate.
Subsequent0340 implements a global non-secret revision gate: shared read fences,
operator-only audited disable/activate CAS and exact external-material revisions.
Old grants remain unusable after rotation; external snapshots must be immutable and
disable must commit before secret replacement. Lint and **150 targeted tests** pass;
expanded lifecycle **9 tests** pass; full lint/test validation **945 passed** (136 warnings),
including disposable reset and unchanged standalone ingress limits.
Gateway source now requires0340, persistent dev0300. User made the preceding latency
failure non-blocking for development, not a benchmark pass or verified home-server cause.
Subsequent0350 adds offline one-use authorization attempts and atomic reconnection
through an injected trusted verifier, not a TikTok exchange implementation or callback route.
Current connection remains intact until success; consumed attempts cannot replay.
New credentials/grant/shop evidence commit together, preserving terminal grant history.
**116 targeted tests** and expanded **39 connection/grant tests** pass; full lint passes,
full tests **971 passed / 1 TCP latency failure** (213.232 ms, unchanged <200 ms).
Latency is user-accepted as non-blocking for development, not a green full result.
Gateway source now requires0360 (identity-only inventory scaffolding); no persistent
migration or service rollout was performed. Last approved persistent dev stays0300. Partner onboarding
still gates live authorization; no secrets, services, webhooks or UI-guide are changed.
Staff Business store reads now expose advisory lifecycle status, never live authority.
The UI labels locally recorded authorization as not verified live, explains eligibility
gates and keeps Connect/Reconnect disabled. Metadata projection selects no tokens,
ciphers, scope values or attempt secrets. **30 targeted backend tests** and isolated
**16 transport + 2 state tests**, lint/types/build pass; full backend lint/tests
**975 passed** (136 warnings), including disposable reset and unchanged standalone ingress limits.
Subsequent offline token-response parser uses explicit absolute Unix expirations,
strict standard-seller type and exact scopes with masked token output; it does not
infer app/shop binding or authorize reads. An isolated single-attempt exchange transport
has **105 focused transport/parser/reconnect tests passing** and full lint pass. It uses
a raw no-hooks/no-retry transport, does not follow redirects, caps response size and
returns safe errors. One full run was **1052 passed / 1 TCP latency failure**; a later
full run was **1065 passed**, 136 warnings. Latest standalone ASGI/TCP ack p95
**74.585/174.885 ms** both pass unchanged **<200 ms** acceptance. The timestamp guard
false positive is fixed. Earlier latency failure remains historical; one passing full
run does not establish stable capacity. Transport alone does not verify app/shop provenance; no live request or connection is
enabled. A trusted verifier
composition now binds the parsed token to an injected immutable app revision and fresh
authorized-shop match for the exact selected shop. It returns the existing
`VerifiedAuthorizationResult` contract and passes **173 focused tests** plus full lint.
The full run preceded the final refresh-expiry boundary check; that check passes in the
focused selection. The verifier is not connected to a callback/runtime or real app
registry; no live request or connection is enabled.
The subsequent synthetic verifier/completion composition test is complete; its dated
evidence is in the [M6B verifier handoff](docs/handoffs/2026-10-18-m6b-verifier-next-session.md).
The newer verifier is not runtime-registered; a separately mounted legacy OAuth route
is not the supported M6B authorization flow and must not be exposed by a domain change.
Continue from the [current domain/release handoff](docs/handoffs/2026-10-19-domain-release-next-session.md).
Use [the console API contract](docs/CONSOLE_API.md) and the relevant
[phase plan](docs/langgraph-rebuild/LG00_master_plan.md) before changing behavior.
The [adopted frontend guide](CSA_WebConsole/csa-console/web/README.md) describes the
safe build boundary. Provider-neutral agent preparation is documented in the
[Wave 2/3 handoff](docs/handoffs/2026-10-15-wave23-preparation.md).

Superseded console handoffs, legacy n8n setup/chat briefs and duplicate PM Markdown
have been removed. The canonical
[PM reference pack](CSA_WebConsole/Docs/csa-agentic-pm/README.md) is retained for
deferred work, not as current CSAV2 implementation or deployment guidance.

## Development

```sh
uv sync --frozen
uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run lint-imports
uv run pytest
```

Only `csa/graph/` may import `langgraph` (enforced by import-linter and
`tests/test_import_boundary.py`).
