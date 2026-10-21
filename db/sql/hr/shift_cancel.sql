UPDATE public.hr_shifts SET status='cancelled',cancellation_reason=%s WHERE client_id=%s AND id=%s AND status='scheduled' RETURNING id;
