"""Thread-safe Roomba OI controller with a hard command watchdog."""

from __future__ import annotations

import os
import math
import struct
import threading
import time
from typing import Any, Callable

import serial

from roomba import CHARGING_STATES, find_port
from terminal_ui import event


WATCHDOG_SECONDS = 0.75
MAX_SPEED_MM_S = 500
MIN_BATTERY_PERCENT = float(os.getenv("ROOMBA_MIN_BATTERY", "0"))


def validate_battery_packet(
    state: int, millivolts: int, temp_c: int, charge_mah: int, capacity_mah: int
) -> None:
    """Reject corrupt serial packets before they can unlock motor control."""
    problems = []
    if state not in CHARGING_STATES:
        problems.append(f"charging state {state}")
    if not 8000 <= millivolts <= 22000:
        problems.append(f"voltage {millivolts}mV")
    if not -20 <= temp_c <= 80:
        problems.append(f"temperature {temp_c}C")
    if not 100 <= capacity_mah <= 10000:
        problems.append(f"capacity {capacity_mah}mAh")
    if not 0 <= charge_mah <= capacity_mah:
        problems.append(f"charge {charge_mah}/{capacity_mah}mAh")
    if problems:
        raise ValueError("invalid battery packet: " + ", ".join(problems))


def _u16(raw: bytes, offset: int) -> int:
    return struct.unpack_from(">H", raw, offset)[0]


def _s16(raw: bytes, offset: int) -> int:
    return struct.unpack_from(">h", raw, offset)[0]


