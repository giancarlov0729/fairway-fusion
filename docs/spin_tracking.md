# Marked-ball spin pipeline

The implementation follows the R1 design: two IMX296 global-shutter cameras and
multiple Pico-timed IR flashes during one exposure. Flash centre times define the
observations; camera FPS is NOT the spin sampling rate.

## Quick replay

From the repository root in an activated Python 3.10+ environment:

```sh
python -m pip install -e ".[dev,hardware]"
python -m fairway_fusion.synthetic data/recordings/demo
fairway-analyze data/recordings/demo/shot.json --output data/recordings/demo/result.json
python -m pytest -q
```

Windows activation: `.venv\Scripts\Activate.ps1`; Linux: `source .venv/bin/activate`.
The demo renders three flash-separated perspectives of a sphere in each camera at
3,000 RPM and 65 m/s, including rasterization and seeded noise. This verifies software
on synthetic inputs; it is not evidence of physical measurement accuracy.

## Ball markings

Use irregularly spaced matte dark dots that remain dark under the actual IR light.
An arbitrary asymmetric layout is supported; no labelled IDs or premeasured pattern
are required. Aim for 6–10 visible dots and at least FOUR shared dots between adjacent
flashes in EACH camera. Spread dots across the hemisphere; avoid a ring, grid, cluster,
repeated spacing, logos and merged marks. Mark enough of the ball to retain visibility
as it rotates, but the default matcher rejects more than 16 candidates per view.
Target approximately 3–8 pixel dot diameters. Limb markers are excluded.

## Algorithm

1. Validate optical pulse times, uncertainty and coverage by both exposure windows.
2. Segment isolated bright ball silhouettes on a dark background. Reject clipped,
   merged or noncircular blobs. Sort along each camera's configured image-space
   flight direction. The exact pulse count must be visible; no missed-flash guessing.
3. Undistort silhouette rays and fit a known-radius sphere to their tangent cone.
   This accounts for perspective instead of treating the ball as a flat disk.
   Transform centres to the common world frame and require stereo agreement.
4. Detect ink components and intersect each dot ray with the front sphere surface.
   Rotate the resulting outward unit normals into world coordinates.
5. Match triangle chord lengths, fit proper rotations by SVD, find one-to-one inlier
   assignments and refit. Reject competing orientations with similar support. Missing
   dots and isolated false detections are tolerated; arbitrary clutter is not.
6. Divide the relative rotation vector by the flash interval. Require agreement
   between cameras and intervals. Report RPM, axis unit vector and rad/s components.

Coordinates: right-handed world **+X downrange, +Y left, +Z up**. Calibration uses
`X_camera = R @ X_world + t`, in metres. Spin sign follows the right-hand rule.
Zero spin has `axis_world: null`. No generic spin-axis angle is reported because it
requires a specified reference plane. Launch elevation is positive up, direction
positive left. Calibrate extrinsics into this world frame before using these labels.

## Limits that affect the capture design

At 60 FPS, the principal rotation ambiguity begins at 1,800 RPM. Two simultaneous
cameras do not resolve complete revolutions between observations. At 10,000 RPM a
1 ms interval spans 60 degrees. Acceptance requires:

`max_rpm * 2*pi/60 * (dt + 2*uncertainty) < 0.9*pi`

The configured max_rpm is a true physical upper bound, not a knob to lower simply to
accept data. Timing error must also be less than 5% of the interval per endpoint.

This version requires **separated flash silhouettes**. At 15–90 m/s a 250 us interval
moves the ball just 3.75–22.5 mm, less than its 42.67 mm diameter. Those images overlap.
At 65 m/s, 1 ms gives 65 mm separation. Choose spacing using projected separation AND
the spin bound. Some low-speed/high-spin shots have no valid spacing with this method
and are rejected. Overlapping-exposure deconvolution is not implemented. Travel near
an optical axis can prevent separation even with sufficient physical displacement.

Ambient light can still cause blur during the long camera exposure. Control lighting,
background, gain, focus and IR filtering. Tune config/processing.yaml using real
captures. The silhouette detector expects modest off-axis ellipticity; strongly
elongated outlines are rejected. At least four common dots per camera are mandatory.

## Manifest and output

A shot manifest references two grayscale PNGs and a calibration JSON using paths
relative to the manifest. The synthetic demo generates a complete example. Real
capture timing uses the following structure (EXAMPLE VALUES ONLY):

```json
{
  "verified": true,
  "source": "bench-qualified optical timing; frame association checked",
  "pulse_centers_us": [1000, 2000, 3000],
  "pulse_widths_us": [8, 8, 8],
  "uncertainty_us": 2,
  "exposure_windows_us": [[100, 4000], [102, 4002]]
}
```

All times share one reference, such as Pico gate assertion, and describe optical
pulses rather than USB arrival. Never compare Pi SensorTimestamp directly to Pico
relative microseconds. Qualify camera latency, optical latency, oscillator error and
jitter on the bench. verified is a caller assertion, not something a CRC proves.
The Pi utility always writes verified=false until timing and association are checked.

accepted contains launch and spin. partial retains launch with null spin and a reason.
rejected cannot support launch measurements. CLI exits: 0 accepted, 1 partial/rejected,
2 malformed input/configuration. Residuals and inlier counts are diagnostics, not
calibrated confidence probabilities or accuracy guarantees.

## Calibration and validation

Run `python scripts/calibrate_cameras.py --help`. Provide at least 10 paired chessboard
views with varied positions/tilts, fixed focus and capture resolution. Pair list:
`[["left01.png","right01.png"], ...]`. Preserve physical corner ordering in both views;
symmetric chessboards can reverse it. The required --world-to-camera0 JSON contains
measured R and t for the golf frame. Output includes both intrinsics/distortion/world
poses and RMS errors. Validate with held-out board views and known distances.

Validate physical spin on an independently measured rotating-ball fixture, then paired
reference shots. Include low spin, 10,000 RPM, both signs, tilted axes, dropout, logos,
shadow, clipping and IR washout. Report error and rejection rates separately indoors
and outdoors. No real captures or measured calibration were available for this work.
Hardware accuracy, Pico electrical timing and Pi 5 processing latency remain unverified.

References: [OpenCV calibration](https://docs.opencv.org/4.11.0/d9/d0c/group__calib3d.html),
[project draft](https://docs.google.com/document/d/1_h8c6dmO0zNMb_MdtyRUbm4tI5UWQOrNig82ROOJvd8/edit).
