-- Put the Meet link Google made on a booking, only if it still has none (a link staff typed always
-- wins) and is still a confirmed online booking. The version moves, as for any change to what a
-- booking shows; no row back means the booking changed first and the link stays on the event alone.
UPDATE public.bookings
   SET meeting_link = %(link)s, version = version + 1
 WHERE client_id = %(client_id)s AND id = %(booking_id)s
   AND meeting_link IS NULL AND status = 'confirmed' AND location_kind = 'online'
RETURNING version;
