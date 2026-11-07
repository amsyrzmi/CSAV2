-- The person disconnected: nothing more is pushed for them until they connect again.
UPDATE public.booking_calendar_events
   SET status = 'dropped', updated_at = now()
 WHERE client_id = %(client_id)s AND person_id = %(person_id)s
   AND status NOT IN ('deleted', 'dropped');
