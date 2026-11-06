-- Which companies have queued email reminders, the one waiting longest first. Run as the owner,
-- which is all the mail job uses it for: every read and write for a company then goes through that
-- company's own transaction, as `csa_app`, with row security on.
SELECT client_id
FROM public.booking_reminders
WHERE channel = 'email' AND status = 'queued'
GROUP BY client_id
ORDER BY min(created_at), client_id;
