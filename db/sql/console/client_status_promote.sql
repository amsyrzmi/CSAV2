UPDATE public.clients
SET status = 'pilot', updated_at = now()
WHERE id = %s AND status = 'onboarding'
RETURNING id;
