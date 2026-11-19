"""Measure a short chip that stays in a calibrated vertical plane."""

import cv2
import numpy as np

from fairway_fusion.analysis.launch_angle import launch_metrics
from fairway_fusion.analysis.velocity import estimate_velocity


def calibrate_plane(corners, width_m, height_m):
    """Map top-left, top-right, bottom-right, bottom-left pixels to X/Z meters."""
    corners = np.asarray(corners, dtype=np.float32)
    if (corners.shape != (4, 2) or not np.isfinite(corners).all()
            or not cv2.isContourConvex(corners) or cv2.contourArea(corners) < 2500):
        raise ValueError("Select four distinct corners of a large, visible rectangle in order")
    if not all(np.isfinite(value) and value > 0 for value in (width_m, height_m)):
        raise ValueError("Calibration dimensions must be positive meters")
    plane = np.float32([[0, height_m], [width_m, height_m], [width_m, 0], [0, 0]])
    return cv2.getPerspectiveTransform(corners, plane)


def measure_chip(frames, camera, event_ns, corners, width_m, height_m):
    """Fit the first rising segment, using sensor timestamps from one camera."""
    transform = calibrate_plane(corners, width_m, height_m)
    contour = np.asarray(corners, dtype=np.float32)
    result = dict(method="side_view_planar_early_flight", camera=camera,
                  corners_px=corners, width_m=width_m, height_m=height_m,
                  status="rejected", metrics=None)
    samples = []
    for frame in frames:
        if frame.camera != camera or frame.ball is None:
            continue
        t = frame.metadata.get("SensorTimestamp")
        elapsed = (frame.host_ns - event_ns) / 1e9
        pixel = tuple(map(float, frame.ball["center"]))
        if (not isinstance(t, int) or t <= 0 or not -.8 <= elapsed <= .3
                or cv2.pointPolygonTest(contour, pixel, False) < 0):
            continue
        position = cv2.perspectiveTransform(np.array([[pixel]]), transform)[0, 0]
        samples.append((t, elapsed, position, frame.ball["radius"]))

    baseline = [position for _, elapsed, position, _ in samples if -.8 <= elapsed <= -.22]
    if len(baseline) < 3:
        return {**result, "reason": "Stationary ball missing before impact in the calibrated view"}
    origin = np.median(baseline, axis=0)
    if np.median(np.linalg.norm(baseline - origin, axis=1)) > .012:
        return {**result, "reason": "Ball must stay still before the chip"}

    flight = []
    for t, elapsed, position, radius in samples:
        if elapsed < -.22:
            continue
        x, z = position - origin
        if x < .008 or z < .008:
            if flight:
                break
            continue
        if flight:
            previous_ns, previous, previous_radius = flight[-1]
            dt = (t - previous_ns) / 1e9
            dx, dz = position - previous
            if (not 0 < dt <= .055 or t - flight[0][0] > 120_000_000
                    or dx < -.004 or dz < -.006
                    or not .65 <= radius / previous_radius <= 1.5):
                break
        flight.append((t, position, radius))

    if len(flight) < 4 or flight[-1][0] - flight[0][0] < 35_000_000:
        return {**result, "reason": "Need four rising ball detections inside the calibration area"}
    times = np.array([(t - flight[0][0]) / 1e9 for t, _, _ in flight])
    positions = np.array([[x, 0, z] for _, (x, z), _ in flight])
    velocity = estimate_velocity(positions, times)
    fitted = positions.mean(axis=0) + (times[:, None] - times.mean()) * velocity
    residual = float(np.max(np.linalg.norm(positions - fitted, axis=1)))
    if velocity[0] <= 0 or velocity[2] <= 0 or residual > .02:
        return {**result, "reason": "Early-flight detections do not fit a rising chip"}
    metrics = launch_metrics(velocity)
    del metrics["launch_direction_deg"]
    return {**result, "status": "estimated", "metrics": metrics,
            "sample_count": len(flight), "positions_m": positions[:, [0, 2]].tolist(),
            "sensor_elapsed_s": times.tolist(), "fit_max_error_m": residual}
