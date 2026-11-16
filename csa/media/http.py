"""The response headers and revalidation every picture route shares.

The generic read route (`csa.media.api`) and the training thumbnail route
(`csa.training.service.TrainingLibrary.open_thumbnail`) send the same headers: the `ETag` is the
picture's SHA-256 (its version, which changes on a replace), `Cache-Control: private, no-cache`
(the browser keeps a copy and asks again each time), `nosniff`, and a sandbox policy so a picture
opened on its own can never run anything.
"""

from __future__ import annotations

__all__ = ["CACHE_CONTROL", "CSP", "etag", "etag_matches", "picture_headers"]

CACHE_CONTROL = "private, no-cache"
CSP = "default-src 'none'; sandbox"


def etag(sha256: str) -> str:
    return f'"{sha256}"'


def etag_matches(if_none_match: str | None, current: str) -> bool:
    """Whether `If-None-Match` names `current` (a weak `W/` form or `*` also match)."""
    if if_none_match is None:
        return False
    for candidate in if_none_match.split(","):
        candidate = candidate.strip()
        if candidate == "*" or candidate.removeprefix("W/") == current:
            return True
    return False


def picture_headers(sha256: str) -> dict[str, str]:
    return {
        "ETag": etag(sha256),
        "Cache-Control": CACHE_CONTROL,
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": CSP,
    }
