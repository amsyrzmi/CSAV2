-- What a payment reminder needs of its contact: the language, the addresses (contacts.email,
-- else the customer_given_email typed in a chat) and the name the mail greets them by. Nothing
-- else of the contact is read.
SELECT id, full_name, email, customer_given_email, locale
FROM public.contacts
WHERE client_id = %(client_id)s AND id = %(contact_id)s;
