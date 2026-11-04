-- Approved leave as dates only; the function never returns a type or a reason.
SELECT employee_id, starts_on, ends_on
FROM public.booking_leave_days(%(client_id)s, %(employees)s::uuid[], %(from)s::date, %(to)s::date);
