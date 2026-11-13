-- A field passed as NULL stays as it is. The version guard makes a change from an older read
-- touch nothing (the caller then answers 409); the caller holds the payments lock as well.
UPDATE public.payment_methods
SET label = coalesce(%(label)s, label),
    details = coalesce(%(details)s::jsonb, details),
    instructions = coalesce(%(instructions)s, instructions),
    is_active = coalesce(%(is_active)s, is_active),
    sort_order = coalesce(%(sort_order)s, sort_order),
    version = version + 1,
    updated_at = now()
WHERE client_id = %(client_id)s AND id = %(method_id)s AND version = %(version)s
RETURNING id, kind, label, details, instructions, is_active, sort_order, version;
