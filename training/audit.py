from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path

from training.common import discover_runs, enrich_run, load_jsonl


def audit(run_path: Path) -> dict:
    raw = load_jsonl(run_path / "labels.jsonl")
    rows = enrich_run(run_path)
    gaps = [b["monotonic_s"] - a["monotonic_s"] for a, b in zip(raw, raw[1:])]
    events = load_jsonl(run_path / "events.jsonl")
    sequences = [e["data"]["sequence"] for e in events if "sequence" in e.get("data", {})]
    sequence_gaps = sum(max(0, b - a - 1) for a, b in zip(sequences, sequences[1:]))
    watchdogs = [e for e in events if e["type"] == "watchdog_stop"]
    moving_watchdogs = 0
    last_executed = (0, 0)
    for event in events:
        if event["type"] == "executed_drive":
            data = event["data"]
            last_executed = (data.get("left_mm_s", 0), data.get("right_mm_s", 0))
        elif event["type"] == "watchdog_stop" and any(last_executed):
            moving_watchdogs += 1
            last_executed = (0, 0)
    complete_sensors = sum(r.get("sensors", {}).get("packet_group") == 100 for r in raw)
    return {
        "run_id": run_path.name,
        "samples": len(raw),
        "duration_s": rows[-1]["time_s"],
        "frequency_hz": (len(raw) - 1) / rows[-1]["time_s"],
        "max_sample_gap_s": max(gaps, default=0),
        "sample_gaps_over_0_5_s": sum(g > 0.5 for g in gaps),
        "complete_group_100": complete_sensors,
        "missing_sensor_samples": len(raw) - complete_sensors,
        "requested_sequence_gaps": sequence_gaps,
        "watchdog_events": len(watchdogs),
        "watchdog_while_moving": moving_watchdogs,
        "left_endpoint_mm": rows[-1]["left_mm"],
        "right_endpoint_mm": rows[-1]["right_mm"],
        "distance_endpoint_mm": rows[-1]["distance_mm"],
        "heading_endpoint_deg": rows[-1]["heading_rad"] * 180 / 3.141592653589793,
        "actions": Counter(r["action"] for r in raw),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--datasets", type=Path, default=Path("datasets"))
    parser.add_argument("--json", type=Path, default=Path("training/artifacts/audit.json"))
    parser.add_argument("--report", type=Path, default=Path("training/artifacts/AUDIT.md"))
    args = parser.parse_args()
    results = [audit(path) for path in discover_runs(args.datasets)]
    if not results:
        raise SystemExit("No runs found")
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(results, indent=2, default=dict) + "\n", encoding="utf-8")
    endpoints = [r["distance_endpoint_mm"] for r in results]
    lines = [
        "# Offline dataset audit", "",
        f"Runs: **{len(results)}**; samples: **{sum(r['samples'] for r in results)}**.", "",
        "| Run | Samples | Hz | Max gap (s) | Sensor missing | Sequence gaps | Moving watchdogs | Endpoint (m) | Heading (deg) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        lines.append(f"| {r['run_id']} | {r['samples']} | {r['frequency_hz']:.2f} | {r['max_sample_gap_s']:.3f} | {r['missing_sensor_samples']} | {r['requested_sequence_gaps']} | {r['watchdog_while_moving']} | {r['distance_endpoint_mm']/1000:.2f} | {r['heading_endpoint_deg']:.1f} |")
    lines += ["", f"Mean endpoint: **{statistics.mean(endpoints)/1000:.2f} m**; coefficient of variation: **{statistics.stdev(endpoints)/statistics.mean(endpoints)*100:.2f}%**.", ""]
    args.report.write_text("\n".join(lines), encoding="utf-8")
    print(f"Audited {len(results)} runs; wrote {args.json} and {args.report}")


if __name__ == "__main__":
    main()
