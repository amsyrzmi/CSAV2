-- Push a claimed reminder whose kind this process has no handler for to %(due_at)s, with
-- %(last_error)s. It stays scheduled and no attempt is counted: a worker that knows the kind (after
-- a rolling deploy, or one that registered it) fires it then.
UPDATE public.reminders
SET due_at = %(due_at)s,
    last_error = %(last_error)s,
    updated_at = now()
WHERE client_id = %(client_id)s AND id = %(id)s;
