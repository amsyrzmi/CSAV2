-- Every markup tier row; the open-ended tier (NULL) sorts last within its set.
SELECT stage, bundle, up_to_executions, markup_pct, effective_from
FROM billing_markup_tiers
ORDER BY stage, bundle, effective_from DESC, up_to_executions NULLS LAST;
