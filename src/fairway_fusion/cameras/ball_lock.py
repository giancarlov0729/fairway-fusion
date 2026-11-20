"""Confirm a stationary ball in two views before allowing a bench capture."""

from dataclasses import dataclass
from itertools import product
import json
import math

import cv2
import numpy as np

from fairway_fusion.calibration.stereo_calibration import CameraCalibration, triangulate_pair
from fairway_fusion.detection.ball_detector import detect_ball_candidates


SCAN_SAMPLES = 7
ARM_SAMPLES = 5
BALL_DIAMETER_M = .04267
_ANGLES = np.linspace(0, 2 * np.pi, 48, endpoint=False)
_RIM = np.stack((np.cos(_ANGLES), np.sin(_ANGLES)))
_DX, _DY = np.meshgrid(np.arange(-.7, .71, .175), np.arange(-.7, .71, .175))
_INSIDE = _DX**2 + _DY**2 <= .7**2
_CORE = np.stack((_DX[_INSIDE], _DY[_INSIDE]))


class LockError(ValueError):
    """A failed check with an actionable message for the operator."""


class BallValidationError(LockError):
    """The cameras are usable, but the scene has yet to show one stable ball."""


def crop(image, box):
    x, y, width, height = box
    return image[y:y + height, x:x + width]


def _camera_label(index):
    return "Camera A" if index == 0 else "Camera B"


def _check_frames(pairs, cameras, minimum):
    if len(pairs) < minimum:
        raise LockError("Too few camera frames. Check both feeds and try again.")
    if any(tuple(frame.camera for frame in pair) != cameras for pair in pairs):
        raise LockError("Both camera views are required. Check camera connections.")
    for index in range(2):
        timestamps = [pair[index].metadata.get("SensorTimestamp") for pair in pairs]
        if all(isinstance(value, int) for value in timestamps) and any(
                right <= left for left, right in zip(timestamps, timestamps[1:])):
            raise LockError(f"{_camera_label(index)} frames stalled. Restart the camera preview.")
    for left, right in pairs:
        a, b = (frame.metadata.get("SensorTimestamp") for frame in (left, right))
        if isinstance(a, int) and isinstance(b, int) and abs(a - b) > 50_000_000:
            raise LockError("Camera views are too far apart in time. Check capture cadence and retry.")


def scan_empty_area(pairs, cameras, regions):
    """Build per-camera median references from an operator-confirmed empty mat."""
    _check_frames(pairs, cameras, SCAN_SAMPLES)
    references = {}
    for index, number in enumerate(cameras):
        zone = [_zone(pair[index].image, regions[number]) for pair in pairs]
        stack = np.stack(zone)
        median = np.median(stack, axis=0).astype(np.uint8)
        if np.mean(median <= 16) > .85 or np.mean(median >= 245) > .85:
            raise LockError(f"{_camera_label(index)} hitting area is too dark or overexposed. Adjust lighting and rescan.")
        change = np.abs(stack.astype(np.int16) - median)
        if np.max(np.mean(change > 15, axis=(1, 2))) > .035:
            raise LockError(f"{_camera_label(index)} saw movement or flicker. Clear the area, steady the unit and rescan.")
        references[number] = median
    return references


def _zone(image, box):
    x, y, width, height = box
    if (image.ndim != 2 or image.dtype != np.uint8 or x < 0 or y < 0
            or min(width, height) <= 0 or y + height > image.shape[0] or x + width > image.shape[1]):
        raise LockError("The hitting zone is outside the camera view. Check the camera profile.")
    return crop(image, box)


def _disk(image, center, radius):
    """Sample the inside of the silhouette, away from the changing background."""
    return _sample_pixels(image, center, radius, _CORE)


def _sample_pixels(image, center, radius, offsets):
    xx, yy = np.rint(np.asarray(center)[:, None] + radius * offsets).astype(int)
    return image[np.clip(yy, 0, image.shape[0] - 1),
                 np.clip(xx, 0, image.shape[1] - 1)].astype(np.int16)


