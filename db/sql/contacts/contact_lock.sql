-- Transaction lock on one key: 'contact:<id>' for a contact a link, unlink or ingress write
-- touches, 'phone:<client>:<digits>' for a phone being verified. Taken in that order (phone,
-- then contacts in sorted id order), never the other way round: verify_phone runs before
-- live_contact, and unlink takes the phones it may give back before its two contacts.
SELECT pg_advisory_xact_lock(hashtextextended(%(key)s, 0));
