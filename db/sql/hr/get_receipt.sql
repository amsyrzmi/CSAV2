SELECT request_id, fingerprint, action, status, version, updated_at
FROM public.hr_training_receipts WHERE client_id=%s AND subject_id=%s AND request_key=%s;
