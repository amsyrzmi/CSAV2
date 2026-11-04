-- Every hold (booking or block, buffer included) that overlaps the window, except one booking's
-- own holds when it is being moved.
SELECT h.subject_kind, h.subject_id, lower(h.during) AS start, upper(h.during) AS "end"
FROM public.booking_holds h
WHERE h.client_id = %(client_id)s
  AND h.during && tstzrange(%(from)s, %(to)s)
  AND (%(except)s::uuid IS NULL OR h.booking_id IS DISTINCT FROM %(except)s::uuid);
