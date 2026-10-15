UPDATE console_memberships SET role=coalesce(%s,role), is_enabled=coalesce(%s,is_enabled),updated_at=now()
WHERE user_id=%s AND client_id=%s;
