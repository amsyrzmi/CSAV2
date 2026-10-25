SELECT operation,fingerprint,receipt FROM public.email_receipts WHERE client_id=%s AND subject_id=%s AND request_key=%s;
