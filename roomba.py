"""Minimal, read-only-friendly iRobot Open Interface serial helper."""

from __future__ import annotations

import glob
import struct
import time

import serial


CHARGING_STATES = {
    0: "not charging",
    1: "reconditioning",
    2: "full charging",
    3: "trickle charging",
    4: "waiting",
    5: "charging fault",
}


def find_port() -> str:
    candidates = sorted(
        glob.glob("/dev/cu.usbserial-*")
        + glob.glob("/dev/cu.usbmodem*")
        + glob.glob("/dev/ttyUSB*")
        + glob.glob("/dev/ttyACM*")
    )
    if not candidates:
        raise RuntimeError("No USB serial adapter found (checked macOS and Linux device names)")
    return candidates[0]


class Roomba:
    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 2.0):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.serial: serial.Serial | None = None

    def __enter__(self) -> "Roomba":
        self.serial = serial.Serial(self.port, self.baudrate, timeout=self.timeout)
        self.serial.reset_input_buffer()
        self.serial.write(bytes([128]))  # OI START: passive mode, motors remain off.
        self.serial.flush()
        time.sleep(0.1)
        return self

    def __exit__(self, *_: object) -> None:
        if self.serial is not None:
            self.serial.close()

    def battery(self) -> dict[str, int | float | str]:
        if self.serial is None:
            raise RuntimeError("Serial port is not open")

        # Query list: charging state, voltage, current, temperature, charge, capacity.
        self.serial.reset_input_buffer()
        self.serial.write(bytes([149, 6, 21, 22, 23, 24, 25, 26]))
        self.serial.flush()
        raw = self.serial.read(10)
        if len(raw) != 10:
            raise TimeoutError(f"expected 10 sensor bytes, received {len(raw)}")

        state, millivolts, milliamps, temp_c, charge, capacity = struct.unpack(">BHhbHH", raw)
        return {
            "volts": millivolts / 1000,
            "amps": milliamps / 1000,
            "temp_c": temp_c,
            "charge_mah": charge,
            "capacity_mah": capacity,
            "percent": (100 * charge / capacity) if capacity else 0.0,
            "charging": CHARGING_STATES.get(state, f"unknown ({state})"),
        }
