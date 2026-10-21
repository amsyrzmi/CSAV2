SELECT EXISTS(SELECT 1 FROM public.hr_cases c WHERE c.client_id=%s AND c.employee_id=%s
 AND c.domain='attendance' AND c.status='approved' AND c.id<>%s
 AND (%s::uuid IS NULL OR c.id<>%s::uuid)
 AND NOT EXISTS(SELECT 1 FROM public.hr_cases replacement WHERE replacement.client_id=c.client_id
     AND replacement.domain='attendance' AND replacement.status='approved'
     AND replacement.payload->>'correction_of'=c.id::text)
 AND ((c.payload->>'starts_at')::timestamptz<%s
 AND (c.payload->>'ends_at')::timestamptz>%s)) AS conflict;
