-- Locks one plan for a PATCH so the in-use check and the update see the same plan row.
SELECT billing_interval
FROM public.plans
WHERE client_id = %(client_id)s AND code = %(code)s
FOR UPDATE;
