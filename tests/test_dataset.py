import tempfile
import time
import unittest
from pathlib import Path

from dataset import DatasetRecorder, _direction


class FakeCamera:
    def status(self):
        return {"online": True}

    def latest(self):
        return 7, b"\xff\xd8test\xff\xd9"


class DatasetTests(unittest.TestCase):
    def test_direction_labels(self):
        self.assertEqual(_direction(0, 0), "stop")
        self.assertEqual(_direction(500, 500), "forward")
        self.assertEqual(_direction(-500, -500), "backward")
        self.assertEqual(_direction(-500, 500), "left")
        self.assertEqual(_direction(500, -500), "right")

    def test_session_writes_frames_and_labels(self):
        state = {
            "motors": {"left": -500, "right": 500},
            "armed": True,
            "watchdog_ok": True,
            "battery": {"percent": 80},
        }
        recorder = DatasetRecorder(FakeCamera(), lambda: state)
        recorder.root = Path(tempfile.mkdtemp())
        recorder.hz = 20
        try:
            recorder.start()
            time.sleep(0.25)
            recorder.stop()
            status = recorder.status()
            session = recorder.root / str(status["session_id"])
            self.assertTrue((session / "metadata.json").exists())
            self.assertTrue((session / "labels.jsonl").exists())
            self.assertGreaterEqual(len(list((session / "frames").glob("*.jpg"))), 1)
            self.assertIn('"action":"left"', (session / "labels.jsonl").read_text())
        finally:
            recorder.shutdown()


if __name__ == "__main__":
    unittest.main()
