import json
import tempfile
import time
import unittest
from pathlib import Path

from autonomous import AutonomousRunner


class FakeController:
    def __init__(self, hazard=False):
        self.armed = False
        self.emergency = False
        self.hazard = hazard
        self.commands = []

    def snapshot(self):
        return {
            "battery_ok": True,
            "status": "connected",
            "armed": self.armed,
            "emergency": self.emergency,
            "sensors": {
                "encoders": {"left": 100, "right": 100},
                "bumps": {"left": self.hazard, "right": False},
                "wheel_drops": {"caster": False, "left": False, "right": False},
                "cliff": {"left": False, "front_left": False, "front_right": False, "right": False},
            },
        }

    def arm(self): self.armed = True; return True
    def disarm(self): self.armed = False
    def clear_emergency(self): self.emergency = False
    def stop_motion(self): self.commands.append((0, 0))
    def emergency_stop(self): self.emergency = True; self.armed = False
    def command_wheels(self, left, right, limit): self.commands.append((left, right)); return self.armed


def model_path():
    path = Path(tempfile.mkdtemp()) / "route.json"
    point = {"left_mm": 0, "right_mm": 0, "x_mm": 0, "y_mm": 0, "heading_rad": 0, "left_velocity_mm_s": 10, "right_velocity_mm_s": 10}
    end = dict(point, left_mm=100, right_mm=100)
    path.write_text(json.dumps({"model_type": "median_odometry_route_v1", "reference": [dict(point, progress=0), dict(end, progress=1)]}))
    return path


class AutonomousTests(unittest.TestCase):
    def test_ready_requires_clear_safety_sensors(self):
        runner = AutonomousRunner(FakeController(hazard=True), model_path())
        self.assertFalse(runner.ready())
        self.assertEqual(runner.status()["state"], "fault")

    def test_two_step_confirmation_and_emergency(self):
        controller = FakeController()
        runner = AutonomousRunner(controller, model_path())
        self.assertFalse(runner.start())
        self.assertTrue(runner.ready())
        self.assertEqual(runner.status()["state"], "ready")
        self.assertTrue(runner.start())
        self.assertEqual(runner.status()["state"], "running")
        runner.emergency_stop()
        self.assertEqual(runner.status()["state"], "fault")
        self.assertTrue(controller.emergency)

    def test_route_launches_from_stationary_reference_with_speed_cap(self):
        controller = FakeController()
        runner = AutonomousRunner(controller, model_path())
        self.assertTrue(runner.ready())
        self.assertTrue(runner.start())
        time.sleep(0.25)
        runner.pause()
        moving = [command for command in controller.commands if command != (0, 0)]
        self.assertTrue(moving)
        self.assertTrue(all(max(abs(left), abs(right)) <= 125 for left, right in moving))


if __name__ == "__main__":
    unittest.main()
