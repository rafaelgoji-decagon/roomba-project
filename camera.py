"""Single-process webcam capture shared by the web stream and dataset recorder."""

from __future__ import annotations

import os
import subprocess
import threading
import time
from io import BytesIO
from typing import Iterator

from PIL import Image

from terminal_ui import event


class Camera:
    def __init__(self) -> None:
        self.device = os.getenv("ROOMBA_CAMERA", "/dev/video0")
        self.width = int(os.getenv("ROOMBA_CAMERA_WIDTH", "1280"))
        self.height = int(os.getenv("ROOMBA_CAMERA_HEIGHT", "720"))
        self.fps = int(os.getenv("ROOMBA_CAMERA_FPS", "30"))
        self.preview_width = int(os.getenv("ROOMBA_PREVIEW_WIDTH", "320"))
        self.preview_height = int(os.getenv("ROOMBA_PREVIEW_HEIGHT", "180"))
        self.preview_fps = float(os.getenv("ROOMBA_PREVIEW_FPS", "15"))
        self.preview_quality = int(os.getenv("ROOMBA_PREVIEW_QUALITY", "30"))
        self._condition = threading.Condition()
        self._frame: bytes | None = None
        self._sequence = 0
        self._preview: bytes | None = None
        self._preview_sequence = 0
        self._running = False
        self._thread: threading.Thread | None = None
        self._preview_thread: threading.Thread | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._error = "camera starting"

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="camera", daemon=True)
        self._preview_thread = threading.Thread(
            target=self._preview_loop, name="camera-preview", daemon=True
        )
        self._thread.start()
        self._preview_thread.start()

    def stop(self) -> None:
        self._running = False
        process = self._process
        if process is not None:
            process.terminate()
        with self._condition:
            self._condition.notify_all()
        if self._thread:
            self._thread.join(timeout=3)
        if self._preview_thread:
            self._preview_thread.join(timeout=3)

    def status(self) -> dict[str, object]:
        with self._condition:
            return {
                "online": self._frame is not None,
                "device": self.device,
                "capture": {"width": self.width, "height": self.height, "fps": self.fps, "color": True},
                "preview": {
                    "width": self.preview_width,
                    "height": self.preview_height,
                    "fps": self.preview_fps,
                    "grayscale": True,
                    "jpeg_quality": self.preview_quality,
                },
                "error": self._error,
            }

    def latest(self) -> tuple[int, bytes | None]:
        with self._condition:
            return self._sequence, self._frame

    def frames(self) -> Iterator[bytes]:
        seen = -1
        while self._running:
            with self._condition:
                self._condition.wait_for(
                    lambda: self._preview_sequence != seen or not self._running, timeout=2
                )
                if not self._running:
                    return
                seen, frame = self._preview_sequence, self._preview
            if frame:
                yield (
                    b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                    + str(len(frame)).encode("ascii")
                    + b"\r\n\r\n"
                    + frame
                    + b"\r\n"
                )

    def _command(self) -> list[str]:
        if os.getenv("ROOMBA_MOCK") == "1":
            return [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-re",
                "-f", "lavfi", "-i", f"testsrc=size={self.width}x{self.height}:rate={self.fps}",
                "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "5", "-",
            ]
        return [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "v4l2", "-input_format", "mjpeg", "-framerate", str(self.fps),
            "-video_size", f"{self.width}x{self.height}", "-i", self.device,
            "-f", "image2pipe", "-vcodec", "copy", "-",
        ]

    def _loop(self) -> None:
        while self._running:
            try:
                event("camera", f"Opening {self.device} · {self.width}x{self.height}@{self.fps}")
                self._process = subprocess.Popen(
                    self._command(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0
                )
                assert self._process.stdout is not None
                buffer = bytearray()
                while self._running:
                    chunk = self._process.stdout.read(4096)
                    if not chunk:
                        stderr = self._process.stderr.read().decode("utf-8", "replace").strip()
                        raise RuntimeError(stderr or "camera process ended")
                    buffer.extend(chunk)
                    while True:
                        start = buffer.find(b"\xff\xd8")
                        end = buffer.find(b"\xff\xd9", start + 2) if start >= 0 else -1
                        if start < 0 or end < 0:
                            if start > 0:
                                del buffer[:start]
                            break
                        frame = bytes(buffer[start : end + 2])
                        del buffer[: end + 2]
                        with self._condition:
                            self._frame = frame
                            self._sequence += 1
                            self._error = ""
                            self._condition.notify_all()
            except Exception as error:
                with self._condition:
                    self._frame = None
                    self._error = str(error)
                if self._running:
                    event("camera", f"Offline · {error}", "danger")
            finally:
                if self._process is not None and self._process.poll() is None:
                    self._process.terminate()
                self._process = None
            if self._running:
                time.sleep(2)
        event("camera", "Capture offline", "ok")

    def _preview_loop(self) -> None:
        """Encode only the freshest frame so preview work cannot form a queue."""
        seen = -1
        period = 1 / max(1, self.preview_fps)
        next_preview = time.monotonic()
        while self._running:
            with self._condition:
                self._condition.wait_for(
                    lambda: self._sequence != seen or not self._running, timeout=1
                )
                if not self._running:
                    return
                seen, frame = self._sequence, self._frame
            if frame is None:
                continue
            now = time.monotonic()
            if now < next_preview:
                time.sleep(next_preview - now)
                with self._condition:
                    seen, frame = self._sequence, self._frame
                if frame is None:
                    continue
            next_preview = max(next_preview + period, time.monotonic())
            try:
                preview = self._make_preview(frame)
            except Exception as error:
                event("camera", f"Preview failed · {error}", "warn")
                continue
            with self._condition:
                self._preview = preview
                self._preview_sequence += 1
                self._condition.notify_all()

    def _make_preview(self, frame: bytes) -> bytes:
        with Image.open(BytesIO(frame)) as image:
            image = image.convert("L")
            image.thumbnail((self.preview_width, self.preview_height), Image.Resampling.BILINEAR)
            output = BytesIO()
            image.save(output, format="JPEG", quality=self.preview_quality)
            return output.getvalue()
