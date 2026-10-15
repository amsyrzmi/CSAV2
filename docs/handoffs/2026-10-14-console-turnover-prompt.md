# Kickoff prompt — LG15 console turnover

Paste the block below into a fresh session in `~/CSAV2`. Attach the console source (or its path)
when available; Step 0 is blocked without it.

```text
Continue from docs/handoffs/2026-10-14-console-turnover.md. Before changing anything, read
AGENTS.md, docs/langgraph-rebuild/LG15_console_turnover.md, LG09_cutover_ops_cloud.md,
LG13_agent_catalog_and_console.md (including the KB console upload contract), LG07_egress_outbox_hitl.md,
docs/CONSOLE_API.md, and the existing csa/console/ and approval/staff callback code.

Goal: move the web console off n8n onto the CSA console API (/console/v1), then prepare
per-client runtime cutover per LG09.

Work in this order:
1. Step 0 discovery. The console source is at: <PATH OR "not yet available">. Inventory every
   console → n8n call and every direct DB access. Write the mapping table (feature → n8n source →
   CSA endpoint → tables) to docs/handoffs/. If the source is not available, say so and continue
   with step 2 only.
2. P0 fixes, each with focused tests:
   (a) invoice download: remove the hard-coded and CREDENTIALS_KEY fallbacks, require
       INVOICE_SIGNING_SECRET, and implement GET /api/v1/invoices/{id}/download with signature,
       expiry and tenant checks;
   (b) RBAC: actor id + role on every console request; is_enabled is platform_admin only,
       client_enabled is tenant_admin+, writes are tenant_staff+;
   (c) audit of capability toggles (additive migration if needed);
   (d) restrict /docs and /openapi.json outside dev and publish docs/console_openapi.json.
3. Parity endpoints in the LG15 order (conversations, takeover/release/reply, approvals
   queue + decide, KB multipart upload, ...). Approvals must reuse the LG07 approval service:
   same edited-text bounds, expiry, single-use/idempotency, approval_audit, outbox delivery. Do
   not write a second implementation. Every endpoint needs tests for: authorised role, forbidden
   role, wrong tenant, invalid input, and idempotent writes.
Ask me (ask_user, one question at a time) about the open decisions in handoff §5 when they block you.

Safety: dev stack and synthetic fixtures only. Do not read or print .env.local or secrets; tell me
which variable names you need and I will set them. Do not touch the production DB, bots, n8n,
containers, hostnames, Caddy/Cloudflare, webhook settings or running processes. Use a disposable
database for migration checks (make db-reset only there). Run targeted tests, then make lint and
ENV=ci make test. Update LG15, docs/CONSOLE_API.md and docs/PROGRESS.md with accurate evidence,
and report what was not tested live.
```