def _rim_contrast(image, center, radius):
    inside = _sample_pixels(image, center, .86 * radius, _RIM)
    outside = _sample_pixels(image, center, 1.12 * radius, _RIM)
    return inside - outside


def _foreground(image, empty, box, label):
    zone = _zone(image, box)
    if zone.shape != empty.shape:
        raise LockError(f"{label} layout changed. Recalibrate the empty hitting area.")
    delta = zone.astype(np.int16) - empty
    shift = int(np.rint(np.median(delta)))
    delta -= shift
    if abs(shift) > 24 or np.mean(np.abs(delta) > 24) > .38:
        raise LockError(f"{label} scene or lighting changed. Clear the mat and recalibrate.")
    return zone, delta


def _stationary_match(zone, delta, texture, ball, hint):
    center, radius = ball["center"], ball["radius"]
    if hint is not None and math.dist(hint, center) > .5 * radius:
        return False
    change = _disk(delta, center, radius)
    rim = _rim_contrast(zone, center, radius)
    # A clean ball has a mostly smooth interior even when one side is shaded.
    # Shoe seams and laces can form plausible circular edges, but create many
    # small intensity changes inside. Allow a printed logo or a few marks.
    smooth_core = np.mean(_disk(texture, center, radius) <= 4)
    # A white ball in front of a shoe can darken part of the reference.
    # Require a changed interior and a mostly brighter, visible rim.
    visible_rim = np.abs(rim) >= 12
    covered_sectors = sum(sector.mean() >= .33 for sector in np.roll(visible_rim, 3).reshape(8, 6))
    return (np.mean(np.abs(change) >= 15) >= .65 and np.mean(change >= 15) >= .35
            and visible_rim.mean() >= .65 and np.mean(rim >= 10) >= .50
            and covered_sectors >= 7 and smooth_core >= .80)


def _same_ball(first, second):
    radius = first["radius"]
    return (math.dist(first["center"], second["center"]) <= max(3, .25 * radius)
            and abs(radius - second["radius"]) <= max(3, .20 * radius))


def _stationary_observations(frames, empty, box, min_radius, max_radius, hint, label):
    x, y, _, _ = box
    local_hint = None if hint is None else (hint[0] - x, hint[1] - y)
    patches = [_foreground(frame.image, empty, box, label) for frame in frames]
    textures = [cv2.absdiff(zone, cv2.GaussianBlur(zone, (7, 7), 0)) for zone, _ in patches]
    observations = [
        [ball for ball in detect_ball_candidates(zone, min_radius, max_radius, prefer_roundness=False)
         if _stationary_match(zone, delta, texture, ball, local_hint)]
        for (zone, delta), texture in zip(patches, textures)
    ]
    seeds = [balls[0] for balls in observations if len(balls) == 1]
    if len(seeds) >= 2 and all(_same_ball(seeds[0], ball) for ball in seeds[1:]):
        # A club or a shoe can interrupt a clean circle fit. Check the same
        # position directly only when two independent fits agree on it.
        seed = {**seeds[0], "center": tuple(int(v) for v in np.rint(
                    np.median([ball["center"] for ball in seeds], axis=0))),
                "radius": round(float(np.median([ball["radius"] for ball in seeds])))}
        for index, ((zone, delta), texture, balls) in enumerate(zip(patches, textures, observations)):
            if not balls and _stationary_match(zone, delta, texture, seed, local_hint):
                observations[index] = [seed]
    return [[{**ball, "center": (ball["center"][0] + x, ball["center"][1] + y)} for ball in balls]
            for balls in observations]


