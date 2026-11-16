-- A snoozed item whose time has come needs a person again, with a fresh clock. Re-checked under the
-- row lock, so an item a customer message or a person moved meanwhile is left alone. No
-- inbox_version bump: a system move.
UPDATE public.conversations
SET queue_status = 'needs_staff',
    queue_reason = %s,
    needs_staff_since = now(),
    due_at = now() + make_interval(mins => %s),
    snoozed_until = NULL,
    resolved_at = NULL
WHERE client_id = %s AND id = %s AND queue_status = 'snoozed' AND snoozed_until <= now()
RETURNING id;
