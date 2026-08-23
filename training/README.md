# Local route training

This directory builds an offline, robust trajectory reference from complete recorded runs. It does not connect to or deploy anything on the Raspberry Pi.

From the repository root:

```bash
.venv/bin/python -m training.audit
.venv/bin/python -m training.export_dataset
.venv/bin/python -m training.train_route
.venv/bin/python -m training.plot_routes
```

Outputs are written to `training/artifacts/`:

- `route_reference.json`: fitted median trajectory and source-data hashes.
- `validation.json`: leave-one-run-out metrics for all eight demonstrations.
- `samples.csv`: tabular sample export retaining `run_id` to prevent leakage.
- `AUDIT.md` and `audit.json`: dataset integrity and route statistics.
- `TRAINING_REPORT.md`: model choice and aggregate validation.
- `routes.svg`: trajectory and executed-wheel-speed diagnostics.

Encoder rollover is unwrapped before conversion to relative wheel distance. Runs are aligned by normalized mean encoder distance, and each reference point is the median across complete runs. Validation always holds out an entire run.

The reference is an offline baseline, not a deployable autonomous controller. Real execution still requires feedback control, deviation limits, speed/acceleration limits, and the existing safety interlocks.
