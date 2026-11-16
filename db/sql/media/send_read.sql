-- The picture an image message shows, with its bytes, read by delivery at send time: the only read
-- of picture bytes when a picture is sent. Params: company, asset id (from the message's
-- attachments). The company is in the query as well as in the row security, so another company's
-- asset id gives no row. A row only while the picture's subject is still offered: a payment_qr
-- needs an active payment method of kind qr, a plan an active plan, a product an active product.
-- Anything else (a removed picture, a deactivated or archived subject, a training thumbnail) gives
-- no row, and delivery skips the message as picture_unavailable.
SELECT a.media_type, a.sha256, d.data, a.kind, a.subject_key
FROM public.media_assets a
JOIN public.media_asset_data d ON d.asset_id = a.id AND d.client_id = a.client_id
WHERE a.client_id = %s
  AND a.id = %s
  AND CASE a.kind
      WHEN 'payment_qr' THEN EXISTS (
          SELECT 1 FROM public.payment_methods m
          WHERE m.client_id = a.client_id AND m.id::text = a.subject_key
            AND m.kind = 'qr' AND m.is_active)
      WHEN 'plan' THEN EXISTS (
          SELECT 1 FROM public.plans p
          WHERE p.client_id = a.client_id AND p.code = a.subject_key AND p.is_active)
      WHEN 'product' THEN EXISTS (
          SELECT 1 FROM public.products pr
          WHERE pr.client_id = a.client_id AND pr.id::text = a.subject_key AND pr.is_active)
      ELSE false
  END;
