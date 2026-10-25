# M6B offline authorization verifier handoff (2026-10-18)

> Dated subsystem evidence. Current continuation:
> [domain/release handoff](2026-10-19-domain-release-next-session.md).
> Source now expects 0360, not the historical 0350 below. Persistent dev0300 is the
> last recorded approval, not a fresh probe. "No callback" here refers to the new M6B
> verifier flow; separately mounted legacy marketplace OAuth routes exist and must not
> be presented as this flow or exposed by a domain-only change.

## Start here

Continue the offline M6B TikTok authorization work in `/home/amsyrzmi/CSAV2`.
Read [repository rules](../../../AGENTS.md), [current progress](../../PROGRESS.md),
[this M6B implementation plan](2026-10-15-console-source-migration-plan.md),
and the relevant authorization sections in
[LG15](../../langgraph-rebuild/LG15_console_turnover.md) before editing.

The current worktree is intentionally dirty and contains a broad set of earlier console,
runtime, M6 and user changes. Preserve them. Do not reset, stash, restore or broadly
reformat/rewrite existing files. Inspect `git status --short` before edits and limit
changes to the new task.

## Current status

M6B is still an **offline foundation**, not a live TikTok integration:

- The selected response parser is in `csa/agents/ecommerce/token_response.py`.
- The fixed-endpoint, single-attempt exchange transport is in
  `csa/agents/ecommerce/token_exchange.py`.
- The latest verifier composition is in
  `csa/agents/ecommerce/authorization_verifier.py`. It accepts the exact local context
  and code, requires trusted injected app material with exact app ID/enabled state/
  revision, exchanges once, verifies a fresh authorized-shops response has the exact
   server-selected shop ID, and returns `VerifiedAuthorizationResult` with the masked
   fresh shop cipher, token scopes and expiry.
- `TikTokShopAdapter.authorized_shop_cipher` handles first authorization, when there is
  no previous cipher to compare. Existing per-operation `require_authorized_shop`
  still compares fresh provider cipher with the stored cipher.
- `complete_authorization` already accepts this verifier signature and holds the
  attempt/app fences during verification, then atomically persists encrypted tokens,
  grant and shop binding. **The new verifier is not wired into an API callback or
  runtime**, and no real app-secret registry/key source is implemented.
- One synthetic end-to-end test now composes the concrete verifier and completion
  function using separate `httpx.MockTransport` handlers plus disposable testcontainers.
  It verifies consumed-before-exchange, exact app ID/revision, exact intended-shop
  matching, encrypted token/grant/binding persistence, preservation on shop failure,
  burned-attempt no-replay and secret-free failures. No production code change was needed.
- No live callback or provider exchange/request, real credential/key read, live
  connection, rollout or app registration has been performed. Seller/partner eligibility
  and any separately approved live acceptance remain external gates.

The composition-test task is complete. The success flow observes the durable attempt
status from inside the token `MockTransport` handler, validates the exchange's exact
app-key/secret/code query, and checks the saved credentials and shop cipher decrypt only
with the existing field/grant AAD. A follow-up reconnect returns only another shop;
it proves the previous credential snapshot, active grant, grant history and binding are
preserved. A third run forces shop-binding encryption to fail after persistence has
started and confirms credential, grant and binding changes roll back atomically while
the attempt remains burned. Retrying either burned attempt performs no second app load
or HTTP request.

If this reveals a missing boundary, make the smallest change and add a direct regression
test. Do not infer authorization state from an app ID, open_id, seller name or arbitrary
legacy metadata. Shop matching proves no fresh order scope; order permission remains
enforced by the actual order endpoint.

## Validation evidence

- Latest full lint: `NO_LOCAL_ENV=1 ENV=ci make lint` passed; Ruff checks, 289 Python
  files formatted, Pyright reported 0 errors, and import-linter passed.
- Latest full test run, disposable testcontainers only:
  `NO_LOCAL_ENV=1 ENV=ci PYTEST_ADDOPTS=-s make test` — **1065 passed**, 136 warnings,
  **3001.57 s**.
