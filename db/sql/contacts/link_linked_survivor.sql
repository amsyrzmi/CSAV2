-- verify_phone stamps the survivor of the link it has just made: record the survivor's columns as
-- the link (with that stamp) left them, so an unlink tells them from a later change.
UPDATE public.contact_links
   SET moved = jsonb_set(moved, '{contacts,linked,survivor}', %(columns)s)
 WHERE client_id = %(client_id)s AND id = %(link_id)s;
