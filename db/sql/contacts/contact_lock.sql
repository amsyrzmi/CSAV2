-- Transaction lock on one key: 'contact:<id>' for a contact a link, unlink or ingress write
-- touches, 'phone:<client>:<digits>' for a phone being verified. Taken in that order (phone,
-- then contacts in sorted id order), never the other way round.
SELECT pg_advisory_xact_lock(hashtextextended(%(key)s, 0));
