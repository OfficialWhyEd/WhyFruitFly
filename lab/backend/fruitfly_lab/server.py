"""Local-only FastAPI server and authenticated WebSocket control channel."""

from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import socket
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import qrcode
import uvicorn
from fastapi import Cookie, FastAPI, WebSocket, WebSocketDisconnect, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from .archive.catalog import Catalog
from .archive.recorder import DEFAULT_MARGIN_BYTES, free_bytes
from .service import EngineService

DEFAULT_PORT = 8765
DEFAULT_ARCHIVE_DIR = Path(__file__).resolve().parents[2] / "data" / "archive"
SESSION_COOKIE = "fruitfly_session"
ALLOWED_COMMANDS = {"start", "pause", "stop", "reset"}


class CommandMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str
    request_id: uuid.UUID


def discover_lan_ip() -> str:
    candidates = socket.gethostbyname_ex(socket.gethostname())[2]
    private = [
        ip
        for ip in candidates
        if ip.startswith("192.168.")
        or ip.startswith("10.")
        or ip.startswith("172.16.")
        or ip.startswith("172.17.")
        or ip.startswith("172.18.")
        or ip.startswith("172.19.")
        or ip.startswith("172.2")
        or ip.startswith("172.3")
    ]
    return private[0] if private else "127.0.0.1"


def write_connection_info(output_dir: Path, join_url: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "connection.json").write_text(
        json.dumps({"join_url": join_url}, indent=2), encoding="utf-8"
    )
    qrcode.make(join_url).save(output_dir / "connection-qr.png")


