-- One link, locked for an unlink. window_closed: older than the 30 days an unlink is allowed.
SELECT id, survivor_id, loser_id, moved, unlinked_at,
       created_at < now() - interval '30 days' AS window_closed
FROM public.contact_links
WHERE client_id = %(client_id)s AND id = %(link_id)s
FOR UPDATE;
