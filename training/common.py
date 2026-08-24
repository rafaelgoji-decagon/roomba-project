from __future__ import annotations

import json
import math
from pathlib import Path

ENCODER_MODULUS = 65536
ENCODER_COUNTS_PER_REV = 508.8
WHEEL_DIAMETER_MM = 72.0
MM_PER_COUNT = math.pi * WHEEL_DIAMETER_MM / ENCODER_COUNTS_PER_REV
WHEEL_BASE_MM = 235.0


def load_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def discover_runs(dataset_dir: Path, route_id: str | None = None) -> list[Path]:
    runs = []
    for path in dataset_dir.glob("run-*"):
        metadata_path = path / "metadata.json"
        if not (path / "labels.jsonl").is_file() or not metadata_path.is_file():
            continue
        if route_id is not None:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata.get("route_id") != route_id:
                continue
        runs.append(path)
    return sorted(runs)


def unwrap(values: list[int]) -> list[int]:
    if not values:
        return []
    result = [values[0]]
    for previous, current in zip(values, values[1:]):
        delta = (current - previous + ENCODER_MODULUS // 2) % ENCODER_MODULUS
        delta -= ENCODER_MODULUS // 2
        result.append(result[-1] + delta)
    return result


def enrich_run(path: Path) -> list[dict]:
    rows = load_jsonl(path / "labels.jsonl")
    left = unwrap([row["sensors"]["encoders"]["left"] for row in rows])
    right = unwrap([row["sensors"]["encoders"]["right"] for row in rows])
    left0, right0 = left[0], right[0]
    x = y = heading = 0.0
    previous_left = previous_right = 0.0
    enriched = []
    for index, (row, left_count, right_count) in enumerate(zip(rows, left, right)):
        left_mm = (left_count - left0) * MM_PER_COUNT
        right_mm = (right_count - right0) * MM_PER_COUNT
        dl, dr = left_mm - previous_left, right_mm - previous_right
        distance = (dl + dr) / 2.0
        delta_heading = (dr - dl) / WHEEL_BASE_MM
        x += distance * math.cos(heading + delta_heading / 2.0)
        y += distance * math.sin(heading + delta_heading / 2.0)
        heading += delta_heading
        previous_left, previous_right = left_mm, right_mm
        enriched.append({
            "run_id": path.name,
            "sample": row["sample"],
            "time_s": row["monotonic_s"] - rows[0]["monotonic_s"],
            "left_mm": left_mm,
            "right_mm": right_mm,
            "distance_mm": (left_mm + right_mm) / 2.0,
            "heading_rad": heading,
            "x_mm": x,
            "y_mm": y,
            "left_velocity_mm_s": row["executed"]["left_mm_s"],
            "right_velocity_mm_s": row["executed"]["right_mm_s"],
            "requested_left_mm_s": row["requested"]["left_mm_s"],
            "requested_right_mm_s": row["requested"]["right_mm_s"],
            "action": row["action"],
            "watchdog_ok": row["watchdog_ok"],
        })
    endpoint = max(enriched[-1]["distance_mm"], 1.0)
    for item in enriched:
        item["progress"] = min(1.0, max(0.0, item["distance_mm"] / endpoint))
    return enriched


def interpolate(rows: list[dict], progress: float, field: str) -> float:
    if progress <= rows[0]["progress"]:
        return float(rows[0][field])
    for before, after in zip(rows, rows[1:]):
        if after["progress"] >= progress:
            width = after["progress"] - before["progress"]
            if width <= 0:
                return float(after[field])
            ratio = (progress - before["progress"]) / width
            return float(before[field]) + ratio * (float(after[field]) - float(before[field]))
    return float(rows[-1][field])