- Standalone ingress p95 in that run: ASGI ack/scheduled **74.585/76.926 ms**;
  TCP **174.885/175.282 ms**. Both passed the unchanged `<200 ms` ack threshold,
 150 requests and 150 durable deliveries each. Simultaneous TCP **506.924/537.490 ms**
  and graph-only **105.615 ms** are diagnostic. This is one passing run, not a stable
  capacity guarantee. Prior latency failures remain recorded in `docs/PROGRESS.md`.
- After that full suite started, a narrow regression was added to recheck both access
  and refresh expiry after the authorized-shop call. The final focused set
  (verifier, shop adapter, parser, transport, reader, resolver and authorization
  lifecycle) passed **173 tests**, 1 testcontainers deprecation warning, **76.18 s**.
  Thus the full suite did not include that final tiny boundary addition; focused tests
  did.
- Latest focused continuation:
  `NO_LOCAL_ENV=1 ENV=ci uv run pytest -q tests/test_ecommerce_connections.py tests/test_tiktok_authorization_verifier.py tests/test_tiktok_token_exchange.py tests/test_tiktok_authorized_shops.py tests/test_tiktok_token_response.py`
  — **139 passed**, 1 testcontainers deprecation warning, **35.18 s**. This includes the
  new end-to-end composition test. Latest `NO_LOCAL_ENV=1 ENV=ci make lint` passed:
  Ruff checks, **289 files formatted**, Pyright0 errors, and import-linter kept.
  The full suite was not rerun because this continuation changed tests only; previous
  full-suite results and latency failures remain historical evidence.
- `git diff --check` passed. The deleted duplicate check scripts were byte-identical
  copies of the canonical PM pack's checks.
- Persistent dev remains schema **0300**, while source expects **0350**. Do not run
  `make db-reset`, apply migrations, restart services or use persistent dev without
  explicit separate approval. Testcontainers use disposable databases.

## Environment and cleanup boundaries

- Never open attached `.env.local` snapshots or any environment file to inspect values.
- Never use production DB, bot tokens, n8n, provider credentials or production data.
- Do not contact TikTok or use real credentials. Keep fixtures synthetic.
- Preserve `ui-guide/` completely untouched.
- Do not change production, infrastructure, webhook settings, service processes,
  uploads, rollout state or public app settings.
- At this handoff, `CSA_WebConsole/Docs/agentic-pm/` contained only three byte-for-byte
  duplicate check scripts; those exact duplicates were removed. The canonical
  deferred PM pack was retained then, but the user later identified the entire
  `CSA_WebConsole/Docs/` tree as obsolete n8n-era material and requested its removal
  on 2026-10-20. Its historical content remains in Git history, not active context.
  The imported `CSA_WebConsole/csa-console/STATE.md`, active `web/AGENTS.md`,
  `web/CLAUDE.md` and frontend README remain. PM/Hermes/workroom work is deferred.
- No dependencies changed. Use `apply_patch` for edits and run focused tests followed
  by `make lint`; run the full test target when code scope justifies its runtime.

## Copy-ready prompt for the next session

```text
Continue from this handoff and preserve the existing dirty worktree.

The verifier/completion composition test is done. Keep M6B offline: do not add a callback
route, runtime/default verifier registration, real app registry, provider/live request,
production wiring or test secrets. Do not inspect environment files, connect to production,
touch persistent dev, run make db-reset, restart services, alter infrastructure/webhooks/
uploads or modify ui-guide/.

The next gate is external confirmation of seller/app eligibility and authorization access,
plus separately approved live acceptance. Until those are available, do not begin callback
or runtime wiring. After the gate, first scope/review the immutable revisioned app-material
and key lifecycle source; implement nothing live without separate approval.

Use synthetic fixtures and disposable testcontainers only. Run focused tests and `make lint`
for any later offline change; if a full suite is warranted, use disposable testcontainers
and report the historical latency failures honestly.
```
