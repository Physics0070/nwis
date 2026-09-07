"""Replay control endpoints and the live telemetry WebSocket."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect

from backend.app.schemas.models import ReplayState
from backend.app.services.replay import manager
from nwis_common import get_logger

log = get_logger("nwis.api.replay")

router = APIRouter(prefix="/api/replay", tags=["replay"])
ws_router = APIRouter()


def _state(session) -> ReplayState:
    return ReplayState(**session.state())


@router.post("/{well_id}/start", response_model=ReplayState)
async def start(well_id: int, speed: float | None = Query(default=None, gt=0)):
    try:
        session = await manager.start(well_id, speed)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _state(session)


@router.post("/{well_id}/pause", response_model=ReplayState)
async def pause(well_id: int):
    try:
        return _state(await manager.pause(well_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/{well_id}/resume", response_model=ReplayState)
async def resume(well_id: int):
    try:
        return _state(await manager.resume(well_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/{well_id}/stop", response_model=ReplayState)
async def stop(well_id: int):
    try:
        return _state(await manager.stop(well_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/{well_id}/speed", response_model=ReplayState)
async def set_speed(well_id: int, speed: float = Query(..., gt=0)):
    try:
        return _state(await manager.set_speed(well_id, speed))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/{well_id}/seek", response_model=ReplayState)
async def seek(well_id: int, index: int = Query(..., ge=0)):
    try:
        return _state(await manager.seek(well_id, index))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/{well_id}", response_model=ReplayState)
async def state(well_id: int):
    session = manager.get(well_id)
    if session is None:
        try:
            session = await manager.get_or_create(well_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
    return _state(session)


@ws_router.websocket("/ws/telemetry/{well_id}")
async def telemetry_socket(websocket: WebSocket, well_id: int):
    """Stream replayed telemetry for one well.

    The client receives the current server-side replay state on connect, then a message
    per sample. All state transitions are driven by the REST control endpoints, so
    multiple viewers stay consistent.
    """
    await websocket.accept()
    try:
        session = await manager.get_or_create(well_id)
    except LookupError as exc:
        await websocket.send_json({"event": "error", "detail": str(exc)})
        await websocket.close()
        return

    session.subscribers.add(websocket)
    await websocket.send_json(
        {"event": "connected", "state": session.state(), "sample": session.current}
    )
    try:
        while True:
            # Keeps the connection open; control happens over REST.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # pragma: no cover - transport level
        log.warning("websocket_error", well_id=well_id, error=str(exc))
    finally:
        session.subscribers.discard(websocket)
