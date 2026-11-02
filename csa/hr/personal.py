"""Personal details: the masked read, the audited Show and the encrypted save.

The IC or passport, bank account, tax, EPF and SOCSO numbers are encrypted here (AES-GCM, bound to
the table, column and person) before they reach the database, and decrypted only to mask or to Show.
The database decides who may read or save what (`hr_personal_*`); this module never logs, stores,
returns in an error or puts in a receipt any personal value.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import date
from typing import Any, Literal, get_args
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from pydantic import ValidationInfo, field_validator

from csa.core import crypto
from csa.core.db import q as sql
from csa.core.settings import get_settings
from csa.core.tenancy import tx
from csa.hr.automation_models import (
    OperationReceipt,
    PersonalSave,
    personal_text_without_controls,
)
from csa.hr.models import HrModel, Version
from csa.hr.operations import HrOperations
from csa.hr.people_types import EncryptedField, SelfField
from csa.hr.policy import HrPolicyError
from csa.hr.records import HrSession
from csa.hr.service import HrService

__all__ = [
    "EncryptedField",
    "HrPersonal",
    "PersonalOut",
    "PersonalReveal",
    "PersonalRevealIn",
    "SelfField",
    "fingerprint_payload",
    "mask",
    "personal_fingerprint",
]

_TABLE = "hr_employee_personal"
_ENCRYPTED: tuple[str, ...] = get_args(EncryptedField)
_DOTS = "••••"


def mask(value: str | None) -> str | None:
    """A number as the page shows it: only its last four characters."""
    if value is None:
        return None
    return _DOTS if len(value) <= 4 else f"{_DOTS} {value[-4:]}"


class PersonalOut(HrModel):
    date_of_birth: date | None
    home_address: str | None
    personal_phone: str | None
    emergency_name: str | None
    emergency_relationship: str | None
    emergency_phone: str | None
    bank_name: str | None
    # The numbers, masked. The full value only comes from the audited reveal.
    ic_or_passport: str | None
    bank_account: str | None
    tax_number: str | None
    epf_number: str | None
    socso_number: str | None
    version: Version | None  # None until the first save
    can_edit: Literal["all", "contact"]

    @field_validator("*", mode="after")
    @classmethod
    def reject_controls(  # pyright: ignore[reportIncompatibleMethodOverride]
        cls, value: object, info: ValidationInfo
    ) -> object:
        return personal_text_without_controls(value, info.field_name)


class PersonalRevealIn(HrModel):
    field: EncryptedField


class PersonalReveal(HrModel):
    value: str | None


def _aad(column: str, employee_id: UUID) -> bytes:
    return crypto.credential_aad(_TABLE, column, str(employee_id))


def fingerprint_payload(body: PersonalSave) -> str:
    """The canonical text of a save, plaintext numbers included. Only ever hashed with a key."""
    payload = {
        "operation": body.operation,
        "employee_id": str(body.employee_id),
        "expected_version": body.expected_version,
        # Only the keys that were sent: absent keeps a value and null clears it.
        "values": body.values.model_dump(mode="json", exclude_unset=True),
        "encrypted": {
            field: None if secret is None else secret.get_secret_value()
            for field, secret in body.encrypted.items()
        },
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def personal_fingerprint(body: PersonalSave) -> str:
    """HMAC-SHA256 of the save under a key derived from CREDENTIALS_KEY. A plain hash would let
    anyone with the database guess an IC number from a birth date."""
    subkey = hmac.new(
        get_settings().credentials_key_bytes(), b"hr-personal-fingerprint", hashlib.sha256
    ).digest()
    return hmac.new(subkey, fingerprint_payload(body).encode(), hashlib.sha256).hexdigest()


class HrPersonal:
    def __init__(self, core: HrService):
        self.core = core

    async def get(self, session: HrSession, employee_id: UUID) -> PersonalOut:
        async with tx(self.core.pool, str(session.tenant_id)) as conn:
            await self.core._access(conn, session)
            rows = await sql.fetch_all(
                "hr/personal_row.sql", (session.tenant_id, employee_id), conn=conn
            )
            await self.core._session(conn, session)
        if not rows:
            # No access, or no such person: the same answer.
            raise HrPolicyError("resource_unavailable")
        row = rows[0]
        shown: dict[str, Any] = {
            column: mask(
                None
                if row[column] is None
                else crypto.decrypt(row[column], _aad(column, employee_id))
            )
            for column in _ENCRYPTED
        }
        return PersonalOut(
            date_of_birth=row["date_of_birth"],
            home_address=row["home_address"],
            personal_phone=row["personal_phone"],
            emergency_name=row["emergency_name"],
            emergency_relationship=row["emergency_relationship"],
            emergency_phone=row["emergency_phone"],
            bank_name=row["bank_name"],
            **shown,
            version=row["version"],
            can_edit="all" if row["access"] == "hr" else "contact",
        )

    async def reveal(
        self, session: HrSession, employee_id: UUID, field: EncryptedField
    ) -> str | None:
        """One number in full, to the HR owner, a Sensitive details holder or the person. The
        database writes the audit row in the same transaction, so a Show is never unrecorded."""
        async with tx(self.core.pool, str(session.tenant_id)) as conn:
            await self.core._access(conn, session)
            row = await HrOperations._people_apply(
                conn, "hr/personal_reveal.sql", (session.tenant_id, employee_id, field)
            )
            await self.core._session(conn, session)
        token = row["ciphertext"]
        return None if token is None else crypto.decrypt(token, _aad(field, employee_id))

    async def save(self, session: HrSession, body: PersonalSave, key: UUID) -> OperationReceipt:
        # The fingerprint covers the request as sent, so a retry matches whatever is trimmed below.
        fingerprint = personal_fingerprint(body)
        async with tx(self.core.pool, str(session.tenant_id)) as conn:
            await self.core._access(conn, session)
            changed = await self._changed_numbers(conn, session, body)
            sent = {
                "operation": body.operation,
                "employee_id": str(body.employee_id),
                "expected_version": body.expected_version,
                "values": body.values.model_dump(mode="json", exclude_unset=True),
                "encrypted": {
                    field: None
                    if plain is None
                    else crypto.encrypt(plain, _aad(field, body.employee_id))
                    for field, plain in changed.items()
                },
            }
            row = await HrOperations._people_apply(
                conn,
                "hr/personal_apply.sql",
                (session.tenant_id, key, fingerprint, Jsonb(sent)),
            )
            await self.core._session(conn, session)
        return OperationReceipt.model_validate(row["receipt"])

    async def _changed_numbers(
        self, conn: AsyncConnection, session: HrSession, body: PersonalSave
    ) -> dict[str, str | None]:
        """The numbers to write: those sent, less any equal to what is stored. Every encryption
        has a fresh nonce, so an unchanged number would otherwise count as a change. Only for HR:
        the person themself may send no number, and the database refuses it."""
        sent = {
            field: None if secret is None else secret.get_secret_value()
            for field, secret in body.encrypted.items()
        }
        if not any(plain is not None for plain in sent.values()):
            return sent
        rows = await sql.fetch_all(
            "hr/personal_row.sql", (session.tenant_id, body.employee_id), conn=conn
        )
        if not rows or rows[0]["access"] != "hr":
            return sent
        stored = rows[0]
        kept: dict[str, str | None] = {}
        for field, plain in sent.items():
            if plain is not None and stored[field] is not None:
                try:
                    if crypto.decrypt(stored[field], _aad(field, body.employee_id)) == plain:
                        continue
                except crypto.CryptoError:
                    pass  # unreadable: write the new value
            kept[field] = plain
        return kept
