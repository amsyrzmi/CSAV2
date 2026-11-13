-- Stamps an undone link.
UPDATE public.contact_links
   SET unlinked_at = now(), unlinked_by = %(unlinked_by)s
 WHERE client_id = %(client_id)s AND id = %(link_id)s AND unlinked_at IS NULL;
