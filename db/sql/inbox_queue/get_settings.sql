-- The company's inbox settings; no row means the defaults (csa.inbox_queue.settings).
SELECT target_urgent_min, target_high_min, target_normal_min, target_low_min, repeat_minutes,
       max_repeats, follow_up_hours, stale_claim_hours, handback_prompt_hours
FROM public.inbox_settings
WHERE client_id = %s;
