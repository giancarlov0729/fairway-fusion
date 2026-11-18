"""Two free-running Pi cameras: preview, ball candidates, and manual evidence clips."""

import argparse
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
import time

import cv2
import numpy as np

from fairway_fusion.cameras.bench import Frame, FrameBuffer
from fairway_fusion.detection.ball_detector import detect_ball, detect_moving_ball


def read_requests(jobs):
    """Release every acquired request, including when one image conversion fails."""
    with ExitStack() as stack:
        requests = []
        for job in jobs:
            request = job.get_result(timeout=2)
            stack.callback(request.release)
            requests.append(request)
        return [(request.make_array("main")[:480, :640].copy(), request.get_metadata())
                for request in requests]


def detect_frame(image, roi, min_radius, max_radius, previous=None, *,
                 motion_reference=None, motion_only=False):
    x, y, width, height = roi or (0, 0, image.shape[1], image.shape[0])
    region = np.s_[y:y + height, x:x + width]
    prior = None
    if previous is not None and previous.ball:
        previous_x, previous_y = previous.ball["center"]
        prior = (previous_x - x, previous_y - y, previous.ball["radius"])
    if motion_reference is None and previous is not None:
        motion_reference = previous.image
    reference = motion_reference[region] if motion_reference is not None else None
    if motion_only:
        if reference is None:
            return None
        ball = detect_moving_ball(image[region], reference, min_radius, max_radius)
    else:
        ball = detect_ball(image[region], min_radius, max_radius, previous=reference, prior=prior)
    if ball:
        local_x, local_y = ball["center"]
        ball["center"] = (local_x + x, local_y + y)
    return ball


def analyze_frames(frames, regions, min_radius, max_radius, history_by_camera):
    for frame in frames:
        if frame.camera not in history_by_camera:
            history_by_camera[frame.camera] = deque(maxlen=3)
        history = history_by_camera[frame.camera]
        # A ball may overlap its previous position at 60 FPS. Older footage
        # exposes more of its silhouette without moving the last-center hint.
        frame.ball = detect_frame(
            frame.image, regions.get(frame.camera), min_radius, max_radius,
            history[-1] if history else None,
            motion_reference=history[0].image if history else None,
        )
        history.append(frame)


def analyze_preview(frames, regions, min_radius, max_radius, history_by_camera):
    analyze_frames(frames, regions, min_radius, max_radius, history_by_camera)
    return {frame.camera: frame for frame in frames}


def current_preview_ball(frame, detection):
    if detection is None or not detection.ball:
        return None
    ball = detection.ball
    x, y = ball["center"]
    radius = ball["radius"]
    region = np.s_[y - radius:y + radius + 1, x - radius:x + radius + 1]
    current = frame.image[region]
    # The marker belongs to a sampled frame. Hide it when that part of the
    # live image has changed so a stale circle does not follow a moving ball.
    if current.size and cv2.norm(current, detection.image[region], cv2.NORM_L1) / current.size <= 10:
        return ball
    return None


