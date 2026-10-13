# LG12 — Development environment: isolated dev stack on the home server (production runs alongside)

## CONTEXT FOR CODING AGENT
Code is written and tested in the dev workspace. The home server (`nexusserver`) runs production: `csa_db`, n8n, TEI, the MCP server, the voice gateway, and (after deploy) the new `gateway` / `worker` / `delivery` containers. **An agent session must never connect to the production database, production bot tokens or the production n8n.** Everything it needs locally comes from a schema-only dump, generated seeds and recorded fixtures.

## 1. Topology
| Where | What runs | Data/secrets |
|---|---|---|
| Dev workspace | repo, isolated Postgres (same image/versions as prod), the three app processes, tests, Claude Code / Copilot | local `.env.local`; a **separate dev Telegram bot**; dev LLM key; generated seed data only |
| GitHub (private repo) | CI: lint, types, tests against a Postgres service container; builds images | CI secrets only (no prod secrets) |
| Home server | production compose (images pulled from the registry), `csa_db`, n8n | production secrets in `.env` / Docker secrets on the server only |
| Access dev → production | read-only inspection | Tailscale/WireGuard or `ssh -L`, with the `csa_ro` role; Postgres stays bound to `127.0.0.1` on the server |

## 2. One-time dev-workspace setup
1. **Match the database image.** On the server: `docker inspect csa_db --format '{{.Config.Image}}'`. Use that exact image in the dev workspace — it must provide PostgreSQL 16, PostGIS 3.4.x, pgvector 0.8.x, `pg_trgm`, `uuid-ossp` (plus the `contrib` modules if you later add `pgcrypto`).
2. **Export the schema (no data)** on the server and copy it over:
   ```
   docker exec csa_db pg_dump -U csa -d csa --schema-only --no-owner --no-privileges > csa_schema.sql
   scp nexusserver:~/csa_schema.sql ./db/schema_baseline.sql
   ```
   Read it once for anything that shouldn't be committed (hard-coded ids in function bodies); schema dumps contain no table data.
