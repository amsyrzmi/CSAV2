-- One of the customer's own bookings, whatever its status, by the same rules as the list: never a
-- private meeting, and never a pending booking a member of staff made.
SELECT id, status
FROM public.bookings
WHERE client_id = %(client_id)s AND contact_id = %(contact_id)s AND id = %(id)s
  AND NOT private
  AND NOT (status = 'pending' AND source <> 'bot');
