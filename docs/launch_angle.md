# Launch angle template

This module estimates **vertical elevation above the ground**, not horizontal
launch direction. It runs without cameras on a laptop or Raspberry Pi.

## Inputs and calculation

- Coordinates must be calibrated 3D world positions: x downrange, y sideways,
  z up. All three axes use the same distance units (meters recommended).
- Use actual capture timestamps in seconds, not USB message arrival times.
- Pass only the first few milliseconds of flight after impact, excluding the
  stationary ball. At least two observations are required; more can reduce noise.
- Fit position versus time independently on each axis with a straight line.
- Calculate `degrees(atan2(vz, hypot(vx, vy)))`.

Raw pixel coordinates are not valid inputs. Image y usually points down, and
stereo reconstruction must be aligned with the ground before using this module.
The future detection/calibration/tracking modules should supply these positions.

```python
from fairway_fusion.analysis.launch_angle import estimate_launch_angle

angle = estimate_launch_angle(
    positions=[(0, 0, 0.03), (0.05, 0, 0.0434)],
    timestamps=[0, 0.001],
)
print(f"Vertical launch angle: {angle:.2f} degrees")
```

## Run locally

From the repository root, with your Python environment activated:

```text
python -m pip install numpy pytest
python -m pip install -e .
python scripts/launch_angle_demo.py
python -m pytest
```

The demo uses synthetic positions for a 15-degree launch. It proves that the
calculation can run, not that the camera system achieves any particular accuracy.

## Known limitations

- No camera capture, ball detection, stereo reconstruction, or calibration yet.
- The fitted angle is an average over the supplied window; gravity and drag are
  not modeled. Long trajectories will not estimate the initial angle correctly.
- No outlier rejection, confidence score, or minimum useful displacement check.
  Near-stationary noisy data can produce an unreliable angle.
- Invalid shapes, nonfinite data, nonincreasing times, and zero fitted velocity
  raise ValueError. Callers should display an invalid-shot status in those cases.
- Hardware accuracy and performance are unvalidated.
