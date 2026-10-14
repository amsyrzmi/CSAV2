INSERT INTO public.approval_audit (client_id, approval_id, action, actor, old_values, new_values)
VALUES (%s, %s, %s, %s, %s, %s);
