import unittest

from training.common import interpolate, unwrap
from training.train_route import fit


class TrainingTests(unittest.TestCase):
    def test_encoder_rollover_is_unwrapped(self):
        self.assertEqual(unwrap([65530, 3, 10]), [65530, 65539, 65546])

    def test_interpolation(self):
        rows = [{"progress": 0.0, "left_mm": 0}, {"progress": 1.0, "left_mm": 100}]
        self.assertEqual(interpolate(rows, 0.25, "left_mm"), 25)

    def test_fit_uses_median(self):
        def run(endpoint):
            return [{"progress": p, **{f: endpoint * p for f in ("left_mm", "right_mm", "x_mm", "y_mm", "heading_rad", "left_velocity_mm_s", "right_velocity_mm_s")}} for p in (0.0, 1.0)]
        model = fit([run(100), run(200), run(900)], 2)
        self.assertEqual(model[-1]["left_mm"], 200)


if __name__ == "__main__":
    unittest.main()
