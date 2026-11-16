-- Customer Inbox refinement, part 1a: the queue state on public.conversations (design
--   docs/superpowers/specs/2026-11-16-inbox-refinement-design.md, sections 5.1, 5.2 and 6.5).
-- What it adds:
--   queue_status      text NOT NULL DEFAULT 'bot': bot, needs_staff, waiting_customer, snoozed, resolved.
--   queue_reason      why the item needs a person: escalation, held_reply, customer_message, manual.
--   topic             purchase, question, complaint, payment, booking, subscription, other (backfill
--                     leaves it NULL; the code that enters the queue sets it).
--   priority          text NOT NULL DEFAULT 'normal': low, normal, high, urgent.
--   assignee_user_id  the console user holding the item (foreign key to console_users), claimed_at.
--   needs_staff_since, due_at, snoozed_until, resolved_at, last_customer_at, last_staff_reply_at.
--   last_activity_at  timestamptz NOT NULL DEFAULT now(): last message time, else opened_at.
--   One check per enum column and conversations_needs_staff_complete_ck: a needs_staff row always has
--   queue_reason, needs_staff_since and due_at. Every constraint is added NOT VALID, then validated
--   after the backfill.
--   Indexes (design 6.5): last activity; due time, since time and the two staff-clock lists as partial
--   indexes (so the bot and resolved rows, the large majority, never enter them); the snooze and
--   waiting-on-customer lists; GIN trigram indexes on the contact name, @username and phone digits
--   (pg_trgm is installed by the baseline; the btree on phone_digits from 0581 does not serve a
--   contains match).
--   public.inbox_queue_backfill(p_client uuid DEFAULT NULL) RETURNS integer: sets the new columns from
--   the existing messages, escalations and approvals for conversations still in the default queue
--   state (queue_status 'bot', no reason, no claim) and returns how many it changed. Called once
--   below for every company; safe to call again (it only changes a row whose values differ, and a row
--   that moved out of 'bot' is not looked at). It never touches conversations of other channels
--   than telegram and whatsapp (the sandbox) or console chat conversations, as the Inbox list
--   (db/sql/inbox/list_conversations.sql) excludes them: those keep queue_status 'bot' for good.
-- Backfill rules (one CASE each below):
--   needs_staff when, within the last 30 days, the conversation has an open escalation (reason
--   'escalation', since = the oldest such escalation's created_at, priority = the highest of its
--   open escalations), else a live pending approval (reason 'held_reply', since = its created_at),
--   else a human-owned conversation whose newest message is inbound (reason 'customer_message',
--   since = that message's time). Reason precedence: escalation, held_reply, customer_message.
--   waiting_customer when a person owns the conversation and the newest message is a person's reply.
--   Everything else, including an escalation older than 30 days, stays 'bot'.
--   due_at = needs_staff_since + 240 minutes for every priority. This is a placeholder: the company
--   response targets (inbox_settings, 0621) do not exist yet and 240 minutes is the 'normal' default.
--   The rollout job of the reminder work gives these items a fresh clock.
-- last_staff_reply_at is the newest outbound message by a person (author 'human_support') or an
--   approved or edited held reply (send_authority 'reviewed'); last_customer_at the newest message by
--   the contact.
-- Backfill timing: it reads three tables and rewrites the conversations still in the default state,
--   once, at migration time. The ADD COLUMN with now() as default is a fast default (no table
--   rewrite). Indexes are built without CONCURRENTLY, as the migration rules require.
-- Safe to run twice: ADD COLUMN IF NOT EXISTS, constraints added only when missing, CREATE INDEX IF
--   NOT EXISTS, CREATE OR REPLACE FUNCTION, and the backfill finds nothing left to change.
-- Rollback: DROP FUNCTION public.inbox_queue_backfill(uuid); drop the new indexes by name
--   (conversations_activity_idx, conversations_queue_due_idx, conversations_queue_mine_idx,
--   conversations_queue_since_idx, conversations_queue_snoozed_idx, conversations_queue_waiting_idx,
--   contacts_full_name_trgm_idx, contacts_telegram_username_trgm_idx, contacts_phone_digits_trgm_idx);
--   ALTER TABLE public.conversations DROP COLUMN for each column above (the constraints go with
--   them); then delete the schema_migrations row for 0620, or the runner thinks it is still applied.

ALTER TABLE public.conversations
    ADD COLUMN IF NOT EXISTS queue_status text NOT NULL DEFAULT 'bot',
    ADD COLUMN IF NOT EXISTS queue_reason text,
    ADD COLUMN IF NOT EXISTS topic text,
    ADD COLUMN IF NOT EXISTS priority text NOT NULL DEFAULT 'normal',
    ADD COLUMN IF NOT EXISTS assignee_user_id uuid,
    ADD COLUMN IF NOT EXISTS claimed_at timestamptz,
    ADD COLUMN IF NOT EXISTS needs_staff_since timestamptz,
    ADD COLUMN IF NOT EXISTS due_at timestamptz,
    ADD COLUMN IF NOT EXISTS snoozed_until timestamptz,
    ADD COLUMN IF NOT EXISTS resolved_at timestamptz,
    ADD COLUMN IF NOT EXISTS last_activity_at timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS last_customer_at timestamptz,
    ADD COLUMN IF NOT EXISTS last_staff_reply_at timestamptz;

DO $$
DECLARE
  c record;
BEGIN
  FOR c IN
    SELECT * FROM (VALUES
      ('conversations_queue_status_check',
       'CHECK (queue_status IN (''bot'', ''needs_staff'', ''waiting_customer'', ''snoozed'', ''resolved''))'),
      ('conversations_queue_reason_check',
       'CHECK (queue_reason IN (''escalation'', ''held_reply'', ''customer_message'', ''manual''))'),
      ('conversations_topic_check',
       'CHECK (topic IN (''purchase'', ''question'', ''complaint'', ''payment'', ''booking'','
       ' ''subscription'', ''other''))'),
      ('conversations_priority_check',
       'CHECK (priority IN (''low'', ''normal'', ''high'', ''urgent''))'),
      ('conversations_needs_staff_complete_ck',
       'CHECK (queue_status <> ''needs_staff'''
       ' OR (queue_reason IS NOT NULL AND needs_staff_since IS NOT NULL AND due_at IS NOT NULL))'),
      ('conversations_assignee_user_id_fkey',
       'FOREIGN KEY (assignee_user_id) REFERENCES public.console_users (id)')
    ) AS v(name, def)
  LOOP
    IF NOT EXISTS (
      SELECT 1 FROM pg_catalog.pg_constraint
       WHERE conrelid = 'public.conversations'::regclass AND conname = c.name
    ) THEN
      EXECUTE format('ALTER TABLE public.conversations ADD CONSTRAINT %I %s NOT VALID', c.name, c.def);
    END IF;
  END LOOP;
END $$;

CREATE OR REPLACE FUNCTION public.inbox_queue_backfill(p_client uuid DEFAULT NULL)
RETURNS integer
LANGUAGE plpgsql
AS $$
DECLARE
  changed integer;
BEGIN
  WITH facts AS (
    SELECT c.id,
           c.opened_at,
           (c.owned_by = 'human_support') AS human_owned,
           (c.channel IN ('telegram', 'whatsapp')
             AND NOT EXISTS (SELECT 1 FROM public.console_chat_threads t WHERE t.id = c.id)
             AND NOT EXISTS (SELECT 1 FROM public.console_chat_participants p
                              WHERE p.contact_id = c.contact_id)) AS in_queue_scope,
           lm.created_at AS newest_at,
           (lm.direction = 'inbound') AS newest_is_inbound,
           (lm.direction = 'outbound'
             AND (lm.author = 'human_support' OR lm.send_authority = 'reviewed')) AS newest_is_staff,
           lc.at AS last_customer_at,
           ls.at AS last_staff_reply_at,
           esc.since AS escalation_since,
           esc.priority AS escalation_priority,
           ap.since AS approval_since
      FROM public.conversations c
      LEFT JOIN LATERAL (
        SELECT m.created_at, m.direction, m.author, m.send_authority
          FROM public.messages m
         WHERE m.client_id = c.client_id AND m.conversation_id = c.id
         ORDER BY m.created_at DESC, m.id DESC
         LIMIT 1
      ) lm ON true
      CROSS JOIN LATERAL (
        SELECT max(m.created_at) AS at
          FROM public.messages m
         WHERE m.client_id = c.client_id AND m.conversation_id = c.id
           AND m.direction = 'inbound' AND m.author = 'contact'
      ) lc
      CROSS JOIN LATERAL (
        SELECT max(m.created_at) AS at
          FROM public.messages m
         WHERE m.client_id = c.client_id AND m.conversation_id = c.id
           AND m.direction = 'outbound'
           AND (m.author = 'human_support' OR m.send_authority = 'reviewed')
      ) ls
      CROSS JOIN LATERAL (
        SELECT min(e.created_at) FILTER (WHERE e.created_at >= now() - interval '30 days') AS since,
               (array_agg(e.priority ORDER BY CASE e.priority WHEN 'urgent' THEN 4 WHEN 'high' THEN 3
                                                              WHEN 'normal' THEN 2 ELSE 1 END DESC))[1]
                 AS priority
          FROM public.escalations e
         WHERE e.client_id = c.client_id AND e.conversation_id = c.id AND e.status = 'open'
      ) esc
      CROSS JOIN LATERAL (
        SELECT min(a.created_at) AS since
          FROM public.approvals a
         WHERE a.client_id = c.client_id AND a.conversation_id = c.id
           AND a.status = 'pending' AND a.expires_at > now()
           AND a.created_at >= now() - interval '30 days'
      ) ap
     WHERE c.queue_status = 'bot' AND c.queue_reason IS NULL AND c.claimed_at IS NULL
       AND (p_client IS NULL OR c.client_id = p_client)
  ), decided AS (
    SELECT f.*,
           CASE WHEN f.in_queue_scope AND f.escalation_since IS NOT NULL THEN 'escalation'
                WHEN f.in_queue_scope AND f.approval_since IS NOT NULL THEN 'held_reply'
                WHEN f.in_queue_scope AND f.human_owned AND f.newest_is_inbound
                     AND f.newest_at >= now() - interval '30 days' THEN 'customer_message'
           END AS reason
      FROM facts f
  ), planned AS (
    SELECT d.id,
           coalesce(d.newest_at, d.opened_at) AS last_activity_at,
           d.last_customer_at,
           d.last_staff_reply_at,
           d.reason,
           CASE d.reason WHEN 'escalation' THEN d.escalation_since
                         WHEN 'held_reply' THEN d.approval_since
                         WHEN 'customer_message' THEN d.newest_at
           END AS since,
           CASE WHEN d.reason IS NOT NULL THEN 'needs_staff'
                WHEN d.in_queue_scope AND d.human_owned AND d.newest_is_staff THEN 'waiting_customer'
                ELSE 'bot'
           END AS status,
           CASE WHEN d.reason IS NOT NULL THEN coalesce(d.escalation_priority, 'normal') END
             AS priority
      FROM decided d
  )
  UPDATE public.conversations c
     SET last_activity_at = p.last_activity_at,
         last_customer_at = p.last_customer_at,
         last_staff_reply_at = p.last_staff_reply_at,
         queue_status = p.status,
         queue_reason = p.reason,
         needs_staff_since = p.since,
         -- Placeholder: 240 minutes (the 'normal' target) for every priority; see the header.
         due_at = p.since + interval '240 minutes',
         priority = coalesce(p.priority, c.priority)
    FROM planned p
   WHERE c.id = p.id
     AND (p.status <> 'bot'
          OR c.last_activity_at IS DISTINCT FROM p.last_activity_at
          OR c.last_customer_at IS DISTINCT FROM p.last_customer_at
          OR c.last_staff_reply_at IS DISTINCT FROM p.last_staff_reply_at);
  GET DIAGNOSTICS changed = ROW_COUNT;
  RETURN changed;
END $$;

SELECT public.inbox_queue_backfill();

ALTER TABLE public.conversations VALIDATE CONSTRAINT conversations_queue_status_check;
ALTER TABLE public.conversations VALIDATE CONSTRAINT conversations_queue_reason_check;
ALTER TABLE public.conversations VALIDATE CONSTRAINT conversations_topic_check;
ALTER TABLE public.conversations VALIDATE CONSTRAINT conversations_priority_check;
ALTER TABLE public.conversations VALIDATE CONSTRAINT conversations_needs_staff_complete_ck;
ALTER TABLE public.conversations VALIDATE CONSTRAINT conversations_assignee_user_id_fkey;

CREATE INDEX IF NOT EXISTS conversations_activity_idx
    ON public.conversations (client_id, last_activity_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS conversations_queue_due_idx
    ON public.conversations (client_id, due_at, id)
    WHERE queue_status = 'needs_staff';
CREATE INDEX IF NOT EXISTS conversations_queue_mine_idx
    ON public.conversations (client_id, assignee_user_id, due_at, id)
    WHERE queue_status IN ('needs_staff', 'waiting_customer') AND assignee_user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS conversations_queue_since_idx
    ON public.conversations (client_id, needs_staff_since, id)
    WHERE queue_status = 'needs_staff';
CREATE INDEX IF NOT EXISTS conversations_queue_snoozed_idx
    ON public.conversations (client_id, snoozed_until)
    WHERE queue_status = 'snoozed';
CREATE INDEX IF NOT EXISTS conversations_queue_waiting_idx
    ON public.conversations (client_id, last_staff_reply_at)
    WHERE queue_status = 'waiting_customer';

CREATE INDEX IF NOT EXISTS contacts_full_name_trgm_idx
    ON public.contacts USING gin (full_name public.gin_trgm_ops);
CREATE INDEX IF NOT EXISTS contacts_telegram_username_trgm_idx
    ON public.contacts USING gin (telegram_username public.gin_trgm_ops);
CREATE INDEX IF NOT EXISTS contacts_phone_digits_trgm_idx
    ON public.contacts USING gin (phone_digits public.gin_trgm_ops);
