SELECT c.slug,
    (SELECT count(*) FROM public.console_memberships m WHERE m.client_id = c.id) AS members,
    (SELECT count(*) FROM public.capability_registry r
     WHERE r.client_id = c.id AND r.client_enabled) AS capabilities_enabled,
    (SELECT count(*) FROM public.products p WHERE p.client_id = c.id) AS products,
    (SELECT count(*) FROM public.contacts x WHERE x.client_id = c.id) AS contacts,
    (SELECT count(*) FROM public.conversations v WHERE v.client_id = c.id) AS conversations,
    (SELECT count(*) FROM public.messages g WHERE g.client_id = c.id) AS messages,
    (SELECT count(*) FROM public.orders o WHERE o.client_id = c.id) AS orders,
    (SELECT count(*) FROM public.tickets t WHERE t.client_id = c.id) AS tickets,
    (SELECT count(*) FROM public.invoices i WHERE i.client_id = c.id) AS invoices,
    (SELECT count(*) FROM public.kb_gaps k WHERE k.client_id = c.id) AS kb_gaps,
    (SELECT count(*) FROM public.hr_employees e WHERE e.client_id = c.id) AS hr_employees,
    (SELECT count(*) FROM public.hr_grants h WHERE h.client_id = c.id) AS hr_grants,
    (SELECT count(*) FROM public.hr_workflow_grants w WHERE w.client_id = c.id) AS hr_workflow_grants,
    (SELECT count(*) FROM public.hr_private_grants q WHERE q.client_id = c.id) AS hr_private_grants
FROM public.clients c WHERE c.id = ANY(%s) ORDER BY c.slug;
