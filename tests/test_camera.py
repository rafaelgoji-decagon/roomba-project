import os
import unittest
from io import BytesIO
from unittest.mock import patch

from PIL import Image

from camera import Camera


class CameraTests(unittest.TestCase):
    def test_preview_defaults_balance_latency_and_vision_load(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ROOMBA_CAMERA_FPS", None)
            os.environ.pop("ROOMBA_PREVIEW_FPS", None)
            camera = Camera()
        self.assertEqual(camera.fps, 30)
        self.assertEqual(camera.preview_fps, 15)

    def test_preview_is_grayscale_and_bounded(self):
        source = BytesIO()
        Image.new("RGB", (1280, 720), "red").save(source, format="JPEG")
        camera = Camera()
        preview = camera._make_preview(source.getvalue())
        with Image.open(BytesIO(preview)) as image:
            self.assertEqual(image.mode, "L")
            self.assertLessEqual(image.width, camera.preview_width)
            self.assertLessEqual(image.height, camera.preview_height)


if __name__ == "__main__":
    unittest.main()
