-- Email management (lane B): sending after confirmation, replies, CC/BCC, "email my team", the
-- attention summary and the 90-day load-more sync.
-- Changes:
--   public.email_drafts: to_addresses (backfilled from recipient), cc, bcc, a 1-20 total address
--     check, status also allows 'sending' and 'sent', sent_at, gmail_message_id,
--     reply_to_message_id (the Gmail API id of the message answered), needs_staff_input,
--     send_claim_id (the idempotency key of the send attempt holding the 'sending' claim; the
--     outcome of an older, released attempt can never change the row).
--     recipient stays as the first address for older readers. to_addresses stays nullable: rows
--     written by code that predates this migration have none until they are saved again.
--   public.email_threads: latest_rfc_message_id, latest_references, latest_reply_to and
--     latest_from_owner, taken from the thread's latest message when it is synced.
--   public.email_mailboxes: sync_page_token (the Gmail pageToken for the next load-more) and
--     last_synced_at.
--   public.console_proposals_kind_check also allows 'email_send' (every earlier kind is kept).
--   public.console_workspace_turns_action_check also allows 'email_reply', 'email_team',
--     'inbox_summary' and 'email_send' (every earlier action is kept).
--   public.email_my_direct_reports(p_client): SECURITY DEFINER, pinned to search_path, EXECUTE to
--     csa_app only. Returns the active employees whose assigned manager is the caller's employee,
--     where the caller comes from app.hr_user_id through hr_employee_accounts. It returns nothing
--     unless the caller is an enabled member of p_client and holds directory.read on
--     assigned_manager (the approves_team switch rows).
-- No new tables, roles or policies, and no grants to csa_identity. RLS on the email tables is
-- unchanged (owner-only on app.hr_user_id); nothing is FORCEd.
-- Pre-check: 0430 (email tables), 0460 (console_proposals, console_workspace_turns), 0480
--   (console_proposals_kind_check) and 0380 (hr_grants, hr_employees, hr_employee_accounts) applied.
--   Safe to run twice: columns use IF NOT EXISTS, the backfill only touches rows still NULL, the
--   constraints are dropped by name and re-added, and the function is CREATE OR REPLACE.
-- Rollback: DROP FUNCTION public.email_my_direct_reports(uuid);
--   ALTER TABLE public.console_workspace_turns: restore console_workspace_turns_action_check
--   without the four new actions once no such rows remain;
--   ALTER TABLE public.console_proposals: restore console_proposals_kind_check without
--   'email_send' once no such rows remain;
--   ALTER TABLE public.email_drafts DROP CONSTRAINT email_drafts_address_count, restore
--   email_drafts_status_check as IN ('draft','exported') once no sending/sent rows remain, then
--   drop the new columns; drop the new columns on public.email_threads and public.email_mailboxes.

DO $$
BEGIN
 IF to_regclass('public.email_drafts') IS NULL OR to_regclass('public.email_threads') IS NULL
    OR to_regclass('public.email_mailboxes') IS NULL THEN
  RAISE EXCEPTION '0484: the email tables from 0430 are missing';
 END IF;
 IF to_regclass('public.hr_grants') IS NULL OR to_regclass('public.hr_employee_accounts') IS NULL THEN
  RAISE EXCEPTION '0484: the HR tables from 0380 are missing';
 END IF;
END $$;

ALTER TABLE public.email_drafts
    ADD COLUMN IF NOT EXISTS to_addresses text[],
    ADD COLUMN IF NOT EXISTS cc text[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS bcc text[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS sent_at timestamptz,
    ADD COLUMN IF NOT EXISTS gmail_message_id text,
    ADD COLUMN IF NOT EXISTS reply_to_message_id text,
    ADD COLUMN IF NOT EXISTS needs_staff_input boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS send_claim_id uuid;

UPDATE public.email_drafts SET to_addresses = ARRAY[recipient] WHERE to_addresses IS NULL;

ALTER TABLE public.email_drafts
    DROP CONSTRAINT IF EXISTS email_drafts_status_check,
    DROP CONSTRAINT IF EXISTS email_drafts_address_count;
ALTER TABLE public.email_drafts
    ADD CONSTRAINT email_drafts_status_check
        CHECK (status IN ('draft','exported','sending','sent')),
    ADD CONSTRAINT email_drafts_address_count
        CHECK (cardinality(to_addresses) + cardinality(cc) + cardinality(bcc) BETWEEN 1 AND 20);

ALTER TABLE public.email_threads
    ADD COLUMN IF NOT EXISTS latest_rfc_message_id text,
    ADD COLUMN IF NOT EXISTS latest_references text,
    ADD COLUMN IF NOT EXISTS latest_reply_to text,
    ADD COLUMN IF NOT EXISTS latest_from_owner boolean;

ALTER TABLE public.email_mailboxes
    ADD COLUMN IF NOT EXISTS sync_page_token text,
    ADD COLUMN IF NOT EXISTS last_synced_at timestamptz;

ALTER TABLE public.console_proposals DROP CONSTRAINT IF EXISTS console_proposals_kind_check;
ALTER TABLE public.console_proposals ADD CONSTRAINT console_proposals_kind_check
    CHECK (kind IN ('email_export', 'training_submit', 'inbox_decision', 'subscription_action',
                    'email_send'));

ALTER TABLE public.console_workspace_turns
    DROP CONSTRAINT IF EXISTS console_workspace_turns_action_check;
ALTER TABLE public.console_workspace_turns
    ADD CONSTRAINT console_workspace_turns_action_check CHECK (action IN (
        'answer','navigate','directory_lookup','email_compose','training_submit',
        'inbox_decision','clarification','page_guidance','local_order_read',
        'email_reply','email_team','inbox_summary','email_send'));

CREATE OR REPLACE FUNCTION public.email_my_direct_reports(p_client uuid) RETURNS TABLE(
    employee_id uuid, display_name text, work_email text)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE
 v_user uuid := nullif(current_setting('app.hr_user_id', true), '')::uuid;
BEGIN
 IF v_user IS NULL OR p_client IS NULL THEN RETURN; END IF;
 RETURN QUERY
 SELECT r.id, r.display_name, r.work_email
 FROM public.console_memberships m
 JOIN public.console_users u ON u.id = m.user_id AND u.is_enabled
 JOIN public.hr_grants g ON g.client_id = m.client_id AND g.user_id = m.user_id
      AND g.action = 'directory.read' AND g.scope = 'assigned_manager'
 JOIN public.hr_employee_accounts a ON a.client_id = m.client_id AND a.user_id = m.user_id
 JOIN public.hr_employees me ON me.client_id = a.client_id AND me.id = a.employee_id AND me.is_active
 JOIN public.hr_employees r ON r.client_id = me.client_id AND r.manager_id = me.id AND r.is_active
 WHERE m.client_id = p_client AND m.user_id = v_user AND m.is_enabled
 ORDER BY lower(r.display_name), r.id;
END $$;

REVOKE ALL ON FUNCTION public.email_my_direct_reports(uuid) FROM PUBLIC,csa_ro,csa_identity;
GRANT EXECUTE ON FUNCTION public.email_my_direct_reports(uuid) TO csa_app;
