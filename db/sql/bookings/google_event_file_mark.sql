-- Records, for a booking's connected people, who was mailed without a calendar file (their Google
-- event holds it) and who was not (they were, or will be, sent one). Once anyone is sent a file it
-- stays recorded, because the event that file made may outlive a later mail without one: a
-- cancellation sends the cancel file to everyone who may hold such an event.
UPDATE public.booking_calendar_events
   SET file_sent = file_sent OR NOT (person_id = ANY(%(omitted)s::uuid[])),
       file_free = NOT (file_sent OR NOT (person_id = ANY(%(omitted)s::uuid[])))
 WHERE client_id = %(client_id)s AND booking_id = %(booking_id)s AND status <> 'dropped';
