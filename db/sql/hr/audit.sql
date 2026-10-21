INSERT INTO public.hr_training_audit
    (client_id,request_id,subject_id,request_key,action,before_status,after_status,version,
     authorized_scope,manager_id,occurred_at)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s);
