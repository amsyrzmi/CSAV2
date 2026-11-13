-- What a payment reminder needs of its contact: the language and the addresses (contacts.email,
-- else the customer_given_email typed in a chat). Nothing else of the contact is read.
SELECT id, email, customer_given_email, locale
FROM public.contacts
WHERE client_id = %(client_id)s AND id = %(contact_id)s;
