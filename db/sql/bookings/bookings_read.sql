-- Bookings for the calendar (not cancelled, ending after %(from)s and starting before %(to)s),
-- or the one booking named by %(id)s whatever its status. Callers redact in service.to_out.
SELECT b.id, b.status, b.kind, b.type_id, coalesce(b.title, b.service) AS title, b.private,
       b.starts_at, b.ends_at, b.version, b.created_by, b.location_kind, b.address,
       b.meeting_link, b.contact_id,
       c.full_name AS customer_name, c.phone AS customer_phone, c.email AS customer_email
FROM public.bookings b
LEFT JOIN public.contacts c ON c.client_id = b.client_id AND c.id = b.contact_id
WHERE b.client_id = %(client_id)s
  AND (%(id)s::uuid IS NULL OR b.id = %(id)s::uuid)
  AND (%(id)s::uuid IS NOT NULL OR b.status <> 'cancelled')
  AND (%(from)s::timestamptz IS NULL OR b.ends_at > %(from)s::timestamptz)
  AND (%(to)s::timestamptz IS NULL OR b.starts_at < %(to)s::timestamptz)
  AND (%(person)s::uuid IS NULL OR EXISTS (
        SELECT 1 FROM public.booking_holds h
        WHERE h.client_id = b.client_id AND h.booking_id = b.id
          AND h.subject_kind = 'person' AND h.subject_id = %(person)s::uuid))
  AND (%(resource)s::uuid IS NULL OR EXISTS (
        SELECT 1 FROM public.booking_holds h
        WHERE h.client_id = b.client_id AND h.booking_id = b.id
          AND h.subject_kind = 'resource' AND h.subject_id = %(resource)s::uuid))
ORDER BY b.starts_at, b.id
LIMIT 1000;
