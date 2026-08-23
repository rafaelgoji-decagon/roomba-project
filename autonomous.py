"""Safe, cancellable odometry-feedback playback for a trained local route."""

from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path
from typing import Any, Callable

from controller import RobotController
from terminal_ui import event
from training.common import ENCODER_MODULUS, MM_PER_COUNT

AUTONOMOUS_MAX_SPEED_MM_S = 125
AUTONOMOUS_MAX_ACCEL_MM_S2 = 100
AUTONOMOUS_MAX_TRACK_ERROR_MM = 550
AUTONOMOUS_CONTROL_HZ = 10
POSITION_KP = 0.35


class AutonomousRunner:
    STATES = {"idle", "ready", "running", "paused", "complete", "fault"}

    def __init__(
        self,
        controller: RobotController,
        model_path: Path,
        event_sink: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.controller = controller
        self.model_path = model_path
        self.event_sink = event_sink
        self._lock = threading.Lock()
        self._state = "idle"
        self._fault = ""
        self._progress = 0.0
        self._left_mm = 0.0
        self._right_mm = 0.0
        self._started_at: float | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._model = self._load_model()

    def _load_model(self) -> dict[str, Any]:
        data = json.loads(self.model_path.read_text(encoding="utf-8"))
        if data.get("model_type") != "median_odometry_route_v1" or len(data.get("reference", [])) < 2:
            raise ValueError("unsupported or empty autonomous route model")
        return data

    def _emit(self, kind: str, payload: dict[str, Any]) -> None:
        if self.event_sink:
            self.event_sink(kind, payload)

    def _set_state(self, state: str, fault: str = "") -> None:
        with self._lock:
            self._state = state
            self._fault = fault
        self._emit("autonomous_state", {"state": state, "fault": fault})

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "state": self._state,
                "fault": self._fault,
                "progress": round(self._progress, 4),
                "progress_percent": round(self._progress * 100, 1),
                "left_mm": round(self._left_mm, 1),
                "right_mm": round(self._right_mm, 1),
                "max_speed_mm_s": AUTONOMOUS_MAX_SPEED_MM_S,
                "model": self.model_path.name,
            }

    def ready(self) -> bool:
        self.cancel("prepare")
        state = self.controller.snapshot()
        sensors = state.get("sensors", {})
        if not state.get("battery_ok"):
            self._set_state("fault", "Battery or serial telemetry unavailable")
            return False
        if not isinstance(sensors.get("encoders"), dict):
            self._set_state("fault", "Extended encoder telemetry unavailable")
            return False
        if self._hazard(sensors):
            self._set_state("fault", "Safety sensor is active")
            return False
        self.controller.disarm()
        with self._lock:
            self._progress = self._left_mm = self._right_mm = 0.0
        self._set_state("ready")
        event("auto", "Route ready · waiting for Play confirmation", "warn")
        return True

    def start(self) -> bool:
        with self._lock:
            allowed = self._state in {"ready", "paused"}
        if not allowed:
            return False
        self._stop.clear()
        self.controller.clear_emergency()
        if not self.controller.arm():
            self._set_state("fault", "Unable to arm motor control")
            return False
        self._set_state("running")
        self._started_at = time.monotonic()
        self._thread = threading.Thread(target=self._run, name="autonomous-route", daemon=True)
        self._thread.start()
        event("auto", f"Route started · capped at {AUTONOMOUS_MAX_SPEED_MM_S}mm/s", "warn")
        return True

    def pause(self) -> None:
        self._stop.set()
        self.controller.stop_motion()
        self.controller.disarm()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=1)
        with self._lock:
            if self._state == "running":
                self._state = "paused"
        self._emit("autonomous_state", {"state": "paused", "fault": ""})
        event("auto", "Route paused · motors disarmed", "ok")

    def cancel(self, reason: str = "cancelled") -> None:
        self._stop.set()
        self.controller.stop_motion()
        self.controller.disarm()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=1)
        with self._lock:
            active = self._state in {"ready", "running", "paused"}
            if active:
                self._state = "idle"
                self._fault = ""
        if active:
            self._emit("autonomous_cancelled", {"reason": reason})

    def emergency_stop(self) -> None:
        self._stop.set()
        self.controller.emergency_stop()
        self._set_state("fault", "Emergency stop")

    @staticmethod
    def _hazard(sensors: dict[str, Any]) -> bool:
        groups = (sensors.get("bumps", {}), sensors.get("wheel_drops", {}), sensors.get("cliff", {}))
        return any(bool(value) for group in groups for value in group.values())

    @staticmethod
    def _delta_encoder(previous: int, current: int) -> int:
        return (current - previous + ENCODER_MODULUS // 2) % ENCODER_MODULUS - ENCODER_MODULUS // 2

    def _reference_at(self, progress: float) -> dict[str, float]:
        points = self._model["reference"]
        index = min(len(points) - 2, max(0, int(progress * (len(points) - 1))))
        before, after = points[index], points[index + 1]
        width = after["progress"] - before["progress"]
        ratio = 0.0 if width <= 0 else (progress - before["progress"]) / width
        return {key: float(before[key]) + ratio * (float(after[key]) - float(before[key])) for key in before if key != "progress"}

    @staticmethod
    def _ramp(current: float, target: float, max_delta: float) -> float:
        return current + max(-max_delta, min(max_delta, target - current))

    def _run(self) -> None:
        period = 1 / AUTONOMOUS_CONTROL_HZ
        snapshot = self.controller.snapshot()
        encoders = snapshot.get("sensors", {}).get("encoders", {})
        previous_left, previous_right = encoders.get("left"), encoders.get("right")
        if previous_left is None or previous_right is None:
            self._fail("Encoder telemetry lost before start")
            return
        with self._lock:
            left_mm, right_mm = self._left_mm, self._right_mm
        current_left = current_right = 0.0
        endpoint = (self._model["reference"][-1]["left_mm"] + self._model["reference"][-1]["right_mm"]) / 2
        launch_progress = next(
            (
                point["progress"]
                for point in self._model["reference"]
                if abs(point["left_velocity_mm_s"]) + abs(point["right_velocity_mm_s"]) >= 20
            ),
            0.0,
        )
        while not self._stop.wait(period):
            snapshot = self.controller.snapshot()
            sensors = snapshot.get("sensors", {})
            encoders = sensors.get("encoders")
            if snapshot.get("emergency"):
                self._fail("Emergency stop")
                return
            if snapshot.get("status") not in {"connected", "simulated"} or not isinstance(encoders, dict):
                self._fail("Serial or encoder telemetry lost")
                return
            if self._hazard(sensors):
                self._fail("Bump, cliff, or wheel-drop sensor active")
                return
            left_mm += self._delta_encoder(previous_left, encoders["left"]) * MM_PER_COUNT
            right_mm += self._delta_encoder(previous_right, encoders["right"]) * MM_PER_COUNT
            previous_left, previous_right = encoders["left"], encoders["right"]
            progress = max(0.0, (left_mm + right_mm) / 2 / endpoint)
            if progress >= 0.999:
                self.controller.stop_motion()
                self.controller.disarm()
                with self._lock:
                    self._progress = 1.0
                    self._left_mm, self._right_mm = left_mm, right_mm
                self._set_state("complete")
                event("auto", "Route complete · motors disarmed", "ok")
                return
            # Normalized-distance alignment has no time axis. Skip the initial
            # stationary samples so the route can establish forward progress.
            reference = self._reference_at(max(progress, launch_progress))
            left_error = reference["left_mm"] - left_mm
            right_error = reference["right_mm"] - right_mm
            if max(abs(left_error), abs(right_error)) > AUTONOMOUS_MAX_TRACK_ERROR_MM:
                self._fail("Odometry deviation exceeded safety limit")
                return
            peak = max(abs(reference["left_velocity_mm_s"]), abs(reference["right_velocity_mm_s"]), 1)
            scale = min(1.0, AUTONOMOUS_MAX_SPEED_MM_S / peak)
            target_left = reference["left_velocity_mm_s"] * scale + POSITION_KP * left_error
            target_right = reference["right_velocity_mm_s"] * scale + POSITION_KP * right_error
            max_delta = AUTONOMOUS_MAX_ACCEL_MM_S2 * period
            current_left = self._ramp(current_left, target_left, max_delta)
            current_right = self._ramp(current_right, target_right, max_delta)
            if not self.controller.command_wheels(current_left, current_right, AUTONOMOUS_MAX_SPEED_MM_S):
                self._fail("Motor control became disarmed")
                return
            with self._lock:
                self._progress = min(progress, 1.0)
                self._left_mm, self._right_mm = left_mm, right_mm
        self.controller.stop_motion()

    def _fail(self, reason: str) -> None:
        self._stop.set()
        self.controller.stop_motion()
        self.controller.disarm()
        self._set_state("fault", reason)
        event("auto", f"Route fault · {reason}", "danger")
