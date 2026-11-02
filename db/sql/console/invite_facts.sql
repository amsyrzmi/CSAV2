-- Mail facts and HR-owner status for one invitation. Run inside tx(pool, client_id) after
-- invite_facts_context.sql; hr_owner_self shows the owner row only when app.hr_user_id is the actor.
SELECT c.name AS business_name,
       c.timezone,
       EXISTS (
           SELECT 1 FROM public.hr_client_owners o
           WHERE o.client_id = c.id AND o.user_id = %s
       ) AS may_grant_hr
FROM public.clients c
WHERE c.id = %s;
