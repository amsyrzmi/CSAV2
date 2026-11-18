-- Everyone who may use the company's Inbox (0624: enabled tenant admins and staff), by user id.
SELECT user_id FROM public.inbox_team_user_ids(%(client_id)s) AS t (user_id);
