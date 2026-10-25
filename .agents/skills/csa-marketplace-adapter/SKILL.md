---
name: csa-marketplace-adapter
description: Implement or review CSA marketplace adapters, authorization, credential resolution and provider reads. Use for csa/agents/ecommerce and TikTok, Shopee or Lazada integration work.
---

# Build a marketplace integration slice

Read [AGENTS.md](../../../AGENTS.md), current marketplace entries in
[PROGRESS.md](../../../docs/PROGRESS.md), the relevant M6B sections of the
[M6 roadmap](../../../docs/archive/handoffs/2026-10-15-console-source-migration-plan.md), and
the latest applicable handoff. Treat older handoffs as historical evidence and inspect
current source before identifying the next gate.

## Establish trustworthy provider behavior

- Verify request paths, versions, market applicability, pagination, response envelopes
  and field meaning against official provider material before implementing them. Record
  what remains unverified rather than inferring semantics from names or snippets.
- Preserve provider-native typed snapshots when quantity, price, status or inventory
  normalization is not established. Keep local projections explicitly separate.
- Bind every operation to exact tenant, local store record, platform, provider shop,
  app ID and immutable app/credential revision. Browser data, seller names, `open_id` and
  arbitrary legacy metadata do not establish authority.
- Keep secrets in `SecretStr` or encrypted storage, exclude them from repr/logs/errors,
  and pin field-specific AAD. Never expose tokens, shop ciphers or app secrets through
  console projections.
- Use fresh provider evidence where required. Recheck app, credential generation, grant,
  scope, expiry and shop binding under the operation's database fences.
- Consume one-use attempts or read operations in the defined order. After an ambiguous
  external outcome, do not make the attempt reusable. Provider transports are bounded,
  cancellation-cooperative, single-attempt and configured without environment proxies or
  redirects unless verified provider behavior requires otherwise.
- Keep credential rotation, grant history, shop binding and audit writes atomic. Failed
  reconnection must preserve the prior usable connection while burning a consumed attempt.

Use mocked HTTP transports, frozen clocks, synthetic credentials and disposable
PostgreSQL. Test exact request construction, strict parsing, missing/extra fields,
expiry during verification, wrong shop/app/revision, secret-free failures, persistence
rollback, replay and concurrent callbacks. Select affected `test_tiktok_*` and
`test_ecommerce_*` files rather than contacting a provider.

Use `$csa-db-change` for persisted lifecycle changes and `$csa-verify-change` for final
validation. Offline contracts, mocks and disposable composition tests do not establish
seller eligibility, live authorization, runtime registration or production readiness.
