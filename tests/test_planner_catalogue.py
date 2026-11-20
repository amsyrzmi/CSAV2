"""The compact action catalogue the planner prompt is built from (generated from the contract)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from csa.workspace.planner import PlannerOutput
from csa.workspace.planner_catalogue import NESTED_NOTES, NOTES_SKIPPED, compact_catalogue

SCHEMA = PlannerOutput.model_json_schema()
MAPPING = SCHEMA["properties"]["proposal"]["discriminator"]["mapping"]


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _def(ref: str) -> dict[str, Any]:
    return SCHEMA["$defs"][ref.split("/")[-1]]


def _tiny(**fields: dict[str, Any]) -> dict[str, Any]:
    """A one-action schema in the shape pydantic emits, for notation tests."""
    return {
        "properties": {"proposal": {"discriminator": {"mapping": {"act": "#/$defs/Act"}}}},
        "$defs": {
            "Act": {
                "type": "object",
                "properties": {"action": {"const": "act"}, **fields},
                "required": ["action", *[n for n, f in fields.items() if f.pop("_req", False)]],
            }
        },
    }


def test_every_action_is_listed_with_every_field() -> None:
    catalogue = compact_catalogue(SCHEMA)
    blocks = {b.split(":", 1)[0].lstrip("- "): b for b in catalogue.split("\n- ")}
    for action, ref in MAPPING.items():
        assert action in blocks, f"{action} is missing from the catalogue"
        line = blocks[action].split("\n", 1)[0]
        for field in _def(ref)["properties"]:
            if field != "action":
                assert field in line, f"{action}.{field} is missing from the catalogue"


def test_optional_fields_are_marked_and_required_are_not() -> None:
    schema = _tiny(name={"type": "string", "_req": True}, note={"type": "string"})
    assert "- act: {name: string, note?: string}" in compact_catalogue(schema)


def test_action_docstring_follows_its_line() -> None:
    catalogue = compact_catalogue(SCHEMA)
    described = [ref for ref in MAPPING.values() if _def(ref).get("description")]
    assert described
    for ref in described:
        assert _collapse(_def(ref)["description"]) in catalogue


def test_nested_notes_are_present() -> None:
    catalogue = compact_catalogue(SCHEMA)
    for name in NESTED_NOTES:
        assert _collapse(SCHEMA["$defs"][name]["description"]) in catalogue


def test_every_described_nested_model_is_decided() -> None:
    action_defs = {ref.split("/")[-1] for ref in MAPPING.values()}
    for name, node in SCHEMA["$defs"].items():
        if name in action_defs or not node.get("description"):
            continue
        assert name in NESTED_NOTES or name in NOTES_SKIPPED, (
            f"{name} has a docstring that may carry a prompt rule: add it to NESTED_NOTES "
            "(it goes into the prompt) or NOTES_SKIPPED (it does not) in planner_catalogue.py"
        )


def test_enums_dates_and_nullables_render() -> None:
    schema = _tiny(
        kind={"enum": ["a", "b"]},
        day={"type": "string", "format": "date"},
        maybe={"anyOf": [{"type": "string"}, {"type": "null"}]},
        rows={
            "type": "array",
            "items": {"type": "object", "properties": {"n": {"type": "integer"}}},
        },
    )
    line = compact_catalogue(schema)
    assert 'kind?: "a"|"b"' in line
    assert 'day?: "YYYY-MM-DD"' in line
    assert "maybe?: string" in line and "null" not in line
    assert "rows?: [{n?: integer}]" in line


@pytest.mark.parametrize(
    ("node", "needle"),
    [
        ({"allOf": [{"type": "string"}]}, "allOf"),
        ({"type": ["string", "null"]}, "type list"),
        ({"type": "object", "additionalProperties": {"type": "string"}}, "additionalProperties"),
        ({"prefixItems": [{"type": "string"}]}, "prefixItems"),
    ],
)
def test_unknown_nodes_raise(node: dict[str, Any], needle: str) -> None:
    with pytest.raises(ValueError, match=needle):
        compact_catalogue(_tiny(odd=node))


def test_self_reference_raises() -> None:
    schema = _tiny(me={"$ref": "#/$defs/Act"})
    with pytest.raises(ValueError, match="recursive"):
        compact_catalogue(schema)


def test_real_schema_is_smaller_than_json() -> None:
    assert len(compact_catalogue(SCHEMA)) < len(json.dumps(SCHEMA, separators=(",", ":"))) * 0.7


def test_an_action_with_no_fields_says_so_instead_of_an_empty_object() -> None:
    """``{}`` made the model write a second key after ``action`` and then close the object (a
    malformed-JSON 400 on ``inbox_summary``); the old JSON schema had said it plainly."""
    catalogue = compact_catalogue(_tiny())
    assert "- act: no other fields" in catalogue
    assert "{}" not in catalogue
