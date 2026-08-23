from __future__ import annotations

import argparse
from pathlib import Path

from training.common import discover_runs, enrich_run

COLORS = ("#2563eb", "#dc2626", "#16a34a", "#9333ea", "#ea580c", "#0891b2", "#4f46e5", "#be123c")


def scaled_polyline(rows, x_field, y_field, box, bounds):
    x0, y0, width, height = box
    xmin, xmax, ymin, ymax = bounds
    def point(row):
        x = x0 + (row[x_field] - xmin) / max(xmax - xmin, 1) * width
        y = y0 + height - (row[y_field] - ymin) / max(ymax - ymin, 1) * height
        return f"{x:.1f},{y:.1f}"
    return " ".join(point(row) for row in rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render dependency-free route diagnostics")
    parser.add_argument("--datasets", type=Path, default=Path("datasets"))
    parser.add_argument("--output", type=Path, default=Path("training/artifacts/routes.svg"))
    args = parser.parse_args()
    runs = [enrich_run(path) for path in discover_runs(args.datasets)]
    all_rows = [row for run in runs for row in run]
    trajectory_bounds = (min(r["x_mm"] for r in all_rows), max(r["x_mm"] for r in all_rows), min(r["y_mm"] for r in all_rows), max(r["y_mm"] for r in all_rows))
    velocity_bounds = (0, 1, min(min(r["left_velocity_mm_s"], r["right_velocity_mm_s"]) for r in all_rows), max(max(r["left_velocity_mm_s"], r["right_velocity_mm_s"]) for r in all_rows))
    svg = ['<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="620" viewBox="0 0 1200 620">', '<rect width="1200" height="620" fill="#fafafa"/>', '<style>text{font-family:system-ui,sans-serif;fill:#111827}.axis{stroke:#9ca3af}.run{fill:none;stroke-width:2;opacity:.72}</style>', '<text x="35" y="32" font-size="20" font-weight="700">Eight recorded routes — odometry diagnostics</text>', '<text x="35" y="65" font-size="15">Integrated encoder trajectory (millimetres)</text>', '<text x="635" y="65" font-size="15">Executed wheel velocity vs normalized progress</text>', '<rect x="35" y="80" width="530" height="480" fill="white" stroke="#d1d5db"/>', '<rect x="635" y="80" width="530" height="480" fill="white" stroke="#d1d5db"/>']
    for index, run in enumerate(runs):
        color = COLORS[index % len(COLORS)]
        svg.append(f'<polyline class="run" stroke="{color}" points="{scaled_polyline(run, "x_mm", "y_mm", (35,80,530,480), trajectory_bounds)}"/>')
        svg.append(f'<polyline class="run" stroke="{color}" points="{scaled_polyline(run, "progress", "left_velocity_mm_s", (635,80,530,480), velocity_bounds)}"/>')
        svg.append(f'<polyline class="run" stroke="{color}" stroke-dasharray="5 3" points="{scaled_polyline(run, "progress", "right_velocity_mm_s", (635,80,530,480), velocity_bounds)}"/>')
        svg.append(f'<circle cx="{45 + index*140}" cy="590" r="5" fill="{color}"/><text x="55" y="595" font-size="11">{run[0]["run_id"][13:19]}</text>')
    svg.append('<text x="940" y="585" font-size="12">solid: left · dashed: right</text></svg>')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(svg), encoding="utf-8")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
