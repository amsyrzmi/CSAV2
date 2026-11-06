-- Records, for a booking's connected people, who was mailed without a calendar file (their Google
-- event holds it) and who was not (they were, or will be, sent one). A cancellation reads this to
-- send the cancel file to exactly the people who may hold a mail-made event.
UPDATE public.booking_calendar_events
   SET file_free = (person_id = ANY(%(omitted)s::uuid[]))
 WHERE client_id = %(client_id)s AND booking_id = %(booking_id)s AND status <> 'dropped';
