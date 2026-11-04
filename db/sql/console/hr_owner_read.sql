-- Run inside tx(pool, client_id) after invite_facts_context.sql: the function checks both settings.
SELECT user_id, display_name, usable
FROM public.hr_owner_read(%s, %s);
