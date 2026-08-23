"""Local mobile control portal for the Roomba."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from controller import RobotController
from camera import Camera
from dataset import DatasetRecorder
from terminal_ui import event


ROOT = Path(__file__).parent
controller = RobotController()
camera = Camera()
recorder = DatasetRecorder(camera, controller.snapshot)
control_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    controller.start()
    camera.start()
    yield
    recorder.shutdown()
    camera.stop()
    controller.shutdown()


app = FastAPI(title="Roomba Deck", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/status")
async def status() -> dict:
    return full_status()


def full_status() -> dict:
    state = controller.snapshot()
    state["camera"] = camera.status()
    state["dataset"] = recorder.status()
    return state


@app.get("/camera.mjpg")
def camera_stream() -> StreamingResponse:
    return StreamingResponse(camera.frames(), media_type="multipart/x-mixed-replace; boundary=frame")


@app.websocket("/ws/control")
async def control_socket(socket: WebSocket) -> None:
    await socket.accept()
    client = socket.client.host if socket.client else "unknown"
    event("client", f"Web control connected · {client}", "ok")
    if control_lock.locked():
        event("client", f"Rejected {client} · another pilot is active", "warn")
        await socket.send_json({"type": "busy", "message": "Otro dispositivo tiene el control"})
        await socket.close(code=1008)
        return
    async with control_lock:
        try:
            await socket.send_json({"type": "status", "data": full_status()})
            while True:
                try:
                    message = await asyncio.wait_for(socket.receive_json(), timeout=0.25)
                except asyncio.TimeoutError:
                    await socket.send_json({"type": "status", "data": full_status()})
                    continue
                kind = message.get("type")
                if kind == "arm":
                    controller.clear_emergency()
                    armed = controller.arm()
                    await socket.send_json({"type": "armed", "ok": armed})
                elif kind == "drive":
                    controller.command(float(message.get("x", 0)), float(message.get("y", 0)))
                elif kind == "stop":
                    controller.stop_motion()
                elif kind == "emergency":
                    controller.emergency_stop()
                elif kind == "disarm":
                    controller.disarm()
                elif kind == "record_start":
                    recorder.start()
                elif kind == "record_stop":
                    recorder.stop()
                await socket.send_json({"type": "status", "data": full_status()})
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            controller.disarm()
            recorder.stop()
            event("client", f"Web control disconnected · {client}", "warn")
