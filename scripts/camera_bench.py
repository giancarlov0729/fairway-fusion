"""Two free-running Pi cameras: preview, ball candidates, and manual evidence clips."""

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
import time

import cv2
import numpy as np

from fairway_fusion.cameras.bench import Frame, FrameBuffer
from fairway_fusion.detection.ball_detector import detect_ball


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


def detect_frame(image, roi, min_radius, max_radius, previous=None):
    x, y, width, height = roi or (0, 0, image.shape[1], image.shape[0])
    region = np.s_[y:y + height, x:x + width]
    prior = None
    if previous is not None and previous.ball:
        previous_x, previous_y = previous.ball["center"]
        prior = (previous_x - x, previous_y - y, previous.ball["radius"])
    ball = detect_ball(
        image[region], min_radius, max_radius,
        previous=previous.image[region] if previous is not None else None, prior=prior,
    )
    if ball:
        local_x, local_y = ball["center"]
        ball["center"] = (local_x + x, local_y + y)
    return ball


def analyze_frames(frames, regions, min_radius, max_radius, previous_frames):
    for frame in frames:
        frame.ball = detect_frame(frame.image, regions.get(frame.camera), min_radius,
                                  max_radius, previous_frames.get(frame.camera))
        previous_frames[frame.camera] = frame


def show_preview(frames, status, previous_sensor_ns, *, detecting):
    panels = []
    for frame in frames:
        panel = cv2.cvtColor(frame.image, cv2.COLOR_GRAY2BGR)
        if frame.ball:
            cv2.circle(panel, frame.ball["center"], frame.ball["radius"], (0, 255, 0), 2)
        sensor_ns = frame.metadata.get("SensorTimestamp")
        prior = previous_sensor_ns.get(frame.camera)
        fps_text = f"{1e9 / (sensor_ns - prior):.1f} fps" if (
            sensor_ns is not None and prior is not None and sensor_ns > prior
        ) else "fps unknown"
        detection_text = "DETECTION AFTER CAPTURE"
        if detecting:
            detection_text = "BALL CANDIDATE" if frame.ball else "NO BALL"
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
    previous_sensor_ns = {}
    previous_frames = {}
    next_preview_ns = 0
    print("FREE-RUNNING cameras; no Pico control, hardware synchronization, or shot metrics.")
    print("a: software arm | space: save last ~2 s + next 1 s | d: disarm | q: quit")
    with ExitStack() as stack:
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
                previous_sensor_ns.clear()
            if headless:
                continue
            if not armed:
                analyze_frames(frames, regions, args.min_radius, args.max_radius, previous_frames)
                show_preview(frames, "READY / DISARMED", previous_sensor_ns, detecting=True)
            elif now_ns >= next_preview_ns:
                # Refresh the display occasionally and keep acquiring between refreshes.
                status = "RECORDING" if deadline_ns is not None else "SOFTWARE ARMED"
                show_preview(frames, status, previous_sensor_ns, detecting=False)
                next_preview_ns = now_ns + 100_000_000
            previous_sensor_ns = {
                frame.camera: frame.metadata.get("SensorTimestamp") for frame in frames
            }
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord("a") and not armed:
                armed = True
                previous_frames.clear()
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
