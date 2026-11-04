# Shot analysis: velocity and launch metrics

## Scope

This module estimates the initial **early-flight** velocity and launch metrics from calibrated 3D ball-center positions. It does not process camera pixels, infer spin, or predict carry distance. Measurements from a short post-impact window approximate launch conditions; a straight-line fit does not model gravity, drag, or camera uncertainty.

## Coordinate and unit contract

- World coordinates: **+X downrange, +Y left, +Z up**.
- `estimate_velocity(positions, times_s)`: positions are an `(N, 3)` array in **meters**, timestamps an `(N,)` array in **seconds**, with `N >= 2`.
- Timestamps must be strictly increasing; all coordinates and times must be finite.
- Output: NumPy velocity vector `[vx, vy, vz]` in **m/s**.
- `launch_metrics(velocity)`: velocity in m/s, returns a dictionary with `speed_m_s`, `speed_mph`, `launch_angle_deg`, and `launch_direction_deg`. Undefined angles are `None` for zero speed or zero horizontal speed as appropriate.
- Positive horizontal launch direction means **left**; negative means **right**.

## Estimation

The estimator fits `p(t) = p0 + v*t` independently along all three axes using ordinary least squares. Subtracting the means of times and positions reduces numerical cancellation. For each axis:

```text
v = sum((t_i - mean(t)) * (p_i - mean(p))) / sum((t_i - mean(t))**2)
```

The resulting vector supplies all metrics consistently:

```text
speed_m_s           = sqrt(vx**2 + vy**2 + vz**2)
speed_mph           = speed_m_s * 2.2369362921
launch_angle_deg    = degrees(atan2(vz, hypot(vx, vy)))
launch_direction_deg= degrees(atan2(vy, vx))
```

The estimator rejects malformed shapes, insufficient samples, nonnumeric/nonfinite values, and duplicate or decreasing timestamps. It does **not** currently remove outliers, estimate confidence intervals, or enforce plausible golf-ball speed ranges.

## Simulator handoff

The `feature/simulation-pi` branch defines `generate_shot()`, `calculation_input(packet)`, and `analyze_samples(packet)`. Its packets contain `samples` with `t_ms`, `x_mm`, `y_mm`, and `z_mm`. The simulator's `calculation_input` adapter validates those samples and converts **mm -> m** and **ms -> s** before calling the shot-analysis functions. **Do not pass the raw packet directly to `estimate_velocity`.**

```python
from fairway_fusion.analysis.velocity import estimate_velocity
from fairway_fusion.analysis.launch_angle import launch_metrics

# positions_m, times_s = calculation_input(packet)  # simulation branch
velocity = estimate_velocity(positions_m, times_s)
result = {
    "velocity_m_s": velocity.tolist(),
    **launch_metrics(velocity),
}
```

Expected noise-free simulator scenarios:

| Scenario | Speed (m/s) | Elevation | Direction |
| --- | ---: | ---: | ---: |
| straight | 50 | 15° | 0° |
| high-launch | 35 | 35° | 0° |
| right-launch | 60 | 12° | -4° |

The simulator's `analyze_samples(packet)` also adds `shot_id` and `source` to its output.

## Testing and future validation

Run `python -m pytest tests/ -v` from the repository root. Existing unit tests cover ideal trajectories, uneven timing, invalid inputs, and the derived metrics. They do not by themselves establish an end-to-end integration pass against the separate simulation branch.

Future work: execute cross-branch integration tests, characterize noise and timing error, consider outlier-resistant fitting and confidence intervals, and evaluate gravity compensation. Claims of real-world accuracy require calibrated camera measurements.
