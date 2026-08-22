"""Thread-safe Roomba OI controller with a hard command watchdog."""

from __future__ import annotations

import os
import struct
import threading
import time
from typing import Any

import serial

from roomba import CHARGING_STATES, find_port
from terminal_ui import event


WATCHDOG_SECONDS = 0.40
MAX_SPEED_MM_S = 180
MIN_BATTERY_PERCENT = 20.0


class SerialRobot:
    def __init__(self) -> None:
        self.port = os.getenv("ROOMBA_PORT") or find_port()
        self.baudrate = int(os.getenv("ROOMBA_BAUD", "115200"))
        self.serial = serial.Serial(self.port, self.baudrate, timeout=1.0)
        self.serial.reset_input_buffer()
        # START leaves the robot passive: sensors work, motors cannot move, and
        # dock charging is not disturbed. SAFE is requested only when armed.
        self.serial.write(bytes([128]))
        self.serial.flush()
        time.sleep(0.1)

    def drive(self, left: int, right: int) -> None:
        left = max(-MAX_SPEED_MM_S, min(MAX_SPEED_MM_S, int(left)))
        right = max(-MAX_SPEED_MM_S, min(MAX_SPEED_MM_S, int(right)))
        self.serial.write(bytes([145]) + struct.pack(">hh", right, left))
        self.serial.flush()

    def enable_control(self) -> None:
        self.serial.write(bytes([131]))  # OI SAFE mode.
        self.serial.flush()

    def disable_control(self) -> None:
        self.serial.write(bytes([128]))  # OI passive mode.
        self.serial.flush()

    def battery(self) -> dict[str, Any]:
        self.serial.reset_input_buffer()
        self.serial.write(bytes([149, 6, 21, 22, 23, 24, 25, 26]))
        self.serial.flush()
        raw = self.serial.read(10)
        if len(raw) != 10:
            raise TimeoutError(f"expected 10 sensor bytes, received {len(raw)}")
        state, mv, ma, temp, charge, capacity = struct.unpack(">BHhbHH", raw)
        return {
            "volts": round(mv / 1000, 2),
            "amps": round(ma / 1000, 3),
            "temp_c": temp,
            "charge_mah": charge,
            "capacity_mah": capacity,
            "percent": round(100 * charge / capacity, 1) if capacity else 0,
            "charging": CHARGING_STATES.get(state, f"unknown ({state})"),
        }

    def close(self) -> None:
        try:
            self.drive(0, 0)
            self.serial.write(bytes([128]))  # Return to passive mode.
            self.serial.flush()
        finally:
            self.serial.close()


class MockRobot:
    def __init__(self) -> None:
        self.port = "simulation"
        self.left = 0
        self.right = 0

    def drive(self, left: int, right: int) -> None:
        self.left, self.right = left, right

    def battery(self) -> dict[str, Any]:
        return {
            "volts": 16.1,
            "amps": -0.08 if (self.left or self.right) else 0.0,
            "temp_c": 22,
            "charge_mah": 1450,
            "capacity_mah": 2697,
            "percent": 53.8,
            "charging": "not charging",
        }

    def enable_control(self) -> None:
        pass

    def disable_control(self) -> None:
        self.drive(0, 0)

    def close(self) -> None:
        self.drive(0, 0)


