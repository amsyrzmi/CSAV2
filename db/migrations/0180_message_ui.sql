-- Interactive options (confirm/cancel buttons) travel with the outbound message so the
-- delivery worker can render them.

ALTER TABLE public.messages
    ADD COLUMN IF NOT EXISTS ui jsonb;
