"""Single-process webcam capture shared by the web stream and dataset recorder."""

from __future__ import annotations

import os
import subprocess
import threading
import time
from typing import Iterator

from terminal_ui import event


class Camera:
    def __init__(self) -> None:
        self.device = os.getenv("ROOMBA_CAMERA", "/dev/video0")
        self.width = int(os.getenv("ROOMBA_CAMERA_WIDTH", "640"))
        self.height = int(os.getenv("ROOMBA_CAMERA_HEIGHT", "480"))
        self.fps = int(os.getenv("ROOMBA_CAMERA_FPS", "10"))
        self._condition = threading.Condition()
        self._frame: bytes | None = None
        self._sequence = 0
        self._running = False
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._error = "camera starting"

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="camera", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        process = self._process
        if process is not None:
            process.terminate()
        with self._condition:
            self._condition.notify_all()
        if self._thread:
            self._thread.join(timeout=3)

    def status(self) -> dict[str, object]:
        with self._condition:
            return {
                "online": self._frame is not None,
                "device": self.device,
                "width": self.width,
                "height": self.height,
                "fps": self.fps,
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
                    lambda: self._sequence != seen or not self._running, timeout=2
                )
                if not self._running:
                    return
                seen, frame = self._sequence, self._frame
            if frame:
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"

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
