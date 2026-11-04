-- The B2 approves-team switch: the caller's own directory.read grant on assigned_manager.
-- hr_grant_self limits rows to app.client_id and app.hr_user_id.
SELECT EXISTS (
    SELECT 1 FROM public.hr_grants
    WHERE client_id = %s AND user_id = %s
      AND action = 'directory.read' AND scope = 'assigned_manager'
) AS approves_team
