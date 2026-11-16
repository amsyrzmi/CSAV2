"""Which payment QR picture a customer's reply carries: a pure choice, no database.

The payment agent lists the company's first `CUSTOMER_METHODS` active QR and transfer methods
(`payments/agent_methods.sql`), each with its stored picture or None. The QR sent is chosen among
exactly those, so the picture never shows a method the text does not name: the first `qr` method
in the listed order that has a picture. A QR without a picture is passed over, a transfer is never
chosen, and with none the reply is text only.

The caption is `copy.qr_caption` (the method's label), written by code and stored as it stands.
"""

from __future__ import annotations

from collections.abc import Sequence

from csa.contracts.facts import PictureRef
from csa.payments import copy
from csa.payments.contracts import MethodOut


def qr_picture(methods: Sequence[MethodOut], locale: copy.Locale) -> PictureRef | None:
    """The first listed `qr` method that has a picture, as the reply's picture; else None."""
    for method in methods:
        if method.kind == "qr" and method.picture is not None:
            return PictureRef(
                kind="payment_qr",
                subject_key=str(method.id),
                caption=copy.qr_caption(method.label, locale),
            )
    return None
