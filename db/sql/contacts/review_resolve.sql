-- Resolves the open review of a pair of contacts, whichever order it names them in.
UPDATE public.contact_link_reviews
   SET status = 'resolved', resolution = %(resolution)s, resolved_by = %(resolved_by)s,
       resolved_at = now()
 WHERE client_id = %(client_id)s
   AND status = 'open'
   AND least(contact_a, contact_b) = least(%(contact_a)s::uuid, %(contact_b)s::uuid)
   AND greatest(contact_a, contact_b) = greatest(%(contact_a)s::uuid, %(contact_b)s::uuid);
