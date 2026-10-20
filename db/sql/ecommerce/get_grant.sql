SELECT g.id, g.revision, g.state, g.source, g.scopes, g.verified_at, g.expires_at,
       jsonb_build_object(
           'binding', jsonb_build_object(
               'client_id', g.client_id, 'record_id', g.record_id,
               'platform', g.platform, 'shop_id', g.shop_id),
           'app_id', g.app_id, 'app_revision', g.app_revision,
           'credential_generation', g.credential_generation
       ) AS context
FROM ecommerce_grants g
WHERE g.client_id = %s AND g.id = %s;
