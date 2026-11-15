"""Contracts for pictures: the kinds, and the small shape every owner's answer carries."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from csa.console.contracts import Contract

PictureKind = Literal["payment_qr", "plan", "product", "training_thumbnail"]

# Every owner query (a payment method, a plan, a product) that lists or reads one thing selects its
# picture as this jsonb or NULL, joining `media_assets a` on (client_id, kind, subject_key):
#   CASE WHEN a.id IS NULL THEN NULL
#        ELSE jsonb_build_object('id', a.id, 'version', a.sha256) END AS picture
# `version` is the picture's sha256; it changes when the picture is replaced.
PICTURE_JSON_SQL = "jsonb_build_object('id', a.id, 'version', a.sha256)"


class PictureOut(Contract):
    id: UUID
    version: str


class ThumbnailOut(Contract):
    version: str
