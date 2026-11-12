"""Customer confirm/cancel buttons are rendered from the stored ``Ui`` (LG05)."""

from __future__ import annotations

import uuid

from apps.delivery.main import _buttons, _request_phone_label
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


def test_request_phone_ui_renders_the_share_my_number_label() -> None:
    assert _request_phone_label({"request_phone": True}) == "Share my number / Kongsi nombor saya"
    # The buttons stay as they were: a share keyboard is not an inline button.
    assert _buttons({"request_phone": True}) == []


def test_other_ui_does_not_ask_for_a_phone() -> None:
    assert _request_phone_label(None) is None
    assert _request_phone_label({}) is None
    assert _request_phone_label({"request_phone": False}) is None
    assert _request_phone_label({"options": [{"label": "Yes", "action_id": "abc"}]}) is None
