"""Two free-running Pi cameras: preview, ball candidates, and manual evidence clips."""

import argparse
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime, timezone
import math
from pathlib import Path
import time

import cv2
import numpy as np

from fairway_fusion.cameras.bench import Frame, FrameBuffer
from fairway_fusion.cameras.chip import TeeDeparture, calibrate_plane, measure_chip, recover_short_chip
from fairway_fusion.detection.ball_detector import detect_ball, detect_moving_ball, follow_ball


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
                 motion_reference=None, motion_only=False, mixed_contrast=False,
                 prefer_roundness=False):
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
        ball = detect_moving_ball(image[region], reference, min_radius, max_radius,
                                  mixed_contrast=mixed_contrast)
    else:
        ball = detect_ball(image[region], min_radius, max_radius, previous=reference, prior=prior,
                           prefer_roundness=prefer_roundness)
    if ball:
        local_x, local_y = ball["center"]
        ball["center"] = (local_x + x, local_y + y)
    return ball


def analyze_frames(frames, regions, min_radius, max_radius, history_by_camera, *,
                   acquire_radius=None, acquisition_regions=None):
    for frame in frames:
        if frame.camera not in history_by_camera:
            history_by_camera[frame.camera] = deque(maxlen=6 if acquire_radius is not None else 3)
        history = history_by_camera[frame.camera]
        previous = history[-1] if history else None
        roi = regions.get(frame.camera)
        if acquire_radius is not None:
            frame.ball = None
            recent = list(history)[-3:]
            prior = next((sample.ball for sample in reversed(recent) if sample.ball), None)
            if prior:
                frame.ball = preview_ball(frame, previous, roi, min_radius, max_radius, prior,
                                          None, older_reference=recent[0])
            elif len(history) >= 6:
                samples = ([history[-6]], [history[-3]], [frame])
                seed = analyze_preview(samples, regions, acquire_radius, max_radius, acquisition_regions)
                frame.ball = preview_ball(frame, None, roi, min_radius, max_radius, None,
                                          seed[frame.camera], acquire_radius=acquire_radius,
                                          acquire_roi=(acquisition_regions or {}).get(frame.camera))
            history.append(frame)
            continue
        # A ball may overlap its previous position at 60 FPS. Older footage
        # exposes more of its silhouette without moving the last-center hint.
        frame.ball = detect_frame(
            frame.image, roi, min_radius, max_radius, previous,
            motion_reference=history[0].image if history else None,
        )
        history.append(frame)


def analyze_preview(samples, regions, min_radius, max_radius, acquisition_regions=None):
    detections = {}
    for first, middle, latest in zip(*samples):
        # A short median suppresses reflections or flicker that only affected
        # one frame. The live tracker checks each result on the current image.
        lower = cv2.min(first.image, middle.image)
        upper = cv2.max(first.image, middle.image)
        image = cv2.max(lower, cv2.min(upper, latest.image))
        detections[latest.camera] = detect_frame(
            image, (acquisition_regions or {}).get(latest.camera, regions.get(latest.camera)),
            min_radius, max_radius, prefer_roundness=True,
        )
    return detections


def preview_ball(frame, reference, roi, min_radius, max_radius, prior, detection, *,
                 older_reference=None, acquire_radius=None, acquire_roi=None):
    acquiring = prior is None and acquire_radius is not None
    if acquiring:
        # Wait for the stationary search before allowing motion to steer the lock.
        # A tracked ball can shrink and leave the tee area after impact.
        min_radius = acquire_radius
        roi = acquire_roi or roi
        reference = older_reference = None
    moving = detect_frame(frame.image, roi, min_radius, max_radius,
                          motion_reference=reference.image, motion_only=True) if reference else None
    if moving is None and older_reference is not None and older_reference is not reference:
        moving = detect_frame(frame.image, roi, min_radius, max_radius,
                              motion_reference=older_reference.image, motion_only=True,
                              mixed_contrast=True)
    if moving:
        if prior is None:
            return moving
        distance = math.dist(prior["center"], moving["center"])
        if abs(prior["radius"] - moving["radius"]) > 0.5 * prior["radius"] or distance > 6 * prior["radius"]:
            moving = None
        elif distance <= 1.25 * prior["radius"]:
            return moving
    seed = prior or detection
    if seed:
        x, y, width, height = roi or (0, 0, frame.image.shape[1], frame.image.shape[0])
        px, py = seed["center"]
        local = {**seed, "center": (px - x, py - y)}
        tracked = follow_ball(frame.image[y:y + height, x:x + width], local, min_radius, max_radius)
        if tracked:
            tx, ty = tracked["center"]
            tracked["center"] = (tx + x, ty + y)
            displacement = math.dist(tracked["center"], seed["center"])
            if acquiring and displacement > 0.45 * seed["radius"]:
                return None
            # A nearby stationary ball takes precedence over unrelated motion.
            if moving and displacement > 0.45 * seed["radius"]:
                return moving
            return tracked
    return moving


