-- Opens a review for a pair of contacts; a pair that already has an open review (in either
-- order, contact_link_reviews_one_open) keeps that one.
INSERT INTO public.contact_link_reviews (client_id, contact_a, contact_b, reason)
VALUES (%(client_id)s, %(contact_a)s, %(contact_b)s, %(reason)s)
ON CONFLICT DO NOTHING;
