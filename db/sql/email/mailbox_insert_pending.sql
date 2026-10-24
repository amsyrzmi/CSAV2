INSERT INTO public.email_mailboxes(client_id,user_id,id,status) VALUES(%s,%s,%s,'pending')
ON CONFLICT (client_id,user_id) DO UPDATE SET status=CASE WHEN public.email_mailboxes.status='connected' THEN 'connected' ELSE 'pending' END, updated_at=clock_timestamp()
RETURNING id,status;
