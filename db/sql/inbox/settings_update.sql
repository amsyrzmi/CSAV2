-- A later save: replaces every value and raises the version by one.
UPDATE public.inbox_settings
SET target_urgent_min = %(target_urgent_min)s, target_high_min = %(target_high_min)s,
    target_normal_min = %(target_normal_min)s, target_low_min = %(target_low_min)s,
    repeat_minutes = %(repeat_minutes)s, max_repeats = %(max_repeats)s,
    follow_up_hours = %(follow_up_hours)s, stale_claim_hours = %(stale_claim_hours)s,
    handback_prompt_hours = %(handback_prompt_hours)s, version = version + 1,
    updated_at = now()
WHERE client_id = %(client_id)s;
