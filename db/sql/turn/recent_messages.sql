-- The last messages of this conversation before the one being answered, oldest first. Redacted
-- text only: a message with no redacted copy is left out rather than shown raw. An outbound image
-- reply (has_attachment; its text is only the caption) is left out; inbound photos and voice notes
-- also have has_attachment and stay.
SELECT direction, redacted_body
FROM (
    SELECT direction, redacted_body, created_at, id
    FROM public.messages
    WHERE client_id = %(client_id)s AND conversation_id = %(conversation_id)s
      AND redacted_body IS NOT NULL AND btrim(redacted_body) <> ''
      AND NOT (direction = 'outbound' AND has_attachment)
      AND (trace_id IS NULL OR trace_id <> %(trace_id)s)
    ORDER BY created_at DESC, id DESC
    LIMIT %(limit)s
) AS latest
ORDER BY created_at, id;
