SELECT 1 FROM public.console_service_packages WHERE client_id=%s AND status='active' AND package = ANY(%s) LIMIT 1;
