SELECT id, coalesce(service, title) AS service, status, starts_at, ends_at FROM bookings
WHERE client_id = %s AND contact_id = %s AND id = %s;
