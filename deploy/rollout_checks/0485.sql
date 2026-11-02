-- Read-only check for migration 0485 (email scrub). scripts/staging_rollout.py runs this
-- file on every apply, after the services are back, and it must print a single 0.
-- It counts email_export rows still in the old shape, using the scrub's own predicates.
-- Check files may contain only SELECTs (review policy; the script does not parse them).

SELECT
  (SELECT count(*) FROM public.console_proposals
    WHERE kind = 'email_export'
      AND (card->>'title' = 'Save email draft to Gmail'
           OR strpos(card::text, '@') > 0
           OR jsonb_path_exists(card, '$.fields[*] ? (@.label == "To"
                  || (@.label == "Subject" && @.value != "(shown on open)"))')
           OR (jsonb_typeof(result) = 'object'
               AND result - ARRAY['draft_id', 'status', 'gmail_draft_id'] <> '{}'::jsonb)))
  + (SELECT count(*) FROM public.console_workspace_turns
    WHERE (result->>'action' = 'proposal' AND result#>>'{proposal,kind}' = 'email_export'
           AND (result#>>'{proposal,card,title}' = 'Save email draft to Gmail'
                OR strpos((result#>'{proposal,card}')::text, '@') > 0
                OR jsonb_path_exists(result, '$.proposal.card.fields[*] ? (@.label == "To"
                       || (@.label == "Subject" && @.value != "(shown on open)"))')
                OR (jsonb_typeof(result#>'{proposal,result}') = 'object'
                    AND (result#>'{proposal,result}')
                        - ARRAY['draft_id', 'status', 'gmail_draft_id'] <> '{}'::jsonb)))
       OR (result->>'action' = 'answer'
           AND result->>'markdown' ~ ('Email draft [0-9a-f-]{36}: subject ' || chr(8220) || '.*'
                                      || chr(8221) || ', status [a-z_]+\.')))
  AS old_rows;
