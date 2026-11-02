"""Leaf types for the people directory, kept import-light so other HR modules can share them."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from csa.hr.models import HrModel
from csa.hr.workflows import Region

EmploymentType = Literal["full_time", "part_time", "contract", "intern"]

# Personal details: the numbers kept encrypted, and the contact fields the person edits themself.
EncryptedField = Literal[
    "ic_or_passport", "bank_account", "tax_number", "epf_number", "socso_number"
]
SelfField = Literal[
    "home_address", "personal_phone", "emergency_name", "emergency_relationship", "emergency_phone"
]


class EmploymentProfile(HrModel):
    region: Region
    legal_entity_reference: str = Field(min_length=1, max_length=100)
    employment_category: str = Field(min_length=1, max_length=100)
