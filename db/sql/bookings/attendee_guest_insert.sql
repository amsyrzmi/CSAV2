INSERT INTO public.booking_attendees (client_id, booking_id, guest_name, guest_email)
VALUES (%(client_id)s, %(booking_id)s, %(name)s, %(email)s);
