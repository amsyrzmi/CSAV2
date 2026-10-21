"""Keep the published frontend contract synchronized with actual Pydantic routes."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from csa.console.kb_contracts import public_job
from scripts.export_console_openapi import OUTPUT, contract


def test_published_console_contract_matches_routes() -> None:
    assert json.loads(OUTPUT.read_text(encoding="utf-8")) == contract(), (
        "Run python -m scripts.export_console_openapi and review the contract changes"
    )


def test_workspace_request_schema_references_resolve_in_openapi() -> None:
    document = contract()
    schema = document["paths"]["/console/v1/workspace/conversations/{thread_id}/turns"]["post"][
        "requestBody"
    ]["content"]["application/json"]["schema"]

    def check(value: object) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "$ref":
                    assert isinstance(item, str) and item.startswith("#/")
                    resolved = document
                    for part in item[2:].split("/"):
                        resolved = resolved[part.replace("~1", "/").replace("~0", "~")]
                    assert isinstance(resolved, dict)
                else:
                    check(item)
        elif isinstance(value, list):
            for item in value:
                check(item)

    check(schema)


def test_kb_job_projection_omits_internal_paths_requesters_and_metadata() -> None:
    job = public_job(
        {
            "id": uuid4(),
            "source": "upload",
            "status": "failed",
            "attempts": 1,
            "files": ["/private/server/source.csv"],
            "requested_by": "internal-requester",
            "meta": {
                "private_setting": "not-for-browser",
                "results": [
                    {
                        "file": "/private/server/source.csv",
                        "path": "/private/server/source.csv",
                        "status": "failed",
                        "error": "cannot open /private/server/source.csv",
                    }
                ],
            },
            "error": "cannot open /private/server/source.csv",
            "created_at": datetime.now(UTC),
            "started_at": None,
            "finished_at": None,
        }
    )
    encoded = job.model_dump_json()
    for internal in ["/private", '"path"', "internal-requester", "not-for-browser"]:
        assert internal not in encoded
    assert job.files[0].name == "source.csv"
    assert job.meta.results[0].file == "source.csv"
    assert job.meta.results[0].error == "File processing failed; inspect the server logs."
