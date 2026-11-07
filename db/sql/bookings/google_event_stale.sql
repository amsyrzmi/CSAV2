-- An answer was refused because a newer version is recorded, yet our write may have reached Google
-- after the newer one: the row is pushed again at the booking's current version.
UPDATE public.booking_calendar_events
   SET status = 'pending', updated_at = now()
 WHERE client_id = %(client_id)s AND booking_id = %(booking_id)s AND person_id = %(person_id)s
   AND status <> 'dropped' AND synced_version > %(version)s;
