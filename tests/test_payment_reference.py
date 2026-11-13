"""Invoice references (XXXX-XXC, Luhn mod 31) and time-ordered invoice ids. Pure: no database."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest
from csa.payments.reference import (
    ALPHABET,
    REFERENCE_PATTERN,
    check_char,
    new_invoice_id,
    new_reference,
    normalise_reference,
)

# Golden check characters, worked by hand from the rule, not read off the code. Alphabet index:
# 2..9 are 0..7, A=8 B=9 C=10 D=11 E=12 F=13 G=14 H=15, J=16 K=17 M=18 N=19, P=20 Q=21 R=22 S=23
# T=24 U=25 V=26 W=27 X=28 Y=29 Z=30. The rule is Luhn mod 31 with the doubled value reduced mod
# 31 (not folded): from the right of the body, double every other value (the rightmost first) mod
# 31, add all, and the check index is (31 - sum % 31) % 31.
#   K7QX4M: M=18 doubled 36 -> 5; 4=2; X=28 doubled 56 -> 25; Q=21; 7=5 doubled 10; K=17.
#           Sum 5+2+25+21+10+17 = 80, 80 % 31 = 18, check index 13 = F.
#   ZZZZZZ: Z=30 doubled 60 -> 29, so 3 * 29 + 3 * 30 = 177, 177 % 31 = 22, index 9 = B.
#   ABCDEF: F=13 doubled 26; E=12; D=11 doubled 22; C=10; B=9 doubled 18; A=8. Sum 96,
#           96 % 31 = 3, index 28 = X.
#   JJJJJJ: J=16 doubled 32 -> 1, so 3 * 1 + 3 * 16 = 51, 51 % 31 = 20, index 11 = D.
#   222222: every value 0, sum 0, index 0 = 2.
GOLDEN = [
    ("K7QX4M", "F"),
    ("ZZZZZZ", "B"),
    ("ABCDEF", "X"),
    ("JJJJJJ", "D"),
    ("222222", "2"),
]
VALID = "K7QX-4MF"


def test_alphabet_is_the_31_unambiguous_characters() -> None:
    assert ALPHABET == "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
    assert len(ALPHABET) == 31
    assert not set("01ILO") & set(ALPHABET)


@pytest.mark.parametrize(("body", "expected"), GOLDEN)
def test_check_char_matches_the_hand_worked_values(body: str, expected: str) -> None:
    assert check_char(body) == expected
    full = f"{body[:4]}-{body[4:]}{expected}"
    assert normalise_reference(full) == full


def test_check_char_refuses_a_character_outside_the_alphabet() -> None:
    for bad in ("K7QX4O", "K7QX40", "K7QX41", "K7QX4I", "K7QX4L", "k7qx4m", "K7QX4-"):
        with pytest.raises(ValueError):
            check_char(bad)


def test_new_reference_matches_the_pattern_and_has_a_valid_check() -> None:
    drawn = {new_reference() for _ in range(1000)}
    assert len(drawn) > 990  # about 31 ** 6 bodies: a collision in 1,000 draws is a bug
    for reference in drawn:
        assert re.fullmatch(REFERENCE_PATTERN, reference)
        assert normalise_reference(reference) == reference


def test_new_reference_uses_the_injected_source_for_each_of_the_six_characters() -> None:
    draws = iter([17, 5, 21, 28, 2, 18])  # K 7 Q X 4 M
    assert new_reference(lambda n: next(draws) % n) == VALID


def test_check_char_catches_every_single_substitution_and_adjacent_swap() -> None:
    compact = VALID.replace("-", "")
    for i, kept in enumerate(compact):
        for other in ALPHABET:
            if other != kept:
                changed = compact[:i] + other + compact[i + 1 :]
                assert normalise_reference(changed) is None, (i, other)
    for i in range(len(compact) - 1):
        if compact[i] != compact[i + 1]:
            swapped = compact[:i] + compact[i + 1] + compact[i] + compact[i + 2 :]
            assert normalise_reference(swapped) is None, i


def test_the_check_misses_no_character_pair_that_textbook_luhn_mod_31_misses() -> None:
    # Textbook Luhn mod 31 folds a doubled value into two base-31 digits, which maps x and x + 15
    # to the same value: swapping 7 for P in the doubled second place goes unnoticed. Here it is
    # caught, and so is every other single change at every place of a body, and so is every
    # swap of two different neighbours (even 2 and Z, which textbook Luhn mod 31 also misses).
    body = "K7QX4M"
    assert normalise_reference("KPQX-4MF") is None
    for i in range(len(body)):
        for other in ALPHABET:
            if other != body[i]:
                assert check_char(body[:i] + other + body[i + 1 :]) != check_char(body)
    for first in ALPHABET:
        for second in ALPHABET:
            if first != second:
                assert check_char("K7Q" + first + second + "M") != check_char(
                    "K7Q" + second + first + "M"
                )
    assert check_char("K7QX2Z") != check_char("K7QXZ2")


def test_normalise_accepts_spacing_and_case() -> None:
    for typed in ("k7qx 4mf", "K7QX4MF", " k7qx-4mf ", "k7qx - 4mf", "K7-QX4-MF", "K7QX\t4MF"):
        assert normalise_reference(typed) == VALID, typed


def test_normalise_refuses_ambiguous_and_wrong_length() -> None:
    for typed in (
        "K7QX-4M0",  # zero is not in the alphabet
        "K7QX-4MO",
        "K7QX-4M1",
        "K7QX-4MI",
        "K7QX-4ML",
        "O7QX-4MF",
        "K7QX-4MP",  # right characters, wrong check
        "K7QX4M",  # six
        "K7QX-4MFF",  # eight
        "",
        "-",
        "K7QX-4MF!",
        "K7QX-4MFß",  # an upper-casing quirk must not let a foreign character through
        "K7QX-ß4MF",
    ):
        assert normalise_reference(typed) is None, typed


def test_new_invoice_id_is_time_ordered() -> None:
    t = datetime(2031, 1, 1, 12, 0, tzinfo=UTC)
    first = new_invoice_id(t, lambda n: "f" * (2 * n))
    second = new_invoice_id(t + timedelta(milliseconds=1), lambda n: "0" * (2 * n))
    assert sorted([second, first]) == [first, second]
    for made in (first, second, new_invoice_id(t)):
        assert re.fullmatch(r"inv_[0-9a-f]{25}", made)


def test_new_invoice_id_layout_is_milliseconds_then_the_random_part() -> None:
    one_second = datetime(1970, 1, 1, 0, 0, 1, tzinfo=UTC)
    assert new_invoice_id(one_second, lambda n: "ab" * n) == "inv_00000000003e8" + "ab" * 6
    plus_a_millisecond = one_second + timedelta(milliseconds=1)
    assert new_invoice_id(plus_a_millisecond, lambda n: "00" * n).startswith("inv_00000000003e9")


def test_new_invoice_id_needs_an_aware_time() -> None:
    with pytest.raises(ValueError):
        new_invoice_id(datetime(2031, 1, 1, 12, 0))
