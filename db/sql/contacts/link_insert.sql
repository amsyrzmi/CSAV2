-- The audit row of a link, with what it moved (ids per table, the contacts' prior columns).
INSERT INTO public.contact_links (client_id, survivor_id, loser_id, reason, moved, created_by)
VALUES (%(client_id)s, %(survivor_id)s, %(loser_id)s, %(reason)s, %(moved)s, %(created_by)s)
RETURNING id;
