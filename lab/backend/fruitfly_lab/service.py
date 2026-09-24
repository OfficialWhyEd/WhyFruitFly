"""Lifecycle and message bridge for the dedicated engine process."""

from __future__ import annotations

import asyncio
import multiprocessing as mp
import queue
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Awaitable, Callable

from .worker import run_engine_worker

SHUTDOWN_TIMEOUT_S = 180.0

Subscriber = Callable[[dict[str, Any]], Awaitable[None]]


class EngineService:
    def __init__(self, archive_dir: Path | None = None) -> None:
        context = mp.get_context("spawn")
        self.commands = context.Queue(maxsize=128)
        self.events = context.Queue(maxsize=16)
        self.process = context.Process(
            target=run_engine_worker,
            args=(self.commands, self.events),
            kwargs={"archive_dir": str(archive_dir) if archive_dir else None},
            name="fruitfly-engine",
        )
        self.recording_run_id: str | None = None
        self.last_archive_event: dict[str, Any] | None = None
        self.subscribers: set[Subscriber] = set()
        self.recent_request_ids: deque[str] = deque(maxlen=1024)
        self.metadata: dict[str, Any] | None = None
        self.latest_snapshot: dict[str, Any] | None = None
        self.last_heartbeat: dict[str, Any] | None = None
        self.fatal_error: str | None = None
        self._pump_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if not self.process.is_alive():
            self.process.start()
        self._pump_task = asyncio.create_task(self._pump(), name="engine-event-pump")

    async def close(self) -> None:
        if self.process.is_alive():
            try:
                self.commands.put_nowait(
                    {"type": "shutdown", "request_id": str(uuid.uuid4())}
                )
            except queue.Full:
                pass
            # Sealing a long run (Parquet close, MCAP re-read, hashes) can take a while.
            await asyncio.to_thread(self.process.join, SHUTDOWN_TIMEOUT_S)
            if self.process.is_alive():
                self.process.terminate()
                await asyncio.to_thread(self.process.join, 2.0)
        if self._pump_task is not None:
            self._pump_task.cancel()
            await asyncio.gather(self._pump_task, return_exceptions=True)

    def send_command(self, kind: str, request_id: str) -> bool:
        if request_id in self.recent_request_ids:
            return False
        self.commands.put_nowait({"type": kind, "request_id": request_id})
        self.recent_request_ids.append(request_id)
        return True

    async def _pump(self) -> None:
        while True:
            try:
                message = await asyncio.to_thread(self.events.get, True, 0.5)
            except queue.Empty:
                if self.process.exitcode not in (None, 0):
                    self.fatal_error = f"engine exited with code {self.process.exitcode}"
                continue
            kind = message.get("type")
            if kind == "metadata":
                self.metadata = message["payload"]
            elif kind == "snapshot":
                self.latest_snapshot = message["payload"]
            elif kind == "heartbeat":
                self.last_heartbeat = message
                self.recording_run_id = message.get("recording_run_id")
            elif kind == "archive":
                self.last_archive_event = message
                if message.get("event") == "recording":
                    self.recording_run_id = message.get("run_id")
                elif message.get("event") in ("sealed", "failed"):
                    self.recording_run_id = None
            elif kind == "fatal":
                self.fatal_error = message.get("error", "unknown engine error")
            dead: list[Subscriber] = []
            for subscriber in tuple(self.subscribers):
                try:
                    await subscriber(message)
                except Exception:
                    dead.append(subscriber)
            for subscriber in dead:
                self.subscribers.discard(subscriber)

