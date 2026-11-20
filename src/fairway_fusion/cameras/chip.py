"""Measure a short chip that stays in a calibrated vertical plane."""

from collections import deque
from itertools import product

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


class TeeDeparture:
    """One-shot trigger on a rising, downrange departure from a stable tee position."""

    def __init__(self, corners, width_m, height_m):
        self.contour = np.asarray(corners, dtype=np.float32)
        self.transform = calibrate_plane(corners, width_m, height_m)
        self.rest = deque(maxlen=20)
        self.origin = None
        self.last_rest_ns = 0
        self.first_departure = None

    @property
    def ready(self):
        return self.origin is not None

    def observe(self, timestamp_ns, ball):
        if self.ready and timestamp_ns - self.last_rest_ns > 180_000_000:
            self.origin = self.first_departure = None
            self.rest.clear()
        if ball is None:
            if not self.ready:
                self.rest.clear()
            return None
        pixel = tuple(map(float, ball["center"]))
        if cv2.pointPolygonTest(self.contour, pixel, False) < 0:
            if not self.ready:
                self.rest.clear()
            return None
        position = cv2.perspectiveTransform(np.array([[pixel]]), self.transform)[0, 0]
        if not self.ready:
            if position[1] > .075:
                self.rest.clear()
                return None
            if self.rest:
                start_ns, start = self.rest[0]
                if (np.linalg.norm(position - start) > .012
                        or timestamp_ns - self.rest[-1][0] > 90_000_000):
                    self.rest.clear()
                elif len(self.rest) >= 4 and timestamp_ns - start_ns >= 250_000_000:
                    self.origin = np.median([p for _, p in self.rest], axis=0)
                    self.last_rest_ns = timestamp_ns
            self.rest.append((timestamp_ns, position))
            return None

        forward, rise = position - self.origin
        if np.linalg.norm(position - self.origin) <= .012:
            self.last_rest_ns = timestamp_ns
            self.first_departure = None
        elif forward >= .014 and rise >= .010:
            if self.first_departure is not None:
                first_ns, first_position = self.first_departure
                dx, dz = position - first_position
                if 0 < timestamp_ns - first_ns <= 100_000_000 and dx >= .004 and dz >= -.004:
                    return first_ns
                if timestamp_ns - first_ns <= 100_000_000:
                    return None
            if forward >= .3 * (timestamp_ns - self.last_rest_ns) / 1e9:
                self.first_departure = (timestamp_ns, position)
        else:
            self.first_departure = None
        return None


def measure_chip(frames, camera, event_ns, corners, width_m, height_m):
    """Fit the first rising segment, using sensor timestamps from one camera."""
    transform = calibrate_plane(corners, width_m, height_m)
    contour = np.asarray(corners, dtype=np.float32)
    result = dict(method="side_view_planar_early_flight", camera=camera,
                  corners_px=corners, width_m=width_m, height_m=height_m,
                  status="rejected", metrics=None)
    samples = []
    centers_by_sensor_time = {}
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
        centers_by_sensor_time[t] = list(frame.ball["center"])
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

    if len(flight) < 3 or flight[-1][0] - flight[0][0] < 25_000_000:
        return {**result, "reason": "Need three rising ball detections inside the calibration area"}
    return _fit_flight(flight, {**result,
                                "flight_sensor_timestamps_ns": [point[0] for point in flight],
                                "flight_centers_px": [centers_by_sensor_time[point[0]] for point in flight]})


def _fit_flight(flight, result):
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


_RIM_ANGLES = np.linspace(0, 2 * np.pi, 48, endpoint=False)
_RIM_OFFSETS = np.stack((np.cos(_RIM_ANGLES), np.sin(_RIM_ANGLES)), axis=1)


def _circles(image, minimum, maximum, *, threshold=16):
    circles = cv2.HoughCircles(image, cv2.HOUGH_GRADIENT, 1, 13, param1=65, param2=threshold,
                              minRadius=minimum, maxRadius=maximum)
    return circles[0] if circles is not None else ()


def _ring(image, center, radius, scale):
    xx, yy = np.rint(center + radius * scale * _RIM_OFFSETS).astype(int).T
    return image[np.clip(yy, 0, image.shape[0] - 1), np.clip(xx, 0, image.shape[1] - 1)].astype(np.int16)


