"""The training library's console routes: list, add, edit, remove and open an item.

Every route belongs to the ``training_library`` package and to human sessions only. Who may
manage is decided per request in ``access.can_manage``; reading is open to every member of the
company, ``read_only`` included.
"""

# No ``from __future__ import annotations``: the store dependency is a closure here, and FastAPI
# resolves string annotations in module globals only.

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from csa.console.access import tenant
from csa.console.contracts import ConsoleActor
from csa.console.identity import IdentityService
from csa.console.kb_uploads import human
from csa.console.services import package_gate
from csa.storage.base import ObjectStore
from csa.training.models import ItemList, ItemOut, ItemPatch
from csa.training.service import MEDIA_TYPE, STORAGE_UNAVAILABLE, TrainingLibrary

Actor = Annotated[ConsoleActor, Depends(human)]
Company = Annotated[UUID, Depends(tenant)]

_UPLOAD_BODY = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {"file": {"type": "string", "format": "binary"}},
                }
            }
        },
    }
}
_PDF_BODY: dict[int | str, dict[str, Any]] = {
    200: {"content": {MEDIA_TYPE: {"schema": {"type": "string", "format": "binary"}}}}
}


def build_training_router(store: ObjectStore | None) -> APIRouter:
    # Human first, so a machine principal is refused before the package is looked up.
    router = APIRouter(dependencies=[Depends(human), Depends(package_gate("training_library"))])

    def library(request: Request) -> TrainingLibrary:
        if store is None:
            raise HTTPException(503, STORAGE_UNAVAILABLE)
        identity = getattr(request.app.state, "identity_service", None)
        return TrainingLibrary(
            request.app.state.pool,
            store,
            identity_pool=identity.pool if isinstance(identity, IdentityService) else None,
        )

    @router.get("/training/items", response_model=ItemList)
    async def list_items(
        actor: Actor,
        client_id: Company,
        lib: Annotated[TrainingLibrary, Depends(library)],
    ) -> ItemList:
        return await lib.list_items(actor, client_id)

    @router.post(
        "/training/items", response_model=ItemOut, status_code=201, openapi_extra=_UPLOAD_BODY
    )
    async def add_item(
        request: Request,
        actor: Actor,
        client_id: Company,
        lib: Annotated[TrainingLibrary, Depends(library)],
        idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
    ) -> ItemOut:
        return await lib.add_item(actor, client_id, request, idempotency_key)

    @router.patch("/training/items/{item_id}", response_model=ItemOut)
    async def patch_item(
        item_id: UUID,
        body: ItemPatch,
        actor: Actor,
        client_id: Company,
        lib: Annotated[TrainingLibrary, Depends(library)],
    ) -> ItemOut:
        return await lib.patch_item(actor, client_id, item_id, body)

    @router.delete("/training/items/{item_id}", status_code=204)
    async def remove_item(
        item_id: UUID,
        actor: Actor,
        client_id: Company,
        lib: Annotated[TrainingLibrary, Depends(library)],
    ) -> None:
        await lib.remove_item(actor, client_id, item_id)

    @router.get(
        "/training/items/{item_id}/file", response_class=StreamingResponse, responses=_PDF_BODY
    )
    async def open_file(
        item_id: UUID,
        client_id: Company,
        lib: Annotated[TrainingLibrary, Depends(library)],
    ) -> StreamingResponse:
        return await lib.open_file(client_id, item_id)

    return router
