SELECT t.id
FROM public.console_chat_threads t
JOIN public.conversations c ON c.id=t.id AND c.client_id=t.client_id
JOIN public.messages m ON m.conversation_id=c.id AND m.client_id=c.client_id
JOIN public.console_chat_turns r ON r.conversation_id=t.id AND r.client_id=t.client_id
  AND r.trace_id=m.trace_id
WHERE t.client_id=%s AND t.id=%s AND t.endpoint_id=%s AND m.id=%s
  AND r.trace_id=%s
  AND c.channel='web' AND c.contact_id=t.contact_id AND m.direction='outbound';
