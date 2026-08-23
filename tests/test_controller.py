import os
import struct
import time
import unittest

os.environ["ROOMBA_MOCK"] = "1"

from controller import RobotController, WATCHDOG_SECONDS, parse_sensor_packet, validate_battery_packet


class ControllerSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = RobotController()
        self.controller.start()
        deadline = time.monotonic() + 1
        while not self.controller.snapshot()["battery"] and time.monotonic() < deadline:
            time.sleep(0.01)

    def tearDown(self) -> None:
        self.controller.shutdown()

    def test_watchdog_stops_stale_drive_command(self) -> None:
        self.assertTrue(self.controller.arm())
        self.controller.command(0, 1)
        deadline = time.monotonic() + 0.3
        while self.controller.snapshot()["motors"]["left"] == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertGreater(self.controller.snapshot()["motors"]["left"], 0)
        time.sleep(WATCHDOG_SECONDS + 0.12)
        state = self.controller.snapshot()
        self.assertEqual(state["motors"], {"left": 0, "right": 0})
        self.assertFalse(state["watchdog_ok"])

    def test_emergency_stop_disarms(self) -> None:
        self.assertTrue(self.controller.arm())
        self.controller.command(1, 0)
        self.controller.emergency_stop()
        state = self.controller.snapshot()
        self.assertFalse(state["armed"])
        self.assertTrue(state["emergency"])
        self.assertEqual(state["motors"], {"left": 0, "right": 0})

    def test_explicit_wheel_command_keeps_safety_gates_and_speed_limit(self) -> None:
        self.assertFalse(self.controller.command_wheels(400, 400, 125))
        self.assertTrue(self.controller.arm())
        self.assertTrue(self.controller.command_wheels(400, -400, 125))
        deadline = time.monotonic() + 0.3
        while self.controller.snapshot()["motors"]["left"] == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.controller.snapshot()["motors"], {"left": 125, "right": -125})

    def test_low_battery_can_arm_when_threshold_disabled(self) -> None:
        with self.controller._lock:
            self.controller._telemetry["percent"] = 3.0
        self.assertTrue(self.controller.arm())
        self.assertTrue(self.controller.snapshot()["battery_ok"])

    def test_corrupt_battery_packets_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid battery packet"):
            validate_battery_packet(254, 65280, -2, 65278, 65278)
        validate_battery_packet(0, 16170, 21, 2522, 2697)

    def test_extended_sensor_packet_is_parsed(self) -> None:
        raw = bytearray(80)
        raw[0] = 0x03
        raw[2] = 1
        struct.pack_into(">h", raw, 12, 42)
        struct.pack_into(">h", raw, 14, -7)
        raw[16] = 0
        struct.pack_into(">H", raw, 17, 16170)
        struct.pack_into(">h", raw, 19, -111)
        struct.pack_into(">b", raw, 21, 21)
        struct.pack_into(">H", raw, 22, 2522)
        struct.pack_into(">H", raw, 24, 2697)
        raw[40] = 2
        struct.pack_into(">h", raw, 48, 300)
        struct.pack_into(">h", raw, 50, 250)
        struct.pack_into(">H", raw, 52, 1234)
        struct.pack_into(">H", raw, 54, 2345)
        raw[56] = 0x21
        struct.pack_into(">h", raw, 71, -120)
        sensors = parse_sensor_packet(bytes(raw))
        self.assertEqual(sensors["packet_group"], 100)
        self.assertTrue(sensors["bumps"]["left"])
        self.assertEqual(sensors["distance_mm_delta"], 42)
        self.assertEqual(sensors["angle_deg_delta"], -7)
        self.assertEqual(sensors["encoders"], {"left": 1234, "right": 2345})
        self.assertEqual(sensors["motor_currents_ma"]["left"], -120)
        self.assertEqual(sensors["battery"]["percent"], 93.5)


if __name__ == "__main__":
    unittest.main()
