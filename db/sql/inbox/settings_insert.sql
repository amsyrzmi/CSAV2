-- The first save of a company's inbox settings (version 0 becomes 1).
INSERT INTO public.inbox_settings (
    client_id, target_urgent_min, target_high_min, target_normal_min, target_low_min,
    repeat_minutes, max_repeats, follow_up_hours, stale_claim_hours, handback_prompt_hours,
    version)
VALUES (
    %(client_id)s, %(target_urgent_min)s, %(target_high_min)s, %(target_normal_min)s,
    %(target_low_min)s, %(repeat_minutes)s, %(max_repeats)s, %(follow_up_hours)s,
    %(stale_claim_hours)s, %(handback_prompt_hours)s, 1);
