SELECT id, service, status, starts_at, ends_at FROM bookings
WHERE client_id = %s AND contact_id = %s
ORDER BY starts_at DESC LIMIT 50;
