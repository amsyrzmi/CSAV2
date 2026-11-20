"""A compact action catalogue generated from the planner's contract (not used by the planner yet).

The planner's prompt still embeds the JSON schema: a prompt built from this catalogue was 40%
smaller and scored 479/495 against 475/495, but failed the smoke gate on two cases (see the
Decision section of the 2026-11-20 planner prompt restructure design). The generator is kept for
the next design round (area-scoped prompts).

``compact_catalogue`` turns ``PlannerOutput.model_json_schema()`` into one line per action (its
fields, ``?`` marking the optional ones, enums inline, nested models expanded) with the action's
docstring under it, then the notes of the nested models that carry rules. Pydantic docstrings
become schema ``description`` text and are load-bearing prompt text: ``RecipientSpec`` says a
group recipient needs ``group``, and a catalogue without it produced invalid contracts.

A schema construct the generator does not know raises instead of being dropped, so a new pydantic
shape cannot silently vanish from a prompt. This module takes a schema dict and does not import
the planner.
"""

from __future__ import annotations

import json
from typing import Any

# Nested models whose docstring goes into the prompt, in the order they are written.
NESTED_NOTES: tuple[str, ...] = ("BookingFreeAsk", "RecipientSpec", "RecordRef")
# Nested models that have a docstring but are deliberately not in the prompt.
NOTES_SKIPPED: frozenset[str] = frozenset()

# Keys the generator renders, or ignores because they only constrain a value (length, range).
_RENDERED = {
    "$ref",
    "const",
    "enum",
    "anyOf",
    "oneOf",
    "type",
    "items",
    "properties",
    "required",
    "format",
}
_IGNORED = {
    "description",
    "title",
    "default",
    "discriminator",
    "maxItems",
    "minItems",
    "maxLength",
    "minLength",
    "maximum",
    "minimum",
    "pattern",
}


def _check(node: dict[str, Any]) -> None:
    for key in node:
        if key == "additionalProperties" and node[key] is False:
            continue  # pydantic's forbid-extra setting: nothing to render
        if key not in _RENDERED and key not in _IGNORED:
            raise ValueError(f"planner catalogue cannot render {key}")
    if isinstance(node.get("type"), list):
        raise ValueError(f"planner catalogue cannot render a type list: {node['type']}")


def _type(node: dict[str, Any], defs: dict[str, Any], stack: tuple[str, ...]) -> str:
    _check(node)
    if "$ref" in node:
        name = node["$ref"].split("/")[-1]
        if name in stack:
            raise ValueError(f"planner catalogue: recursive model {name}")
        return _obj(defs[name], defs, (*stack, name))
    if "const" in node:
        return json.dumps(node["const"])
    if "enum" in node:
        return "|".join(json.dumps(v) for v in node["enum"])
    for key in ("anyOf", "oneOf"):
        if key in node:
            parts = [_type(n, defs, stack) for n in node[key] if n.get("type") != "null"]
            return "|".join(parts)
    kind = node.get("type")
    if kind == "array":
        return "[" + _type(node.get("items", {}), defs, stack) + "]"
    if node.get("format") == "date":
        return '"YYYY-MM-DD"'
    if kind == "object":
        return _obj(node, defs, stack) if "properties" in node else "object"
    return str(kind or "any")


def _obj(node: dict[str, Any], defs: dict[str, Any], stack: tuple[str, ...], skip: str = "") -> str:
    _check(node)
    required = set(node.get("required", []))
    fields = [
        f"{name}{'' if name in required else '?'}: {_type(spec, defs, stack)}"
        for name, spec in node.get("properties", {}).items()
        if name != skip
    ]
    return "{" + ", ".join(fields) + "}"


def _note(node: dict[str, Any]) -> str:
    return " ".join(node.get("description", "").split())


def compact_catalogue(schema: dict[str, Any]) -> str:
    """The catalogue text that would replace the prompt's JSON schema (see the module docstring)."""
    defs = schema["$defs"]
    mapping = schema["properties"]["proposal"]["discriminator"]["mapping"]
    lines = [
        'Return {"proposal": P}. P is ONE object with "action" set to one of the names below '
        "and only that action's fields; a field marked ? may be left out."
    ]
    for action, ref in mapping.items():
        name = ref.split("/")[-1]
        node = defs[name]
        note = _note(node)
        fields = _obj(node, defs, (name,), skip="action")
        # An empty "{}" made the model add a second key after "action" and abort (a
        # malformed-JSON 400 on inbox_summary), so a no-field action says so in words.
        lines.append(f"- {action}: {fields if fields != '{}' else 'no other fields'}")
        if note:
            lines.append(f"  {note}")
    for name in NESTED_NOTES:
        note = _note(defs.get(name, {}))
        if note:
            lines.append(f"{name}: {note}")
    return "\n".join(lines)
