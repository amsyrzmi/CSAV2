-- Record how a claimed payment reminder email row ended: sent, failed or dropped. Only a row still
-- `sending` changes.
UPDATE public.invoice_reminders
   SET status = %(status)s, sent_at = %(sent_at)s
 WHERE client_id = %(client_id)s AND id = %(id)s AND status = 'sending';