def track_preview(frames, references, older_references, regions, min_radius, max_radius,
                  tracks, detections, acquire_radius=None, acquisition_regions=None):
    balls = {}
    for frame, reference, older in zip(frames, references, older_references):
        prior, seen_ns = tracks.get(frame.camera, (None, 0))
        if frame.host_ns - seen_ns > 200_000_000:
            prior = None
        ball = preview_ball(
            frame, reference if reference is not frame else None,
            regions.get(frame.camera), min_radius, max_radius,
            prior, detections.get(frame.camera),
            older_reference=older if older is not frame else None,
            acquire_radius=acquire_radius,
            acquire_roi=(acquisition_regions or {}).get(frame.camera),
        )
        if ball:
            balls[frame.camera] = ball
    return frames, balls


PREVIEW_WINDOW = "Fairway Fusion camera bench"


def show_preview(frames, status, sensor_timestamps, balls, acquisition_regions=None):
    panels = []
    for frame in frames:
        panel = cv2.cvtColor(frame.image, cv2.COLOR_GRAY2BGR)
        timestamps = sensor_timestamps[frame.camera]
        fps_text = "capture fps unknown"
        if len(timestamps) >= 2 and timestamps[-1] > timestamps[0]:
            fps = 1e9 * (len(timestamps) - 1) / (timestamps[-1] - timestamps[0])
            fps_text = f"{fps:.1f} capture fps"
        ball = balls.get(frame.camera)
        detection_text = "BALL CANDIDATE" if ball else "SEARCHING"
        tee = (acquisition_regions or {}).get(frame.camera)
        if tee and not ball:
            x, y, width, height = tee
            cv2.rectangle(panel, (x, y), (x + width - 1, y + height - 1), (255, 200, 0), 1)
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
    cv2.imshow(PREVIEW_WINDOW, view)


def select_chip_plane(frame, width_m, height_m):
    title = "Chip calibration"
    corners = []

    def click(event, x, y, *_):
        if event == cv2.EVENT_LBUTTONDOWN and len(corners) < 4:
            corners.append((x, y))

    print(f"Camera {frame.camera}: select a {width_m:g} x {height_m:g} m vertical rectangle.", flush=True)
    print("Click tee-top, far-top, far-bottom, tee-bottom. Enter saves, R resets, Esc cancels.", flush=True)
    cv2.namedWindow(title, cv2.WINDOW_AUTOSIZE)
    cv2.setMouseCallback(title, click)
    try:
        labels = ("Tee top", "Downrange top", "Downrange bottom", "Tee bottom")
        while True:
            panel = cv2.cvtColor(frame.image, cv2.COLOR_GRAY2BGR)
            for index, point in enumerate(corners, start=1):
                cv2.circle(panel, point, 5, (0, 255, 0), -1)
                cv2.putText(panel, str(index), point, cv2.FONT_HERSHEY_SIMPLEX, .6, (0, 255, 255), 2)
            hint = labels[len(corners)] if len(corners) < 4 else "Enter to save"
            cv2.putText(panel, f"{hint} | R reset | Esc cancel", (8, 24),
                        cv2.FONT_HERSHEY_SIMPLEX, .6, (0, 255, 255), 1)
            cv2.imshow(title, panel)
            key = cv2.waitKey(30) & 0xFF
            if key == 27 or cv2.getWindowProperty(title, cv2.WND_PROP_VISIBLE) < 1:
                return None
            if key == ord("r"):
                corners.clear()
            if key in (10, 13) and len(corners) == 4:
                try:
                    calibrate_plane(corners, width_m, height_m)
                except ValueError as error:
                    print(error, flush=True)
                    corners.clear()
                else:
                    return corners
    finally:
        if cv2.getWindowProperty(title, cv2.WND_PROP_VISIBLE) >= 1:
            cv2.destroyWindow(title)


