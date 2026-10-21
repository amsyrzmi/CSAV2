SELECT region,year,payload,version FROM public.hr_calendars WHERE client_id=%s ORDER BY year DESC,region LIMIT 100;
