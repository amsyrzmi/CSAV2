-- A walk-in with an email the company already has is that customer: the existing contact is used.
INSERT INTO public.contacts (client_id, full_name, phone, email, contact_kind)
VALUES (%(client_id)s, %(name)s, %(phone)s, %(email)s, 'customer')
ON CONFLICT (client_id, email) DO UPDATE SET email = public.contacts.email
RETURNING id;
