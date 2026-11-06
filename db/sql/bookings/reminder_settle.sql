-- Record how a claimed row ended: sent, failed or dropped. Only a row still `sending` changes.
UPDATE public.booking_reminders
   SET status = %(status)s, sent_at = %(sent_at)s
 WHERE client_id = %(client_id)s AND id = %(id)s AND status = 'sending';
