"""Review saved frames without changing the capture evidence."""

from bisect import bisect_left
import math
from pathlib import Path
from tempfile import NamedTemporaryFile

import cv2

from fairway_fusion.cameras.chip import measure_image_motion
from fairway_fusion.cameras.recording_store import recording_frame_path


def frame_time(frame):
    elapsed = frame.get("relative_to_host_event_s", frame.get("relative_to_manual_trigger_s", 0))
    return elapsed if type(elapsed) in (int, float) and math.isfinite(elapsed) else 0


def detection(frame):
    ball = frame.get("ball_candidate")
    if not isinstance(ball, dict):
        return None
    center, radius = ball.get("center"), ball.get("radius")
    if (not isinstance(center, (list, tuple)) or len(center) != 2
            or any(type(value) not in (int, float) or not 0 <= value <= 4096 for value in center)
            or type(radius) not in (int, float) or not 0 < radius <= 4096):
        return None
    return tuple(round(value) for value in center), max(1, round(radius))


def frame_labels(manifest):
    frames = manifest["frames"]
    cameras = manifest.get("requested_settings", {}).get("camera_numbers") or dict.fromkeys(
        frame["camera"] for frame in frames if type(frame.get("camera")) is int)
    return {number: chr(ord("A") + index) for index, number in enumerate(cameras)}


def recording_measurement(manifest):
    """Recover image-motion evidence for recordings saved before it was stored."""
    measurement = manifest.get("measurement") or {}
    tracks = measurement.get("locked_flight_tracks") or {}
    if "image_motion" in measurement or not tracks:
        return measurement
    resting = (manifest.get("ball_lock") or {}).get("camera_candidates") or {}
    motion = []
    for camera in frame_labels(manifest):
        track = tracks.get(str(camera), {})
        count = track.get("flight_observations", 0)
        tee = resting.get(str(camera), {}).get("center")
        if count < 2 or not tee:
            continue
        points = []
        for frame in manifest["frames"]:
            if frame.get("camera") != camera:
                continue
            point = detection(frame)
            if point is not None and point[0] != tuple(tee):
                points.append(((frame.get("metadata") or {}).get("SensorTimestamp"), point[0]))
        if len(points) == count:
            times, centers = zip(*points)
            fit = measure_image_motion(camera, times, centers)
            if fit is not None:
                motion.append(fit)
    if (not measurement.get("metrics") and not measurement.get("corners_px")
            and any(track.get("flight_observations") for track in tracks.values())):
        measurement = {**measurement, "reason": "This recording has no measured side plane for calculating ball speed and launch angle."}
    return {**measurement, "image_motion": motion}


def review_frames(manifest, measurement=None):
    if measurement is None:
        measurement = recording_measurement(manifest)
    metrics = measurement.get("metrics")
    used = {(measurement.get("camera"), stamp)
            for stamp in measurement.get("flight_sensor_timestamps_ns") or []} if metrics else set()
    if not metrics:
        for motion in measurement.get("image_motion") or []:
            used.update((motion["camera"], stamp) for stamp in motion["flight_sensor_timestamps_ns"])
    labels = frame_labels(manifest)
    result = []
    for frame in manifest["frames"]:
        point = detection(frame)
        timestamp = (frame.get("metadata") or {}).get("SensorTimestamp")
        result.append({**frame, "label": labels.get(frame.get("camera"), "?"), "time_s": frame_time(frame),
                       "detection": point, "used": point is not None and (frame.get("camera"), timestamp) in used})
    return result


def annotated_png(path, frame):
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        return None
    mark = detection(frame)
    if mark:
        center, radius = mark
        cv2.circle(image, center, radius, (20, 20, 20), 4, cv2.LINE_AA)
        cv2.circle(image, center, radius, (80, 245, 170), 2, cv2.LINE_AA)
        cv2.drawMarker(image, center, (80, 245, 170), cv2.MARKER_CROSS, 11, 1, cv2.LINE_AA)
    ok, encoded = cv2.imencode(".png", image)
    return encoded.tobytes() if ok else None


def _clip_frames(manifest):
    frames, labels = manifest["frames"], frame_labels(manifest)
    streams = [sorted((frame for frame in frames if frame.get("camera") == number), key=frame_time)
               for number in labels]
    streams = [stream for stream in streams if stream]
    if not streams:
        return
    times = [[frame_time(frame) for frame in stream] for stream in streams]
    primary = [frame for frame in streams[0] if -.85 <= frame_time(frame) <= 1]
    for frame in (primary or streams[0])[:160]:
        pair = [frame]
        for stream, clock in zip(streams[1:], times[1:]):
            index = bisect_left(clock, frame_time(frame))
            nearest = min({max(0, index - 1), min(len(clock) - 1, index)},
                          key=lambda item: abs(clock[item] - frame_time(frame)))
            pair.append(stream[nearest])
        yield pair, labels


def render_webm(manifest_path, manifest):
    """Make a short slow-motion preview, keeping the original PNGs intact."""
    destination = manifest_path.parent / "preview-v1.webm"
    if destination.is_file() and destination.resolve() == destination:
        return destination
    with NamedTemporaryFile(dir=destination.parent, suffix=".webm", delete=False) as file:
        temporary = Path(file.name)
    writer = None
    written = 0
    try:
        try:
            for pair, labels in _clip_frames(manifest):
                images = []
                for frame in pair:
                    source = recording_frame_path(manifest_path, frame["file"])
                    image = cv2.imread(str(source), cv2.IMREAD_COLOR) if source else None
                    if image is None:
                        break
                    image = cv2.resize(image, (480, 360), interpolation=cv2.INTER_AREA)
                    cv2.putText(image, f"Camera {labels[frame['camera']]}", (12, 26),
                                cv2.FONT_HERSHEY_SIMPLEX, .65, (240, 248, 240), 2)
                    images.append(image)
                if len(images) != len(pair):
                    continue
                if writer is None:
                    writer = cv2.VideoWriter(str(temporary), cv2.VideoWriter_fourcc(*"VP80"),
                                             20, (480 * len(images), 360))
                    if not writer.isOpened():
                        return None
                writer.write(cv2.hconcat(images))
                written += 1
        finally:
            if writer is not None:
                writer.release()
        if written and temporary.stat().st_size:
            temporary.replace(destination)
            return destination
        return None
    finally:
        temporary.unlink(missing_ok=True)
