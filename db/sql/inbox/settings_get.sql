-- The company's inbox settings with their version; no row until the first save (the API then
-- answers the defaults with version 0).
SELECT target_urgent_min, target_high_min, target_normal_min, target_low_min, repeat_minutes,
       max_repeats, follow_up_hours, stale_claim_hours, handback_prompt_hours, version
FROM public.inbox_settings
WHERE client_id = %(client_id)s;
