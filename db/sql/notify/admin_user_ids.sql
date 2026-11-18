-- The company's enabled tenant admins (0624), by user id.
SELECT user_id FROM public.inbox_admin_user_ids(%(client_id)s) AS t (user_id);