@dataclass(frozen=True)
class StereoZone:
    cameras: dict
    lower_m: np.ndarray
    upper_m: np.ndarray
    diameter_m: float

    @classmethod
    def load(cls, path, cameras):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            lower, upper = (np.asarray(data[key], dtype=float) for key in ("center_min_m", "center_max_m"))
            diameter = float(data.get("ball_diameter_m", BALL_DIAMETER_M))
            if (lower.shape != (3,) or upper.shape != (3,) or not np.all(np.isfinite([lower, upper]))
                    or np.any(lower >= upper) or not .02 <= diameter <= .08):
                raise ValueError("invalid hitting bounds or ball diameter")
            profiles = {}
            for number in cameras:
                item = data["cameras"][str(number)]
                matrix, distortion, rotation, translation = (np.asarray(item[key], dtype=float)
                                                               for key in ("K", "distortion", "R", "t"))
                if (item["image_size"] != [640, 480] or matrix.shape != (3, 3) or rotation.shape != (3, 3)
                        or translation.size != 3 or distortion.size not in {4, 5, 8, 12, 14}
                        or not all(np.isfinite(value).all() for value in (matrix, distortion, rotation, translation))
                        or matrix[0, 0] <= 0 or matrix[1, 1] <= 0 or not np.allclose(matrix[2], [0, 0, 1])
                        or not np.allclose(rotation @ rotation.T, np.eye(3), atol=1e-5)
                        or not np.isclose(np.linalg.det(rotation), 1)):
                    raise ValueError(f"invalid calibration for camera {number}; expected 640x480")
                profiles[number] = CameraCalibration(matrix, distortion.reshape(-1), rotation,
                                                     translation.reshape(3), (640, 480))
            origins = [-(cam.R.T @ cam.t) for cam in profiles.values()]
            if np.linalg.norm(origins[0] - origins[1]) < .001:
                raise ValueError("camera centers have no usable separation")
        except (OSError, ValueError, TypeError, KeyError, OverflowError) as error:
            raise LockError(f"Stereo profile could not be loaded: {error}") from error
        return cls(profiles, lower, upper, diameter)

    def image_region(self, number):
        """Project the permitted ball volume, with room for the detector's rim checks."""
        camera = self.cameras[number]
        padding = .65 * self.diameter_m
        vertices = np.array(list(product(*zip(self.lower_m - padding, self.upper_m + padding))))
        if np.any(vertices @ camera.R[2] + camera.t[2] <= 0):
            raise LockError(f"Hitting volume is behind camera {number}. Check the stereo profile.")
        pixels = camera.project(vertices)
        if not np.all(np.isfinite(pixels)):
            raise LockError(f"Hitting volume cannot be projected in camera {number}. Check the stereo profile.")
        left, top = np.maximum(np.floor(pixels.min(axis=0)) - 5, 0).astype(int)
        right, bottom = np.minimum(np.ceil(pixels.max(axis=0)) + 6, camera.image_size).astype(int)
        if right <= left or bottom <= top:
            raise LockError(f"Hitting volume is not visible in camera {number}. Check the stereo profile.")
        return int(left), int(top), int(right - left), int(bottom - top)

    def matches(self, cameras, a, b):
        first, second = (self.cameras[number] for number in cameras)
        solution = triangulate_pair(a["center"], b["center"], first, second)
        if solution is None:
            return None
        point, error = solution
        if error > 2.5 or np.any(point < self.lower_m) or np.any(point > self.upper_m):
            return None
        for camera, ball in ((first, a), (second, b)):
            # Project a ball diameter along both image axes at the candidate's depth.
            axes = camera.R[:2] * (.5 * self.diameter_m)
            pixels = camera.project(np.concatenate((point + axes, point - axes)))
            expected = np.mean(np.linalg.norm(pixels[:2] - pixels[2:], axis=1)) / 2
            if not np.isfinite(expected) or not .72 * expected <= ball["radius"] <= 1.28 * expected:
                return None
        return point


