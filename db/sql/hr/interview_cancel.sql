UPDATE public.hr_interviews SET status='cancelled',cancellation_reason=%s WHERE client_id=%s AND id=%s AND status='scheduled' RETURNING id;