def show_preview(frames, status, sensor_timestamps, *, detections, motion_detections):
    panels = []
    for frame in frames:
        panel = cv2.cvtColor(frame.image, cv2.COLOR_GRAY2BGR)
        timestamps = sensor_timestamps[frame.camera]
        fps_text = "capture fps unknown"
        if len(timestamps) >= 2 and timestamps[-1] > timestamps[0]:
            fps = 1e9 * (len(timestamps) - 1) / (timestamps[-1] - timestamps[0])
            fps_text = f"{fps:.1f} capture fps"
        ball = (current_preview_ball(frame, motion_detections.get(frame.camera))
                or current_preview_ball(frame, detections.get(frame.camera)))
        detection_text = "BALL CANDIDATE" if ball else "SEARCHING"
        if ball:
            cv2.circle(panel, ball["center"], ball["radius"], (0, 255, 0), 2)
        cv2.putText(panel, f"Cam {frame.camera}: {detection_text} | {fps_text}", (8, 25),
                    cv2.FONT_HERSHEY_SIMPLEX, .5, (0, 255, 255), 1)
        cv2.putText(panel, f"Exposure {frame.metadata.get('ExposureTime', '?')} us", (8, 48),
                    cv2.FONT_HERSHEY_SIMPLEX, .5, (0, 255, 255), 1)
        panels.append(panel)
    view = np.hstack(panels)
    cv2.putText(view, status + " | free-running / unsynchronized", (8, 470),
                cv2.FONT_HERSHEY_SIMPLEX, .6, (0, 255, 255), 2)
    cv2.imshow("Fairway Fusion camera bench", view)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cameras", type=int, nargs=2, default=[0, 1])
    parser.add_argument("--fps", type=float, default=60)
    parser.add_argument("--exposure-us", type=int, default=1000)
    parser.add_argument("--gain", type=float, default=2)
    parser.add_argument("--min-radius", type=int, default=5)
    parser.add_argument("--max-radius", type=int, default=100)
    parser.add_argument("--roi", type=int, nargs=5, action="append",
                        metavar=("CAMERA", "X", "Y", "WIDTH", "HEIGHT"),
                        help="Limit detection to this part of a camera's 640x480 view")
    parser.add_argument("--output", type=Path, default=Path("data/recordings/bench"))
    parser.add_argument("--headless-seconds", type=float,
                        help="Save one timed clip without a preview, then exit (maximum 5 s)")
    args = parser.parse_args()
    if len(set(args.cameras)) != 2 or min(args.cameras) < 0:
        parser.error("Choose two distinct nonnegative camera indices")
    if not 1 <= args.fps <= 60 or not 1 <= args.exposure_us <= 1e6 / args.fps:
        parser.error("Use 1-60 FPS and an exposure within the requested frame period")
    if not 1 <= args.gain <= 16 or not 1 <= args.min_radius < args.max_radius <= 240:
        parser.error("Use gain 1-16 and 1 <= min-radius < max-radius <= 240")
    if args.headless_seconds is not None and not 0 < args.headless_seconds <= 5:
        parser.error("--headless-seconds must be greater than zero and at most 5")
    regions = {}
    for number, x, y, width, height in args.roi or ():
        if (number not in args.cameras or number in regions or x < 0 or y < 0
                or width <= 0 or height <= 0 or x + width > 640 or y + height > 480):
            parser.error("Choose at most one valid 640x480 image region for each selected camera")
        regions[number] = (x, y, width, height)
    try:
        from picamera2 import Picamera2
    except ImportError:
        parser.exit(1, "Picamera2 is required on the Pi. See docs/camera_first_demo.md.\n")

    settings = dict(size=[640, 480], fps=args.fps, exposure_us=args.exposure_us, gain=args.gain,
                    min_radius=args.min_radius, max_radius=args.max_radius, regions=regions)
    headless = args.headless_seconds is not None
    duration = args.headless_seconds or 2
    buffer = FrameBuffer(max_frames=int(2 * args.fps * (duration + 1)))
    armed = False
    trigger_ns = None
    deadline_ns = None
    sensor_timestamps = {number: deque(maxlen=31) for number in args.cameras}
    preview_history = {}
    preview_results = {}
    preview_job = None
    motion_history = deque(maxlen=4)
    motion_results = {}
    next_preview_ns = 0
    print("FREE-RUNNING cameras; no Pico control, hardware synchronization, or shot metrics.")
    print("a: software arm | space: save last ~2 s + next 1 s | d: disarm | q: quit")
    with ExitStack() as stack:
        if not headless:
            # Leave CPU for libcamera and the GUI while OpenCV scans a preview.
            cv2.setNumThreads(2)
            preview_worker = stack.enter_context(ThreadPoolExecutor(max_workers=1))
        cameras = []
        for number in args.cameras:
            camera = Picamera2(number)
            stack.callback(camera.close)
            config = camera.create_video_configuration(
                main={"size": (640, 480), "format": "YUV420"}, buffer_count=4,
                controls={"AeEnable": False, "ExposureTime": args.exposure_us,
                          "AnalogueGain": args.gain, "FrameRate": args.fps},
                queue=False,
            )
            camera.configure(config)
            cameras.append(camera)
        # Stop capture before analysis even though the camera objects remain open.
        capture = stack.enter_context(ExitStack())
        for camera in cameras:
            camera.start()
            capture.callback(camera.stop)
        if not headless:
            stack.callback(cv2.destroyAllWindows)
        # The first camera can deliver a frame while the second starts.
        read_requests([camera.capture_request(wait=False) for camera in cameras])
        ready = False
        while True:
            jobs = [camera.capture_request(wait=False) for camera in cameras]
            frames = [
                Frame(number, time.monotonic_ns(), image, metadata, None)
                for number, (image, metadata) in zip(args.cameras, read_requests(jobs))
            ]
            if armed or headless:
                for frame in frames:
                    buffer.append(frame)
            now_ns = time.monotonic_ns()
            if not ready:
                ready = True
                print("READY: both cameras delivered images.")
                if headless:
                    trigger_ns = now_ns
                    deadline_ns = now_ns + int(args.headless_seconds * 1e9)
            if deadline_ns is not None and now_ns >= deadline_ns:
                folder = args.output / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
                if headless:
                    capture.close()
                # Both recording modes defer detection to protect capture cadence.
                print(f"Analyzing {len(buffer.frames)} captured frames.", flush=True)
                analyze_frames(buffer.frames, regions, args.min_radius, args.max_radius, {})
                manifest = buffer.save(folder, trigger_ns=trigger_ns, settings=settings)
                print(f"Saved {len(manifest['frames'])} frames: {folder / 'manifest.json'}")
                if headless:
                    break
                armed, deadline_ns = False, None
                buffer.frames.clear()
                for timestamps in sensor_timestamps.values():
                    timestamps.clear()
            if headless:
                continue
            if preview_job is not None and preview_job.done():
                preview_results = preview_job.result()
                preview_job = None
            if not armed and preview_job is None:
                # One in-flight sample at a time. The camera loop never waits
                # for it or queues frames when the detector falls behind.
                preview_job = preview_worker.submit(
                    analyze_preview, frames, regions, args.min_radius, args.max_radius, preview_history,
                )
            for frame in frames:
                sensor_ns = frame.metadata.get("SensorTimestamp")
                if sensor_ns is not None:
                    sensor_timestamps[frame.camera].append(sensor_ns)
            motion_history.append(frames)
            if now_ns >= next_preview_ns:
                if len(motion_history) > 1:
                    for frame, reference in zip(frames, motion_history[0]):
                        ball = detect_frame(
                            frame.image, regions.get(frame.camera), args.min_radius, args.max_radius,
                            motion_reference=reference.image, motion_only=True,
                        )
                        if ball:
                            motion_results[frame.camera] = Frame(
                                frame.camera, frame.host_ns, frame.image, frame.metadata, ball,
                            )
                status = "RECORDING" if deadline_ns is not None else (
                    "SOFTWARE ARMED" if armed else "READY / DISARMED"
                )
                show_preview(frames, status, sensor_timestamps,
                             detections=preview_results, motion_detections=motion_results)
                next_preview_ns += 33_333_333
                if next_preview_ns <= now_ns:
                    next_preview_ns = now_ns + 33_333_333
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord("a") and not armed:
                armed = True
                preview_history = {}
                next_preview_ns = 0
                print("SOFTWARE ARMED: manual clip saving enabled; Pico is not armed.")
            elif key == ord("d"):
                armed, deadline_ns = False, None
                buffer.frames.clear()
                print("Software disarmed; pending clip cancelled.")
            elif key == 32 and armed and deadline_ns is None:
                trigger_ns = time.monotonic_ns()
                deadline_ns = trigger_ns + 1_000_000_000


if __name__ == "__main__":
    main()
