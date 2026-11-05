-- Record what a customer message's key did. Written once, in the savepoint of the write itself.
INSERT INTO public.booking_turn_actions (client_id, turn_key, booking_id, action)
VALUES (%(client_id)s, %(turn_key)s, %(booking_id)s, %(action)s);
