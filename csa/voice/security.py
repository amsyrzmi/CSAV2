"""Session-specific, expiring media request MACs; no browser cookie forwarding."""

import hashlib
import hmac
import time


def media_signature(secret: str, path: str, timestamp: str, body: bytes) -> str:
    payload = f"POST\n{path}\n{timestamp}\n{hashlib.sha256(body).hexdigest()}".encode()
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def valid_media_signature(
    secret: str, path: str, timestamp: str, body: bytes, signature: str
) -> bool:
    try:
        fresh = abs(time.time() - int(timestamp)) <= 30
    except ValueError:
        return False
    return fresh and hmac.compare_digest(
        media_signature(secret, path, timestamp, body).encode(), signature.encode()
    )
