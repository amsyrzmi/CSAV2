-- Opens a review for a pair of contacts; a pair that already has an open review (in either
-- order, contact_link_reviews_one_open) keeps that one. A pair staff dismissed for the same
-- reason (in either order) gets no new review: the decision sticks until staff link by hand.
INSERT INTO public.contact_link_reviews (client_id, contact_a, contact_b, reason)
SELECT %(client_id)s, %(contact_a)s, %(contact_b)s, %(reason)s
 WHERE NOT EXISTS (
       SELECT 1
         FROM public.contact_link_reviews r
        WHERE r.client_id = %(client_id)s
          AND r.reason = %(reason)s
          AND r.resolution = 'dismissed'
          AND ((r.contact_a = %(contact_a)s AND r.contact_b = %(contact_b)s)
               OR (r.contact_a = %(contact_b)s AND r.contact_b = %(contact_a)s))
       )
ON CONFLICT DO NOTHING;
