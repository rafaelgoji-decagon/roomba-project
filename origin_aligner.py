"""Low-speed visual servo that returns the robot to a saved ArUco origin."""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable

from controller import RobotController
from terminal_ui import event

ALIGN_MAX_SPEED_MM_S = 85
ALIGN_CONTROL_HZ = 10
ALIGN_TIMEOUT_SECONDS = 90
ALIGN_STABLE_READINGS = 5
MAX_OBSERVATION_AGE_MS = 400
MARKER_LOSS_GRACE_SECONDS = 0.60
FILTER_ALPHA = 0.45
MAX_COMMAND_STEP_MM_S = 18


@dataclass(frozen=True)
class MotionProfile:
    stage: str
    speed_limit_mm_s: int
    step_label: str


def motion_profile(error_size: float) -> MotionProfile:
    """Return a conservative motion band that shrinks near the saved pose."""
    if error_size >= 8:
        return MotionProfile("coarse", 75, "Corrección amplia")
    if error_size >= 4:
        return MotionProfile("approach", 60, "Aproximación")
    if error_size >= 2:
        return MotionProfile("refine", 48, "Corrección precisa")
    return MotionProfile("fine", 30, "Ajuste fino")


class OriginAligner:
    def __init__(self, controller: RobotController, origin_status: Callable[[], dict[str, Any]]) -> None:
        self.controller = controller
        self.origin_status = origin_status
        self._lock = threading.Lock()
        self._state = "idle"
        self._message = ""
        self._details: dict[str, Any] = {
            "stage": None,
            "phase": "observe",
            "cycle": 0,
            "speed_limit_mm_s": 0,
            "step_label": "",
            "command": {"left_mm_s": 0, "right_mm_s": 0},
            "errors": None,
            "control_mode": "continuous",
            "observation_age_ms": None,
            "vision_hz": ALIGN_CONTROL_HZ,
            "update_interval_ms": round(1000 / ALIGN_CONTROL_HZ),
        }
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @staticmethod
    def _hazard(sensors: dict[str, Any]) -> bool:
        groups = (sensors.get("bumps", {}), sensors.get("wheel_drops", {}), sensors.get("cliff", {}))
        return any(bool(value) for group in groups for value in group.values())

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "state": self._state,
                "message": self._message,
                "max_speed_mm_s": ALIGN_MAX_SPEED_MM_S,
                **self._details,
                "command": dict(self._details["command"]),
                "errors": dict(self._details["errors"]) if self._details["errors"] else None,
            }

    def _set(self, state: str, message: str, **details: Any) -> None:
        with self._lock:
            self._state = state
            self._message = message
            self._details.update(details)

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
        self._set("running", "Midiendo la pose actual", phase="observe", cycle=0)
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
            self._set("idle", reason, phase="observe", command={"left_mm_s": 0, "right_mm_s": 0})

    def emergency_stop(self) -> None:
        self._stop.set()
        self.controller.emergency_stop()
        self._set("fault", "Parada de emergencia", command={"left_mm_s": 0, "right_mm_s": 0})

    def _fail(self, message: str) -> None:
        self._stop.set()
        self.controller.stop_motion()
        self.controller.disarm()
        self._set(
            "fault",
            message,
            stage=None,
            phase="observe",
            speed_limit_mm_s=0,
            step_label="Ajuste detenido",
            command={"left_mm_s": 0, "right_mm_s": 0},
            errors=None,
        )
        event("origin", f"Visual alignment stopped · {message}", "danger")

    @staticmethod
    def _clamp(value: float, limit: float) -> float:
        return max(-limit, min(limit, value))

    def _run(self) -> None:
        began = time.monotonic()
        stable = 0
        stable_sequence = None
        last_sequence = None
        last_marker_at = time.monotonic()
        filtered: dict[str, float] | None = None
        commanded = (0.0, 0.0)
        cycle = 0
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
            observation_age = origin.get("observation_age_ms", 0)
            if not marker_ids:
                self.controller.stop_motion()
                commanded = (0.0, 0.0)
                self._set(
                    "running",
                    "Buscando el tablero; motores detenidos",
                    stage="search",
                    phase="observe",
                    step_label="Esperando marcador",
                    command={"left_mm_s": 0, "right_mm_s": 0},
                    observation_age_ms=observation_age,
                )
                if time.monotonic() - last_marker_at >= MARKER_LOSS_GRACE_SECONDS:
                    self._fail("Se perdió el marcador; coloca otra vez un código dentro del encuadre")
                    return
                continue
            last_marker_at = time.monotonic()
            if observation_age is None or observation_age > MAX_OBSERVATION_AGE_MS:
                self.controller.stop_motion()
                self._fail("La imagen dejó de actualizarse; motores detenidos")
                return
            sequence = origin.get("detection", {}).get("frame_sequence")
            if sequence == last_sequence:
                if not self.controller.command_wheels(commanded[0], commanded[1], ALIGN_MAX_SPEED_MM_S):
                    self._fail("El control de motores quedó desactivado")
                    return
                continue
            last_sequence = sequence
            comparison = origin.get("comparison")
            if comparison and comparison.get("aligned"):
                if sequence != stable_sequence:
                    stable += 1
                    stable_sequence = sequence
                commanded = (0.0, 0.0)
                self.controller.stop_motion()
                self._set(
                    "running",
                    "Confirmando el origen",
                    stage="confirm",
                    phase="settle",
                    step_label=f"Confirmando {stable}/{ALIGN_STABLE_READINGS}",
                    command={"left_mm_s": 0, "right_mm_s": 0},
                    observation_age_ms=observation_age,
                )
                if stable >= ALIGN_STABLE_READINGS:
                    self.controller.disarm()
                    self._set("aligned", "Origen listo", phase="observe", command={"left_mm_s": 0, "right_mm_s": 0})
                    event("origin", "Visual origin aligned", "ok")
                    return
                continue
            stable = 0
            if comparison:
                measured = {
                    "dx": float(comparison["offset_x_percent"]) / 100,
                    "scale": 1 - float(comparison["scale_ratio"]),
                    "angle": float(comparison["angle_error_deg"]),
                }
                filtered = measured if filtered is None else {
                    key: FILTER_ALPHA * value + (1 - FILTER_ALPHA) * filtered[key]
                    for key, value in measured.items()
                }
                dx, scale_error, angle = filtered["dx"], filtered["scale"], filtered["angle"]
                error_size = max(abs(dx) / 0.015, abs(scale_error) / 0.04, abs(angle) / 1.5)
                profile = motion_profile(error_size)
                left, right, phase = self._pose_command(dx, scale_error, angle)
                errors = {
                    "distance_percent": round(scale_error * 100, 1),
                    "horizontal_percent": round(float(comparison["offset_x_percent"]), 1),
                    "angle_deg": round(angle, 1),
                    "score": round(float(comparison.get("score", 0)), 1),
                }
            else:
                center_x = float(origin.get("detection", {}).get("partial_center_x", 0.5))
                turn = self._clamp((center_x - 0.5) * 100, 35)
                if 0 < abs(turn) < 18:
                    turn = 18 if turn > 0 else -18
                linear = -18 if abs(turn) < 1 else 0
                left, right = linear + turn, linear - turn
                profile = MotionProfile("search", 35, "Búsqueda visual")
                errors = None
                phase = "Buscando el tablero completo"
            left, right = self._scale_wheels(left, right, profile.speed_limit_mm_s)
            left = self._slew(commanded[0], left)
            right = self._slew(commanded[1], right)
            commanded = (left, right)
            cycle += 1
            details = asdict(profile)
            details.update(
                phase="move",
                cycle=cycle,
                command={"left_mm_s": round(left), "right_mm_s": round(right)},
                errors=errors,
                observation_age_ms=observation_age,
            )
            self._set("running", phase, **details)
            if not self.controller.command_wheels(left, right, profile.speed_limit_mm_s):
                self._fail("El control de motores quedó desactivado")
                return

    @classmethod
    def _slew(cls, current: float, target: float) -> float:
        return current + cls._clamp(target - current, MAX_COMMAND_STEP_MM_S)

    def _pose_command(self, dx: float, scale_error: float, angle: float) -> tuple[float, float, str]:
        if abs(scale_error) > 0.025:
            linear = self._clamp(scale_error * 110, 55)
            if abs(linear) < 16:
                linear = 16 if linear > 0 else -16
            turn = 0
            message = "Siguiendo distancia al origen"
        elif abs(dx) > 0.018:
            linear = 28
            turn = self._clamp(dx * 190, 30)
            if abs(turn) < 12:
                turn = 12 if turn > 0 else -12
            message = "Siguiendo corrección lateral"
        elif abs(angle) > 1.0:
            linear = 0
            turn = self._clamp(angle * 3, 34)
            if abs(turn) < 12:
                turn = 12 if turn > 0 else -12
            message = "Siguiendo orientación final"
        else:
            linear = self._clamp(scale_error * 75, 24)
            turn = self._clamp(dx * 120 + angle * 2, 20)
            message = "Ajuste visual fino continuo"
        return linear + turn, linear - turn, message

    @staticmethod
    def _scale_wheels(left: float, right: float, limit: int) -> tuple[float, float]:
        scale = max(1.0, abs(left) / limit, abs(right) / limit)
        return left / scale, right / scale
