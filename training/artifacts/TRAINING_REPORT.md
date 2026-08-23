# Local route training report

Trained `median_odometry_route_v1` on **8 complete demonstrations** with **201 reference points**.

Validation uses leave-one-run-out: every run is evaluated against a reference fitted only on the other seven.

| Metric | Mean |
|---|---:|
| left_position_mae_mm | 192.64 |
| right_position_mae_mm | 191.48 |
| cross_track_mae_mm | 485.58 |
| heading_mae_deg | 7.06 |
| left_velocity_mae_mm_s | 23.49 |
| right_velocity_mae_mm_s | 21.58 |
| endpoint_distance_error_mm | -74.41 |

Decision: retain the robust odometry reference as the first offline baseline. These metrics measure demonstration agreement; they do not prove autonomous closed-loop success.

No artifact in this directory has been deployed to the Raspberry Pi.
