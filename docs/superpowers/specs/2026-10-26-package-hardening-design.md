# Package hardening design — 2026-10-26

Short design for a small follow-up to the service packages feature (PR #32). Approved by
the owner on 2026-10-26 through two explicit choices.

## Intent
Close three operational gaps found during the service packages review and rollout.

## Decisions
1. **Inbox stays reachable while Customer Support is suspended.** The Inbox gate accepts the
   `customer_support` package in status `active` or `suspended`, so staff can still see and
   approve held replies and escalations (escalation and order approval never switch off).
   Tenants that never bought the package (no row, or `requested`) still get 403
   `package_inactive`. Knowledge base, Business, Invoices, HR and Email gates are unchanged
   (`active` only).
2. **Staging application containers restart automatically.** `restart: unless-stopped` for
   `gateway`, `worker`, `delivery`, `staging-embeddings`, `frontend` and `staging-db` in the
   staging compose files, so a host reboot or crash no longer leaves the demo down (the
   embedder's runtime-only policy from the 2026-10-24 recovery becomes declarative).
3. **Readable copy for gated pages.** The console's API client maps the server detail
   `package_inactive` to a sentence telling the user the area is not in their organisation's
   active service packages and to ask an administrator. Server responses are unchanged.

## Approaches considered
- Gate statuses as a parameter on `package_gate`/`require_package` (chosen) versus a
  separate "inbox gate" function (duplication) versus dropping the Inbox gate entirely
  (would expose the Inbox to tenants that never had Customer Support).
- Mapping the copy in the shared API client's error decoding (chosen, one place) versus
  per-workspace mappings (five files).

## Non-goals
Pausing the channel on suspension; changing policy mode; any new routes or tables.

## Tests
Suspended package → Inbox 200 / KB 403 over HTTP; rendered compose config carries the
restart policy for the six services; API client maps the code and the browser acceptance
asserts the sentence on a gated page.