def create_app(
    *,
    start_worker: bool = True,
    session_token: str | None = None,
    archive_dir: Path | None = DEFAULT_ARCHIVE_DIR,
) -> FastAPI:
    token = session_token or secrets.token_urlsafe(9)
    service = EngineService(archive_dir) if start_worker else None
    frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if service is not None:
            await service.start()
        yield
        if service is not None:
            await service.close()

    app = FastAPI(title="Fruit Fly Lab", version="0.1.0", lifespan=lifespan)
    app.state.session_token = token
    app.state.engine_service = service
    if (frontend_dist / "assets").is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=frontend_dist / "assets"),
            name="frontend-assets",
        )

    @app.get("/join/{candidate}", include_in_schema=False)
    async def join(candidate: str) -> RedirectResponse:
        if not secrets.compare_digest(candidate, token):
            return RedirectResponse(url="/?auth=failed", status_code=303)
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie(
            SESSION_COOKIE,
            token,
            httponly=True,
            samesite="strict",
            secure=False,
            max_age=12 * 60 * 60,
        )
        return response

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def home(session: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
        authenticated = session is not None and secrets.compare_digest(session, token)
        if authenticated and (frontend_dist / "index.html").is_file():
            return FileResponse(frontend_dist / "index.html")
        state = "collegato" if authenticated else "serve il link completo mostrato sul PC"
        return HTMLResponse(f"""<!doctype html><html lang='it'><meta name='viewport' content='width=device-width,initial-scale=1'>
        <title>Fruit Fly Lab</title><body style='font-family:system-ui;background:#f5f0e6;color:#151515;padding:32px'>
        <h1>Fruit Fly Lab</h1><p>Stato accesso: <strong>{state}</strong></p>
        <p>Il laboratorio 3D e in costruzione. Questa pagina conferma che il telefono raggiunge il PC.</p>
        </body></html>""")

    @app.get("/api/status")
    async def api_status(
        session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> JSONResponse:
        if session is None or not secrets.compare_digest(session, token):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        if service is None:
            return JSONResponse({"engine": "test", "worker_alive": False})
        return JSONResponse(
            {
                "engine": (
                    service.latest_snapshot.get("status")
                    if service.latest_snapshot
                    else "starting"
                ),
                "worker_alive": service.process.is_alive(),
                "fatal_error": service.fatal_error,
                "latest_sequence": (
                    service.latest_snapshot.get("sequence")
                    if service.latest_snapshot
                    else None
                ),
            }
        )

    def authorized(session: str | None) -> bool:
        return session is not None and secrets.compare_digest(session, token)

    def open_catalog() -> Catalog | None:
        if archive_dir is None or not (Path(archive_dir) / "catalog.sqlite").is_file():
            return None
        return Catalog(Path(archive_dir) / "catalog.sqlite", read_only=True)

    @app.get("/api/archive")
    async def api_archive(
        session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> JSONResponse:
        if not authorized(session):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        info: dict[str, Any] = {
            "enabled": archive_dir is not None,
            "recording_run_id": service.recording_run_id if service else None,
            "last_event": service.last_archive_event if service else None,
            "runs": 0,
            "stored_bytes": 0,
        }
        if archive_dir is not None:
            Path(archive_dir).mkdir(parents=True, exist_ok=True)
            info["free_bytes"] = free_bytes(Path(archive_dir))
            info["margin_bytes"] = DEFAULT_MARGIN_BYTES
        catalog = open_catalog()
        if catalog is not None:
            try:
                db = catalog.conn
                info["runs"] = db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
                info["stored_bytes"] = db.execute("SELECT COALESCE(SUM(size_bytes), 0) FROM blobs").fetchone()[0]
                info["by_state"] = {
                    row[0]: row[1] for row in db.execute("SELECT state, COUNT(*) FROM runs GROUP BY state")
                }
            finally:
                catalog.close()
        return JSONResponse(info)

    @app.get("/api/runs")
    async def api_runs(
        limit: int = 50,
        session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> JSONResponse:
        if not authorized(session):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        catalog = open_catalog()
        if catalog is None:
            return JSONResponse({"runs": []})
        try:
            return JSONResponse({"runs": catalog.list_runs(max(1, min(limit, 500)))})
        finally:
            catalog.close()

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        session = websocket.cookies.get(SESSION_COOKIE)
        origin = websocket.headers.get("origin")
        expected_origins = {
            f"http://{websocket.url.hostname}:{websocket.url.port or DEFAULT_PORT}",
            f"http://{websocket.headers.get('host')}",
        }
        if (
            session is None
            or not secrets.compare_digest(session, token)
            or origin not in expected_origins
            or service is None
        ):
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        await websocket.accept()
        send_lock = asyncio.Lock()

        async def send(message: dict[str, Any]) -> None:
            async with send_lock:
                await websocket.send_json(message)

        service.subscribers.add(send)
        try:
            if service.metadata is not None:
                await send({"type": "metadata", "payload": service.metadata})
            if service.latest_snapshot is not None:
                await send({"type": "snapshot", "payload": service.latest_snapshot})
            while True:
                parsed = CommandMessage.model_validate(await websocket.receive_json())
                if parsed.type not in ALLOWED_COMMANDS:
                    await send(
                        {
                            "type": "error",
                            "request_id": str(parsed.request_id),
                            "error": "unsupported command",
                        }
                    )
                    continue
                accepted = service.send_command(parsed.type, str(parsed.request_id))
                if not accepted:
                    await send(
                        {
                            "type": "ack",
                            "request_id": str(parsed.request_id),
                            "command": parsed.type,
                            "duplicate": True,
                        }
                    )
        except WebSocketDisconnect:
            pass
        finally:
            service.subscribers.discard(send)

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Fruit Fly Lab on the local network")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--data-dir", type=Path, default=Path("lab/data/runtime"))
    args = parser.parse_args()

    token = secrets.token_urlsafe(9)
    lan_ip = discover_lan_ip()
    join_url = f"http://{lan_ip}:{args.port}/join/{token}"
    write_connection_info(args.data_dir, join_url)
    print(f"Fruit Fly Lab: {join_url}")
    app = create_app(session_token=token)
    uvicorn.run(app, host=args.host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()
