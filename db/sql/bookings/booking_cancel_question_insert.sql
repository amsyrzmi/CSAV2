-- The bot asked the customer to confirm a cancel: one row for the message that asked, with the
-- booking's version, the chat and the turn it was asked on, and when the question runs out.
INSERT INTO public.booking_turn_actions
    (client_id, turn_key, booking_id, action, booking_version, conversation_id, trace_id,
     expires_at)
VALUES (%(client_id)s, %(turn_key)s, %(booking_id)s, 'ask_cancel', %(booking_version)s,
        %(conversation_id)s, %(trace_id)s, %(expires_at)s);
