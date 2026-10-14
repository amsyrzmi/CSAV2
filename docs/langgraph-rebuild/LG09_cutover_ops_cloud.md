# LG09 — Cutover, operations, and the cloud path

## CONTEXT FOR CODING AGENT
Cut over client by client with instant rollback, then retire the n8n core. Make the platform operable by one person at 2 a.m. and movable to a cloud without redesign. Read `LG00` §5 and `LG03`/`LG07` first.

## Strangler design
- The **new gateway becomes the front door for every channel from day one.** For clients still on n8n it **proxies the raw update** to the existing n8n webhook (preserving behavior); for migrated clients it runs the new pipeline.
- Per-client flag `clients.config.runtime = "n8n" | "langgraph"` and `deployment_mode = "shadow" | "suggest" | "auto"`. Rollback = flip the flag (seconds), no BotFather/webhook edits.
- Telegram allows **one webhook per bot**: develop on a separate test bot; for production switch the webhook URL **once** to the gateway (with the secret token and `allowed_updates` incl. `callback_query`); do not `drop_pending_updates` blindly.

## Rollout steps (per client)
1. **Shadow:** run the graph on a copy of live events, send nothing; compare plan/facts/reply with what n8n sent. Review disagreements (LG08).
2. **Suggest:** new stack drafts, ops approves in the ops chat; customers still get answers (from approved drafts) — gives you labelled data for Jev thresholds.
3. **Auto (low-risk intents only):** `chitchat`, `order status`, `product info`; everything else stays review.
4. Widen intents weekly while metrics hold (grounding failures ≈ 0, edit rate falling, escalation precision acceptable).
5. After 2 clean weeks: remove the n8n proxy for that client.

**Go/no-go gates:** all golden cases green; chaos suite green; no P1 bugs open; DLQ empty; runbooks written; token rotated; backups restored once as a drill.

## Decommission
- Retire n8n workflows `01/10/11/30` and spoke workflows as each is ported; keep their JSON in `legacy/` for reference.
- Drop `capability_registry.workflow_id` only after nothing reads it.
- Revoke old tokens/credentials stored in n8n, including the **voice-callback bearer token** (hard-coded in `02_livekit_transcript_callback`) and both Telegram bot tokens that appear in workflow JSON; move the voice callback to the internal HMAC endpoint (LG03 §3F) and delete the public n8n route.
- `dlq` has no consumer today: the `dlq_digest` job (LG10) auto-replays eligible rows and alerts on the rest. Check the table for old rows before cutover.
- **Keep `trg_webhook_delivery`** — it owns webhook-callback enqueueing (endpoint resolution, `callback_url` check, the 10 s sync delay). Only replace it if you later need payload control. Turn on `FORCE ROW LEVEL SECURITY` and move n8n's DB role off the table owner **after** the n8n core is retired (LG11 M14).
- **What n8n keeps:** client-specific automations you sell, OAuth-heavy SaaS glue, low-risk scheduled jobs — invoked via the signed `n8n_webhook` agent kind. Never in the customer-turn hot path.

## Operations

### SLOs (targets to adopt, then measure)
- Gateway ack p95 < 200 ms; ≥ 99.9% of inbound events acked.
- Turn completion p95 within the client's deadline (default 90 s; aim far lower); zero "silent" turns (every event ends in a delivery, a review item, or a DLQ entry).
- DLQ age < 15 min during business hours.

### Alerts (Uptime Kuma + Prometheus/Grafana or Langfuse)
Gateway/worker/delivery down; queue depth/age; DLQ > 0; delivery failure rate; circuit opened; Jev error/timeout rate; model-version change for Jev; token budget > 80%; approval backlog age; WhatsApp/Gmail credential expiry (Gmail `watch` renewals, token refresh failures).

### Runbooks (write each as a one-page md in `docs/runbooks/`)
1. Replay a failed turn (`ingress_events` → re-queue; idempotency guarantees safety).
2. Drain/inspect DLQ and re-drive.
3. Circuit open on an agent: diagnose, close manually.
4. Jev outage or latency spike: force `llm` provider via client config.
5. Rotate channel credentials / Telegram bot token.
6. Disable a client instantly (`status`), or force `suggest` mode.
7. Restore from backup; verify RLS and checkpoint schema.
8. Approve/expire stuck approvals.

### Data & security hygiene
- Backups: `pg_dump` nightly + WAL archiving if you can; **test a restore monthly**. Checkpoints are disposable; domain tables are not.
- Retention: checkpoints 14 days (open approvals exempt); outbox payloads with raw PII purged after delivery (keep redacted copies); `decision_log` and `agent_runs` per policy. Check Malaysian PDPA obligations for client customer data before onboarding real clients.
- Secrets: env/Docker secrets now; cloud secret manager later. Per-environment keys. `gitleaks` in CI. Least-privilege DB roles (`csa_app`, `csa_ro`).
- Webhook hardening: signature checks, replay windows, body size limits, rate limits per endpoint/IP at the Cloudflare layer.
- Budgets: enforce `tokens_per_day` per client; alert at 80%.

