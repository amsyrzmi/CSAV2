"""The attachment that marks an outbound message as an image reply.

An image reply is stored as an ordinary outbound message (body and redacted body = its caption,
``has_attachment`` true) whose ``attachments`` is ``[{"type": "image", "asset_id": "<uuid>"}]``.
Inbound attachments are stored with ``kind`` (the envelope's ``Attachment``), never ``type``, so
an inbound photo is never taken for an image reply. ``image_asset_id`` is the only place that
tells an image message apart: delivery, the Inbox and anything else read it through here.
"""

from __future__ import annotations

from uuid import UUID

__all__ = ["image_asset_id", "image_attachments"]


def image_attachments(asset_id: UUID) -> list[dict[str, str]]:
    """The ``attachments`` value of an image reply showing the stored picture ``asset_id``."""
    return [{"type": "image", "asset_id": str(asset_id)}]


def image_asset_id(attachments: object) -> UUID | None:
    """The picture an image reply shows, from its stored ``attachments``; None for anything else.

    Only the first attachment counts, and only in the exact shape ``image_attachments`` writes.
    """
    if not isinstance(attachments, list) or not attachments:
        return None
    first: object = attachments[0]
    if not isinstance(first, dict) or first.get("type") != "image":
        return None
    asset_id: object = first.get("asset_id")
    if not isinstance(asset_id, str):
        return None
    try:
        return UUID(asset_id)
    except ValueError:
        return None
