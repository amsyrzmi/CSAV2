# Handoff — Console turnover (n8n → CSA), 2026-10-14

Plan: [LG15_console_turnover.md](../langgraph-rebuild/LG15_console_turnover.md). Kickoff prompt:
[2026-10-14-console-turnover-prompt.md](./2026-10-14-console-turnover-prompt.md).

## 1. Where things stand
| Area | State | Evidence / location |
|---|---|---|
| LG01–LG08, LG10 | Done | `docs/PROGRESS.md` |
| LG07 staff approval incl. Edit | Done (Telegram button + text reply, single-use, expiry, `approval_audit`) | `test_staff_path.py`, `test_ingress.py` |
| LG09 strangler + runbooks | Done in code: `clients.config.runtime` n8n/langgraph, shadow mode | `tests/test_cutover_ops.py`, `docs/runbooks/` |
| LG09 actual client cutover | **Not started**; n8n core still serves production | — |
| LG13 Wave 1 agents | kb_agent, invoice_agent, ecommerce_agent (TikTok signer; Shopee/Lazada drafts) | `csa/agents/` |
| KB console upload ingestion | Done, **off by default** (`KB_INGEST_ENABLED`) | `tests/test_kb_ingest.py` (25), LG13 "KB console upload contract" |
| Marketplace ingress | TikTok webhook + OAuth; Shopee/Lazada drafts | `tests/test_marketplace_ingress.py` |
| Langfuse | Live trace verified; **ingestion API sunsets 2026-11-16 → migrate to OTLP** | `csa/core/langfuse.py` |
| Console API | `/console/v1`: traces, events, capabilities, specialised tabs, KB, invoices | `docs/CONSOLE_API.md`, `csa/console/` |
| LG14 teach-a-task | Planned only | `LG14_teach_task_desktop_agent.md` |

Latest test status: `make lint` is green. The full suite ran 448 tests: 442 passed and 6 failed.
Three failures were test drift and are fixed. The other three were latency thresholds under host
load and pass when run alone. **A clean full rerun is owed.** Most LG13+ work is uncommitted on
`main` (`git status`).

## 2. Environment (dev only)
- Dev DB `csa_dev_db` on `127.0.0.1:5434`; dev gateway on `127.0.0.1:18080`; public dev URL
  `https://csa-dev.nexusmandiri.com` (Caddy → Cloudflare). Synthetic tenants `sandbox-amsyar` and
  `sandbox-ihsan`.
- Local TEI at `127.0.0.1:8081` serves `intfloat/multilingual-e5-small` (384-dim). A live semantic
  check passed.
- Settings come from `.env.local`, loaded only with `ENV=dev`. **Do not open or print it.** The
  variable names are in `.env.example`. For console work, the user must set `CONSOLE_API_TOKEN`
  (≥32 chars), `INVOICE_SIGNING_SECRET`, and, for KB uploads, `TEI_ENDPOINT`,
  `KB_INGEST_ENABLED=true` and `KB_UPLOAD_ROOT`.
- Production (`csa_db`, n8n, production gateway `:8080`, bots, Caddy/Cloudflare) is off-limits.

## 3. Known gaps that block console turnover (P0)
1. `csa/console/router.py` `get_invoice_download_link`: on any settings error it signs with the
   hard-coded `"fallback_dev_signing_key_secret_123"`, and it falls back to `CREDENTIALS_KEY`. This
   violates the "no silent fallbacks / no secrets in source" rules.
2. The signed link targets `/api/v1/invoices/{id}/download`, which **no route serves**.
3. One shared console token can change both `is_enabled` (the admin subscription) and
   `client_enabled`. There is no actor or role, and no audit of capability changes.
4. `/docs` and `/openapi.json` are public on the gateway.
5. There is no upload endpoint yet, so the console must write files and the `kb_ingest_jobs` row
   itself. `POST /kb/gaps/{id}/resolve` accepts no answer text.
6. `csa/console/capabilities.py` hard-codes `embed_model "tei-384"` in KB tab metrics.

## 4. n8n inventory (from `~/n8n_backup` 2026-09-20; re-export read-only to confirm)
| n8n workflow | Active | CSA equivalent |
|---|---|---|
| 01_ingress_telegram | yes | gateway `/hooks/{public_id}` ✅ |
| 10_coordinator, 11_context | yes | coordinator graph ✅ |
| 21_agent_order | yes | `order_agent` ✅ |
| 22_agent_ticket | yes | `ticket_agent` ✅ |
| 23_agent_escalation | yes | escalation path ✅ |
| 20_agent_kb | yes | `kb_agent` ✅ (ingest gated) |
| 25_agent_invoice | yes | `invoice_agent` ⚠️ (P0 items 1–2) |
| 30_composer_egress | yes | compose/verify/gate + outbox ✅ |
| 24_agent_booking | yes | ❌ not ported (`bookings` table exists; LG05) |
| 26_agent_refund_status | yes | ❌ (`refunds`) |
| 28_agent_warranty | yes | ❌ (`warranty_claims`) |
| 27_agent_subscription | yes | ❌ (`subscriptions`, `subscription_requests`) |
| 29_store_agent, 30_quote_agent, 31_account_agent | yes | ❌ (scope to confirm; `quotes`, `branches`) |
| 01_voice_submit_gateway, 02_livekit_transcript_callback | yes | ❌ voice → internal HMAC endpoint (LG03 §3F); revoke the hard-coded bearer |
| PropertyManagementHybrid (`POST /webapp/event`, `/webapp/action`, schedules) | yes | client automation: keep in n8n via signed `n8n_webhook` kind, or port; **confirm with user** |
| 40_kb_drive_ingest | no | replaced by `kb_ingest_jobs` (sheet source not built) |
| Property sub-agents, Deterministic — *, Route AI Action Node | no | ignore |

Console-facing n8n webhooks are **unknown** until the console code is provided. The only HTTP
webhooks in the backup are `/livekit-transcript` and `/webapp/*`.

## 5. Decisions needed from the user
1. Console code and stack: framework, hosting, and whether admin and client consoles are one app.
2. Identity: who logs users in, and how users map to tenants and roles. Is service token + actor
   headers acceptable to start, or are signed JWTs required now?
3. Where invoice PDFs come from (generated or stored files), for the download route.
4. Whether answering a KB gap from the console publishes immediately or needs approval.
5. PropertyManagementHybrid and the unported agents: port, keep in n8n, or drop. Which client
   goes first?
6. The KB upload root path on the server, and whether the console can share that filesystem or
   must upload over HTTP.

## 6. Rules for the next session
Follow `AGENTS.md`. Use the dev stack and synthetic data only. Never read or print secrets or
`.env.local`. Never touch production DB, bots, n8n, Caddy/Cloudflare, webhooks or running
processes. Use a disposable database for migration checks. Before claiming done, run
`make lint test` (and `make db-reset` on a disposable DB for migrations) and update LG15 and
`docs/PROGRESS.md` with evidence.
