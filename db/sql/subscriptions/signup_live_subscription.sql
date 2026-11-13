-- Whether the contact already holds a live (active, paused or pending) subscription on the plan.
SELECT EXISTS (
    SELECT 1 FROM public.subscriptions
    WHERE client_id = %(client_id)s AND contact_id = %(contact_id)s AND plan = %(plan_code)s
      AND status IN ('active', 'paused', 'pending')
) AS live;
