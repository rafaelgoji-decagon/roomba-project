import os
import time
import unittest

os.environ["ROOMBA_MOCK"] = "1"

from controller import RobotController, WATCHDOG_SECONDS


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

    def test_low_battery_cannot_arm(self) -> None:
        with self.controller._lock:
            self.controller._telemetry["percent"] = 3.0
        self.assertFalse(self.controller.arm())
        self.assertFalse(self.controller.snapshot()["battery_ok"])


if __name__ == "__main__":
    unittest.main()
