from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from pathlib import Path

from training.common import MM_PER_COUNT, WHEEL_BASE_MM, discover_runs, enrich_run, interpolate

FIELDS = ("left_mm", "right_mm", "x_mm", "y_mm", "heading_rad", "left_velocity_mm_s", "right_velocity_mm_s")


def fit(runs: list[list[dict]], points: int) -> list[dict]:
    result = []
    for index in range(points):
        progress = index / (points - 1)
        point = {"progress": progress}
        for field in FIELDS:
            point[field] = statistics.median(interpolate(run, progress, field) for run in runs)
        result.append(point)
    return result


def evaluate(reference: list[dict], run: list[dict]) -> dict:
    errors = {field: [] for field in FIELDS}
    for point in reference:
        for field in FIELDS:
            errors[field].append(interpolate(run, point["progress"], field) - point[field])
    return {
        "run_id": run[0]["run_id"],
        "left_position_mae_mm": statistics.mean(abs(x) for x in errors["left_mm"]),
        "right_position_mae_mm": statistics.mean(abs(x) for x in errors["right_mm"]),
        "cross_track_mae_mm": statistics.mean(math.hypot(x, y) for x, y in zip(errors["x_mm"], errors["y_mm"])),
        "heading_mae_deg": statistics.mean(abs(x) for x in errors["heading_rad"]) * 180 / math.pi,
        "left_velocity_mae_mm_s": statistics.mean(abs(x) for x in errors["left_velocity_mm_s"]),
        "right_velocity_mae_mm_s": statistics.mean(abs(x) for x in errors["right_velocity_mm_s"]),
        "endpoint_distance_error_mm": run[-1]["distance_mm"] - reference[-1]["left_mm"] / 2 - reference[-1]["right_mm"] / 2,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Fit a robust odometry-aligned route reference")
    parser.add_argument("--datasets", type=Path, default=Path("datasets"))
    parser.add_argument("--output-dir", type=Path, default=Path("training/artifacts"))
    parser.add_argument("--points", type=int, default=201)
    args = parser.parse_args()
    paths = discover_runs(args.datasets)
    runs = [enrich_run(path) for path in paths]
    if len(runs) < 3:
        raise SystemExit("At least three complete runs are required")
    folds = []
    for held_index, held_run in enumerate(runs):
        folds.append(evaluate(fit([run for i, run in enumerate(runs) if i != held_index], args.points), held_run))
    reference = fit(runs, args.points)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    model = {
        "model_type": "median_odometry_route_v1",
        "training_runs": [path.name for path in paths],
        "alignment": "normalized mean encoder distance",
        "points": args.points,
        "constants": {"mm_per_encoder_count": MM_PER_COUNT, "wheel_base_mm": WHEEL_BASE_MM},
        "source_sha256": {
            path.name: hashlib.sha256((path / "labels.jsonl").read_bytes()).hexdigest()
            for path in paths
        },
        "reference": reference,
    }
    (args.output_dir / "route_reference.json").write_text(json.dumps(model, indent=2) + "\n", encoding="utf-8")
    (args.output_dir / "validation.json").write_text(json.dumps(folds, indent=2) + "\n", encoding="utf-8")
    metrics = {key: statistics.mean(fold[key] for fold in folds) for key in folds[0] if key != "run_id"}
    report = [
        "# Local route training report", "",
        f"Trained `median_odometry_route_v1` on **{len(runs)} complete demonstrations** with **{args.points} reference points**.", "",
        "Validation uses leave-one-run-out: every run is evaluated against a reference fitted only on the other seven.", "",
        "| Metric | Mean |", "|---|---:|",
    ]
    for key, value in metrics.items():
        report.append(f"| {key} | {value:.2f} |")
    report += ["", "Decision: retain the robust odometry reference as the first offline baseline. These metrics measure demonstration agreement; they do not prove autonomous closed-loop success.", "", "No artifact in this directory has been deployed to the Raspberry Pi.", ""]
    (args.output_dir / "TRAINING_REPORT.md").write_text("\n".join(report), encoding="utf-8")
    print(f"Trained on {len(runs)} runs. Model: {args.output_dir / 'route_reference.json'}")
    for key, value in metrics.items():
        print(f"{key}: {value:.2f}")


if __name__ == "__main__":
    main()
