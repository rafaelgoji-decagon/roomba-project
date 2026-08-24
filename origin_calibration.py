"""ArUco-based capture and validation of the route's fixed origin pose."""

from __future__ import annotations

import io
import json
import math
import statistics
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np
from PIL import Image

from terminal_ui import event

EXPECTED_IDS = (0, 1, 2, 3)
CAPTURE_SAMPLES = 20
CAPTURE_TIMEOUT_SECONDS = 8


class OriginCalibration:
    def __init__(
        self,
        latest_frame: Callable[[], tuple[int, bytes | None]],
        path: Path,
        route_id: str = "nogal",
    ) -> None:
        self.latest_frame = latest_frame
        self.path = path
        self.route_id = route_id
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._last_sequence = -1
        self._detection: dict[str, Any] = {"visible": False, "marker_ids": []}
        self._target: dict[str, Any] | None = self._load_target()
        self._capture: list[dict[str, Any]] = []
        self._capture_started = 0.0
        self._state = "saved" if self._target else "looking"
        self._error = ""
        self._dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        parameters = cv2.aruco.DetectorParameters()
        parameters.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
        self._detector = cv2.aruco.ArucoDetector(self._dictionary, parameters)

    def _load_target(self) -> dict[str, Any] | None:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except Exception as error:
            event("origin", f"Unable to load origin calibration · {error}", "danger")
            return None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="origin-vision", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)

    def capture(self) -> bool:
        with self._lock:
            if not self._detection.get("visible"):
                self._error = "El tablero ArUco completo no está visible"
                return False
            self._capture = []
            self._capture_started = time.monotonic()
            self._state = "capturing"
            self._error = ""
        event("origin", f"Capturing {CAPTURE_SAMPLES} stable origin observations", "warn")
        return True

    def select_route(self, route_id: str, path: Path) -> None:
        """Switch the active calibration without restarting camera detection."""
        with self._lock:
            self.route_id = route_id
            self.path = path
            self._target = self._load_target()
            self._capture = []
            self._state = "saved" if self._target else "looking"
            self._error = ""
        event("origin", f"Selected {route_id} origin calibration", "ok")

    def status(self) -> dict[str, Any]:
        with self._lock:
            detection = dict(self._detection)
            target = self._target
            state = self._state
            error = self._error
            count = len(self._capture)
        comparison = self._compare(detection, target) if target and detection.get("visible") else None
        return {
            "route_id": self.route_id,
            "state": state,
            "error": error,
            "board_visible": bool(detection.get("visible")),
            "marker_ids": detection.get("marker_ids", []),
            "detection": {key: value for key, value in detection.items() if key not in {"corners", "visible", "marker_ids"}},
            "target_saved": target is not None,
            "saved_at": target.get("saved_at") if target else None,
            "capture_samples": count,
            "capture_required": CAPTURE_SAMPLES,
            "comparison": comparison,
        }

    def _loop(self) -> None:
        while self._running:
            sequence, frame = self.latest_frame()
            if frame is not None and sequence != self._last_sequence:
                self._last_sequence = sequence
                try:
                    detection = self._detect(frame)
                    detection["frame_sequence"] = sequence
                    with self._lock:
                        self._detection = detection
                        capturing = self._state == "capturing"
                        if capturing and detection["visible"]:
                            self._capture.append(detection)
                            if len(self._capture) >= CAPTURE_SAMPLES:
                                self._finish_capture_locked()
                        elif capturing and time.monotonic() - self._capture_started > CAPTURE_TIMEOUT_SECONDS:
                            self._state = "error"
                            self._error = "La captura expiró; mantén visible el tablero completo"
                except Exception as error:
                    with self._lock:
                        self._detection = {"visible": False, "marker_ids": []}
                        self._error = str(error)
            time.sleep(0.20)

    def _detect(self, frame: bytes) -> dict[str, Any]:
        with Image.open(io.BytesIO(frame)) as image:
            rgb = np.asarray(image.convert("RGB"))
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        corners, ids, _ = self._detector.detectMarkers(gray)
        detected: dict[int, list[list[float]]] = {}
        if ids is not None:
            for marker_id, marker_corners in zip(ids.flatten().tolist(), corners):
                if marker_id in EXPECTED_IDS:
                    detected[marker_id] = marker_corners.reshape(4, 2).astype(float).tolist()
        marker_ids = sorted(detected)
        if marker_ids != list(EXPECTED_IDS):
            partial = np.asarray([point for marker_id in marker_ids for point in detected[marker_id]], dtype=float)
            return {
                "visible": False,
                "marker_ids": marker_ids,
                "partial_center_x": round(float(partial[:, 0].mean() / gray.shape[1]), 6) if marker_ids else None,
            }
        ordered = np.asarray([point for marker_id in EXPECTED_IDS for point in detected[marker_id]], dtype=float)
        height, width = gray.shape
        minimum = ordered.min(axis=0)
        maximum = ordered.max(axis=0)
        horizontal_vectors = []
        for marker_id in EXPECTED_IDS:
            points = np.asarray(detected[marker_id])
            horizontal_vectors.extend((points[1] - points[0], points[2] - points[3]))
        angle_deg = statistics.median(math.degrees(math.atan2(vector[1], vector[0])) for vector in horizontal_vectors)
        return {
            "visible": True,
            "marker_ids": marker_ids,
            "center_x": round(float(ordered[:, 0].mean() / width), 6),
            "center_y": round(float(ordered[:, 1].mean() / height), 6),
            "width": round(float((maximum[0] - minimum[0]) / width), 6),
            "height": round(float((maximum[1] - minimum[1]) / height), 6),
            "angle_deg": round(float(angle_deg), 4),
            "image_width": width,
            "image_height": height,
            "corners": ordered.tolist(),
        }

    def _finish_capture_locked(self) -> None:
        fields = ("center_x", "center_y", "width", "height", "angle_deg")
        medians = {field: statistics.median(item[field] for item in self._capture) for field in fields}
        deviations = {field: statistics.pstdev(item[field] for item in self._capture) for field in fields}
        stable = (
            deviations["center_x"] <= 0.003
            and deviations["center_y"] <= 0.003
            and deviations["width"] <= 0.005
            and deviations["height"] <= 0.005
            and deviations["angle_deg"] <= 0.5
        )
        if not stable:
            self._state = "error"
            self._error = "La pose cambió durante la captura; mantén la Roomba completamente quieta"
            return
        corner_count = len(self._capture[0]["corners"])
        corner_medians = [
            [
                statistics.median(item["corners"][index][axis] for item in self._capture)
                for axis in (0, 1)
            ]
            for index in range(corner_count)
        ]
        target = {
            "format_version": 1,
            "marker_dictionary": "DICT_4X4_50",
            "marker_ids": list(EXPECTED_IDS),
            "marker_size_mm": 70.0,
            "marker_gap_mm": 12.0,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "sample_count": len(self._capture),
            "pose": medians,
            "stability": deviations,
            "image_size": [self._capture[0]["image_width"], self._capture[0]["image_height"]],
            "corners": corner_medians,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(target, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)
        self._target = target
        self._state = "saved"
        self._error = ""
        event("origin", "Origin pose saved from stable ArUco observations", "ok")

    @staticmethod
    def _compare(detection: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
        pose = target["pose"]
        dx = detection["center_x"] - pose["center_x"]
        dy = detection["center_y"] - pose["center_y"]
        scale = detection["width"] / max(pose["width"], 1e-6)
        angle = detection["angle_deg"] - pose["angle_deg"]
        target_corners = np.asarray(target["corners"], dtype=float)
        current_corners = np.asarray(detection["corners"], dtype=float)
        diagonal = math.hypot(detection["image_width"], detection["image_height"])
        corner_error = float(np.sqrt(np.mean(np.sum((current_corners - target_corners) ** 2, axis=1))) / diagonal)
        aligned = (
            abs(dx) <= 0.015
            and abs(dy) <= 0.02
            and abs(scale - 1) <= 0.04
            and abs(angle) <= 1.5
            and corner_error <= 0.01
        )
        penalties = (
            abs(dx) / 0.015,
            abs(dy) / 0.02,
            abs(scale - 1) / 0.04,
            abs(angle) / 1.5,
            corner_error / 0.01,
        )
        score = max(0.0, 100.0 * (1.0 - min(1.0, sum(penalties) / 8.0)))
        guidance = "Pose alineada" if aligned else OriginCalibration._guidance(dx, dy, scale, angle, corner_error)
        return {
            "aligned": aligned,
            "score": round(score, 1),
            "offset_x_percent": round(dx * 100, 2),
            "offset_y_percent": round(dy * 100, 2),
            "scale_ratio": round(scale, 4),
            "angle_error_deg": round(angle, 2),
            "corner_error_percent": round(corner_error * 100, 2),
            "guidance": guidance,
        }

    @staticmethod
    def _guidance(dx: float, dy: float, scale: float, angle: float, corner_error: float) -> str:
        if abs(scale - 1) > 0.04:
            return "Aleja la Roomba del marcador" if scale > 1 else "Acerca la Roomba al marcador"
        if abs(dx) > 0.015:
            return "Mueve la Roomba a la derecha" if dx > 0 else "Mueve la Roomba a la izquierda"
        if abs(dy) > 0.02:
            return "Ajusta la posición vertical respecto al tablero"
        if abs(angle) > 1.5:
            return "Gira en sentido antihorario" if angle > 0 else "Gira en sentido horario"
        if corner_error > 0.01:
            return "Ajusta el ángulo hasta igualar la perspectiva del tablero"
        return "Hace falta un ajuste fino"