def recover_short_chip(frames, event_ns, result, tee_region, max_radius):
    """Recover an early flight from round objects that change between saved frames."""
    if not tee_region or result.get("metrics"):
        return result
    x, y, width, height = tee_region
    tee_click = np.array([x + width / 2, y + height / 2])
    reference = [f.image for f in frames if -.28 <= (f.host_ns - event_ns) / 1e9 <= -.095]
    if len(reference) < 3:
        return result
    median = cv2.GaussianBlur(np.median(reference, axis=0).astype(np.uint8), (5, 5), 0)
    tee_choices = []
    for cx, cy, radius in _circles(median, 12, min(max_radius, 42)):
        center = np.array([cx, cy])
        distance = np.linalg.norm(center - tee_click)
        if distance > 38:
            continue
        rim = _ring(median, center, radius, .87) - _ring(median, center, radius, 1.12)
        support = np.mean(rim > 10)
        if support >= .48:
            tee_choices.append((distance - 12 * support - .15 * (cy - tee_click[1]), center, radius))
    if not tee_choices:
        return result
    _, tee_center, tee_radius = min(tee_choices, key=lambda candidate: candidate[0])
    corners = result["corners_px"]
    contour = np.asarray(corners, dtype=np.float32)
    if cv2.pointPolygonTest(contour, tuple(map(float, tee_center)), False) < 0:
        return result
    transform = calibrate_plane(corners, result["width_m"], result["height_m"])

    def project(pixel):
        return cv2.perspectiveTransform(np.array([[pixel]], dtype=float), transform)[0, 0]

    origin = project(tee_center)
    candidates = []
    previous = None
    for frame in frames:
        elapsed = (frame.host_ns - event_ns) / 1e9
        if not -.09 <= elapsed <= .16:
            continue
        blurred = cv2.GaussianBlur(frame.image, (5, 5), 0)
        change = cv2.absdiff(blurred, previous) if previous is not None else None
        previous = blurred
        sensor_ns = frame.metadata.get("SensorTimestamp")
        if change is None or not isinstance(sensor_ns, int) or sensor_ns <= 0:
            continue
        height, width = frame.image.shape
        proposals = []
        for cx, cy, radius in _circles(blurred, max(6, int(.53 * tee_radius)),
                                       min(max_radius, round(1.45 * tee_radius)), threshold=14):
            if not 1.1 * radius <= cx < width - 1.1 * radius or not 1.1 * radius <= cy < height - 1.1 * radius:
                continue
            if cv2.pointPolygonTest(contour, (float(cx), float(cy)), False) < 0:
                continue
            position = project((cx, cy))
            if np.any(position - origin < .008):
                continue

            center = np.array([cx, cy])
            motion = np.mean(_ring(change, center, radius, .65) > 11)
            edge = np.mean(abs(_ring(blurred, center, radius, .89) - _ring(blurred, center, radius, 1.08)) > 10)
            if motion >= .45 and edge >= .39:
                ball = {"center": (round(float(cx)), round(float(cy))),
                        "radius": round(float(radius)), "circularity": float(edge)}
                proposals.append((frame, sensor_ns, position, ball, motion * edge))
        candidates.append(sorted(proposals, key=lambda p: -p[4])[:4])

    best = None
    for a, b, c in zip(candidates, candidates[1:], candidates[2:]):
        for first, middle, last in product(a, b, c):
            points = (first, middle, last)
            t0, t1, t2 = (p[1] for p in points)
            if not (0 < t1 - t0 <= 55_000_000 and 0 < t2 - t1 <= 55_000_000 and t2 - t0 >= 25_000_000):
                continue
            p0, p1, p2 = (p[2] for p in points)
            if np.any(np.diff([p0, p1, p2], axis=0) < [.008, -.003]):
                continue
            span = p2 - p0
            if span[0] < .035 or span[1] < .010:
                continue
            if any(not .65 <= after[3]["radius"] / before[3]["radius"] <= 1.5
                   for before, after in zip(points, points[1:])):
                continue
            error = np.linalg.norm(p1 - (p0 + span * (t1 - t0) / (t2 - t0)))
            start_ratio = (p0[0] - origin[0]) / span[0]
            if error > .014 or abs(p0[1] - start_ratio * span[1] - origin[1]) > .045:
                continue
            first_elapsed = (first[0].host_ns - event_ns) / 1e9
            score = sum(p[4] for p in points) / 3 - 12 * error - 8 * max(0, first_elapsed)
            if best is None or score > best[0]:
                best = score, points
    if best is None:
        return result
    points = best[1]
    fit = _fit_flight([(p[1], p[2], p[3]["radius"]) for p in points],
                      {key: value for key, value in result.items() if key != "reason"})
    if fit.get("metrics"):
        for frame, _, _, ball, _ in points:
            frame.ball = ball
        fit.update(tracking="motion_confirmed_short_flight", tee_center_px=tee_center.tolist(),
                   flight_centers_px=[p[3]["center"] for p in points])
    return fit
