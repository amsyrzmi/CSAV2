# Staging demonstration recovery — 2026-10-24

The owner reported failed synthetic-account login at
`https://staging-console.fourstacks.my/login` and premature Telegram handoff,
then requested an urgent repair before a client presentation.

## Identified causes

All four supplied synthetic accounts existed, were enabled, had enabled tenant
memberships, and did not require password changes. Public HTTPS login returned
401 for each supplied password. Direct verification with the application password
verifier confirmed that all four supplied values differed from their stored
hashes. This identifies a password mismatch; the reason the original values
changed was not established. No passwords or hashes are recorded here.

`csa-staging-staging-embeddings-1` was stopped with exit 137 and
`OOMKilled=true`; Docker recorded its termination at 2026-10-23 23:57:45 UTC
(2026-10-24 07:57:45 Malaysia time). Its restart policy was `no`. Three subsequent
Meranti turns escalated with reason `agent_failure`; their failed KB runs had
`embedding_unavailable` / `upstream`. The escalation agent placed the affected
conversations under human ownership for 24 hours. Worker logs showed 14 muted
routes, explaining subsequent missing responses. No clarification-policy change
was needed to recover this outage.

## Applied recovery

Only isolated staging resources were changed; no production database, n8n,
production bot credential, environment file, or application source was changed.

- Restored the four supplied synthetic passwords using existing identity SQL,
  application scrypt hashing, transaction checks, session/token revocation,
  email-throttle clearance and one `identity.operator_password_recovery` audit
  event per account. Roles and memberships were preserved.
- Started the existing stopped staging embedding container and set its Docker
  restart policy to `unless-stopped`.
- Used the restored support account and the normal HTTPS inbox API to release
  only the three conversations linked to the confirmed embedding failures.
  Each returned HTTP 200, `outcome=applied`, `human_muted=false`; database
  readback confirmed all three were open and AI-owned. An unrelated pre-existing
  human-owned conversation was left alone.

## Evidence

- Public login page: HTTP 200; CSRF acquisition succeeded.
- Actual embedding request: HTTP 200, 384 dimensions; service remained running.
- Four password repairs committed with audit records.
- Public HTTPS login passed for all four supplied accounts with their expected
  support, reader, and tenant-admin roles. Authenticated identity inspection and
  logout were checked for each diagnostic session.
- A new private support sandbox question completed as `available` with one
  nonempty reply. Trace `c3babc6e-3aa4-4990-ae05-5ed69da0cc0c` persisted a successful
  `kb_agent` run, `verified=true`, `gate=send/auto`, and zero escalations. The
  diagnostic thread was archived and the session logged out.
- No Telegram message was sent by the assistant. A fresh owner-sent Telegram
  message remains the direct delivery acceptance check.

This was operational recovery, not an application patch. Full lint/tests/reset
were not run for these runtime changes. The working tree's existing voice work
was preserved. The host had approximately 2.3 GiB available memory and nearly
full 4 GiB swap before restarting the embedder; its measured resident container
usage after recovery was approximately 1.07 GiB. The root cause of host memory
pressure was not established. Automatic restart is a runtime Docker setting;
a future Compose recreation can overwrite it. Durable capacity planning,
restart configuration and embedding-dependency monitoring remain follow-up work.
