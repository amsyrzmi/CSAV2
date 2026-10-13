-- LG07: held AI replies. The approvals row stores the draft and its buttons; staff actions can target it.

ALTER TABLE public.approvals
    ADD COLUMN IF NOT EXISTS ui jsonb NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE public.staff_actions
    ADD COLUMN IF NOT EXISTS approval_id bigint REFERENCES public.approvals (id);

ALTER TABLE public.staff_actions
    DROP CONSTRAINT IF EXISTS staff_actions_action_check;
ALTER TABLE public.staff_actions
    DROP CONSTRAINT IF EXISTS staff_actions_target_ck;

ALTER TABLE public.staff_actions
    ADD CONSTRAINT staff_actions_action_check CHECK (
        action IN ('approve', 'reject', 'resume', 'approve_reply', 'reject_reply')
    );

ALTER TABLE public.staff_actions
    ADD CONSTRAINT staff_actions_target_ck CHECK (
        (action = 'resume' AND conversation_id IS NOT NULL)
        OR (action IN ('approve', 'reject') AND order_id IS NOT NULL)
        OR (action IN ('approve_reply', 'reject_reply') AND approval_id IS NOT NULL)
    );
