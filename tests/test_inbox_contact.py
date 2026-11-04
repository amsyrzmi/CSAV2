"""Pure helpers behind the inbox contact, search and preview fields (no Docker)."""

from __future__ import annotations

import pytest
from csa.console.inbox_contact import (
    Search,
    display_name_or_none,
    mask_phone,
    parse_search,
    preview_text,
)


def test_mask_phone_keeps_last_four() -> None:
    assert mask_phone("+60 12-555 0148") == "••••0148"
    assert mask_phone("+60125550148") == "••••0148"
    assert mask_phone("12") == "••••"
    assert mask_phone("no digits") is None
    assert mask_phone(None) is None


def test_display_name_hides_number_only_names() -> None:
    assert display_name_or_none("+60125550148", "+60125550148") is None
    assert display_name_or_none("+60 12-555 0148", "+60125550148") is None
    assert display_name_or_none("Nur Aisyah", "+60125550148") == "Nur Aisyah"
    assert display_name_or_none("  ", "+60125550148") is None
    assert display_name_or_none(None, None) is None
    assert display_name_or_none(" Nur Aisyah ", None) == "Nur Aisyah"


def test_parse_search_rules() -> None:
    assert parse_search(None) is None
    assert parse_search(" @Aisyah ") == Search(like="%Aisyah%", digits=None)
    for short in ("a", " @ ", "", "   "):
        with pytest.raises(ValueError):
            parse_search(short)
    assert parse_search("50%_\\").like == "%50\\%\\_\\\\%"  # type: ignore[union-attr]
    found = parse_search("012 555")
    assert found is not None and found.digits == "012555"
    short_name = parse_search("Li")
    assert short_name is not None and short_name.digits is None
    assert parse_search("a1") is not None and parse_search("a1").digits is None  # type: ignore[union-attr]


def test_preview_text_redacts_and_truncates() -> None:
    assert "synthetic@example.test" not in preview_text("Mail synthetic@example.test please")
    assert "[EMAIL_0]" in preview_text("Mail synthetic@example.test please")
    assert len(preview_text("x" * 300)) <= 120
    assert preview_text("line one\n\nline   two\tend") == "line one line two end"
