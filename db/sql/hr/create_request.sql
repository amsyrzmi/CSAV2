INSERT INTO public.hr_training_requests
    (client_id,id,employee_id,created_by,title,learning_objective,status,version,updated_at)
VALUES (%s,%s,%s,%s,%s,%s,'draft',0,%s);
