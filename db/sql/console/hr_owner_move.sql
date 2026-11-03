-- Run inside tx(pool, client_id) after invite_facts_context.sql: the function checks both settings.
SELECT old_owner, new_owner, duplicate
FROM public.hr_owner_move(%s, %s, %s, %s, %s, %s);
