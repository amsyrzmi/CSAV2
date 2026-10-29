-- The Gmail pageToken for the next load-more (NULL when no older page is left). With a second
-- token it only writes while the stored token is still the one the page was fetched with.
UPDATE public.email_mailboxes SET sync_page_token=%s,updated_at=clock_timestamp()
WHERE client_id=%s AND id=%s AND (NOT %s OR sync_page_token IS NOT DISTINCT FROM %s);
