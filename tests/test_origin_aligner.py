import time
import unittest

from origin_aligner import OriginAligner
from tests.test_autonomous import FakeController


class OriginAlignerTests(unittest.TestCase):
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

    def test_hazard_blocks_start(self):
        controller = FakeController(hazard=True)
        origin = lambda: {"target_saved": True, "marker_ids": [0]}
        aligner = OriginAligner(controller, origin)
        self.assertFalse(aligner.start())
        self.assertEqual(aligner.status()["state"], "fault")


if __name__ == "__main__":
    unittest.main()
