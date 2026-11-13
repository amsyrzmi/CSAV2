"""Phone numbers in one shape: the Python twin of the SQL function public.my_phone_digits (0581).

The SQL function sits behind the generated column contacts.phone_digits and the unique index on
verified phones, so both must normalise exactly alike (tests/test_phone.py runs one table of
cases through both). Keep the digits 0-9 only; a leading 0 becomes 60 (Malaysian national form);
anything else, a leading 60 included, is kept as it is. A leading 00 is not read as an
international prefix: 0044... becomes 60044..., as in the SQL.
"""

from __future__ import annotations

import re

_NOT_DIGIT = re.compile(r"[^0-9]")
MIN_DIGITS = 8
MAX_DIGITS = 15


def normalise_my_phone(raw: str) -> str | None:
    """``+`` and the digits as ``my_phone_digits`` gives them, or None outside 8 to 15 digits."""
    digits = _NOT_DIGIT.sub("", raw)
    if digits.startswith("0"):
        digits = "60" + digits[1:]
    if not MIN_DIGITS <= len(digits) <= MAX_DIGITS:
        return None
    return "+" + digits
