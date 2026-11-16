-- An active plan of this company, by the code or the name the customer used. has_picture: the
-- company has a picture for this plan (media_assets, kind 'plan', keyed by the plan code); an
-- EXISTS, so a plan without one asks for none and the row is never multiplied. The sign-up's
-- read-back sends it (csa/agents/staged/subscription_facts.py plan_picture).
SELECT p.code, p.name, p.price, p.is_signup_open,
       EXISTS (
           SELECT 1 FROM public.media_assets a
           WHERE a.client_id = %(client_id)s AND a.kind = 'plan' AND a.subject_key = p.code
       ) AS has_picture
FROM public.plans p
WHERE p.client_id = %(client_id)s AND p.is_active
  AND (p.code = lower(%(plan)s) OR lower(p.name) = lower(%(plan)s))
ORDER BY (p.code = lower(%(plan)s)) DESC, p.sort_order, p.code
LIMIT 1;
