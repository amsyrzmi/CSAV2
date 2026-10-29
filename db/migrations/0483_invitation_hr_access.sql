-- Invitation HR access (B4): an invitation records which HR access preset the inviting admin chose,
-- and who chose it, so acceptance can apply it for the new member.
-- Adds two nullable columns to public.console_tokens:
--   hr_preset text: one of staff, manager, hr_team, payroll_preparer, payroll_reviewer (the B2 preset ids).
--   hr_preset_chosen_by uuid: the console user who chose it (an admin, the tenant's HR owner for any
--     preset other than staff).
-- Only invitations may carry a preset, and the preset and its chooser are set together or not at all.
-- No new tables, roles, policies or grants: csa_identity already has SELECT, INSERT and UPDATE on the
-- table, and the identity readiness checks are unchanged. Task 3 of B4 appends the HR functions here.
-- Pre-check: 0270 applied (console_tokens). Safe to run twice: columns use IF NOT EXISTS and the
--   constraints are added only when missing.
-- Rollback: ALTER TABLE public.console_tokens
--   DROP CONSTRAINT console_tokens_hr_preset_pair,
--   DROP CONSTRAINT console_tokens_hr_preset_invite_only,
--   DROP CONSTRAINT console_tokens_hr_preset_known,
--   DROP COLUMN hr_preset_chosen_by, DROP COLUMN hr_preset;

ALTER TABLE public.console_tokens
    ADD COLUMN IF NOT EXISTS hr_preset text,
    ADD COLUMN IF NOT EXISTS hr_preset_chosen_by uuid REFERENCES public.console_users(id);

DO $$
BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='console_tokens_hr_preset_known'
                AND conrelid='public.console_tokens'::regclass) THEN
  ALTER TABLE public.console_tokens ADD CONSTRAINT console_tokens_hr_preset_known
   CHECK (hr_preset IN ('staff','manager','hr_team','payroll_preparer','payroll_reviewer'));
 END IF;
 IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='console_tokens_hr_preset_invite_only'
                AND conrelid='public.console_tokens'::regclass) THEN
  ALTER TABLE public.console_tokens ADD CONSTRAINT console_tokens_hr_preset_invite_only
   CHECK (kind = 'invite' OR (hr_preset IS NULL AND hr_preset_chosen_by IS NULL));
 END IF;
 IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='console_tokens_hr_preset_pair'
                AND conrelid='public.console_tokens'::regclass) THEN
  ALTER TABLE public.console_tokens ADD CONSTRAINT console_tokens_hr_preset_pair
   CHECK ((hr_preset IS NULL) = (hr_preset_chosen_by IS NULL));
 END IF;
END $$;
