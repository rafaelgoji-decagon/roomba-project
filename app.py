"""Local mobile control portal for the Roomba."""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from controller import RobotController
from camera import Camera
from dataset import DatasetRecorder
from autonomous import AutonomousRunner
from origin_calibration import OriginCalibration
from terminal_ui import event


ROOT = Path(__file__).parent
controller = RobotController()
camera = Camera()
recorder = DatasetRecorder(camera, controller.snapshot)
controller.set_event_sink(recorder.record_event)
runner = AutonomousRunner(controller, ROOT / "training" / "artifacts" / "route_reference.json", recorder.record_event)
origin = OriginCalibration(camera.latest, ROOT / "calibration" / "origin.json")
control_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    controller.start()
    camera.start()
    origin.start()
    yield
    runner.cancel("server shutdown")
    origin.stop()
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
    state["autonomous"] = runner.status()
    state["origin"] = origin.status()
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
        previous_auto_state = runner.status()["state"]
        try:
            recorder.record_event("client_connected", {"client": client})
            await socket.send_json({"type": "status", "data": full_status()})
            next_status = time.monotonic() + 0.5
            while True:
                try:
                    message = await asyncio.wait_for(socket.receive_json(), timeout=0.10)
                except asyncio.TimeoutError:
                    message = None
                if message is not None:
                    kind = message.get("type")
                    if kind == "arm":
                        runner.cancel("manual control requested")
                        recorder.record_event("requested_arm", {"client": client})
                        controller.clear_emergency()
                        armed = controller.arm()
                        await socket.send_json({"type": "armed", "ok": armed})
                    elif kind == "drive":
                        x = float(message.get("x", 0))
                        y = float(message.get("y", 0))
                        recorder.record_event(
                            "requested_drive",
                            {"client": client, "sequence": message.get("sequence"), "x": x, "y": y},
                        )
                        if runner.status()["state"] != "running":
                            controller.command(x, y)
                    elif kind == "stop":
                        recorder.record_event("requested_stop", {"client": client, "sequence": message.get("sequence")})
                        if runner.status()["state"] == "running":
                            runner.pause()
                            recorder.stop()
                        else:
                            controller.stop_motion()
                    elif kind == "emergency":
                        recorder.record_event("requested_emergency", {"client": client})
                        runner.emergency_stop()
                    elif kind == "disarm":
                        recorder.record_event("requested_disarm", {"client": client})
                        runner.cancel("control disarmed")
                        controller.disarm()
                    elif kind == "record_start":
                        recorder.start("manual")
                        recorder.record_event("recording_started", {"client": client})
                    elif kind == "record_stop":
                        recorder.record_event("recording_stopped", {"client": client})
                        recorder.stop()
                    elif kind == "auto_ready":
                        recorder.record_event("requested_autonomous_ready", {"client": client})
                        origin_state = origin.status()
                        if not origin_state["target_saved"]:
                            runner.reject("Guarda primero el origen visual")
                        elif not (origin_state.get("comparison") or {}).get("aligned"):
                            runner.reject("La pose no coincide con el origen guardado")
                        else:
                            runner.ready()
                    elif kind == "auto_play":
                        origin_state = origin.status()
                        if not (origin_state.get("comparison") or {}).get("aligned"):
                            runner.reject("La pose visual cambió; vuelve a preparar la ruta")
                            continue
                        recorder.start(
                            "autonomous",
                            {
                                "route_model": runner.status()["model"],
                                "autonomous_max_speed_mm_s": runner.status()["max_speed_mm_s"],
                            },
                        )
                        recorder.record_event("requested_autonomous_play", {"client": client})
                        if not runner.start():
                            recorder.stop()
                    elif kind == "auto_pause":
                        recorder.record_event("requested_autonomous_pause", {"client": client})
                        runner.pause()
                        recorder.stop()
                    elif kind == "auto_cancel":
                        recorder.record_event("requested_autonomous_cancel", {"client": client})
                        runner.cancel("user cancelled")
                        recorder.stop()
                    elif kind == "origin_capture":
                        recorder.record_event("requested_origin_capture", {"client": client})
                        runner.cancel("origin calibration requested")
                        controller.disarm()
                        origin.capture()
                auto_state = runner.status()["state"]
                if previous_auto_state == "running" and auto_state in {"complete", "fault", "idle"}:
                    recorder.stop()
                previous_auto_state = auto_state
                if time.monotonic() >= next_status:
                    await socket.send_json({"type": "status", "data": full_status()})
                    next_status = time.monotonic() + 0.5
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            runner.cancel("control connection lost")
            controller.disarm()
            recorder.record_event("client_disconnected", {"client": client})
            recorder.stop()
            event("client", f"Web control disconnected · {client}", "warn")
