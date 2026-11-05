-- The customer's own live booking of this type at exactly this start, if they already hold one. A
-- second request for it is the same booking, not another (or "taken" by their own booking). A
-- private meeting and a pending request to move another booking are not counted.
SELECT id FROM public.bookings
WHERE client_id = %(client_id)s AND contact_id = %(contact_id)s AND type_id = %(type_id)s
  AND starts_at = %(starts_at)s AND NOT private AND replaces_booking_id IS NULL
  AND status IN ('pending', 'confirmed')
ORDER BY created_at, id
LIMIT 1;
