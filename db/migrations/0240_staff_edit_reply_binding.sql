-- LG07: bind an armed Edit to the alert message whose Edit button was tapped, so a staff group
-- chat can supply replacement text by replying to that alert.

ALTER TABLE public.staff_actions ADD COLUMN IF NOT EXISTS source_message_id bigint;
