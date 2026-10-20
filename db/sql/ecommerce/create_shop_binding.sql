INSERT INTO ecommerce_shop_bindings (client_id, grant_id, shop_cipher)
VALUES (%s, %s, %s)
ON CONFLICT DO NOTHING
RETURNING grant_id;
