# Runbook 05 — Rotate Channel Credentials / Telegram Bot Token

## Objective
Rotate a compromised or refreshed channel credential (such as a Telegram Bot token or webhook secret) with zero downtime.

## Safety Rules
- Secrets must never be stored in plain text in database rows.
- Always use application encryption (`csa.core.crypto.encrypt`) with the designated Authenticated Additional Data (AAD).

## Rotation Steps

### 1. Encrypt New Secret & Token in Python
Run via python script or shell:
```python
from csa.core import crypto
import uuid

credential_id = "<CREDENTIAL_UUID>"
new_bot_token = "<NEW_BOT_TOKEN>"
new_webhook_secret = "<NEW_WEBHOOK_SECRET>"

enc_token = crypto.encrypt(
    new_bot_token,
    crypto.credential_aad("channel_credentials", "access_token", credential_id),
)
enc_secret = crypto.encrypt(
    new_webhook_secret,
    crypto.credential_aad(
        "channel_credentials",
        "meta.telegram_webhook_secret_enc",
        credential_id,
    ),
)
print("Encrypted Token:", enc_token)
print("Encrypted Secret:", enc_secret)
```

### 2. Update Database (SQL)
```sql
UPDATE public.channel_credentials
SET access_token = '<ENC_TOKEN>',
    meta = jsonb_set(
        meta,
        '{telegram_webhook_secret_enc}',
        to_jsonb('<ENC_SECRET>'::text)
    ),
    updated_at = now()
WHERE id = '<CREDENTIAL_UUID>';
```

### 3. Update Webhook with Telegram Bot API
Register the new secret with Telegram:
```sh
curl -s -X POST "https://api.telegram.org/bot<NEW_BOT_TOKEN>/setWebhook" \
     -H "Content-Type: application/json" \
     -d '{
       "url": "https://<GATEWAY_HOSTNAME>/hooks/<PUBLIC_ID>",
       "secret_token": "<NEW_WEBHOOK_SECRET>",
       "allowed_updates": ["message", "callback_query"]
     }'
```
