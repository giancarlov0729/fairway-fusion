# Estimated Carry

Module: `src/fairway_fusion/analysis/carry.py`
Tests: `tests/test_carry.py`
Owner: Steffano

Estimated carry is the distance the ball is predicted to fly before first landing, calculated from the **measured** ball speed and launch angle. It is a **model output, not a measurement**. The prototype does not measure spin, wind, or landing position, so every carry value is an estimate.

## Keeping estimates separate from measured results

| Measured (from cameras)  | Estimated (from the model)                |
| ------------------------ | ----------------------------------------- |
| `speed_m_s`, `speed_mph` | `estimated_carry_m`, `estimated_carry_yd` |
| `launch_angle_deg`       | `apex_height_m`, `flight_time_s`          |
| `launch_direction_deg`   | `carry_model`, `carry_assumptions`        |

Rules for the server, database, and UI:

1. Estimates live in their own `carry` block in the API response, never mixed into the measured fields.
2. The UI must label the value **"Estimated carry"**, not "Carry" or "Distance".
3. Store the model name and assumptions with every estimate, so a saved number can always be traced back to how it was produced.
4. `carry_is_estimate` is always `True`. Do not remove it.

## How to integrate

### Server

```python
from fairway_fusion.analysis.carry import carry_metrics

metrics = launch_metrics(velocity)   # existing call
carry = carry_metrics(metrics)       # one call, same dict
```

`preview_shot()` already does this and returns the result under `"carry"`. Real captures should call the same two lines. `carry_metrics` never raises: for an unusable shot it returns `None` values and a `carry_error` message, so the capture workflow won't crash.

### Returned fields

| Field                | Type             | Meaning                                          |
| -------------------- | ---------------- | ------------------------------------------------ |
| `estimated_carry_m`  | float or `None`  | Estimated carry, metres                          |
| `estimated_carry_yd` | float or `None`  | Estimated carry, yards                           |
| `apex_height_m`      | float or `None`  | Peak height of the flight                        |
| `flight_time_s`      | float or `None`  | Time until landing                               |
| `carry_model`        | string           | `"drag_spin"` (default), `"drag"`, or `"vacuum"` |
| `carry_assumptions`  | object           | Every assumption used (see below)                |
| `carry_is_estimate`  | bool             | Always `true`                                    |
| `carry_error`        | string or `None` | Reason no estimate was produced                  |

Example response block (`straight` simulated shot, 50 m/s at 15°):

```json
"carry": {
  "estimated_carry_m": 147.1,
  "estimated_carry_yd": 160.9,
  "apex_height_m": 17.0,
  "flight_time_s": 4.75,
  "carry_model": "drag_spin",
  "carry_assumptions": { "backspin_rpm": 2500.0, "drag_coeff": 0.26, "...": "..." },
  "carry_is_estimate": true,
  "carry_error": null
}
```

### UI

- Show carry only when `carry.carry_error` is `null`.
- When it is not `null` (no usable launch angle, for example), show a dash or "Not available", and do not show a zero.
- Place it in its own labelled area on the results screen, separate from speed and launch angle, with a short note such as "Estimated, spin not measured".
- Update the note in `the Flask/Jinja interface` and `docs/ui.md` that says carry is not calculated.

### Database

Storage for the estimate is not connected yet (Harshil). Suggested columns, all nullable: `estimated_carry_m`, `estimated_carry_yd`, `carry_model`, `carry_assumptions` (JSON text). Rejected shots have no estimate, so null must be allowed.

## The model

| Model       | What it does                                      | Use                                                          |
| ----------- | ------------------------------------------------- | ------------------------------------------------------------ |
| `vacuum`    | Ideal projectile, no air: `v² sin(2θ) / g`        | Baseline and sanity check only. Greatly over-estimates.      |
| `drag`      | Adds quadratic air drag, solved numerically (RK4) | Conservative floor. Under-estimates, because it has no lift. |
| `drag_spin` | Drag plus Magnus lift from an assumed backspin    | **Default.** Closest to real ball flight.                    |

Carry is measured from launch until the ball returns to ground level.

### Default assumptions

All are stored in `Assumptions` and returned in `carry_assumptions`.

| Assumption               | Default                                               |
| ------------------------ | ----------------------------------------------------- |
| Air density              | 1.225 kg/m³ (sea level, 15 °C)                        |
| Ball mass / diameter     | 0.04593 kg / 0.04267 m (regulation)                   |
| Drag coefficient         | 0.26, constant                                        |
| Backspin                 | 2,500 rpm, **assumed**, constant for the whole flight |
| Maximum lift coefficient | 0.22                                                  |
| Launch height            | 0 m                                                   |
| Ground                   | Flat, same level as launch                            |
| Wind                     | None                                                  |

## Limitations

- **Spin is assumed, not measured.** Spin measurement is deferred in the prototype. Real spin varies from roughly 2,500 rpm (driver) to over 8,000 rpm (wedges) and strongly changes carry. This is the largest source of error.
- **Spin does not decay** during flight in the model, and the drag coefficient is constant. Real balls change both with speed and spin.
- **Ideal conditions:** no wind, no altitude or temperature changes, flat ground.
- **Carry only.** It does not predict roll or total distance, and ignores side spin and curvature, so `launch_direction_deg` does not affect the estimate.
- **Not validated against measured data.** The default drag and lift values were tuned by hand to give plausible distances (about 234 yd for 150 mph at 11° with 2,600 rpm, and about 183 yd for 120 mph at 17° with 6,500 rpm). Treat estimates as approximate until compared with a reference launch monitor.
- **Errors in measured inputs carry through.** A small error in launch angle or speed from the cameras changes the estimate directly.

## Invalid input

`estimate_carry` raises `ValueError` or `TypeError` for non-numeric or non-finite values, speeds outside 0.1 to 100 m/s, launch angles of 0° or below or 90° or above, unknown models or units, and negative physical parameters. `carry_metrics` catches these and reports them in `carry_error` instead of raising.

## Testing

From the repository root:

```
python -m pytest tests/test_carry.py tests/test_shot_service.py -v
```

The tests check the exact vacuum formula, symmetry, model ordering, unit conversion, numerical step-size convergence, realistic ranges, invalid inputs, and the `carry_metrics` handoff.

Numerical integration uses RK4 for both position and velocity. Physical assumptions must be finite numbers; time steps are limited to 0.0001–0.01 seconds to bound processing time. Integration failures return `carry_error` through the server adapter.
