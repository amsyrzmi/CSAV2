-- Links for staff, newest first: all of them, those of one contact (as survivor or loser), or one.
-- Only ids, names, phones and channel names leave this statement: never `moved` itself.
-- channels: the contact's identity channels now; for the loser of a link not yet undone also the
-- channels of the identities that link moved to the survivor (each contact_identities key in
-- `moved` is [client_id, channel, external_id], so element 1 is the channel).
-- can_unlink mirrors unlink_contacts: not undone, within 30 days, and no later active link on
-- either contact (link_later_active.sql; a survivor linked into another is such a link).
SELECT l.id, l.reason, l.created_at, l.unlinked_at,
       (l.unlinked_at IS NULL
        AND l.created_at >= now() - interval '30 days'
        AND NOT EXISTS (
            SELECT 1 FROM public.contact_links later
             WHERE later.client_id = l.client_id
               AND later.id <> l.id
               AND later.created_at > l.created_at
               AND later.unlinked_at IS NULL
               AND (later.survivor_id IN (l.survivor_id, l.loser_id)
                    OR later.loser_id IN (l.survivor_id, l.loser_id)))) AS can_unlink,
       s.id AS survivor_id, s.full_name AS survivor_full_name, s.phone AS survivor_phone,
       ARRAY(SELECT DISTINCT ci.channel FROM public.contact_identities ci
              WHERE ci.client_id = l.client_id AND ci.contact_id = s.id
              ORDER BY ci.channel) AS survivor_channels,
       lo.id AS loser_id, lo.full_name AS loser_full_name, lo.phone AS loser_phone,
       ARRAY(SELECT c.channel FROM (
                 SELECT ci.channel FROM public.contact_identities ci
                  WHERE ci.client_id = l.client_id AND ci.contact_id = lo.id
                 UNION
                 SELECT m.key ->> 1
                   FROM jsonb_array_elements(
                            CASE WHEN l.unlinked_at IS NULL
                                 THEN coalesce(l.moved -> 'contact_identities', '[]'::jsonb)
                                 ELSE '[]'::jsonb END) AS m(key)
             ) c ORDER BY c.channel) AS loser_channels
FROM public.contact_links l
JOIN public.contacts s ON s.client_id = l.client_id AND s.id = l.survivor_id
JOIN public.contacts lo ON lo.client_id = l.client_id AND lo.id = l.loser_id
WHERE l.client_id = %(client_id)s
  AND (%(link_id)s::uuid IS NULL OR l.id = %(link_id)s::uuid)
  AND (%(contact_id)s::uuid IS NULL
       OR l.survivor_id = %(contact_id)s::uuid OR l.loser_id = %(contact_id)s::uuid)
ORDER BY l.created_at DESC, l.id DESC
LIMIT %(limit)s;
