SELECT user_id, display_name, email, employee_id
FROM public.booking_team_members(%(client_id)s);
