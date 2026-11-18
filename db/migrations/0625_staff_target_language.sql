-- Customer Inbox refinement, part 2a: the staff chat speaks the target's language and hears about
--   overdue Inbox work (design docs/superpowers/specs/2026-11-16-inbox-refinement-design.md,
--   sections 7.2, 7.3 and 9; plan
--   docs/superpowers/plans/2026-11-18-inbox-refinement-2a-reminders-backend.md, task 4 and
--   decisions 7 and 8).
-- What it adds:
--   staff_notification_targets.language  text NOT NULL DEFAULT 'en', one of en and ms: the language
--              csa.notify.format.format_notification writes the chat message in. The spec lists this
--              column with PR 3's per-person preferences; the formatter changes now, so it is here
--              and PR 3's preferences take 0626.
--   staff_notification_targets.events    'inbox.overdue' is appended to every enabled target that
--              does not list it, so the chats that are connected today hear about a customer who
--              waits. A disabled target is left alone (it opted out of everything), and so is a
--              target that already lists the event. The column DEFAULT is NOT changed: a chat
--              connected after this migration opts in on the PR 3 page, and
--              tests/test_bookings_bot_schema.py pins the default list.
-- Row security: unchanged (the table keeps its tenant_isolation policy and its grants; a column
--   needs neither).
-- Pre-check: staff_notification_targets exists (0160).
-- Safe to run twice: ADD COLUMN IF NOT EXISTS, and the update only touches an enabled target that
--   lacks the event, so a second run changes nothing.
-- Rollback: ALTER TABLE public.staff_notification_targets DROP COLUMN language; then
--   UPDATE public.staff_notification_targets SET events = array_remove(events, 'inbox.overdue');
--   and delete the schema_migrations row for 0625, or the runner thinks it is still applied.

ALTER TABLE public.staff_notification_targets
    ADD COLUMN IF NOT EXISTS language text NOT NULL DEFAULT 'en';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.staff_notification_targets'::regclass
           AND conname = 'staff_notification_targets_language_check'
    ) THEN
        ALTER TABLE public.staff_notification_targets
            ADD CONSTRAINT staff_notification_targets_language_check
            CHECK (language IN ('en', 'ms'));
    END IF;
END $$;

UPDATE public.staff_notification_targets
   SET events = array_append(events, 'inbox.overdue')
 WHERE is_enabled AND NOT 'inbox.overdue' = ANY (events);
