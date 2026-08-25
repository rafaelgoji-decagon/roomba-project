import time
import unittest

from origin_aligner import MotionProfile, OriginAligner, motion_profile
from tests.test_autonomous import FakeController


class OriginAlignerTests(unittest.TestCase):
    def test_motion_profile_has_four_monotonic_bands(self):
        profiles = [motion_profile(error) for error in (12, 6, 3, 1)]
        self.assertEqual([profile.stage for profile in profiles], ["coarse", "approach", "refine", "fine"])
        self.assertEqual([profile.speed_limit_mm_s for profile in profiles], [75, 60, 48, 30])

    def test_wheel_scaling_preserves_ratio(self):
        left, right = OriginAligner._scale_wheels(100, 50, 75)
        self.assertEqual(left, 75)
        self.assertEqual(right, 37.5)

    def test_command_slew_limits_each_visual_update(self):
        self.assertEqual(OriginAligner._slew(0, 80), 18)
        self.assertEqual(OriginAligner._slew(18, -80), 0)
        self.assertEqual(OriginAligner._slew(10, 15), 15)

    def test_stale_camera_stops_without_motion(self):
        controller = FakeController()
        calls = 0

        def origin():
            nonlocal calls
            calls += 1
            return {
                "target_saved": True,
                "marker_ids": [0, 1, 2, 3],
                "observation_age_ms": 500 if calls > 1 else 0,
                "detection": {"frame_sequence": calls},
                "comparison": {
                    "aligned": False,
                    "offset_x_percent": 0,
                    "scale_ratio": 0.7,
                    "angle_error_deg": 0,
                },
            }

        aligner = OriginAligner(controller, origin)
        self.assertTrue(aligner.start())
        deadline = time.monotonic() + 2
        while aligner.status()["state"] == "running" and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(aligner.status()["state"], "fault")
        self.assertIn("imagen dejó de actualizarse", aligner.status()["message"])
        self.assertFalse(controller.armed)
        self.assertFalse(any(command != (0, 0) for command in controller.commands))

    def test_marker_loss_brakes_before_fault(self):
        controller = FakeController()
        calls = 0

        def origin():
            nonlocal calls
            calls += 1
            if calls <= 2:
                return {
                    "target_saved": True,
                    "marker_ids": [0, 1, 2, 3],
                    "observation_age_ms": 0,
                    "detection": {"frame_sequence": calls},
                    "comparison": {
                        "aligned": False,
                        "offset_x_percent": 0,
                        "scale_ratio": 0.7,
                        "angle_error_deg": 0,
                    },
                }
            return {"target_saved": True, "marker_ids": [], "observation_age_ms": 0}

        aligner = OriginAligner(controller, origin)
        self.assertTrue(aligner.start())
        deadline = time.monotonic() + 2
        while aligner.status()["state"] == "running" and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(aligner.status()["state"], "fault")
        moving = next(index for index, command in enumerate(controller.commands) if command != (0, 0))
        self.assertIn((0, 0), controller.commands[moving + 1:])
        self.assertFalse(controller.armed)

    def test_hazard_during_continuous_tracking_stops_and_disarms(self):
        controller = FakeController()
        sequence = 0

        def origin():
            nonlocal sequence
            sequence += 1
            return {
                "target_saved": True,
                "marker_ids": [0, 1, 2, 3],
                "observation_age_ms": 0,
                "detection": {"frame_sequence": sequence},
                "comparison": {"aligned": False, "offset_x_percent": 0, "scale_ratio": 0.7, "angle_error_deg": 0},
            }

        aligner = OriginAligner(controller, origin)
        self.assertTrue(aligner.start())

        def trigger_hazard():
            time.sleep(0.15)
            controller.hazard = True

        import threading
        threading.Thread(target=trigger_hazard, daemon=True).start()
        deadline = time.monotonic() + 2
        while aligner.status()["state"] == "running" and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(aligner.status()["state"], "fault")
        self.assertEqual(aligner.status()["step_label"], "Ajuste detenido")
        self.assertEqual(aligner.status()["command"], {"left_mm_s": 0, "right_mm_s": 0})
        self.assertIsNone(aligner.status()["errors"])
        self.assertFalse(controller.armed)
        self.assertTrue(any(command != (0, 0) for command in controller.commands))
        self.assertEqual(controller.commands[-1], (0, 0))

    def test_already_aligned_requires_stable_readings_and_disarms(self):
        controller = FakeController()
        sequence = iter(range(100))
        origin = lambda: {
            "target_saved": True,
            "marker_ids": [0, 1, 2, 3],
            "detection": {"frame_sequence": next(sequence)},
            "comparison": {"aligned": True},
        }
        aligner = OriginAligner(controller, origin)
        self.assertTrue(aligner.start())
        deadline = time.monotonic() + 3
        while aligner.status()["state"] == "running" and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(aligner.status()["state"], "aligned")
        self.assertFalse(controller.armed)
        self.assertFalse(any(command != (0, 0) for command in controller.commands))

    def test_motion_tracks_continuously_then_stops_when_aligned(self):
        controller = FakeController()
        calls = 0

        def origin():
            nonlocal calls
            calls += 1
            if calls < 3:
                return {
                    "target_saved": True,
                    "marker_ids": [0, 1, 2, 3],
                    "detection": {"frame_sequence": calls},
                    "comparison": {
                        "aligned": False,
                        "offset_x_percent": 0,
                        "scale_ratio": 0.8,
                        "angle_error_deg": 0,
                    },
                }
            return {
                "target_saved": True,
                "marker_ids": [0, 1, 2, 3],
                "detection": {"frame_sequence": calls},
                "comparison": {"aligned": True},
            }

        aligner = OriginAligner(controller, origin)
        self.assertTrue(aligner.start())
        deadline = time.monotonic() + 5
        while aligner.status()["state"] == "running" and time.monotonic() < deadline:
            time.sleep(0.05)
        moving_indexes = [i for i, command in enumerate(controller.commands) if command != (0, 0)]
        self.assertTrue(moving_indexes)
        first_left, first_right = controller.commands[moving_indexes[0]]
        self.assertEqual(first_left, first_right)
        self.assertTrue(any(command == (0, 0) for command in controller.commands[moving_indexes[-1] + 1:]))

    def test_hazard_blocks_start(self):
        controller = FakeController(hazard=True)
        origin = lambda: {"target_saved": True, "marker_ids": [0]}
        aligner = OriginAligner(controller, origin)
        self.assertFalse(aligner.start())
        self.assertEqual(aligner.status()["state"], "fault")


if __name__ == "__main__":
    unittest.main()
