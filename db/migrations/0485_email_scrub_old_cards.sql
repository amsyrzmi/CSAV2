-- Email scrub (lane B): remove email content from console rows written before the counts-only
-- email cards of 0484 / PR #52.
-- From 27 Oct staging ran the workspace assistant with the earlier email_export card, which
-- stored the recipient and subject in the card and the whole DraftOut (recipient, subject,
-- body) as the executed result; console_workspace_turns kept a copy of the card, and answers
-- that read an open draft quoted its subject. Each row is rewritten to what the current code
-- stores for the same proposal: counts only, the draft id in the "Draft" field (the console
-- reads the draft by that id when it shows the card), and the result {draft_id, status,
-- gmail_draft_id}. Earlier drafts had exactly one recipient, so "1 in To" is exact; the
-- outside-company count is not recoverable, so the title names no audience. A stored answer
-- line "Email draft <id>: subject <quoted subject>, status <s>." becomes
-- "Email draft <id>, status <s>.", the same rewrite csa/workspace/service.py (_stored_answer)
-- applies to new answers. The quotes are U+201C and U+201D, written chr(8220) and chr(8221) so
-- this file stays ASCII.
-- Untouched: other proposal kinds; payload (only {draft_id}); expected_version (a SHA-256
-- digest, not content); console_workspace_turns.body, the user's own typed message, which the
-- current code also stores. console_audit needs no rewrite: every earlier writer stored only
-- {kind}, {thread_id, action}, {thread_id} or {candidates} there, never email content.
-- No new tables, columns, roles, grants or policies; nothing is FORCEd. None of these tables
-- has a trigger or rule, so nothing is disabled. Old writers must be stopped before this
-- applies (deploy/STAGING_PIPELINE.md, section 11), or rows they write afterwards stay.
-- Pre-check: 0460 (console_proposals, console_workspace_turns) applied.
-- Safe to run twice and on an empty database: a rewritten card has no "To" field, no "@" and
--   the new title, a rewritten result has only the three keys, and a scrubbed answer no longer
--   quotes a subject, so a rerun matches no row.
-- Rollback: none. This is a data-only scrub; the removed content is gone by design.

DO $$
BEGIN
 IF to_regclass('public.console_proposals') IS NULL
    OR to_regclass('public.console_workspace_turns') IS NULL THEN
  RAISE EXCEPTION '0485: the workspace tables from 0460 are missing';
 END IF;
END $$;

UPDATE public.console_proposals p SET
    card = CASE WHEN s.old_card THEN p.card || jsonb_build_object(
        'title', 'Save email to Gmail drafts',
        'summary', '1 in To. Nothing is sent.',
        'fields', jsonb_build_array(jsonb_build_object('label', 'Subject', 'value', '(shown on open)'))
            || CASE WHEN s.draft_id IS NULL THEN '[]'::jsonb
                    ELSE jsonb_build_array(jsonb_build_object('label', 'Draft', 'value', s.draft_id)) END)
        ELSE p.card END,
    result = CASE WHEN s.old_result THEN jsonb_build_object(
        'draft_id', coalesce(p.result->>'draft_id', p.result->>'id', s.draft_id),
        'status', p.result->'status',
        'gmail_draft_id', p.result->'gmail_draft_id')
        ELSE p.result END
FROM (
    SELECT id,
           payload->>'draft_id' AS draft_id,
           (card->>'title' = 'Save email draft to Gmail'
            OR strpos(card::text, '@') > 0
            OR jsonb_path_exists(card, '$.fields[*] ? (@.label == "To"
                   || (@.label == "Subject" && @.value != "(shown on open)"))')) AS old_card,
           (jsonb_typeof(result) = 'object'
            AND result - ARRAY['draft_id', 'status', 'gmail_draft_id'] <> '{}'::jsonb) AS old_result
    FROM public.console_proposals
    WHERE kind = 'email_export'
) s
WHERE p.id = s.id AND p.kind = 'email_export' AND (s.old_card OR s.old_result);

UPDATE public.console_workspace_turns t SET
    result = CASE WHEN s.is_proposal THEN t.result || jsonb_build_object('proposal',
            (t.result->'proposal')
            || CASE WHEN s.old_card THEN jsonb_build_object('card', (t.result#>'{proposal,card}')
                   || jsonb_build_object(
                       'title', 'Save email to Gmail drafts',
                       'summary', '1 in To. Nothing is sent.',
                       'fields', jsonb_build_array(
                               jsonb_build_object('label', 'Subject', 'value', '(shown on open)'))
                           || CASE WHEN s.draft_id IS NULL THEN '[]'::jsonb
                                   ELSE jsonb_build_array(
                                       jsonb_build_object('label', 'Draft', 'value', s.draft_id)) END))
                   ELSE '{}'::jsonb END
            || CASE WHEN s.old_result THEN jsonb_build_object('result', jsonb_build_object(
                   'draft_id', coalesce(t.result#>>'{proposal,result,draft_id}',
                                        t.result#>>'{proposal,result,id}', s.draft_id),
                   'status', t.result#>'{proposal,result,status}',
                   'gmail_draft_id', t.result#>'{proposal,result,gmail_draft_id}'))
                   ELSE '{}'::jsonb END)
        ELSE jsonb_set(t.result, '{markdown}', to_jsonb(regexp_replace(t.result->>'markdown',
            '(Email draft [0-9a-f-]{36}): subject ' || chr(8220) || '.*' || chr(8221)
                || ', (status [a-z_]+\.)',
            '\1, \2', 'g')))
        END
FROM (
    SELECT u.id,
           u.result->>'action' = 'proposal' AS is_proposal,
           coalesce((SELECT p.payload->>'draft_id' FROM public.console_proposals p
                     WHERE p.id = (u.result#>>'{proposal,id}')::uuid
                       AND p.client_id = u.client_id AND p.kind = 'email_export'),
                    u.result#>>'{proposal,result,draft_id}',
                    u.result#>>'{proposal,result,id}') AS draft_id,
           (u.result#>>'{proposal,card,title}' = 'Save email draft to Gmail'
            OR strpos((u.result#>'{proposal,card}')::text, '@') > 0
            OR jsonb_path_exists(u.result, '$.proposal.card.fields[*] ? (@.label == "To"
                   || (@.label == "Subject" && @.value != "(shown on open)"))')) AS old_card,
           (jsonb_typeof(u.result#>'{proposal,result}') = 'object'
            AND (u.result#>'{proposal,result}') - ARRAY['draft_id', 'status', 'gmail_draft_id']
                <> '{}'::jsonb) AS old_result
    FROM public.console_workspace_turns u
    WHERE (u.result->>'action' = 'proposal' AND u.result#>>'{proposal,kind}' = 'email_export')
       OR (u.result->>'action' = 'answer'
           AND u.result->>'markdown' ~ ('Email draft [0-9a-f-]{36}: subject ' || chr(8220) || '.*'
                                        || chr(8221) || ', status [a-z_]+\.'))
) s
WHERE t.id = s.id AND (NOT s.is_proposal OR s.old_card OR s.old_result);
