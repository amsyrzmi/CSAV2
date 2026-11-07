-- A new Bookings calendar for a person whose old one the person deleted in Google. Only if the
-- calendar stored is still the one that was found missing: another sync may have made one first.
UPDATE public.booking_calendar_connections
   SET calendar_id = %(calendar_id)s, updated_at = now()
 WHERE client_id = %(client_id)s AND id = %(id)s AND status = 'connected'
   AND calendar_id IS NOT DISTINCT FROM %(old_calendar_id)s
RETURNING id;
