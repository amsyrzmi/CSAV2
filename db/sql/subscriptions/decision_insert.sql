-- Decisions are final: the primary key on request_id keeps the first one. No row comes back
-- when a decision already exists; the caller reads the stored one.
INSERT INTO public.subscription_request_decisions
    (client_id, request_id, decision, note, decided_by, proposal_id)
VALUES (%(client_id)s, %(request_id)s, %(decision)s, %(note)s, %(decided_by)s, %(proposal_id)s)
ON CONFLICT (request_id) DO NOTHING
RETURNING request_id;