3. **`compose.dev.yml`** (dev workspace only): a `db` service using the image from step 1 on port `5433`, a named volume, `POSTGRES_USER=csa`, `POSTGRES_DB=csa`; mount `db/schema_baseline.sql`, then `db/migrations/` (LG11) and `db/seed/` (LG08) in that order via a `make db-reset` target.
4. **`.env.local`** (git-ignored; commit `.env.example`): copy `.env.example`, then fill local DB settings, `GROQ_API_KEY` with a dev key, and `TYPESAFE_API_KEY` only when you have Jev access. Generate a unique `CREDENTIALS_KEY` for this dev workspace. Telegram should use a **separate dev bot** made with BotFather. Optional provider/app configuration is listed in the template; it does not activate an adapter by itself. Per-customer WhatsApp access tokens and Gmail/Calendar refresh tokens belong encrypted in `channel_credentials`, never in `.env.local` or committed files. Keep any Google Pub/Sub push identity separate from mailbox OAuth credentials and outside the repository.
5. **Telegram webhook testing:** create a separate dev bot and put its token and a random webhook secret in `.env.local`. Run `make db-image` (once), `make up`, `make db-reset`, then `make setup-dev-telegram`; this encrypts both values into the dev `channel_credentials` row and prints the endpoint URL without printing the secrets. Expose the dev gateway (`127.0.0.1:$DEV_GATEWAY_PORT`, default 18080) through a **dedicated dev hostname** in Caddy and/or the existing Cloudflare tunnel (never a production hostname), set `PUBLIC_BASE_URL` to it, and re-run the bootstrap. Register the URL and the same secret with Telegram's `setWebhook` (`allowed_updates=["message","edited_message","callback_query"]`) using the **dev bot only**. Run `make run-dev` to receive updates. The dev stack must not join `csa-net` or use production `csa_db`.
6. **Polling:** optional Telegram `getUpdates` polling is implemented for local development. It requires `ENV=dev`, `CHANNEL_MODE=polling`, and the isolated `127.0.0.1:5434/csa` database; it deletes the dev bot webhook without dropping pending updates. Do not run polling and webhook delivery for the same bot concurrently. The currently verified server bot remains in webhook mode.
7. **WhatsApp Cloud API:** create a Meta developer account with MFA, a WhatsApp-use-case Meta app, and use its developer test WABA/phone number plus an allow-listed recipient number that you control. For live customer traffic, separately arrange a verified Business Portfolio/WABA, eligible registered number, business profile/display name, and a customer-authorized system-user token. Configure the app's webhook subscription to the dedicated dev HTTPS hostname. Never reuse production assets or tokens. Meta business verification, number registration, approved templates, app review, or Tech Provider enrollment may require advance lead time; verify current requirements in the Meta dashboard and [official setup guide](https://developers.facebook.com/documentation/business-messaging/whatsapp/get-started).
8. **Gmail and Calendar APIs:** create a Google Cloud project under an account you control; enable Gmail API, Cloud Pub/Sub API, and (for booking) Google Calendar API. Configure OAuth consent, a Web client with the exact dev HTTPS redirect URI, and test-user accounts. Use a throwaway mailbox and a separate synthetic test calendar. Gmail push additionally needs a Pub/Sub topic, push subscription, publisher permission for Google's Gmail push service account, and an authenticated push identity that the gateway validates. Start in OAuth Testing with test users. Gmail's `gmail.readonly` and `gmail.compose` scopes are restricted; `gmail.compose` can send mail even if the product initially only creates drafts, so block send operations in the adapter until an explicit rollout. Restricted scopes can require verification and an independent security assessment for server-side access; check current scope-specific rules before release. Follow the [Gmail push guide](https://developers.google.com/workspace/gmail/api/guides/push), [OAuth restricted-scope guidance](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification), and [Calendar scopes guide](https://developers.google.com/workspace/calendar/api/auth).
**Authenticated Pub/Sub push:** grant the Pub/Sub service agent `service-{PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com` the `roles/iam.serviceAccountTokenCreator` role on the selected push-auth service account. Configure an audience and verify the signed Google OIDC token at the gateway; the project also needs billing enabled for Pub/Sub use.
9. **Fixtures and isolation:** replay recorded payload fixtures (`tests/fixtures/`) through adapters; use provider test assets only for manually triggered dev integration tests. Tests use synthetic data and fixed non-production signature secrets; they must not call production accounts. Do not commit downloaded customer email, calendar events, WhatsApp payloads, OAuth tokens, or service-account keys.
10. **LLMs in the dev workspace:** unit and golden tests run offline with fakes and cassettes (LG06/LG08); a small `--live` suite hits real providers with your dev key.

`make db-reset` is destructive to the isolated development database. The two synthetic sandbox tenant records (`sandbox-amsyar` and `sandbox-ihsan`) are recreated from `db/seed/sandbox_clients.sql`; transient dev-bot credentials, contacts, conversations, and ingress verification rows are intentionally cleared by reset and must be bootstrapped again when needed.

## 3. Production-data policy
- No production dumps *with data* in the dev workspace. Realistic data comes from the generated seeds (LG08). If you need a few real rows, copy **non-personal** tables only (`products`, `capability_registry`, a redacted `clients.config`).
- Your clients' customers' messages are your clients' data. Redact them with the same `pii.py` and check your client agreements and Malaysian PDPA obligations before putting them in any third-party tool, including AI assistants. Prefer synthetic messages for tests and keep real ones for local, offline use.
- Never paste secrets into prompts, issues or workflow exports (scan with the command in LG10 §0).

## 4. Deploy to the home server
1. CI builds one image (`gateway`, `worker` and `delivery` are different commands of the same image) and pushes it to a private registry (GHCR is fine).
2. On the server: `docker compose pull && docker compose up -d`. A one-shot `migrate` service applies `db/migrations/` before the apps start (LG11 rules: additive, idempotent).
3. Rollback = previous image tag; migrations are additive so the old image keeps working (LG11).
4. First deploys run the new stack against the **dev/test bot or a shadow endpoint** on the server, then LG09's per-client rollout.
5. Server-side secrets live only in the server's `.env` / Docker secrets; the dev stack never has them.

## 5. Working with Claude Code, Copilot or any coding agent
- Put the plan in the repo: `docs/langgraph-rebuild/LG00…LG12`. Add an `AGENTS.md` (Claude Code reads `CLAUDE.md`, Copilot reads `.github/copilot-instructions.md`; keep one source of truth and have the others point to it) containing: the stack, `make` targets (`db-reset`, `test`, `lint`, `run-dev`), the "never touch production" rule, code conventions (psycopg3, Pydantic everywhere, SQL in `db/sql/`, only `csa/graph/` imports `langgraph`), and the definition of done below.
- **One phase per session and per branch.** Review every diff; merge only when the acceptance checklist passes.
- Starter prompt (paste at the start of a session):
```text
You are working on the CSA platform rebuild in this repo (Python 3.12, FastAPI, LangGraph, psycopg 3).
Read docs/langgraph-rebuild/LG00_master_plan.md (principles, decisions) and LG12_dev_environment.md (environment rules),
then execute docs/langgraph-rebuild/<PHASE FILE>.

Rules:
- Develop and test only against the local Docker Postgres (compose.dev.yml). Never use production hosts, credentials or tokens.
- Write the tests first, then the code. One phase per branch. Do not start the next phase.
- Anything marked "verify" in the plan: check it against the schema in db/schema_baseline.sql or the installed library version; if the plan is wrong, stop and tell me instead of improvising.
- Keep LangGraph imports inside csa/graph/. Keep SQL in db/sql/. No secrets in code or logs.
Deliver: code + tests, updated docs if behavior changed, a short list of assumptions you could not verify, and the exact commands you ran.
```
- Definition of done for any session: `make lint test` green; `make db-reset` works from scratch; the phase's acceptance checklist is ticked with evidence (test names); no production access was used; `gitleaks` clean.

## 6. Suggested first sessions
1. LG11 migrations M02–M10 against the dev workspace DB (from `schema_baseline.sql`), then on a **restored copy** of production before the real server.
2. LG01 + LG02 (skeleton, contracts), then LG10 (the expiry job) as the first real feature.
3. LG03 Telegram in polling mode with the dev bot, end to end "hello" through the outbox.

## 7. Queries that produce the inputs the plan still needs (run on the server, redact, then share)
```bash
# one client's config and your registry rows — replace anything private (chat ids, emails, phones, URLs with tokens)
docker exec -i csa_db psql -U csa -d csa -x -c "SELECT slug, name, status, tier, region, timezone, languages, core_object, deployment_mode, tokens_per_day, brand_name, brand_tone, jsonb_pretty(config) AS config FROM clients;" > clients_config.txt
docker exec -i csa_db psql -U csa -d csa -x -c "SELECT c.slug, r.name, r.display_name, r.description, r.pattern, r.tier, r.risk_class, r.requires_human, r.is_enabled, r.client_enabled, r.timeout_ms, r.max_retries, r.workflow_id, jsonb_pretty(r.input_schema) AS input_schema FROM capability_registry r JOIN clients c ON c.id=r.client_id ORDER BY c.slug, r.name;" > registry_rows.txt
# sample inbound messages (redacted text only; skip rows where redacted_body is NULL)
docker exec -i csa_db psql -U csa -d csa -c "SELECT m.created_at::date AS day, c.channel, left(m.redacted_body, 300) AS msg FROM messages m JOIN conversations c ON c.id=m.conversation_id WHERE m.direction='inbound' AND m.redacted_body IS NOT NULL ORDER BY random() LIMIT 60;" > sample_messages.txt
```
If you have fewer than ~30 real messages, don't wait: a **synthetic set** (English, Malay, Manglish, Chinese, Tamil, plus injection, angry, empty and voice-failure cases) with an expected outcome per row is better for tests and has no privacy issue. Label the expected outcome yourself; it is the part only you can supply.

## Acceptance criteria
- [ ] `make db-reset && make test` works on a fresh server clone with no network access to the home server.
- [ ] A message sent to the **dev bot** runs through the local stack (polling) and the reply comes back.
- [ ] CI is green on a clean checkout; images build and publish.
- [ ] `docker compose pull && up -d` on the server starts the three services healthy against a copy/test client; production n8n is unaffected.
- [ ] No production secret or token appears anywhere in the repo, CI logs or the plan files.
