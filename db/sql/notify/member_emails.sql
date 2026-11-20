-- The sign-in email of one enabled member of the transaction's company (0626). No row means the
-- person left, was disabled or is a platform admin. Parameters: client_id, user_id.
SELECT user_id, email FROM public.console_member_emails(%s, ARRAY[%s]::uuid[]);
