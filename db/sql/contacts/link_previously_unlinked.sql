-- Whether the two contacts were ever linked and the link undone, in either direction. verify_phone
-- does not link such a pair again on its own; a staff link of it stays allowed.
SELECT EXISTS (
    SELECT 1
    FROM public.contact_links
    WHERE client_id = %(client_id)s
      AND unlinked_at IS NOT NULL
      AND ((survivor_id = %(contact_a)s AND loser_id = %(contact_b)s)
           OR (survivor_id = %(contact_b)s AND loser_id = %(contact_a)s))
) AS was_unlinked;
