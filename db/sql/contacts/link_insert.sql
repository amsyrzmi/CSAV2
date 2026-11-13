-- The audit row of a link, with what it moved (ids per table, the contacts' prior columns).
-- clock_timestamp(), not now(): two links made in one transaction still have an order, which
-- an unlink needs (link_later_active.sql).
INSERT INTO public.contact_links
    (client_id, survivor_id, loser_id, reason, moved, created_by, created_at)
VALUES (%(client_id)s, %(survivor_id)s, %(loser_id)s, %(reason)s, %(moved)s, %(created_by)s,
        clock_timestamp())
RETURNING id;
