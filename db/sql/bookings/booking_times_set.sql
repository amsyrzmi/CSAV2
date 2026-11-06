-- A booking's new start. late_for_reminder is set again with it: the start was set after its
-- reminder was due, so it gets none (see service.late_for_reminder).
UPDATE public.bookings
SET starts_at = %(starts_at)s, ends_at = %(ends_at)s, late_for_reminder = %(late_for_reminder)s,
    version = version + 1
WHERE client_id = %(client_id)s AND id = %(id)s;
