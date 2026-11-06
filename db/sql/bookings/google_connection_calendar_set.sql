-- A new Bookings calendar for a person whose old one the person deleted in Google.
UPDATE public.booking_calendar_connections
   SET calendar_id = %(calendar_id)s, updated_at = now()
 WHERE client_id = %(client_id)s AND id = %(id)s AND status = 'connected'
RETURNING id;