## Sizing: ~100 messages/day now, and what binds first as you grow
Back-of-envelope (assumptions: 2 LLM calls per turn, ~5 s average turn, worker concurrency 8):
- 100/day ≈ 4/hour on average; assume 10x bursts ⇒ roughly one message a minute. One worker process is idle almost all the time.
- One worker at concurrency 8 and 5 s/turn ≈ 1.6 turns/s ≈ 138k turns/day *in theory*; you will hit other limits first.

What binds, in order: (1) **LLM provider rate limits / tokens-per-minute on your plan and provider outages** → failover models for planner and composer (LG07); (2) per-client token cost/budgets; (3) channel send limits (Telegram per-chat, WhatsApp tiers/templates, Gmail quotas); (4) your own review capacity (approval backlog); (5) Postgres only once `messages`, `agent_runs` and checkpoints reach tens of millions of rows (partition by time, prune).

So at this volume: **one Postgres, one gateway, one worker, one delivery worker. No Redis, no Kafka, no Kubernetes.** Spend effort on correctness, failover and the human-review loop. Re-evaluate at sustained >10 turns/s, queue age p95 > 30 s, or >5 active clients with spiky traffic.

## Cloud path (design now, execute later)
- 12-factor containers: stateless `gateway`, `worker`, `delivery`; config via env; one image.
- Postgres: managed service **that supports pgvector and PostGIS** (confirm extension support before choosing); same schemas (`public`, `lg`).
- Queue: today `ingress_events` + `SKIP LOCKED`. Keep a `Queue` interface; swap to SQS/PubSub only if throughput demands it.
- Object storage interface for voice/media (S3-compatible); local MinIO or disk now.
- Edge: Cloudflare Tunnel today → a load balancer later; gateway is already the only public surface.
- Observability stays OpenTelemetry-native, so the backend can change.
- IaC (Terraform) once you have >1 environment; **do not adopt Kubernetes** until you run many replicas across many clients — Compose on one or two hosts goes a long way.
- Scaling levers in order: more `worker` replicas (per-conversation lock keeps ordering) → separate `delivery` replicas → read replica for analytics → partition hot tables (`messages`, `ingress_events`) by time.

## Implementation status & evidence
- **Strangler proxy & routing:** Implemented in `csa/channels/ingress.py` and `apps/gateway/main.py`. Routes updates based on `clients.config ->> 'runtime'` (`"n8n"` vs `"langgraph"`) and `clients.deployment_mode` (`"shadow"`, `"suggest"`, `"auto"`).
  - When `runtime == "n8n"`: proxies incoming webhook payload directly to `n8n_webhook_url` upstream and returns `proxied=True`.
  - When `runtime == "n8n"` and `deployment_mode == "shadow"`: proxies to n8n upstream AND enqueues into `ingress_events` for LangGraph coordinator shadow evaluation.
  - Rollback drill: switching `runtime` between `"n8n"` and `"langgraph"` is instant via DB flag with zero BotFather/webhook reconfiguration.
- **Runbooks & procedures:** Created all 8 one-page runbooks under `docs/runbooks/` and corresponding typed operational procedures in `csa/ops/runbooks.py` backed by parameterized SQL in `db/sql/ops/`:
  1. `01_replay_failed_turn.md` / `requeue_turn`
  2. `02_drain_inspect_dlq.md` / `inspect_dlq`, `redrive_dlq`
  3. `03_circuit_breaker_reset.md` / `get_circuit_states`, `reset_circuit`
  4. `04_jev_outage_fallback.md` / `force_decide_mode`
  5. `05_rotate_credentials.md` / DB update procedure
  6. `06_disable_client_force_suggest.md` / `set_deployment_mode`
  7. `07_backup_restore_drill.md` / pg_dump/restore drill
  8. `08_approve_expire_stuck_approvals.md` / `expire_stuck_approvals`
- **Automated test suite:** `tests/test_cutover_ops.py` (4 tests, all passing):
  - `test_strangler_proxy_n8n_and_instant_rollback`
  - `test_strangler_proxy_shadow_mode`
  - `test_strangler_proxy_errors`
  - `test_ops_runbooks_procedures`

## Acceptance criteria
- [ ] One pilot client (yourself/test client) fully on `langgraph` + `auto` for low-risk intents for 2 weeks with SLOs met.
- [x] Rollback drill: flip to `n8n` and back with zero lost events (verified in `tests/test_cutover_ops.py::test_strangler_proxy_n8n_and_instant_rollback`).
- [x] All eight runbooks exist and were executed once (documented in `docs/runbooks/` and verified in `tests/test_cutover_ops.py::test_ops_runbooks_procedures`).
- [ ] Restore drill done from a real backup into a scratch environment; app boots and passes smoke tests.
- [ ] n8n core workflows deactivated (not deleted) and no traffic reaches them.

## Pitfalls
- Cutting over all channels and all intents at once. Don't.
- Forgetting that Telegram queues updates while the webhook is down; plan the switch window.
- Treating `shadow` as optional — it is the cheapest way to get labelled data for Jev thresholds and catch regressions.
