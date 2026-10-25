INSERT INTO public.messages
    (id, client_id, conversation_id, direction, author, body, redacted_body, idempotency_key, trace_id, created_at)
VALUES
    (%(message_id)s, %(client_id)s, %(conversation_id)s, 'in', 'customer',
     'Where is my synthetic demo order?', 'Where is my synthetic demo order?',
     %(slug)s || ':seed:inbox:in', %(trace_id)s, now() - interval '15 minutes'),
    (%(reply_id)s, %(client_id)s, %(conversation_id)s, 'out', 'assistant',
     'Your demo order has shipped with DemoPost.', 'Your demo order has shipped with DemoPost.',
     %(slug)s || ':seed:inbox:out', %(trace_id)s, now() - interval '14 minutes')
ON CONFLICT DO NOTHING;

INSERT INTO public.approvals
    (client_id, conversation_id, trace_id, draft, reason, risk_score, status, expires_at)
VALUES
    (%(client_id)s, %(conversation_id)s, %(trace_id)s,
     'Unused demo items may be returned within 14 days. Return shipping is paid by the customer.',
     'Synthetic reply awaiting human review', 0.20, 'pending', now() + interval '7 days')
ON CONFLICT (client_id, trace_id) DO NOTHING;

-- Seed preparation registrations disabled: no external integration is activated.
SELECT public.seed_preparation_capabilities(%(client_id)s);
