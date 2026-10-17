-- M5: Telegram customer inbox. Private sandbox authority is unchanged.
ALTER TABLE public.conversations ADD COLUMN ai_suppressed_before timestamptz;
ALTER TABLE public.conversations ADD COLUMN inbox_version bigint NOT NULL DEFAULT 0;
ALTER TABLE public.messages ADD COLUMN send_authority text NOT NULL DEFAULT 'automation'
    CHECK (send_authority IN ('automation', 'human', 'reviewed', 'handoff'));
UPDATE public.messages m SET send_authority = 'reviewed'
FROM public.approvals a
WHERE a.client_id = m.client_id AND a.conversation_id = m.conversation_id
  AND a.trace_id = m.trace_id AND a.status IN ('approved', 'edited')
  AND m.idempotency_key = 'appr:' || a.id || ':' || a.status;
CREATE TABLE public.console_inbox_actions (
    client_id uuid NOT NULL REFERENCES public.clients(id),
    actor_id uuid NOT NULL,
    request_key uuid NOT NULL,
    fingerprint text NOT NULL,
    result jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (client_id, actor_id, request_key)
);
ALTER TABLE public.console_inbox_actions ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON public.console_inbox_actions
    USING (client_id = nullif(current_setting('app.client_id', true), '')::uuid)
    WITH CHECK (client_id = nullif(current_setting('app.client_id', true), '')::uuid);
GRANT SELECT, INSERT ON public.console_inbox_actions TO csa_app;
GRANT SELECT ON public.console_inbox_actions TO csa_ro;
CREATE INDEX console_inbox_conversations ON public.conversations
    (client_id, opened_at DESC, id DESC) WHERE channel = 'telegram';
CREATE INDEX console_inbox_messages ON public.messages
    (client_id, conversation_id, created_at DESC, id DESC);
CREATE INDEX delivery_conversation_order ON public.outbound_deliveries
    (client_id, conversation_id, created_at, id) WHERE status IN ('pending', 'delivering');
