-- The caller's active direct reports (0484 definer; the caller comes from app.hr_user_id).
SELECT employee_id, display_name, work_email
FROM public.email_my_direct_reports(%s)
