# Interim production hosting on Lightsail and S3

Status: approved by the owner on 2026-11-06 (drafted 2026-11-04, updated 2026-11-06 with the owner's answers). The owner is performing the infrastructure steps by hand from numbered commands; the code work (S3 storage interface, production compose overlay) is planned separately. Provisioned so far: S3 bucket, narrow IAM user, Lightsail instance with static IP, Docker, swap, tunnel token, generated secrets.

## Intent and why

Move CSA from the owner's shared home server to AWS so the client's real customers are served from a
host that is not memory-starved (14 GB shared, swap full, disk 96%). The monthly budget is **RM60
(about US$13-14)**, which rules out the planned architecture (EC2, RDS and the rest). This design is
a **temporary first production home** on one Amazon Lightsail instance plus one S3 bucket. When budget
allows, a second migration moves to the planned EC2/RDS architecture. That planned architecture stays
the main target and is not edited, replaced or weakened by this work.

## Goals

- Serve the client's real customers (target around 1,000 messages a day, uneven: some days busy, some quiet) from one Lightsail instance in the Malaysia region (`ap-southeast-5`; availability and price to be confirmed at provisioning). Malaysia is the preferred region. If the 2 GB plan is unavailable there or costs enough to break the budget, Singapore (`ap-southeast-1`) is accepted (owner, 2026-11-06). The S3 bucket goes in the same region as the instance. **Outcome, 2026-11-06:** Malaysia was enabled but the owner's `AccountFullAccessRole` session carries an explicit deny (session policy) for Lightsail there, so the instance and bucket go in Singapore (`ap-southeast-1`). Verified there: bundle `small_3_0`, 2 GB, 2 vCPU, 60 GB, 3 TB transfer, US$12 a month (not the IPv6-only `small_ipv6_3_0`).
- Keep monthly AWS cost inside the RM60 budget.
- Hold customer and HR files in S3, not on the instance disk, so an instance loss does not lose files.
- Back up Postgres nightly to S3 and prove a restore before cutover.
- Carry the existing (small, mostly test) staging data to the new host.
- Make the later move to EC2/RDS a dump-and-restore plus a config change, with no redesign.

## Non-goals and anti-goals

- No change to the EC2/RDS/ECS target architecture or its documents. This is an interim step, not a new target.
- No vectors, queue or checkpoints outside Postgres. pgvector, `SKIP LOCKED`, advisory locks, RLS and the LangGraph checkpointer stay in Postgres.
- No managed database (Lightsail database or RDS) in this phase: cost, and pgvector/PostGIS support on Lightsail's managed database is unconfirmed.
- No Terraform, Kubernetes, autoscaling or multi-host setup.
- Failure to avoid even if it "works": any code or config that ties the app to Lightsail, or that would have to be undone for EC2/RDS.
- Failure to avoid: silent fallbacks. A missing bucket, key or region must stop the process at start, not fall back to local disk (AGENTS.md).

## Constraints

- Budget: about US$13-14 a month for AWS. Lightsail 2 GB Linux plan is US$12 (Malaysia price to confirm), leaving US$1-2 for S3 and nothing for snapshots. The 4 GB plan (US$24) does not fit.
- Groq model calls and Meta WhatsApp charges are billed separately and are assumed outside the RM60 (owner to confirm).
- The repository is public: no secrets, account ids, real addresses or keys in git, docs or logs.
- AGENTS.md conventions: pinned dependency versions with `uv.lock`, psycopg 3 only, Pydantic contracts, SQL under `db/sql/`, additive migrations, surfaced errors.
- Staging at home stays untouched until production passes acceptance.
- Live usage today is about 1.4 GB of RAM across the stack, about 1 GB of it the embedding service. The database is 27 MB.

## Approaches considered

- **A. One 2 GB Lightsail instance, same compose stack, Postgres in a container, S3 for backups only.** No app code. Uploads stay on the instance disk.
- **B (chosen by the owner). A plus live KB and HR uploads in S3.** One code change behind a small storage interface. S3 is also the object store the EC2/RDS design assumes, so the work is not thrown away.
- **C. Vectors in S3 (S3 Vectors, DuckDB, LanceDB).** Rejected: the vector data is tiny so there is no saving, Postgres is still required for the queue, locks, RLS and checkpoints, and S3 Vectors queries run around 500 ms against an 8 s per-step turn budget. It would add a second store and lose tenant isolation.

## Design

### 1. Topology

One Lightsail instance (2 GB, Ubuntu LTS, static IP) running Docker with the same images as staging, pinned by digest:
`staging-db` equivalent (Postgres 16 with PostGIS and pgvector, same image as staging), `gateway`, `worker` (two replicas), `delivery`, `frontend`, the embeddings service, and `cloudflared`. The Cloudflare tunnel stays in front: outbound only, no inbound ports except SSH restricted to the owner, free TLS. The path allow-list that `deploy/staging-hooks.Caddyfile` provides (only `POST /hooks/*` and `/channels/whatsapp` reach the gateway) must exist on the new host in some form (Caddy container or tunnel ingress rules; the builder chooses).

Memory: add a swap file, set Postgres `shared_buffers` and `max_connections` for 2 GB (the gateway budgets eight connections per process), cap container memory so the OOM killer picks a worker, not Postgres.

Config stays environment and Docker-secret driven, so the same images move to EC2/RDS unchanged. Postgres major version stays 16.

### 2. Storage interface (the one code change)

A small storage module with two implementations, local disk (kept for dev and tests) and S3, selected by settings (for example `STORAGE_BACKEND=local|s3`, bucket, region). It replaces direct filesystem use in three places:

- `csa/console/kb_uploads.py`: publish, download, delete, cleanup and reconcile.
- `csa/hr/documents.py`: upload, download, remove, cleanup.
- `csa/agents/kb/ingest_jobs.py` via `read_upload` / `resolve_upload_path`: the worker reads the uploaded file. With S3 the worker no longer needs the gateway's volume.

Rules the interface must keep: keys are tenant-prefixed (`kb/<client_id>/...`, `hr/<client_id>/...`); stored relative paths in the database stay unchanged so existing rows work after files are copied to the same keys; HR and KB stay separated (separate prefixes with separate IAM scope where possible); path-traversal checks stay; downloads stream; deleting a missing object is not an error; a failed or half-finished upload is cleaned up or reconciled as today. S3 puts are atomic, so the temp-file and fsync steps become unnecessary for the S3 path. S3 calls run in a worker thread, matching the existing `asyncio.to_thread` pattern. A new dependency (boto3 or equivalent) is pinned exactly with `uv.lock` updated. Credentials come from a Docker secret file; a missing or wrong bucket stops the process at startup.

Bucket: private, public access blocked, encryption with S3-managed keys (not KMS, which adds cost), TLS-only policy, access key limited to this bucket.

### 3. Backups

`pg_dump` (custom format) **every 6 hours** (owner-approved, 2026-11-06, on the condition that it does not strain the server) plus one dump immediately before every migration, to a `backups/` prefix in S3. The recovery point is therefore up to about 6 hours of data. The load on the server is small by design: the database is tens of megabytes, a dump takes seconds, and `pg_dump` reads a consistent snapshot without blocking writers. It runs at the lowest CPU and disk priority (`nice -n 19`, `ionice -c3`), and the capacity gate measures worker ack latency while a dump runs. A failed or missed backup must alert the owner (the builder picks a free mechanism, such as a dead-man's-switch ping that emails when it stops arriving, and the owner approves it). A lifecycle rule keeps the dumps inside the budget (for example 7 days of 6-hourly dumps plus a few weekly ones; at this size that is well under US$1). Because the instance holds the write key, enable bucket versioning with short noncurrent-version expiry so a compromised instance cannot silently destroy backups. A restore into a scratch environment must pass before cutover (LG09 requires a real restore drill) and be repeated monthly.

### 4. Moving the existing data

One-time carry-over from staging: `pg_dump` of `csa_staging` restored into the new Postgres (roles created by the normal migrate step; schema version must equal `EXPECTED_SCHEMA_VERSION`), and the two upload volumes uploaded to S3 at the same relative keys. The data is small and mostly test data, so the default is to carry everything and let the owner prune in the console. Channel credentials (Telegram, WhatsApp, Gmail tokens) are encrypted with `CREDENTIALS_KEY`. Production gets its own new key (per-environment keys, LG09), so the carried-over encrypted rows are removed before first start and the channel connections are re-made on production: the same Telegram bot token is bound to its tenant again with the existing bot-setup one-shot, and the WhatsApp number's token is entered again in the console. Because no real customer uses the staging channels (owner, 2026-11-06), re-connecting is not customer-visible. The same key also encrypts Gmail mailbox tokens, TikTok shop tokens and the HR personal numbers (IC or passport, bank, tax, EPF, SOCSO); the owner confirmed on 2026-11-06 that all staging data is test data, so those values are cleared during the restore and re-entered in production, rather than reusing staging's key. Gmail connections are re-made by the testers.

### 5. Cutover and rollback

Staging at home keeps running. Steps: provision; deploy the stack; restore and copy data; run smoke and load checks (below); create the production tunnel and hostnames; move the Telegram webhook, WhatsApp callback URL and Google OAuth redirect to the production hostnames; watch. Telegram queues updates while a webhook is down, so the switch is quick and loses nothing. No real customer uses the staging channels, so no customer-facing quiet window is needed; only the testers are told. The same Telegram bot moves to production (owner, 2026-11-06). A bot has one webhook and the bot-setup tool refuses to bind a bot that is already bound to another tenant, so moving the bot ends its use on the home server, and so does moving the WhatsApp callback in Meta (one callback per Meta app). Rollback is therefore repointing the bot's webhook, the WhatsApp callback and the tunnel back to the home server, which stays intact until the owner retires it. The production console hostname is `mito.fourstacks.my` (owner, 2026-11-06); the webhook hostname is `webhooks.fourstacks.my` (owner, 2026-11-06). Both are new names, chosen by the owner at provisioning; the Google OAuth app gets a second redirect added for them.

### 6. Capacity gate (go/no-go before cutover)

All numbers so far come from the repo's own reports on other hardware, not from Lightsail. Before cutover, on the real instance: offer a burst of about 300 messages over 10 minutes with the model stubbed to realistic latency, and a short run against the real model. Pass means: gateway ack p95 under 200 ms during the burst, the queue drains with no lost event, no OOM kill, memory headroom left, and CPU burst credits not exhausted. Existing `tests/test_load_acceptance.py` is the starting harness. If it fails, the fallbacks are tuning (swap, Postgres, worker count), a lighter embedding setup, or a larger plan if the budget changes.

Expected shape: one worker handles about 12 turns a minute (about 5 s a turn), two workers about double; a 300-message burst drains in roughly 12-25 minutes; replies are delayed, never dropped. The first real limits are expected to be Groq rate limits and cost, then RAM, then burstable CPU.

### 7. Operations

Docker restart policies, log rotation, unattended security updates, an external uptime check (not the home server's Uptime Kuma), the LG09 runbooks adapted to the new host, and a credentials-rotation note for the S3 key. No Lightsail snapshots at first (they would break the budget); the S3 dumps are the recovery path.

### 7a. Secrets, domain and settings (added 2026-11-06)

- **Secrets stay as files on the instance**, exactly as on staging: Docker secrets, mode 0400, in a root-only directory, never in git, logs or prompts. AWS Secrets Manager is not used: it costs about US$0.40 per secret per month and staging holds about 20 real secrets, so roughly US$8 a month against a US$12 server. SSM Parameter Store (standard tier) has free storage but is another AWS service, and a Lightsail instance cannot use an IAM role, so it would need an access key stored on the box to protect the other keys. It may be added later as a source of truth; it is not part of this phase.
- **Off-box copy of the secrets.** Losing `CREDENTIALS_KEY` makes every stored channel token unreadable, and the instance disk is not a backup. The owner keeps an encrypted copy of the production secret set outside the instance (password manager or an encrypted file the owner holds), and the runbook says when to refresh it.
- **Which values carry over.** New for production: database passwords and DSNs, `CREDENTIALS_KEY`, and a separate Groq key if the owner wants cost and rate isolation. Carried from staging because the external accounts are shared: Telegram bot token, WhatsApp app secret and verify token (one Meta app, one callback), Google OAuth client id and secret, the SMTP relay credentials. Whether the TikTok and LiveKit secrets are needed in production is for the builder to confirm against what the production release enables.
- **`.env.local` is not copied.** It is the dev file (dev database passwords, dev bot, dev keys) and AGENTS.md forbids using it against production. Staging does not read it either: its secrets are files and its non-secret switches live in a committed file (`deploy/staging-rollout.env`). Production follows the same pattern: a committed file of non-secret switches (no addresses, no secrets) and secret files created on the instance by the owner, entered at a hidden prompt or copied over SSH, never through chat or git.
- **Domain.** `fourstacks.my` stays with Cloudflare for DNS and TLS; the tunnel carries the production hostnames, so no Route 53 zone (about US$0.50 a month) or AWS certificate is needed. Production gets new hostnames chosen by the owner; staging keeps its own.
- **Mail relay.** The Google Workspace SMTP relay only accepts mail from allowed IP addresses, today the home server's. The Lightsail static IP must be added in the Workspace admin console before invitation mail is switched on, or invitations will fail with `smtp_refused`.

### 8. Exit path to EC2/RDS

Same images and environment-driven DSNs. RDS PostgreSQL supports pgvector and PostGIS, so the move is `pg_dump` and restore, point the DSNs at RDS (the `admin-add` one-shot already takes an expected host for this), keep the same S3 bucket, move compose to EC2 and later ECS. The storage interface built here is the object-store interface LG09 already asks for.

## Cost estimate

Lightsail 2 GB about US$12, S3 storage and requests well under US$1 at this size, backups under US$1: about US$13 total against a budget of about US$13-14. This is tight and has no room for snapshots, a larger plan or a managed database. Malaysia-region prices must be checked before provisioning.

## Risks

- 2 GB is tight: embedding service (about 1 GB) plus Postgres plus app. Mitigated by swap, limits, the capacity gate.
- Single instance: an outage means downtime until restore (hours, not minutes).
- Burstable CPU: baseline for this plan unconfirmed.
- Groq limits and cost outside the budget.
- HR files include contracts and ID documents (personal data, Malaysian PDPA): bucket privacy and key handling must be right.
- Instance holds an S3 write key: versioning and a narrow policy limit the damage.

## Decided by the owner

- Production home is this interim Lightsail and S3 setup; EC2/RDS stays the future target and is not to be replaced.
- Approach B (uploads in S3 now).
- Existing data is carried over.
- Budget RM60 a month.
- No real customer uses the staging channels today; the real WhatsApp number and Telegram bot used for testing move to production, and the same Telegram bot is used there (2026-11-06).
- Backups every 6 hours, as long as they do not strain the server (2026-11-06).
- Malaysia region preferred; Singapore accepted if Malaysia does not work (2026-11-06).

## My calls (change any)

Real S3 rather than Lightsail's own bucket product; S3-managed encryption, not KMS; two workers; Postgres stays 16 in a container; no snapshots; carry all staging data; new production `CREDENTIALS_KEY`; Cloudflare tunnel kept; Groq and Meta costs outside the RM60.

## Who does what, deploys, and the capacity-gate fallback (my calls, added after the builder check)

- **Division of work.** The builder writes code, scripts and runbooks. The owner performs every step that needs an account: AWS and Lightsail, IAM, the Cloudflare tunnel and hostnames, Meta (WhatsApp callback), Google Cloud (OAuth redirect) and Telegram webhook moves. Those steps are handed over as numbered, copy-ready commands, as with earlier rollouts.
- **Deploys after cutover.** Adapting `scripts/staging_rollout.py` for the new host is a separate follow-up project, not part of this one. Until then the owner deploys by hand from numbered commands. The 2026-10-30 owner decision stands: a migration stops all four services and the short outage is accepted. Images are linux/amd64, so no architecture change is needed.
- **If the capacity gate fails.** The builder stops and asks the owner, because the remedies cost money or change data. Pre-agreed order to propose: first tune swap, Postgres and worker count; then one worker. A lighter embedding model is not tuning: it changes `KB_EMBED_MODEL_LABEL` and forces a re-embed of the knowledge base, so it needs the owner's approval. The real-model part of the load run is capped at about 50 turns on the owner's Groq key.

## Left to the builder

Exact container memory limits and Postgres settings; swap size; the Caddy-or-tunnel allow-list; S3 library choice and key layout details; lifecycle and versioning values; the load-test script; the cutover runbook wording.
