-- What the reconcile job should bring in line for one company: each connected person's bookings
-- (confirmed and starting within 60 days, or cancelled or never confirmed with an event still out
-- there) where no ledger row exists, or it is pending, or it was synced or deleted at an older
-- version of the booking, or it failed fewer than five times at this version. A row the person
-- dropped by disconnecting matches none of these, so it is left alone until they connect again.
SELECT b.id AS booking_id, c.person_id
FROM public.booking_calendar_connections c
JOIN public.booking_people p ON p.client_id = c.client_id AND p.id = c.person_id
                             AND p.user_id = c.user_id AND p.is_active
JOIN public.booking_attendees a ON a.client_id = c.client_id AND a.person_id = c.person_id
JOIN public.bookings b ON b.client_id = a.client_id AND b.id = a.booking_id
LEFT JOIN public.booking_calendar_events e
       ON e.client_id = b.client_id AND e.booking_id = b.id AND e.person_id = c.person_id
WHERE c.client_id = %(client_id)s AND c.status = 'connected'
  AND public.booking_member_active(c.client_id, c.user_id)
  AND (
        (b.status = 'confirmed'
         AND b.ends_at > now() AND b.starts_at < now() + interval '60 days')
     OR (b.status IN ('cancelled', 'pending') AND e.google_event_id IS NOT NULL
         AND e.status IN ('pending', 'synced', 'failed'))
  )
  AND (
        e.booking_id IS NULL
     OR e.status = 'pending'
     OR (e.status IN ('synced', 'deleted') AND e.synced_version < b.version)
     OR (e.status = 'failed' AND (e.failed_version IS DISTINCT FROM b.version OR e.attempts < 5))
  )
ORDER BY b.starts_at, b.id, c.person_id
LIMIT %(batch_size)s;
