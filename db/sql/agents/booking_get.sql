-- One of the customer's own bookings, whatever its status, by the same rules as the list: never a
-- private meeting, never a pending booking a member of staff made, and never one of staff's that
-- was cancelled or declined while still pending (the customer was never told of it).
SELECT id, status, ends_at
FROM public.bookings
WHERE client_id = %(client_id)s AND contact_id = %(contact_id)s AND id = %(id)s
  AND NOT private
  AND NOT (status = 'pending' AND source <> 'bot')
  AND NOT (source <> 'bot' AND cancel_reason IS NOT DISTINCT FROM 'never_confirmed');
