from __future__ import annotations

import argparse
import csv
from pathlib import Path

from training.common import discover_runs, enrich_run


def main() -> None:
    parser = argparse.ArgumentParser(description="Export route recordings as leakage-safe tabular data")
    parser.add_argument("--datasets", type=Path, default=Path("datasets"))
    parser.add_argument("--output", type=Path, default=Path("training/artifacts/samples.csv"))
    args = parser.parse_args()
    rows = [row for run in discover_runs(args.datasets) for row in enrich_run(run)]
    if not rows:
        raise SystemExit("No complete runs found")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Exported {len(rows)} samples from {len(set(r['run_id'] for r in rows))} runs to {args.output}")


if __name__ == "__main__":
    main()