class RobotController:
    """Owns the serial port and stops motion when commands go stale."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._robot: SerialRobot | MockRobot | None = None
        self._desired = (0, 0)
        self._last_command = 0.0
        self._armed = False
        self._emergency = False
        self._running = False
        self._thread: threading.Thread | None = None
        self._telemetry: dict[str, Any] = {}
        self._status = "starting"
        self._last_error = ""

    def start(self) -> None:
        event("system", "I/O controller online · motors locked", "ok")
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="roomba-io", daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        event("system", "Shutdown requested · forcing motor stop", "warn")
        self._running = False
        self.emergency_stop()
        if self._thread:
            self._thread.join(timeout=2)

    def arm(self) -> bool:
        with self._lock:
            battery_percent = self._telemetry.get("percent")
            if (
                self._robot is None
                or self._emergency
                or battery_percent is None
                or battery_percent < MIN_BATTERY_PERCENT
            ):
                reason = "serial unavailable"
                if self._emergency:
                    reason = "emergency latch active"
                elif battery_percent is None:
                    reason = "battery telemetry unavailable"
                elif battery_percent < MIN_BATTERY_PERCENT:
                    reason = f"battery {battery_percent:.1f}% < {MIN_BATTERY_PERCENT:.0f}%"
                event("safety", f"ARM rejected · {reason}", "danger")
                return False
            self._armed = True
            self._last_command = time.monotonic()
            event("safety", "CONTROL ARMED · OI Safe requested", "warn")
            return True

    def disarm(self) -> None:
        with self._lock:
            was_armed = self._armed
            self._armed = False
            self._desired = (0, 0)
            self._last_command = 0
        if was_armed:
            event("safety", "Control disarmed · returning to passive", "ok")

    def command(self, x: float, y: float) -> None:
        x = max(-1.0, min(1.0, x))
        y = max(-1.0, min(1.0, y))
        if abs(x) < 0.08:
            x = 0
        if abs(y) < 0.08:
            y = 0
        left = int((y + x) * MAX_SPEED_MM_S)
        right = int((y - x) * MAX_SPEED_MM_S)
        scale = max(1.0, abs(left) / MAX_SPEED_MM_S, abs(right) / MAX_SPEED_MM_S)
        with self._lock:
            if self._armed and not self._emergency:
                self._desired = (int(left / scale), int(right / scale))
                self._last_command = time.monotonic()

    def stop_motion(self) -> None:
        with self._lock:
            self._desired = (0, 0)
            self._last_command = time.monotonic()

    def emergency_stop(self) -> None:
        with self._lock:
            self._emergency = True
            self._armed = False
            self._desired = (0, 0)
            self._last_command = 0
        event("e-stop", "EMERGENCY STOP · motors zeroed", "danger")

    def clear_emergency(self) -> None:
        with self._lock:
            self._emergency = False

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            fresh = time.monotonic() - self._last_command <= WATCHDOG_SECONDS
            battery_percent = self._telemetry.get("percent")
            battery_ok = battery_percent is not None and battery_percent >= MIN_BATTERY_PERCENT
            return {
                "status": self._status,
                "armed": self._armed,
                "emergency": self._emergency,
                "watchdog_ok": bool(self._armed and fresh),
                "motors": {"left": self._desired[0], "right": self._desired[1]},
                "battery": dict(self._telemetry),
                "error": self._last_error,
                "max_speed": MAX_SPEED_MM_S,
                "minimum_battery": MIN_BATTERY_PERCENT,
                "battery_ok": battery_ok,
            }

    def _connect(self) -> None:
        try:
            event("serial", "Searching for Roomba OI adapter…")
            self._robot = MockRobot() if os.getenv("ROOMBA_MOCK") == "1" else SerialRobot()
            self._status = "simulated" if isinstance(self._robot, MockRobot) else "connected"
            self._last_error = ""
            event("serial", f"Linked on {self._robot.port} · passive mode", "ok")
        except Exception as error:
            self._robot = None
            self._status = "disconnected"
            self._last_error = str(error)
            event("serial", f"Link failed · {error}", "danger")

    def _loop(self) -> None:
        last_sent: tuple[int, int] | None = None
        control_enabled = False
        last_sensor = float("-inf")
        # Negative infinity guarantees an immediate first connection even on
        # platforms whose monotonic clock begins near process startup.
        last_connect = float("-inf")
        last_battery_signature: tuple[Any, ...] | None = None
        watchdog_fired = False
        while self._running:
            now = time.monotonic()
            if self._robot is None and now - last_connect >= 2:
                last_connect = now
                self._connect()
            with self._lock:
                fresh = now - self._last_command <= WATCHDOG_SECONDS
                target = self._desired if (self._armed and not self._emergency and fresh) else (0, 0)
                if not fresh:
                    self._desired = (0, 0)
                if self._armed and not fresh and not watchdog_fired:
                    watchdog_fired = True
                    event("watchdog", "Command stream lost · motors zeroed", "danger")
                elif fresh:
                    watchdog_fired = False
            if self._robot is not None:
                try:
                    should_enable = self._armed and not self._emergency and fresh
                    if should_enable and not control_enabled:
                        self._robot.enable_control()
                        control_enabled = True
                    elif not should_enable and control_enabled:
                        self._robot.drive(0, 0)
                        self._robot.disable_control()
                        control_enabled = False
                        last_sent = (0, 0)
                    if target != last_sent:
                        self._robot.drive(*target)
                        last_sent = target
                    if now - last_sensor >= 2 and target == (0, 0):
                        self._telemetry = self._robot.battery()
                        signature = (
                            self._telemetry.get("percent"),
                            self._telemetry.get("charging"),
                            self._telemetry.get("amps"),
                        )
                        if signature != last_battery_signature:
                            event(
                                "battery",
                                f"{self._telemetry['percent']:>5.1f}%  "
                                f"{self._telemetry['volts']:.2f}V  "
                                f"{self._telemetry['amps']:+.3f}A  "
                                f"{self._telemetry['charging']}",
                                "ok" if self._telemetry["amps"] > 0 else "warn",
                            )
                            last_battery_signature = signature
                        last_sensor = now
                except Exception as error:
                    try:
                        self._robot.close()
                    except Exception:
                        pass
                    self._robot = None
                    control_enabled = False
                    last_sent = None
                    self._status = "disconnected"
                    self._last_error = str(error)
                    event("serial", f"Connection lost · {error}", "danger")
                    self.disarm()
            time.sleep(0.04)
        if self._robot is not None:
            self._robot.close()
        event("system", "Controller offline · serial released", "ok")