def parse_sensor_packet(raw: bytes) -> dict[str, Any]:
    """Parse OI packet group 6 (52 bytes) or group 100 (80 bytes)."""
    if len(raw) not in (52, 80):
        raise ValueError(f"invalid sensor packet length {len(raw)}")
    state, mv, ma, temp, charge, capacity = (
        raw[16], _u16(raw, 17), _s16(raw, 19), struct.unpack_from(">b", raw, 21)[0],
        _u16(raw, 22), _u16(raw, 24),
    )
    validate_battery_packet(state, mv, temp, charge, capacity)
    bumps = raw[0]
    overcurrents = raw[7]
    buttons = raw[11]
    sources = raw[39]
    data: dict[str, Any] = {
        "packet_group": 100 if len(raw) == 80 else 6,
        "raw_hex": raw.hex(),
        "bumps_wheel_drops_raw": bumps,
        "bumps": {"left": bool(bumps & 0x02), "right": bool(bumps & 0x01)},
        "wheel_drops": {
            "caster": bool(bumps & 0x10), "left": bool(bumps & 0x08), "right": bool(bumps & 0x04)
        },
        "wall": bool(raw[1]),
        "cliff": {"left": bool(raw[2]), "front_left": bool(raw[3]), "front_right": bool(raw[4]), "right": bool(raw[5])},
        "virtual_wall": bool(raw[6]),
        "wheel_overcurrents_raw": overcurrents,
        "wheel_overcurrents": {"left": bool(overcurrents & 0x10), "right": bool(overcurrents & 0x08)},
        "dirt_detect": raw[8],
        "infrared_omni": raw[10],
        "buttons_raw": buttons,
        "distance_mm_delta": _s16(raw, 12),
        "angle_deg_delta": _s16(raw, 14),
        "battery": {
            "volts": round(mv / 1000, 2), "amps": round(ma / 1000, 3), "temp_c": temp,
            "charge_mah": charge, "capacity_mah": capacity,
            "percent": round(100 * charge / capacity, 1),
            "charging": CHARGING_STATES[state],
        },
        "signals": {
            "wall": _u16(raw, 26), "cliff_left": _u16(raw, 28),
            "cliff_front_left": _u16(raw, 30), "cliff_front_right": _u16(raw, 32),
            "cliff_right": _u16(raw, 34),
        },
        "charging_sources_raw": sources,
        "charging_sources": {"internal": bool(sources & 0x01), "home_base": bool(sources & 0x02)},
        "oi_mode": {0: "off", 1: "passive", 2: "safe", 3: "full"}.get(raw[40], f"unknown ({raw[40]})"),
        "song_number": raw[41], "song_playing": bool(raw[42]), "stream_packets": raw[43],
        "requested_oi": {
            "velocity_mm_s": _s16(raw, 44), "radius_mm": _s16(raw, 46),
            "right_velocity_mm_s": _s16(raw, 48), "left_velocity_mm_s": _s16(raw, 50),
        },
    }
    if len(raw) == 80:
        light = raw[56]
        data["encoders"] = {"left": _u16(raw, 52), "right": _u16(raw, 54)}
        data["light_bumper_raw"] = light
        data["light_bumpers"] = {
            "left": bool(light & 0x01), "front_left": bool(light & 0x02),
            "center_left": bool(light & 0x04), "center_right": bool(light & 0x08),
            "front_right": bool(light & 0x10), "right": bool(light & 0x20),
        }
        data["light_bumper_signals"] = {
            "left": _u16(raw, 57), "front_left": _u16(raw, 59),
            "center_left": _u16(raw, 61), "center_right": _u16(raw, 63),
            "front_right": _u16(raw, 65), "right": _u16(raw, 67),
        }
        data["infrared_left"] = raw[69]
        data["infrared_right"] = raw[70]
        data["motor_currents_ma"] = {
            "left": _s16(raw, 71), "right": _s16(raw, 73),
            "main_brush": _s16(raw, 75), "side_brush": _s16(raw, 77),
        }
        data["stasis_raw"] = raw[79]
    return data


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
        self._sensor_group = 100

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

    def sensors(self) -> dict[str, Any]:
        self.serial.reset_input_buffer()
        expected = 80 if self._sensor_group == 100 else 52
        self.serial.write(bytes([142, self._sensor_group]))
        self.serial.flush()
        previous_timeout = self.serial.timeout
        self.serial.timeout = 0.25
        raw = self.serial.read(expected)
        self.serial.timeout = previous_timeout
        if len(raw) != expected and self._sensor_group == 100:
            event("sensor", "Extended OI packet unavailable · falling back to group 6", "warn")
            self._sensor_group = 6
            self.serial.reset_input_buffer()
            self.serial.write(bytes([142, 6]))
            self.serial.flush()
            self.serial.timeout = 0.25
            raw = self.serial.read(52)
            self.serial.timeout = previous_timeout
            expected = 52
        if len(raw) != expected:
            raise TimeoutError(f"expected {expected} sensor bytes, received {len(raw)}")
        return parse_sensor_packet(raw)

    def battery(self) -> dict[str, Any]:
        return self.sensors()["battery"]

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
        self.left_encoder = 0.0
        self.right_encoder = 0.0
        self._last_sensor_at = time.monotonic()

    def drive(self, left: int, right: int) -> None:
        self.left, self.right = left, right

    def sensors(self) -> dict[str, Any]:
        now = time.monotonic()
        elapsed = now - self._last_sensor_at
        self._last_sensor_at = now
        counts_per_mm = 508.8 / (math.pi * 72.0)
        self.left_encoder += self.left * elapsed * counts_per_mm
        self.right_encoder += self.right * elapsed * counts_per_mm
        battery = {
            "volts": 16.1,
            "amps": -0.08 if (self.left or self.right) else 0.0,
            "temp_c": 22,
            "charge_mah": 1450,
            "capacity_mah": 2697,
            "percent": 53.8,
            "charging": "not charging",
        }
        return {
            "packet_group": "simulation", "raw_hex": "", "battery": battery,
            "bumps": {"left": False, "right": False},
            "wheel_drops": {"caster": False, "left": False, "right": False},
            "cliff": {"left": False, "front_left": False, "front_right": False, "right": False},
            "distance_mm_delta": 0, "angle_deg_delta": 0,
            "encoders": {"left": round(self.left_encoder) % 65536, "right": round(self.right_encoder) % 65536},
            "requested_oi": {"left_velocity_mm_s": self.left, "right_velocity_mm_s": self.right},
        }

    def battery(self) -> dict[str, Any]:
        return self.sensors()["battery"]

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
        self._executed = (0, 0)
        self._requested_axes = (0.0, 0.0)
        self._last_command = 0.0
        self._armed = False
        self._emergency = False
        self._running = False
        self._thread: threading.Thread | None = None
        self._telemetry: dict[str, Any] = {}
        self._sensors: dict[str, Any] = {}
        self._status = "starting"
        self._last_error = ""
        self._command_count = 0
        self._watchdog_count = 0
        self._event_sink: Callable[[str, dict[str, Any]], None] | None = None

    def set_event_sink(self, sink: Callable[[str, dict[str, Any]], None]) -> None:
        self._event_sink = sink

    def _data_event(self, kind: str, payload: dict[str, Any]) -> None:
        sink = self._event_sink
        if sink is not None:
            sink(kind, payload)

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
            self._requested_axes = (0.0, 0.0)
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
                self._requested_axes = (x, y)
                self._last_command = time.monotonic()
                self._command_count += 1

    def command_wheels(self, left_mm_s: int, right_mm_s: int, speed_limit: int = MAX_SPEED_MM_S) -> bool:
        """Set an explicit wheel target and refresh the hard watchdog.

        Autonomous control uses this narrow entry point so it retains the same
        armed/emergency/watchdog gates as manual control.
        """
        limit = max(0, min(MAX_SPEED_MM_S, int(speed_limit)))
        left = max(-limit, min(limit, int(left_mm_s)))
        right = max(-limit, min(limit, int(right_mm_s)))
        with self._lock:
            if not self._armed or self._emergency:
                return False
            self._desired = (left, right)
            self._requested_axes = (0.0, 0.0)
            self._last_command = time.monotonic()
            self._command_count += 1
            return True

    def stop_motion(self) -> None:
        with self._lock:
            self._desired = (0, 0)
            self._requested_axes = (0.0, 0.0)
            self._last_command = time.monotonic()

    def emergency_stop(self) -> None:
        with self._lock:
            self._emergency = True
            self._armed = False
            self._desired = (0, 0)
            self._requested_axes = (0.0, 0.0)
            self._last_command = 0
        event("e-stop", "EMERGENCY STOP · motors zeroed", "danger")

    def clear_emergency(self) -> None:
        with self._lock:
            self._emergency = False

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            fresh = time.monotonic() - self._last_command <= WATCHDOG_SECONDS
            battery_percent = self._telemetry.get("percent")
            battery_ok = (
                self._robot is not None
                and battery_percent is not None
                and battery_percent >= MIN_BATTERY_PERCENT
            )
            return {
                "status": self._status,
                "armed": self._armed,
                "emergency": self._emergency,
                "watchdog_ok": bool(self._armed and fresh),
                "motors": {"left": self._executed[0], "right": self._executed[1]},
                "requested": {
                    "x": self._requested_axes[0], "y": self._requested_axes[1],
                    "left_mm_s": self._desired[0], "right_mm_s": self._desired[1],
                },
                "executed": {"left_mm_s": self._executed[0], "right_mm_s": self._executed[1]},
                "battery": dict(self._telemetry),
                "sensors": dict(self._sensors),
                "error": self._last_error,
                "max_speed": MAX_SPEED_MM_S,
                "minimum_battery": MIN_BATTERY_PERCENT,
                "battery_ok": battery_ok,
                "control": {
                    "commands_received": self._command_count,
                    "watchdog_stops": self._watchdog_count,
                    "last_command_age_ms": (
                        round((time.monotonic() - self._last_command) * 1000)
                        if self._last_command
                        else None
                    ),
                    "watchdog_ms": round(WATCHDOG_SECONDS * 1000),
                },
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
                    self._requested_axes = (0.0, 0.0)
                if self._armed and not fresh and not watchdog_fired:
                    watchdog_fired = True
                    self._watchdog_count += 1
                    age_ms = round((now - self._last_command) * 1000)
                    event(
                        "watchdog",
                        f"Command stream lost · {age_ms}ms · stop #{self._watchdog_count}",
                        "danger",
                    )
                    self._data_event("watchdog_stop", {"age_ms": age_ms, "count": self._watchdog_count})
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
                        with self._lock:
                            self._executed = (0, 0)
                        self._data_event("executed_drive", {"left_mm_s": 0, "right_mm_s": 0, "armed": False})
                    if target != last_sent:
                        self._robot.drive(*target)
                        last_sent = target
                        with self._lock:
                            self._executed = target
                        self._data_event(
                            "executed_drive",
                            {"left_mm_s": target[0], "right_mm_s": target[1], "armed": self._armed},
                        )
                    if now - last_sensor >= 0.2:
                        self._sensors = self._robot.sensors()
                        self._telemetry = dict(self._sensors["battery"])
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
                        last_sensor = time.monotonic()
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
                    with self._lock:
                        self._telemetry = {}
                        self._sensors = {}
                        self._executed = (0, 0)
                    event("serial", f"Connection lost · {error}", "danger")
                    self.disarm()
            time.sleep(0.04)
        if self._robot is not None:
            self._robot.close()
        event("system", "Controller offline · serial released", "ok")
