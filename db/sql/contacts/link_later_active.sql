-- Whether a link made after this one, and not undone, involves either of its contacts. Links on a
-- contact are undone newest first, so each unlink finds the contacts as its own link left them.
SELECT EXISTS (
    SELECT 1
    FROM public.contact_links this
    JOIN public.contact_links later
      ON later.client_id = this.client_id
     AND later.id <> this.id
     AND later.created_at > this.created_at
     AND later.unlinked_at IS NULL
     AND (later.survivor_id IN (this.survivor_id, this.loser_id)
          OR later.loser_id IN (this.survivor_id, this.loser_id))
    WHERE this.client_id = %(client_id)s AND this.id = %(link_id)s
) AS later_active;
