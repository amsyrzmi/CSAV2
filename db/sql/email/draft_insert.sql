INSERT INTO public.email_drafts(client_id,mailbox_id,id,thread_id,recipient,subject,body,status,source) VALUES(%s,%s,%s,%s,%s,%s,%s,'draft',%s)
RETURNING id,thread_id,recipient,subject,body,status,source,gmail_draft_id,created_at,updated_at;
