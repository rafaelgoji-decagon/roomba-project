import io
import tempfile
import time
import unittest
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from origin_calibration import CAPTURE_SAMPLES, OriginCalibration


def board_frame(shift_x=0):
    canvas = np.full((720, 1280), 255, dtype=np.uint8)
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    size, gap = 220, 40
    left, top = 390 + shift_x, 100
    for marker_id in range(4):
        marker = cv2.aruco.generateImageMarker(dictionary, marker_id, size)
        x = left + (marker_id % 2) * (size + gap)
        y = top + (marker_id // 2) * (size + gap)
        canvas[y:y + size, x:x + size] = marker
    output = io.BytesIO()
    Image.fromarray(canvas).save(output, format="JPEG", quality=95)
    return output.getvalue()


class FrameSource:
    def __init__(self, frame):
        self.sequence = 0
        self.frame = frame

    def latest(self):
        self.sequence += 1
        return self.sequence, self.frame


class OriginCalibrationTests(unittest.TestCase):
    def test_detects_complete_board(self):
        source = FrameSource(board_frame())
        calibration = OriginCalibration(source.latest, Path(tempfile.mkdtemp()) / "origin.json")
        detection = calibration._detect(source.frame)
        self.assertTrue(detection["visible"])
        self.assertEqual(detection["marker_ids"], [0, 1, 2, 3])

    def test_capture_stable_origin_and_compare_offset(self):
        path = Path(tempfile.mkdtemp()) / "origin.json"
        source = FrameSource(board_frame())
        calibration = OriginCalibration(source.latest, path)
        calibration._detection = calibration._detect(source.frame)
        self.assertTrue(calibration.capture())
        calibration.start()
        deadline = time.monotonic() + 5
        while calibration.status()["state"] == "capturing" and time.monotonic() < deadline:
            time.sleep(0.05)
        calibration.stop()
        status = calibration.status()
        self.assertTrue(path.exists())
        self.assertEqual(status["state"], "saved")
        self.assertTrue(status["comparison"]["aligned"])
        shifted = calibration._detect(board_frame(80))
        comparison = calibration._compare(shifted, calibration._target)
        self.assertFalse(comparison["aligned"])
        self.assertNotEqual(comparison["guidance"], "Pose aligned")


if __name__ == "__main__":
    unittest.main()
