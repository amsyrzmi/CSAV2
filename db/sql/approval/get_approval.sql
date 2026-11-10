SELECT id, client_id, conversation_id, trace_id, draft, reason, status, ui, expires_at,
       (SELECT contacts.locale
        FROM public.conversations
        JOIN public.contacts
          ON contacts.id = conversations.contact_id
         AND contacts.client_id = conversations.client_id
        WHERE conversations.id = approvals.conversation_id
          AND conversations.client_id = approvals.client_id) AS locale
FROM public.approvals
WHERE client_id = %s
  AND id = %s
FOR UPDATE;
