"""Customer confirm/cancel buttons are rendered from the stored ``Ui`` (LG05)."""

from __future__ import annotations

import uuid

from apps.delivery.main import _buttons
from csa.contracts.callbacks import CallbackCodec


def test_confirmation_renders_compact_callbacks() -> None:
    accept, reject = uuid.uuid4(), uuid.uuid4()
    ui = {
        "confirmation": {
            "required": True,
            "order_id": "O1",
            "expires_at": "2026-10-12T07:00:00Z",
            "accept": {"label": "Confirm order", "action_id": CallbackCodec.encode(accept)},
            "reject": {"label": "Cancel", "action_id": CallbackCodec.encode(reject)},
        }
    }
    assert _buttons(ui) == [
        ("Confirm order", CallbackCodec.encode(accept)),
        ("Cancel", CallbackCodec.encode(reject)),
    ]


def test_no_ui_means_no_buttons() -> None:
    assert _buttons(None) == []
    assert _buttons({}) == []
