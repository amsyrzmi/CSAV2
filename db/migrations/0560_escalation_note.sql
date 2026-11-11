-- Customer bot business answers: each escalation can carry a staff context note.
--   * escalations.note (text, NULL): one or two sentences for the staff member who takes the chat,
--     saying what the customer wanted and what the assistant could not do. NULL means no note (every
--     row written before this migration, and every handoff that wrote none). The note is for staff
--     only: it is never sent to the customer.
--   * escalations_note_length: char_length(note) <= 500 (characters, not bytes). The assistant writes
--     at most 300; the column limit leaves room.
--   * escalations_conversation_created, on (client_id, conversation_id, created_at DESC): the read
--     that shows the latest escalation note for a chat.
-- How: the column is added IF NOT EXISTS, the constraint is added inside a DO block only when it is
--   missing, and the index is created IF NOT EXISTS, so running the file again changes nothing. No
--   data backfill: existing rows keep a NULL note.
-- No tables, policies, grants or roles change: the tenant_isolation policy and the csa_app and csa_ro
--   grants from 0150 already cover the new column. FORCE RLS stays off.
-- Pre-check: public.escalations exists (0150); the statements fail with an error if it does not.
-- Rollback: DROP INDEX public.escalations_conversation_created; then ALTER TABLE public.escalations
--   DROP CONSTRAINT escalations_note_length; then ALTER TABLE public.escalations DROP COLUMN note
--   (any notes already written are lost); finally delete the schema_migrations row for 0560, or the
--   runner thinks it is still applied.

ALTER TABLE public.escalations ADD COLUMN IF NOT EXISTS note text;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
      FROM pg_catalog.pg_constraint c
     WHERE c.conrelid = 'public.escalations'::regclass
       AND c.conname = 'escalations_note_length'
  ) THEN
    ALTER TABLE public.escalations
      ADD CONSTRAINT escalations_note_length CHECK (note IS NULL OR char_length(note) <= 500);
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS escalations_conversation_created
  ON public.escalations (client_id, conversation_id, created_at DESC);