def report_chip(folder, manifest):
    measurement = manifest.get("measurement", {})
    metrics = measurement.get("metrics")
    if metrics:
        status = (f"Chip: {metrics['speed_m_s']:.2f} m/s | "
                  f"{metrics['launch_angle_deg']:.1f} deg elevation (estimated)")
    elif manifest.get("pico_rejection") == "GATE_NOT_QUIET":
        status = "MIC GATE BUSY / Check sensor LED and GP7 wiring"
    else:
        status = measurement.get("reason", "Capture " + manifest["status"])
    print(f"{status}\nSaved: {folder / 'manifest.json'}", flush=True)
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cameras", type=int, nargs=2, default=[0, 1])
    parser.add_argument("--fps", type=float, default=60)
    parser.add_argument("--exposure-us", type=int, default=1000)
    parser.add_argument("--gain", type=float, default=2)
    parser.add_argument("--min-radius", type=int, default=5)
    parser.add_argument("--max-radius", type=int, default=100)
    parser.add_argument("--acquire-min-radius", type=int,
                        help="Minimum radius in pixels for initial lock; tracking uses --min-radius")
    parser.add_argument("--roi", type=int, nargs=5, action="append",
                        metavar=("CAMERA", "X", "Y", "WIDTH", "HEIGHT"),
                        help="Limit detection to this part of a camera's 640x480 view")
    parser.add_argument("--acquire-roi", type=int, nargs=5, action="append",
                        metavar=("CAMERA", "X", "Y", "WIDTH", "HEIGHT"),
                        help="Search this tee area for initial lock, then track outside it")
    parser.add_argument("--output", type=Path, default=Path("data/recordings/bench"))
    parser.add_argument("--headless-seconds", type=float,
                        help="Save one timed clip without a preview, then exit (maximum 5 s)")
    parser.add_argument("--pico-port", help="Pico USB serial path, or auto to find one Pico")
    parser.add_argument("--ball-lock", action="store_true", help="Require ball validation before microphone arming")
    parser.add_argument("--stereo-profile", type=Path, help="Real 640x480 stereo calibration and hitting volume JSON")
    parser.add_argument("--allow-unverified-lock", action="store_true",
                        help="Bench only: allow two-view appearance checks without a stereo profile")
    parser.add_argument("--vision-trigger", action="store_true", help="Trigger on a rising ball leaving the tee")
    parser.add_argument("--outputs-disconnected", action="store_true",
                        help="Confirm GP2/GP3/GP4 are disconnected from cameras and lighting")
    parser.add_argument("--chip-camera", type=int, help="Side-view camera index (default first camera)")
    parser.add_argument("--chip-plane-m", nargs=2, type=float, default=(.60, .30),
                        metavar=("WIDTH", "HEIGHT"), help="Measured vertical calibration rectangle in meters")
    args = parser.parse_args()
    if len(set(args.cameras)) != 2 or min(args.cameras) < 0:
        parser.error("Choose two distinct nonnegative camera indices")
    if not 1 <= args.fps <= 60 or not 1 <= args.exposure_us <= 1e6 / args.fps:
        parser.error("Use 1-60 FPS and an exposure within the requested frame period")
    if not 1 <= args.gain <= 16 or not 1 <= args.min_radius < args.max_radius <= 240:
        parser.error("Use gain 1-16 and 1 <= min-radius < max-radius <= 240")
    if args.acquire_min_radius is not None and not args.min_radius <= args.acquire_min_radius < args.max_radius:
        parser.error("Choose an acquisition minimum between min-radius and max-radius")
    if args.headless_seconds is not None and not 0 < args.headless_seconds <= 5:
        parser.error("--headless-seconds must be greater than zero and at most 5")
    chip_camera = args.cameras[0] if args.chip_camera is None else args.chip_camera
    width_m, height_m = args.chip_plane_m
    chip_mode = bool(args.pico_port or args.vision_trigger)
    if args.vision_trigger and (args.pico_port or args.headless_seconds is not None):
        parser.error("Vision trigger requires a desktop preview and uses no Pico port")
    if args.ball_lock and not args.pico_port:
        parser.error("Ball-lock mode requires a Pico microphone connection via --pico-port")
    if not args.ball_lock and (args.stereo_profile or args.allow_unverified_lock):
        parser.error("Stereo and unverified-lock options apply only to --ball-lock")
    if args.ball_lock and not (args.stereo_profile or args.allow_unverified_lock):
        parser.error("Ball-lock mode requires --stereo-profile, or --allow-unverified-lock for bench testing")
    if chip_mode and (chip_camera not in args.cameras or not all(math.isfinite(m) and m > 0 for m in (width_m, height_m))):
        parser.error("Select a connected chip camera and positive calibration dimensions")
    if args.pico_port:
        if not args.outputs_disconnected or args.headless_seconds is not None:
            parser.error("Pico mode requires --outputs-disconnected and a desktop preview")
        if args.pico_port == "auto":
            ports = sorted(Path("/dev/serial/by-id").glob("usb-*Pico*-if00"))
            if len(ports) != 1:
                parser.error("Connect one Pico over USB or supply its serial path with --pico-port")
            args.pico_port = str(ports[0])
        from fairway_fusion.cameras.impact import ImpactRecorder
    regions, acquisition_regions = {}, {}
    for boxes, destination in ((args.roi, regions), (args.acquire_roi, acquisition_regions)):
        for number, x, y, width, height in boxes or ():
            if (number not in args.cameras or number in destination or x < 0 or y < 0
                    or width <= 0 or height <= 0 or x + width > 640 or y + height > 480):
                parser.error("Choose at most one valid 640x480 image region per selected camera per option")
            destination[number] = (x, y, width, height)
    acquire_radius = args.acquire_min_radius
    if acquire_radius is None and acquisition_regions:
        acquire_radius = args.min_radius
    stereo = None
    if args.ball_lock:
        from fairway_fusion.cameras.ball_lock import LockError, StereoZone
        from fairway_fusion.cameras.locked_bench import run_locked_bench

        try:
            if args.stereo_profile:
                stereo = StereoZone.load(args.stereo_profile, tuple(args.cameras))
            for number in args.cameras:
                if number not in acquisition_regions:
                    acquisition_regions[number] = stereo.image_region(number) if stereo else (80, 200, 480, 272)
                if min(acquisition_regions[number][2:]) <= 2 * (acquire_radius or args.min_radius):
                    raise LockError(f"Camera {number} hitting area is too small to contain the ball")
        except LockError as error:
            parser.error(str(error))
    try:
        from picamera2 import Picamera2
    except ImportError:
        parser.exit(1, "Picamera2 is required on the Pi. See docs/camera_first_demo.md.\n")
    if hasattr(Picamera2, "global_camera_info"):
        available = {info["Num"] for info in Picamera2.global_camera_info()}
        missing = set(args.cameras) - available
        if missing:
            parser.exit(1, f"Missing camera indices {sorted(missing)}. "
                        "Run rpicam-hello --list-cameras to check the two sensors.\n")

    settings = dict(size=[640, 480], fps=args.fps, exposure_us=args.exposure_us, gain=args.gain,
                    min_radius=args.min_radius, max_radius=args.max_radius, regions=regions,
                    acquire_min_radius=acquire_radius, acquisition_regions=acquisition_regions)
    headless = args.headless_seconds is not None
    duration = args.headless_seconds or (3 if chip_mode else 2)
    buffer = FrameBuffer(max_frames=int(2 * args.fps * (duration + 1)))
    armed = False
    trigger_ns = None
    deadline_ns = None
    sensor_timestamps = {number: deque(maxlen=31) for number in args.cameras}
    preview_results = {}
    preview_job = None
    preview_job_ns = preview_result_ns = 0
    tracking_job = None
    tracked_frames = []
    motion_history = deque(maxlen=7)
    preview_balls = {}
    preview_tracks = {}
    tee_changed_ns = 0
    tee_click = deque(maxlen=1)
    next_preview_ns = 0
    corners = None
    eligible_arm_ns = 0
    demo_status = "C: calibrate side view"
    vision = None

    def click_tee(event, x, y, *_):
        if event == cv2.EVENT_LBUTTONDOWN and 0 <= x < 640 * len(args.cameras) and 0 <= y < 480:
            tee_click.append((args.cameras[x // 640], x % 640, y))

    def measure(frames, event_ns):
        left, top, width, height = cv2.boundingRect(np.asarray(corners, dtype=np.float32))
        # Include silhouettes that straddle the calibration boundary.
        pad = args.max_radius
        x, y = max(0, left - pad), max(0, top - pad)
        roi = x, y, min(640, left + width + pad) - x, min(480, top + height + pad) - y
        samples = [f for f in frames if f.camera == chip_camera
                   and -850_000_000 <= f.host_ns - event_ns <= 300_000_000]
        analyze_frames(samples, {chip_camera: regions.get(chip_camera, roi)},
                       args.min_radius, args.max_radius, {}, acquire_radius=acquire_radius,
                       acquisition_regions=acquisition_regions)
        result = measure_chip(samples, chip_camera, event_ns, corners, width_m, height_m)
        return recover_short_chip(samples, event_ns, result, acquisition_regions.get(chip_camera), args.max_radius)

    if args.ball_lock:
        print("BALL LOCK | Calibrated scans the empty hitting area | Arm verifies both views")
    elif args.vision_trigger:
        print("CHIP DEMO | C calibrate | A arm camera trigger | D cancel | Q quit", flush=True)
    elif args.pico_port:
        print("CHIP DEMO | C calibrate | A arm microphone | T test trigger | D cancel | Q quit", flush=True)
    else:
        print("FREE-RUNNING cameras; no Pico control, hardware synchronization, or shot metrics.")
        print("a: software arm | space: save last ~2 s + next 1 s | d: disarm | q: quit")
    with ExitStack() as stack:
        if not headless:
            # Leave CPU for libcamera and the GUI while OpenCV scans a preview.
            cv2.setNumThreads(2)
        if not headless and not args.ball_lock:
            preview_worker = stack.enter_context(ThreadPoolExecutor(max_workers=1))
            tracking_worker = stack.enter_context(ThreadPoolExecutor(max_workers=1))
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
        if args.ball_lock:
            def read_pair():
                jobs = [camera.capture_request(wait=False) for camera in cameras]
                return tuple(Frame(number, time.monotonic_ns(), image, metadata, None)
                             for number, (image, metadata) in zip(args.cameras, read_requests(jobs)))

            run_locked_bench(args, settings, acquisition_regions, stereo, buffer, read_pair,
                             select_chip_plane, report_chip)
            return
        if not headless:
            stack.callback(cv2.destroyAllWindows)
            if acquire_radius is not None:
                cv2.namedWindow(PREVIEW_WINDOW, cv2.WINDOW_AUTOSIZE)
                cv2.setMouseCallback(PREVIEW_WINDOW, click_tee)
                print("Click the stationary ball in either view to focus its first-lock search.", flush=True)
        recorder = None
        if args.pico_port:
            recorder = ImpactRecorder(buffer, args.output, settings, args.pico_port, measure=measure)
            stack.callback(recorder.close)
        # The first camera can deliver a frame while the second starts.
        read_requests([camera.capture_request(wait=False) for camera in cameras])
        ready = False
        while True:
            jobs = [camera.capture_request(wait=False) for camera in cameras]
            frames = [
                Frame(number, time.monotonic_ns(), image, metadata, None)
                for number, (image, metadata) in zip(args.cameras, read_requests(jobs))
            ]
            if armed or headless or chip_mode:
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
                if args.vision_trigger:
                    measurement = measure(buffer.frames, trigger_ns)
                    manifest = buffer.save(folder, trigger_ns=trigger_ns, settings=settings, measurement=measurement)
                    demo_status = report_chip(folder, manifest)
                    vision = None
                    eligible_arm_ns = time.monotonic_ns() + 1_000_000_000
                else:
                    analyze_frames(buffer.frames, regions, args.min_radius, args.max_radius, {},
                                   acquire_radius=acquire_radius, acquisition_regions=acquisition_regions)
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
            if tee_click:
                number, x, y = tee_click.pop()
                if not armed and not (recorder and recorder.active):
                    margin = args.max_radius + 12
                    left, top = max(0, x - margin), max(0, y - margin)
                    acquisition_regions[number] = (left, top, min(640, x + margin + 1) - left,
                                                    min(480, y + margin + 1) - top)
                    tee_changed_ns = now_ns
                    preview_tracks.clear()
                    preview_results = {}
                    preview_balls.clear()
                    tracked_frames = []
                    motion_history.clear()
                    print(f"Camera {number} tee search centered at ({x}, {y}).", flush=True)
            if recorder:
                previous_state = recorder.state
                saved = recorder.tick(now_ns)
                if recorder.state == "PICO ARMED" and previous_state != recorder.state:
                    print("PICO ARMED: chip now (10-second arm window).", flush=True)
                if saved:
                    demo_status = report_chip(*saved)
                    buffer.frames.clear()
                    eligible_arm_ns = time.monotonic_ns() + 1_000_000_000
            if preview_job is not None and preview_job.done():
                candidates = preview_job.result()
                if preview_job_ns >= tee_changed_ns:
                    preview_results = candidates
                    preview_result_ns = preview_job_ns
                preview_job = None
            if tracking_job is not None and tracking_job.done():
                tracked, balls = tracking_job.result()
                if tracked[0].host_ns >= tee_changed_ns:
                    tracked_frames, preview_balls = tracked, balls
                    for frame in tracked_frames:
                        if frame.camera in preview_balls:
                            preview_tracks[frame.camera] = (preview_balls[frame.camera], frame.host_ns)
                    if vision is not None and deadline_ns is None:
                        side = next(frame for frame in tracked if frame.camera == chip_camera)
                        was_ready = vision.ready
                        event_ns = vision.observe(side.host_ns, balls.get(chip_camera))
                        if vision.ready and not was_ready:
                            print("CAMERA ARMED: chip now.", flush=True)
                        if event_ns is not None:
                            trigger_ns = event_ns
                            deadline_ns = now_ns + 1_000_000_000
                            print("Ball departure detected. Saving clip.", flush=True)
                tracking_job = None
            motion_history.append(frames)
            if (len(motion_history) == motion_history.maxlen and preview_job is None
                    and deadline_ns is None and len(preview_balls) < len(frames)):
                # One in-flight sample at a time. The camera loop never waits
                # for it or queues frames when the detector falls behind.
                preview_job = preview_worker.submit(
                    analyze_preview, (motion_history[0], motion_history[3], frames),
                    regions, acquire_radius or args.min_radius, args.max_radius, acquisition_regions.copy(),
                )
                preview_job_ns = now_ns
            for frame in frames:
                sensor_ns = frame.metadata.get("SensorTimestamp")
                if sensor_ns is not None:
                    sensor_timestamps[frame.camera].append(sensor_ns)
            if now_ns >= next_preview_ns:
                if tracking_job is None:
                    tracking_job = tracking_worker.submit(
                        track_preview, frames, motion_history[-min(4, len(motion_history))],
                        motion_history[0], regions, args.min_radius, args.max_radius,
                        preview_tracks.copy(),
                        preview_results if now_ns - preview_result_ns <= 300_000_000 else {},
                        acquire_radius, acquisition_regions.copy(),
                    )
                status = "RECORDING" if deadline_ns is not None else (
                    "SOFTWARE ARMED" if armed else "READY / DISARMED"
                )
                if recorder:
                    status = recorder.state if recorder.active else demo_status
                elif args.vision_trigger and deadline_ns is None:
                    status = demo_status
                    if vision is not None:
                        status = "CAMERA ARMED / CHIP NOW" if vision.ready else "WAITING FOR STATIONARY BALL"
                # Apply each circle to the same image the tracker analyzed.
                if tracked_frames and now_ns - tracked_frames[0].host_ns <= 120_000_000:
                    show_preview(tracked_frames, status, sensor_timestamps, preview_balls, acquisition_regions)
                else:
                    show_preview(frames, status, sensor_timestamps, {}, acquisition_regions)
                next_preview_ns += 33_333_333
                if next_preview_ns <= now_ns:
                    next_preview_ns = now_ns + 33_333_333
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if chip_mode:
                active = armed or (recorder and recorder.active)
                if key == ord("c") and not active:
                    selected = next(frame for frame in frames if frame.camera == chip_camera)
                    corners = select_chip_plane(selected, width_m, height_m)
                    buffer.frames.clear()
                    eligible_arm_ns = time.monotonic_ns() + 1_000_000_000
                    demo_status = "Place ball and press A to arm" if corners else "C: calibrate side view"
                elif key == ord("a") and not active:
                    if corners and now_ns >= eligible_arm_ns:
                        if recorder:
                            recorder.arm()
                        else:
                            vision = TeeDeparture(corners, width_m, height_m)
                            armed = True
                            print("Leave the ball still until CAMERA ARMED appears.", flush=True)
                    elif corners:
                        print("Wait one second for the stationary ball before arming.", flush=True)
                    else:
                        print("Press C to calibrate the side view first.", flush=True)
                elif key == ord("d"):
                    if recorder:
                        recorder.cancel()
                    else:
                        armed, deadline_ns, vision = False, None, None
                        buffer.frames.clear()
                        demo_status = "Place ball and press A to arm" if corners else "C: calibrate side view"
                elif key == ord("t") and recorder:
                    recorder.test_trigger()
                continue
            if key == ord("a") and not armed:
                armed = True
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
