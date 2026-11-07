-- For an Admin: each active person who has an account (the only ones who can connect) and where
-- their connection stands. Never returns a token.
SELECT p.id AS person_id, p.display_name AS name, coalesce(c.status, 'pending') AS status
FROM public.booking_people p
LEFT JOIN public.booking_calendar_connections c
       ON c.client_id = p.client_id AND c.person_id = p.id AND c.user_id = p.user_id
WHERE p.client_id = %(client_id)s AND p.is_active AND p.user_id IS NOT NULL
ORDER BY p.display_name, p.id;
