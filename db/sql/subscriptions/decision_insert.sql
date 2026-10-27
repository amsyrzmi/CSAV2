-- Decisions are final: the primary key on request_id makes a second one a unique violation.
INSERT INTO public.subscription_request_decisions
    (client_id, request_id, decision, note, decided_by, proposal_id)
VALUES (%(client_id)s, %(request_id)s, %(decision)s, %(note)s, %(decided_by)s, %(proposal_id)s)
RETURNING request_id;
