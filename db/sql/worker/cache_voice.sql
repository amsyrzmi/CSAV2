UPDATE public.ingress_events SET raw=%s
WHERE id=%s AND client_id=%s AND status='dispatched'
RETURNING id;
