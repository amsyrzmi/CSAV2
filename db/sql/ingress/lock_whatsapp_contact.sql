SELECT pg_advisory_xact_lock(hashtext(%(client_id)s::text || ':' || %(wa_id)s));