@dataclass
class ConfirmedBall:
    balls: dict
    signatures: dict
    empty_signatures: dict
    position_m: list | None

    def evidence(self):
        return {"verification": "stereo" if self.position_m is not None else "two_view_appearance",
                "position_m": self.position_m, "camera_candidates": self.balls}

    def present(self, frame):
        """Check the original location only. Never acquire a replacement while armed."""
        ball = self.balls[frame.camera]
        current = _disk(frame.image, ball["center"], ball["radius"])
        locked, empty = self.signatures[frame.camera], self.empty_signatures[frame.camera]
        if np.mean(np.abs(current - locked) <= 28) < .70 or np.mean(current - empty >= 12) < .35:
            return False
        # A bright region filling this patch is insufficient. Look for a rim
        # at the original radius; absolute contrast allows for bright backgrounds.
        contrast = _rim_contrast(frame.image, ball["center"], ball["radius"])
        return bool(np.mean(np.abs(contrast) >= 12) >= .40)


def confirm_ball(pairs, cameras, regions, references, min_radius, max_radius, hints, stereo=None):
    _check_frames(pairs, cameras, ARM_SAMPLES)
    observations = []
    accepted_frames = []
    missing = None
    views = [_stationary_observations(frames, references[number], regions[number],
                                      min_radius, max_radius, hints.get(number), _camera_label(i))
             for i, (frames, number) in enumerate(zip(zip(*pairs), cameras))]
    for frames, candidates in zip(pairs, zip(*views)):
        if not all(candidates):
            if any(len(view) > 1 for view in candidates):
                raise BallValidationError("Multiple possible balls. Leave one ball visible in both boxes.")
            missing = "both cameras" if not any(candidates) else _camera_label(0 if not candidates[0] else 1)
            continue
        if stereo:
            matches = []
            for a in candidates[0]:
                for b in candidates[1]:
                    point = stereo.matches(cameras, a, b)
                    if point is not None:
                        matches.append((a, b, point))
            if not matches:
                raise BallValidationError("Views disagree or the ball is outside the physical hitting zone. Reposition the ball.")
        else:
            matches = [(a, b, None) for a in candidates[0] for b in candidates[1]]
        if len(matches) != 1:
            raise BallValidationError("Multiple possible balls. Leave one ball visible in both boxes.")
        observations.append(matches[0])
        accepted_frames.append(frames)

    if len(observations) < len(pairs) - 1:
        raise BallValidationError(f"No new ball confirmed in {missing}. Place one visible ball in the zone.")

    first = observations[0]
    for match in observations[1:]:
        if any(not _same_ball(a, b) for a, b in zip(first[:2], match[:2])) or (
                   stereo and np.linalg.norm(match[2] - first[2]) > stereo.diameter_m * .35):
            raise BallValidationError("Ball position or size was unstable. Let the ball settle.")

    balls = dict(zip(cameras, observations[-1][:2]))
    signatures, empty_signatures = {}, {}
    for index, (number, ball) in enumerate(balls.items()):
        x, y, _, _ = regions[number]
        center, radius = ball["center"], ball["radius"]
        signatures[number] = np.median([_disk(pair[index].image, center, radius) for pair in accepted_frames], axis=0)
        empty_signatures[number] = _disk(references[number], (center[0] - x, center[1] - y), radius)
    position = np.median([obs[2] for obs in observations], axis=0).tolist() if stereo else None
    return ConfirmedBall(balls, signatures, empty_signatures, position)


def track_locked_flight(frames, lock, event_ns, min_radius, max_radius):
    """Follow the confirmed tee identity on saved frames, with no global fallback."""
    summaries = {}
    for number, tee in lock.balls.items():
        previous = None
        last_seen_on_tee = None
        flight = []
        gap = 0
        status = "no_departure_confirmed"
        for frame in frames:
            elapsed = frame.host_ns - event_ns
            if frame.camera != number or elapsed < -850_000_000:
                continue
            if not flight and elapsed > 350_000_000:
                break
            if not flight and lock.present(frame):
                frame.ball = tee
                last_seen_on_tee = frame.host_ns
            elif (elapsed >= -220_000_000 and previous is not None
                  and (flight or (last_seen_on_tee is not None
                                  and frame.host_ns - last_seen_on_tee <= 95_000_000))):
                candidate = _flight_candidate(frame, previous, flight, tee, last_seen_on_tee,
                                              min_radius, max_radius)
                if candidate is None:
                    gap += 1
                    if flight and gap > 2:
                        status = "track_ended"
                        break
                else:
                    gap = 0
                    flight.append((frame, candidate))
                    status = "tracked"
            previous = frame
        if len(flight) >= 2:
            for frame, candidate in flight:
                frame.ball = candidate
        else:
            status = "no_departure_confirmed"
        summaries[number] = {
            "status": status,
            "flight_observations": len(flight) if len(flight) >= 2 else 0,
            "flight_sensor_timestamps_ns": [
                frame.metadata.get("SensorTimestamp") for frame, _ in flight
            ] if len(flight) >= 2 else [],
            "flight_centers_px": [
                list(candidate["center"]) for _, candidate in flight
            ] if len(flight) >= 2 else [],
        }
    return summaries


