-- Whether a person was really asked to take over this conversation in the last day. A
-- `fail_safe:*` row only records a failed turn and `approval_expired` a draft nobody answered:
-- neither notified anyone, so neither counts.
SELECT EXISTS (
    SELECT 1
    FROM public.escalations
    WHERE client_id = %(client_id)s AND conversation_id = %(conversation_id)s
      AND created_at > now() - interval '24 hours'
      AND reason <> 'approval_expired'
      AND reason NOT LIKE 'fail_safe:%%'
) AS handoff_recent;
