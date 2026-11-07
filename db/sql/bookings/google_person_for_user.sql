-- The active bookable person linked to this account: the only person a member can connect for.
SELECT id
FROM public.booking_people
WHERE client_id = %(client_id)s AND user_id = %(user_id)s AND is_active;
