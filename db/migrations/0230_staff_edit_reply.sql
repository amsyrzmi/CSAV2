-- LG07: staff Edit of a held reply. The Edit tap arms the action ('awaiting'); the next private
-- message from that staff chat supplies the replacement text and settles the approval once.

ALTER TABLE public.staff_actions DROP CONSTRAINT IF EXISTS staff_actions_status_check;
ALTER TABLE public.staff_actions
    ADD CONSTRAINT staff_actions_status_check CHECK (status IN ('open', 'awaiting', 'used', 'expired'));

ALTER TABLE public.staff_actions DROP CONSTRAINT IF EXISTS staff_actions_action_check;
ALTER TABLE public.staff_actions DROP CONSTRAINT IF EXISTS staff_actions_target_ck;

ALTER TABLE public.staff_actions
    ADD CONSTRAINT staff_actions_action_check CHECK (
        action IN ('approve', 'reject', 'resume', 'approve_reply', 'reject_reply', 'edit_reply')
    );

ALTER TABLE public.staff_actions
    ADD CONSTRAINT staff_actions_target_ck CHECK (
        (action = 'resume' AND conversation_id IS NOT NULL)
        OR (action IN ('approve', 'reject') AND order_id IS NOT NULL)
        OR (action IN ('approve_reply', 'reject_reply', 'edit_reply') AND approval_id IS NOT NULL)
    );

CREATE INDEX IF NOT EXISTS staff_actions_awaiting_idx
    ON public.staff_actions (client_id, target_id)
    WHERE status = 'awaiting';
