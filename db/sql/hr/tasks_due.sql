SELECT client_id, task_id, recipient_id, kind, reminder_key, due_on, title FROM public.hr_tasks_due(%s::uuid, %s::date);
