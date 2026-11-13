-- Dismisses one open review; no row back means it was not open (or not this company's).
UPDATE public.contact_link_reviews
   SET status = 'resolved', resolution = 'dismissed', resolved_by = %(resolved_by)s,
       resolved_at = now()
 WHERE client_id = %(client_id)s AND id = %(review_id)s AND status = 'open'
RETURNING id;
