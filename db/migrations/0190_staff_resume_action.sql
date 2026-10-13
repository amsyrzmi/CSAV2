-- A staff "Resume AI" button on escalation alerts: the action targets a conversation, not an order.

ALTER TABLE public.staff_actions
    ALTER COLUMN order_id DROP NOT NULL;

ALTER TABLE public.staff_actions
    ADD COLUMN IF NOT EXISTS conversation_id uuid REFERENCES public.conversations (id);

ALTER TABLE public.staff_actions
    DROP CONSTRAINT IF EXISTS staff_actions_action_check;

ALTER TABLE public.staff_actions
    ADD CONSTRAINT staff_actions_action_check CHECK (action IN ('approve', 'reject', 'resume'));

ALTER TABLE public.staff_actions
    ADD CONSTRAINT staff_actions_target_ck CHECK (
        (action = 'resume' AND conversation_id IS NOT NULL)
        OR (action IN ('approve', 'reject') AND order_id IS NOT NULL)
    );
