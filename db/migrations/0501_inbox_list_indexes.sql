-- Indexes for the customer inbox list query (db/sql/inbox/list_conversations.sql).
-- Each row probes subscriptions by (client_id, contact_id) for the "known sender" check and approvals
-- by (client_id, conversation_id) for the pending-approval count. Only idx_subscriptions_contact
-- (contact_id) and idx_approvals_conversation (conversation_id, status) existed, so neither probe
-- could use client_id. orders needs nothing new: orders_recent_idx (client_id, contact_id,
-- placed_at DESC) already serves the (client_id, contact_id) probe through its leading columns.
-- Pre-check: subscriptions and approvals exist (baseline). Safe to run twice: CREATE INDEX IF NOT
--   EXISTS. Not CONCURRENTLY: the migration runner applies each file inside a transaction.
-- Rollback: DROP INDEX public.subscriptions_client_contact_idx;
--           DROP INDEX public.approvals_client_conversation_idx;

CREATE INDEX IF NOT EXISTS subscriptions_client_contact_idx
    ON public.subscriptions (client_id, contact_id);
CREATE INDEX IF NOT EXISTS approvals_client_conversation_idx
    ON public.approvals (client_id, conversation_id);
