"""Training-session recorder: camera frames plus synchronized control labels."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from camera import Camera
from terminal_ui import event


def _direction(left: int, right: int) -> str:
    if left == 0 and right == 0:
        return "stop"
    if left > 0 and right > 0:
        return "forward"
    if left < 0 and right < 0:
        return "backward"
    if left < right:
        return "left"
    return "right"


class DatasetRecorder:
    def __init__(self, camera: Camera, snapshot: Callable[[], dict]) -> None:
        self.root = Path(os.getenv("ROOMBA_DATA_DIR", "datasets")).resolve()
        self.camera = camera
        self.snapshot = snapshot
        self.hz = float(os.getenv("ROOMBA_DATA_HZ", "5"))
        self._lock = threading.Lock()
        self._active = False
        self._session_id: str | None = None
        self._session_dir: Path | None = None
        self._started_at: str | None = None
        self._samples = 0
        self._last_error = ""
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="dataset", daemon=True)
        self._thread.start()

    def start(self, run_mode: str = "manual", metadata_extra: dict | None = None) -> bool:
        with self._lock:
            if self._active:
                return True
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            self._session_id = f"run-{stamp}-{uuid.uuid4().hex[:6]}"
            self._session_dir = self.root / self._session_id
            (self._session_dir / "frames").mkdir(parents=True, exist_ok=False)
            self._started_at = datetime.now(timezone.utc).isoformat()
            metadata = {
                "format_version": 2,
                "session_id": self._session_id,
                "started_at": self._started_at,
                "sample_hz": self.hz,
                "camera": self.camera.status(),
                "run_mode": run_mode,
            }
            if metadata_extra:
                metadata.update(metadata_extra)
            (self._session_dir / "metadata.json").write_text(
                json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
            )
            self._samples = 0
            self._last_error = ""
            self._active = True
            session_id = self._session_id
        event("dataset", f"RECORDING · {session_id}", "warn")
        return True

    def record_event(self, kind: str, payload: dict) -> None:
        """Append lossless command/control events while a session is active."""
        with self._lock:
            if not self._active or self._session_dir is None:
                return
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "monotonic_s": round(time.monotonic(), 6),
                "type": kind,
                "data": payload,
            }
            try:
                with (self._session_dir / "events.jsonl").open("a", encoding="utf-8") as events:
                    events.write(json.dumps(record, separators=(",", ":")) + "\n")
            except Exception as error:
                self._last_error = str(error)

    def stop(self) -> None:
        with self._lock:
            was_active = self._active
            session_id = self._session_id
            samples = self._samples
            self._active = False
        if was_active:
            event("dataset", f"SAVED · {session_id} · {samples} samples", "ok")

    def shutdown(self) -> None:
        self.stop()
        self._running = False
        self._thread.join(timeout=2)

    def status(self) -> dict[str, object]:
        with self._lock:
            return {
                "recording": self._active,
                "session_id": self._session_id,
                "started_at": self._started_at,
                "samples": self._samples,
                "error": self._last_error,
            }

    def _loop(self) -> None:
        period = 1 / max(1, self.hz)
        while self._running:
            began = time.monotonic()
            with self._lock:
                active, session_dir, number = self._active, self._session_dir, self._samples
            if active and session_dir is not None:
                try:
                    frame_sequence, frame = self.camera.latest()
                    state = self.snapshot()
                    left = int(state.get("motors", {}).get("left", 0))
                    right = int(state.get("motors", {}).get("right", 0))
                    frame_name = f"frames/{number:07d}.jpg" if frame else None
                    if frame:
                        (session_dir / frame_name).write_bytes(frame)
                    sample = {
                        "sample": number,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "monotonic_s": round(time.monotonic(), 6),
                        "frame": frame_name,
                        "camera_sequence": frame_sequence if frame else None,
                        "action": _direction(left, right),
                        "motors_mm_s": {"left": left, "right": right},
                        "armed": state.get("armed", False),
                        "watchdog_ok": state.get("watchdog_ok", False),
                        "battery": state.get("battery", {}),
                        "requested": state.get("requested", {}),
                        "executed": state.get("executed", {}),
                        "sensors": state.get("sensors", {}),
                        "control": state.get("control", {}),
                        "robot_status": state.get("status"),
                        "emergency": state.get("emergency", False),
                    }
                    with (session_dir / "labels.jsonl").open("a", encoding="utf-8") as labels:
                        labels.write(json.dumps(sample, separators=(",", ":")) + "\n")
                    with self._lock:
                        if self._active and self._session_dir == session_dir:
                            self._samples += 1
                except Exception as error:
                    with self._lock:
                        self._last_error = str(error)
                    event("dataset", f"Write failed · {error}", "danger")
            time.sleep(max(0.01, period - (time.monotonic() - began)))