def _flight_candidate(frame, previous, flight, tee, last_seen_on_tee, min_radius, max_radius):
    last_frame, last = flight[-1] if flight else (previous, tee)
    dt = (frame.host_ns - last_frame.host_ns) / 1e9
    if not 0 < dt <= .075:
        return None
    center = np.array(last["center"], dtype=float)
    radius = last["radius"]
    # At 60 fps a ball can travel several apparent radii per frame. Base
    # acquisition reach on the time since its last confirmed tee observation.
    heading = None
    if not flight:
        since_tee = (frame.host_ns - last_seen_on_tee) / 1e9
        reach = radius * min(16, max(6, 400 * since_tee))
    elif len(flight) == 1:
        reach = radius * min(12, max(2.8, 400 * dt))
        heading = center - tee["center"]
    else:
        reach = 2.8 * radius
        older_frame, older = flight[-2]
        interval = (last_frame.host_ns - older_frame.host_ns) / 1e9
        if interval > 0:
            center += (center - older["center"]) * (dt / interval)
    height, width = frame.image.shape
    pad = reach + 1.4 * radius
    x, y = max(0, math.floor(center[0] - pad)), max(0, math.floor(center[1] - pad))
    right, bottom = min(width, math.ceil(center[0] + pad)), min(height, math.ceil(center[1] + pad))
    lo, hi = max(min_radius, round(.65 * radius)), min(max_radius, round(1.40 * radius))
    if lo >= hi or min(right - x, bottom - y) <= 2 * lo:
        return None
    zone = frame.image[y:bottom, x:right]
    previous_zone = previous.image[y:bottom, x:right]
    difference = cv2.absdiff(zone, previous_zone)
    proposals = detect_ball_candidates(zone, lo, hi, prefer_roundness=False)
    # Against a shoe, pants leg, or bright wall the ball can be darker than
    # part of its backdrop. Motion supplies additional circle proposals.
    for motion in detect_ball_candidates(difference, lo, hi, prefer_roundness=False):
        if not any(math.dist(motion["center"], proposal["center"]) <= .65 * motion["radius"]
                   for proposal in proposals):
            proposals.append(motion)
    candidates = []
    for proposal in proposals:
        point = (proposal["center"][0] + x, proposal["center"][1] + y)
        r = proposal["radius"]
        if math.dist(point, center) > reach or math.dist(point, tee["center"]) < .5 * tee["radius"]:
            continue
        if heading is not None:
            step = point - center
            if np.dot(step, heading) <= .5 * np.linalg.norm(step) * np.linalg.norm(heading):
                continue
        # The ball should appear at a new position in the current frame. A
        # persistent reflection within the prediction gate cannot win on shape alone.
        core = _disk(frame.image, point, r)
        changed = core - _disk(previous.image, point, r)
        rim = _rim_contrast(frame.image, point, r)
        if (np.mean(np.abs(changed) > 12) >= .35 and np.mean(np.abs(rim) >= 10) >= .48
                and np.std(core) <= 35):
            candidates.append({**proposal, "center": point})
    return candidates[0] if len(candidates) == 1 else None
