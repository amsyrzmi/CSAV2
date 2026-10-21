UPDATE public.hr_agent_confirmations SET consumed_at=clock_timestamp() WHERE client_id=%s AND id=%s;
