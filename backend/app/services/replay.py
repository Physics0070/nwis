"""Telemetry replay engine.

Real eRTMAC access is not available, so the live feed is produced by replaying actual
stored Volve telemetry rows at a configurable speed. **No value is synthesised**: every
sample pushed to a client is a row that exists in the database, carrying its original
timestamp and depth.

Replay state lives here, on the server. The frontend renders whatever the backend reports
and never advances a clock of its own.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select

from backend.app.core.database import session_scope
from backend.app.models import TelemetrySample, Well
from nwis_common import get_config, get_logger

log = get_logger("nwis.replay")

STOPPED = "stopped"
RUNNING = "running"
PAUSED = "paused"
FINISHED = "finished"

SOURCE_LABEL = "Volve WITSML replay"


@dataclass
class ReplaySession:
    """One well's replay position. Owned entirely by the server."""

    well_id: int
    well_name: str
    samples: list[dict]
    status: str = STOPPED
    speed: float = 60.0
    index: int = 0
    subscribers: set = field(default_factory=set)
    task: asyncio.Task | None = None

    @property
    def total(self) -> int:
        return len(self.samples)

    @property
    def current(self) -> dict | None:
        if 0 <= self.index < self.total:
            return self.samples[self.index]
        return None

    def state(self) -> dict[str, Any]:
        current = self.current
        return {
            "well_id": self.well_id,
            "well_name": self.well_name,
            "status": self.status,
            "speed": self.speed,
            "current_index": self.index,
            "total_samples": self.total,
            "current_timestamp": current["recorded_at"] if current else None,
            "current_depth_m": current["bit_depth_m"] if current else None,
            "source": SOURCE_LABEL,
        }


class ReplayManager:
    """Holds the replay sessions for the process."""

    def __init__(self) -> None:
        self._sessions: dict[int, ReplaySession] = {}
        self._lock = asyncio.Lock()

    def _load_samples(self, well_id: int) -> tuple[str, list[dict]]:
        session = session_scope()
        try:
            well = session.get(Well, well_id)
            if well is None:
                raise LookupError(f"Well {well_id} not found")
            rows = (
                session.execute(
                    select(TelemetrySample)
                    .where(TelemetrySample.well_id == well_id)
                    .order_by(TelemetrySample.recorded_at)
                )
                .scalars()
                .all()
            )
            samples = [
                {
                    "recorded_at": row.recorded_at.isoformat() if row.recorded_at else None,
                    "bit_depth_m": row.bit_depth_m,
                    "hole_depth_m": row.hole_depth_m,
                    "channels": row.channels or {},
                    "circulating": row.circulating,
                    "rotating": row.rotating,
                    "tripping": row.tripping,
                    "operations_active": row.operations_active,
                }
                for row in rows
            ]
            return well.name, samples
        finally:
            session.close()

    async def get_or_create(self, well_id: int) -> ReplaySession:
        async with self._lock:
            existing = self._sessions.get(well_id)
            if existing is not None:
                return existing
            name, samples = await asyncio.to_thread(self._load_samples, well_id)
            if not samples:
                raise LookupError(
                    f"Well {well_id} has no telemetry to replay"
                )
            config = get_config()
            session = ReplaySession(
                well_id=well_id,
                well_name=name,
                samples=samples,
                speed=float(config.get("replay.default_speed")),
            )
            self._sessions[well_id] = session
            log.info("replay_session_created", well_id=well_id, samples=len(samples))
            return session

    def get(self, well_id: int) -> ReplaySession | None:
        return self._sessions.get(well_id)

    async def start(self, well_id: int, speed: float | None = None) -> ReplaySession:
        config = get_config()
        session = await self.get_or_create(well_id)
        if speed is not None:
            session.speed = _clamp_speed(speed, config)
        if session.status == RUNNING:
            return session
        if session.status in {STOPPED, FINISHED}:
            session.index = 0
        session.status = RUNNING
        if session.task is None or session.task.done():
            session.task = asyncio.create_task(self._run(session))
        log.info("replay_started", well_id=well_id, speed=session.speed)
        return session

    async def pause(self, well_id: int) -> ReplaySession:
        session = self._require(well_id)
        if session.status == RUNNING:
            session.status = PAUSED
        return session

    async def resume(self, well_id: int) -> ReplaySession:
        session = self._require(well_id)
        if session.status == PAUSED:
            session.status = RUNNING
            if session.task is None or session.task.done():
                session.task = asyncio.create_task(self._run(session))
        return session

    async def stop(self, well_id: int) -> ReplaySession:
        session = self._require(well_id)
        session.status = STOPPED
        session.index = 0
        if session.task is not None:
            session.task.cancel()
            session.task = None
        return session

    async def set_speed(self, well_id: int, speed: float) -> ReplaySession:
        session = self._require(well_id)
        session.speed = _clamp_speed(speed, get_config())
        return session

    async def seek(self, well_id: int, index: int) -> ReplaySession:
        session = self._require(well_id)
        session.index = max(0, min(index, session.total - 1))
        return session

    def _require(self, well_id: int) -> ReplaySession:
        session = self._sessions.get(well_id)
        if session is None:
            raise LookupError(f"No replay session for well {well_id}")
        return session

    async def _run(self, session: ReplaySession) -> None:
        """Advance the replay position and broadcast each sample.

        Wall-clock interval is derived from the real spacing of the stored samples
        divided by the replay speed, so a 10-second cadence at speed 60 emits roughly
        every 0.17 s.
        """
        config = get_config()
        cadence = float(config.get("volve_pipeline.resample_seconds"))
        try:
            while session.status in {RUNNING, PAUSED}:
                if session.status == PAUSED:
                    await asyncio.sleep(0.2)
                    continue
                if session.index >= session.total:
                    session.status = FINISHED
                    await self.broadcast(session, event="finished")
                    break

                await self.broadcast(session, event="sample")
                session.index += 1
                await asyncio.sleep(max(0.01, cadence / max(session.speed, 1e-6)))
        except asyncio.CancelledError:  # pragma: no cover - task teardown
            raise
        except Exception as exc:  # pragma: no cover - defensive
            log.error("replay_failed", well_id=session.well_id, error=str(exc))
            session.status = STOPPED

    async def broadcast(self, session: ReplaySession, *, event: str) -> None:
        if not session.subscribers:
            return
        message = {
            "event": event,
            "state": session.state(),
            "sample": session.current,
        }
        dead = []
        for websocket in list(session.subscribers):
            try:
                await websocket.send_json(message)
            except Exception:
                dead.append(websocket)
        for websocket in dead:
            session.subscribers.discard(websocket)


def _clamp_speed(speed: float, config) -> float:
    minimum = float(config.get("replay.min_speed"))
    maximum = float(config.get("replay.max_speed"))
    return max(minimum, min(float(speed), maximum))


manager = ReplayManager()
