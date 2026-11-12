-- Every platform fee row, newest first within a stage.
SELECT stage, monthly_fee_rm, effective_from
FROM billing_stage_fees
ORDER BY stage, effective_from DESC;
