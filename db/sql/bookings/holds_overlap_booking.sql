-- One row when the booking's own holds overlap [from, to): the overlap the database would refuse.
SELECT 1 AS overlaps FROM public.booking_holds
WHERE client_id = %(client_id)s AND booking_id = %(booking_id)s
  AND during && tstzrange(%(from)s, %(to)s)
LIMIT 1;
