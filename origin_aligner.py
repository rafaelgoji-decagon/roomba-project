"""Low-speed visual servo that returns the robot to a saved ArUco origin."""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from controller import RobotController
from terminal_ui import event

ALIGN_MAX_SPEED_MM_S = 50
ALIGN_CONTROL_HZ = 5
ALIGN_TIMEOUT_SECONDS = 90
ALIGN_STABLE_READINGS = 5
SETTLE_SECONDS = 1.0
MAX_RECOVERY_ATTEMPTS = 2


class OriginAligner:
    def __init__(self, controller: RobotController, origin_status: Callable[[], dict[str, Any]]) -> None:
        self.controller = controller
        self.origin_status = origin_status
        self._lock = threading.Lock()
        self._state = "idle"
        self._message = ""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @staticmethod
    def _hazard(sensors: dict[str, Any]) -> bool:
        groups = (sensors.get("bumps", {}), sensors.get("wheel_drops", {}), sensors.get("cliff", {}))
        return any(bool(value) for group in groups for value in group.values())

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {"state": self._state, "message": self._message, "max_speed_mm_s": ALIGN_MAX_SPEED_MM_S}

    def _set(self, state: str, message: str) -> None:
        with self._lock:
            self._state = state
            self._message = message

    def start(self) -> bool:
        if self.status()["state"] == "running":
            return True
        origin = self.origin_status()
        robot = self.controller.snapshot()
        if not origin.get("target_saved"):
            self._set("fault", "Esta ruta no tiene un origen guardado")
            return False
        if not origin.get("marker_ids"):
            self._set("fault", "No se ve ningún código del tablero")
            return False
        if not robot.get("battery_ok") or robot.get("status") not in {"connected", "simulated"}:
            self._set("fault", "La telemetría de la Roomba no está disponible")
            return False
        if self._hazard(robot.get("sensors", {})):
            self._set("fault", "Un sensor de seguridad está activo")
            return False
        self._stop.clear()
        self.controller.clear_emergency()
        if not self.controller.arm():
            self._set("fault", "No fue posible activar los motores")
            return False
        self._set("running", "Ajustando la pose con el marcador")
        self._thread = threading.Thread(target=self._run, name="origin-align", daemon=True)
        self._thread.start()
        event("origin", f"Visual alignment started · capped at {ALIGN_MAX_SPEED_MM_S}mm/s", "warn")
        return True

    def cancel(self, reason: str = "cancelled") -> None:
        self._stop.set()
        self.controller.stop_motion()
        self.controller.disarm()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=1)
        if self.status()["state"] == "running":
            self._set("idle", reason)

    def emergency_stop(self) -> None:
        self._stop.set()
        self.controller.emergency_stop()
        self._set("fault", "Parada de emergencia")

    def _fail(self, message: str) -> None:
        self._stop.set()
        self.controller.stop_motion()
        self.controller.disarm()
        self._set("fault", message)
        event("origin", f"Visual alignment stopped · {message}", "danger")

    @staticmethod
    def _clamp(value: float, limit: float) -> float:
        return max(-limit, min(limit, value))

    def _run(self) -> None:
        began = time.monotonic()
        stable = 0
        stable_sequence = None
        last_motion: tuple[float, float] | None = None
        recovery_attempts = 0
        while not self._stop.wait(1 / ALIGN_CONTROL_HZ):
            now = time.monotonic()
            robot = self.controller.snapshot()
            if now - began > ALIGN_TIMEOUT_SECONDS:
                self._fail("No logró alinearse dentro del tiempo permitido")
                return
            if robot.get("emergency") or self._hazard(robot.get("sensors", {})):
                self._fail("Bumper, cliff o wheel-drop activo")
                return
            if robot.get("status") not in {"connected", "simulated"} or not robot.get("battery_ok"):
                self._fail("Se perdió la telemetría serial")
                return
            origin = self.origin_status()
            marker_ids = origin.get("marker_ids", [])
            if not marker_ids:
                if last_motion is None or recovery_attempts >= MAX_RECOVERY_ATTEMPTS:
                    self._fail("Se perdió el marcador; coloca otra vez un código dentro del encuadre")
                    return
                recovery_attempts += 1
                self._set("running", "Recuperando el marcador")
                if not self._pulse(-last_motion[0], -last_motion[1], 0.30):
                    return
                if not self._settle():
                    return
                continue
            recovery_attempts = 0
            comparison = origin.get("comparison")
            if comparison and comparison.get("aligned"):
                sequence = origin.get("detection", {}).get("frame_sequence")
                if sequence != stable_sequence:
                    stable += 1
                    stable_sequence = sequence
                self.controller.command_wheels(0, 0, ALIGN_MAX_SPEED_MM_S)
                if stable >= ALIGN_STABLE_READINGS:
                    self.controller.disarm()
                    self._set("aligned", "Origen listo")
                    event("origin", "Visual origin aligned", "ok")
                    return
                continue
            stable = 0
            if comparison:
                dx = float(comparison["offset_x_percent"]) / 100
                scale_error = 1 - float(comparison["scale_ratio"])
                angle = float(comparison["angle_error_deg"])
                turn = self._clamp(dx * 180 + angle * 3, 40)
                linear = self._clamp(scale_error * 90, 45)
                if abs(dx) > 0.025 or abs(angle) > 2.0:
                    linear = 0
                if 0 < abs(turn) < 16:
                    turn = 16 if turn > 0 else -16
                if 0 < abs(linear) < 18:
                    linear = 18 if linear > 0 else -18
                left, right = linear + turn, linear - turn
                error_size = max(abs(dx) / 0.015, abs(scale_error) / 0.04, abs(angle) / 1.5)
                pulse_seconds = 0.25 if error_size < 2 else (0.40 if error_size < 5 else 0.55)
            else:
                center_x = float(origin.get("detection", {}).get("partial_center_x", 0.5))
                turn = self._clamp((center_x - 0.5) * 100, 35)
                if 0 < abs(turn) < 18:
                    turn = 18 if turn > 0 else -18
                linear = -18 if abs(turn) < 1 else 0
                left, right = linear + turn, linear - turn
                pulse_seconds = 0.35
            self._set("running", "Moviendo un paso corto")
            if not self._pulse(left, right, pulse_seconds):
                return
            last_motion = (left, right)
            if not self._settle():
                return

    def _pulse(self, left: float, right: float, seconds: float) -> bool:
        return self._hold(left, right, seconds)

    def _settle(self) -> bool:
        self._set("running", "Esperando que la cámara deje de vibrar")
        return self._hold(0, 0, SETTLE_SECONDS)

    def _hold(self, left: float, right: float, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self._stop.is_set():
                self.controller.stop_motion()
                return False
            robot = self.controller.snapshot()
            if robot.get("emergency") or self._hazard(robot.get("sensors", {})):
                self._fail("Bumper, cliff o wheel-drop activo")
                return False
            if robot.get("status") not in {"connected", "simulated"} or not robot.get("battery_ok"):
                self._fail("Se perdió la telemetría serial")
                return False
            if not self.controller.command_wheels(left, right, ALIGN_MAX_SPEED_MM_S):
                self._fail("El control de motores quedó desactivado")
                return False
            self._stop.wait(min(0.10, max(0, deadline - time.monotonic())))
        return True
