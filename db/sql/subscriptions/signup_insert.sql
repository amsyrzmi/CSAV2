-- One pending sign-up request. A second open request for the same contact and plan
-- (signup_requests_one_pending) inserts nothing and returns no row; the caller reads the open one.
-- email: the address given for payment reminders, lower case, or NULL.
INSERT INTO public.signup_requests (client_id, contact_id, plan_code, full_name, phone, email)
VALUES (%(client_id)s, %(contact_id)s, %(plan_code)s, %(full_name)s, %(phone)s, %(email)s)
ON CONFLICT (client_id, contact_id, plan_code) WHERE status = 'pending' DO NOTHING
RETURNING id;
