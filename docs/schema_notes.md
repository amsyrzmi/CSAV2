# CSA schema notes

Historical LG01 baseline/reuse record. `db/schema_baseline.sql` is not the
complete current schema: derive the shipped version from ordered
`db/migrations/`, runtime readiness, and current tests. See
[current progress](./PROGRESS.md) before reasoning about persistent-dev state.
Current gateway source expects migration **0360**; this statement describes source
expectation, not the schema currently applied to any persistent database.

`db/schema_baseline.sql` is the reviewed, schema-only PostgreSQL dump used for
local development and CI. LG01 reuses the existing domain schema; it does not
recreate domain tables or import production rows.

## Reuse decisions

Every public base table in the baseline has an explicit LG01 decision:

| Table | Decision |
|---|---|
| `action_log` | Reuse as the side-effect ledger. |
| `agent_runs` | Reuse for agent-run records. |
| `approval_audit` | Reuse for approval audit history. |
| `approvals` | Reuse; `trace_id` is also the LangGraph thread id and must be unique. |
| `bookings` | Reuse; unchanged in LG01. |
| `branches` | Reuse; unchanged in LG01. |
| `capability_registry` | Reuse; LG02 adds its planned registry columns. |
| `channel_credentials` | Reuse; encrypt credential values at the application layer. |
| `clients` | Reuse as the tenant root; `id` is the tenant key. |
| `contact_identities` | Adopt for identity mapping across every channel. |
| `contact_verifications` | Reuse for contact verification (OTP). |
| `contacts` | Reuse for customer/contact records. |
| `conversations` | Reuse for conversation state and ownership. |
| `dlq` | Reuse for dead-letter records and replay tracking. |
| `ingress_dedup` | Keep during coexistence; redundant after cutover and removed only in LG11. |
| `ingress_events` | Reuse as the inbound queue. |
| `invoices` | Reuse; unchanged in LG01. |
| `kb_chunks` | Reuse; unchanged in LG01. |
| `kb_documents` | Reuse; unchanged in LG01. |
| `kb_gaps` | Reuse; unchanged in LG01. |
| `kb_ingest_jobs` | Reuse; unchanged in LG01. |
| `kb_sources` | Reuse; unchanged in LG01. |
| `messages` | Reuse for inbound and outbound message records. |
| `order_confirmations` | Reuse for order confirmation state. |
| `order_events` | Reuse for order history. |
| `order_items` | Reuse; its existing synchronization trigger is part of the contract. |
| `orders` | Reuse; preserve the existing schema and triggers. |
| `outbound_deliveries` | Reuse as the outbox; LG07 adds delivery payload and routing columns. |
| `outbound_delivery_attempts` | Reuse for per-attempt delivery results. |
| `pending_actions` | Reuse for button and confirmation actions. |
| `plans` | Reuse; unchanged in LG01. |
| `price_book_legacy` | Reuse; unchanged in LG01. |
| `product_channel_map` | Reuse; unchanged in LG01. |
| `products` | Reuse for the product catalog and inventory. |
| `quotes` | Reuse; unchanged in LG01. |
| `refunds` | Reuse; unchanged in LG01. |
| `subscription_events` | Reuse; unchanged in LG01. |
| `subscription_requests` | Reuse; unchanged in LG01. |
| `subscriptions` | Reuse; unchanged in LG01. |
| `ticket_events` | Reuse for ticket history. |
| `tickets` | Reuse for support tickets. |
| `voice_transcripts` | Reuse for voice transcript records. |
| `warranty_claims` | Reuse; unchanged in LG01. |
| `webhook_endpoints` | Reuse as the inbound source registry. |

LG01's schema changes are additive runtime plumbing: roles and grants, staged
row-level security (RLS), endpoint resolution, and the LangGraph checkpoint
schema. Existing application triggers and functions remain part of the
database contract. Later-phase changes are owned by their corresponding phase
plans.

## RLS coverage

The migration derives the enabled set from public base tables that have a
`client_id` column, excluding the two structural cases below. The expected set
is:

```text
action_log, agent_runs, approval_audit, approvals, bookings, branches,
capability_registry, channel_credentials, contact_identities,
contact_verifications, contacts, conversations, dlq, ingress_dedup,
ingress_events, invoices, kb_chunks, kb_documents, kb_gaps, kb_ingest_jobs,
kb_sources, messages, order_confirmations, order_events, order_items, orders,
outbound_deliveries, pending_actions, plans, products, price_book_legacy,
product_channel_map, quotes, refunds, subscription_events,
subscription_requests, subscriptions, ticket_events, tickets,
voice_transcripts, warranty_claims, webhook_endpoints
```

Structural exclusions:

- `clients`: tenant root; `id` is the tenant key and there is no `client_id`
  column.
- `outbound_delivery_attempts`: child of `outbound_deliveries`, keyed through
  `delivery_id`; it has no `client_id`, so direct tenant RLS is not possible
  without a join. Access must be constrained through its parent.

The count and actual table list are derived from the local database after
applying `db/schema_baseline.sql` and the migrations; do not record a
production-derived or hard-coded table count:

```sql
SELECT count(*) FROM pg_tables WHERE schemaname = 'public';
SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename;
SELECT table_name
FROM information_schema.columns
WHERE table_schema = 'public' AND column_name = 'client_id'
ORDER BY table_name;
```

RLS is enabled but not forced in LG01 so the existing owner-based n8n runtime
continues to work. The new runtime connects as `csa_app` and is subject to the
tenant policies; production `FORCE ROW LEVEL SECURITY` is deferred until the
LG11/LG09 cutover plan allows it. The two exclusions must remain excluded from
the RLS target set.

## Views

Views do not receive their own table RLS policies. The nine existing public
views are `price_book`, `quote_catalog`, `webhook_credentials`,
`v_booking_schedule`, `v_invoice_status`, `v_order_status`,
`v_product_catalog`, `v_store_unserved_demand`, and
`v_warranty_eligibility`. Where `csa_app` reads a view, the migration sets
`security_invoker=true` so the underlying table policies are checked as
`csa_app`, not bypassed as the view owner. Grants on referenced tables and
functions are required for this invoker path.
