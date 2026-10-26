SELECT 1 FROM public.console_service_packages WHERE client_id=%s AND package = ANY(%s) AND status = ANY(%s) LIMIT 1;
