"""The training library's video routes: start, piece, resume status, complete, and retry.

Same dependencies as the library's routes (human sessions, the ``training_library`` package); every
route is for managers (``access.can_manage``). The routes always exist: without a training store
they answer 503 ``training_storage_unavailable``, and while ``TRAINING_VIDEO_ENABLED`` is off the
upload routes answer 503 ``training_video_unavailable``. Removing an upload is the library's
``DELETE /training/items/{id}``. Retrying a failed video queues its job again for the training
worker (``video_jobs.retry``).
"""

# No ``from __future__ import annotations``: the store dependency is a closure here, and FastAPI
# resolves string annotations in module globals only.

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from csa.console.access import tenant
from csa.console.contracts import ConsoleActor
from csa.console.identity import IdentityService
from csa.console.kb_uploads import human
from csa.console.services import package_gate
from csa.storage.base import ObjectStore
from csa.storage.multipart import MultipartStore
from csa.training import video_jobs
from csa.training.models import (
    ItemOut,
    PieceOut,
    PlaybackOut,
    TranscriptOut,
    VideoStart,
    VideoUploadOut,
)
from csa.training.service import STORAGE_UNAVAILABLE, VIDEO_UNAVAILABLE, TrainingLibrary
from csa.training.video_playback import VideoPlayback
from csa.training.video_upload import VideoUploads

Actor = Annotated[ConsoleActor, Depends(human)]
Company = Annotated[UUID, Depends(tenant)]

_PIECE_BODY: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
    }
}
NO_STORE = "private, no-store"
_VIDEO_TYPES = {
    media_type: {"schema": {"type": "string", "format": "binary"}}
    for media_type in ("video/mp4", "video/webm")
}
_VIDEO_BODY: dict[int | str, dict[str, Any]] = {
    200: {"content": _VIDEO_TYPES},
    206: {"description": "The requested range", "content": _VIDEO_TYPES},
    416: {"description": "The range is outside the file, or several were asked for"},
}


def build_training_video_router(store: ObjectStore | None) -> APIRouter:
    # Human first, so a machine principal is refused before the package is looked up.
    router = APIRouter(dependencies=[Depends(human), Depends(package_gate("training_library"))])

    def uploads(request: Request) -> VideoUploads:
        if store is None or not isinstance(store, MultipartStore):
            raise HTTPException(503, STORAGE_UNAVAILABLE)
        # Set from TRAINING_VIDEO_ENABLED at gateway startup; absent means off.
        if getattr(request.app.state, "training_video_enabled", False) is not True:
            raise HTTPException(503, VIDEO_UNAVAILABLE)
        identity = getattr(request.app.state, "identity_service", None)
        return VideoUploads(
            request.app.state.pool,
            store,
            store,
            identity_pool=identity.pool if isinstance(identity, IdentityService) else None,
        )

    def library(request: Request) -> TrainingLibrary:
        if store is None:
            raise HTTPException(503, STORAGE_UNAVAILABLE)
        identity = getattr(request.app.state, "identity_service", None)
        return TrainingLibrary(
            request.app.state.pool,
            store,
            identity_pool=identity.pool if isinstance(identity, IdentityService) else None,
        )

    def playback(request: Request) -> VideoPlayback:
        if store is None or not isinstance(store, MultipartStore):
            raise HTTPException(503, STORAGE_UNAVAILABLE)
        return VideoPlayback(request.app.state.pool, store)

    @router.post("/training/videos", response_model=VideoUploadOut, status_code=201)
    async def start_video(
        body: VideoStart,
        actor: Actor,
        client_id: Company,
        videos: Annotated[VideoUploads, Depends(uploads)],
        idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
    ) -> VideoUploadOut:
        return await videos.start(actor, client_id, body, idempotency_key)

    @router.put(
        "/training/videos/{item_id}/pieces/{number}",
        response_model=PieceOut,
        openapi_extra=_PIECE_BODY,
    )
    async def put_piece(
        item_id: UUID,
        number: int,
        request: Request,
        actor: Actor,
        client_id: Company,
        videos: Annotated[VideoUploads, Depends(uploads)],
        piece_sha256: Annotated[str, Header(alias="X-Piece-SHA256", max_length=128)],
    ) -> PieceOut:
        return await videos.put_piece(actor, client_id, item_id, number, request, piece_sha256)

    @router.get("/training/videos/{item_id}/upload", response_model=VideoUploadOut)
    async def upload_status(
        item_id: UUID,
        actor: Actor,
        client_id: Company,
        videos: Annotated[VideoUploads, Depends(uploads)],
    ) -> VideoUploadOut:
        return await videos.received(actor, client_id, item_id)

    @router.post("/training/videos/{item_id}/complete", response_model=ItemOut)
    async def complete_video(
        item_id: UUID,
        actor: Actor,
        client_id: Company,
        videos: Annotated[VideoUploads, Depends(uploads)],
    ) -> ItemOut:
        return await videos.complete(actor, client_id, item_id)

    @router.post("/training/items/{item_id}/retry", response_model=ItemOut)
    async def retry_video(
        item_id: UUID,
        actor: Actor,
        client_id: Company,
        lib: Annotated[TrainingLibrary, Depends(library)],
    ) -> ItemOut:
        return await video_jobs.retry(lib, actor, client_id, item_id)

    @router.get("/training/items/{item_id}/playback", response_model=PlaybackOut)
    async def get_playback(
        response: Response,
        item_id: UUID,
        actor: Actor,
        client_id: Company,
        plays: Annotated[VideoPlayback, Depends(playback)],
    ) -> PlaybackOut:
        # The URL is a bearer link: no cache keeps it.
        response.headers["Cache-Control"] = NO_STORE
        return await plays.playback(actor, client_id, item_id)

    @router.get(
        "/training/items/{item_id}/video",
        response_class=StreamingResponse,
        responses=_VIDEO_BODY,
    )
    async def get_video(
        item_id: UUID,
        actor: Actor,
        client_id: Company,
        plays: Annotated[VideoPlayback, Depends(playback)],
        range_header: Annotated[str | None, Header(alias="Range")] = None,
    ) -> StreamingResponse:
        return await plays.open_video(actor, client_id, item_id, range_header)

    @router.get("/training/items/{item_id}/transcript", response_model=TranscriptOut)
    async def get_transcript(
        response: Response,
        item_id: UUID,
        actor: Actor,
        client_id: Company,
        plays: Annotated[VideoPlayback, Depends(playback)],
    ) -> TranscriptOut:
        response.headers["Cache-Control"] = NO_STORE
        return await plays.transcript(actor, client_id, item_id)

    return router
