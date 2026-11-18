"""Two free-running Pi cameras: preview, ball candidates, and manual evidence clips."""

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
import time

import cv2
import numpy as np

from fairway_fusion.cameras.bench import Frame, FrameBuffer
from fairway_fusion.cameras.impact import ImpactRecorder
from fairway_fusion.cameras.recording_store import RecordingStore
from fairway_fusion.storage import ShotStore
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cameras", type=int, nargs=2, default=[0, 1])
    parser.add_argument("--fps", type=float, default=60)
    parser.add_argument("--exposure-us", type=int, default=1000)
    parser.add_argument("--gain", type=float, default=2)
    parser.add_argument("--min-radius", type=int, default=5)
    parser.add_argument("--max-radius", type=int, default=100)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--headless-seconds", type=float,
                        help="Save one timed clip without a preview, then exit (maximum 5 s)")
    parser.add_argument("--database", type=Path, help="SQLite path; defaults to FAIRWAY_DB or app default")
    parser.add_argument("--pico-port", help="Enable automatic Pico microphone recording")
    parser.add_argument("--outputs-disconnected", action="store_true",
                        help="Confirm Pico GP2/GP3/GP4 are disconnected for this free-running bench")
    args = parser.parse_args()
    if args.pico_port and (not args.outputs_disconnected or args.headless_seconds is not None):
        parser.error("Pico mode requires --outputs-disconnected and an interactive preview")
    if len(set(args.cameras)) != 2 or min(args.cameras) < 0:
        parser.error("Choose two distinct nonnegative camera indices")
    if not 1 <= args.fps <= 60 or not 1 <= args.exposure_us <= 1e6 / args.fps:
        parser.error("Use 1-60 FPS and an exposure within the requested frame period")
    if not 1 <= args.gain <= 16 or not 1 <= args.min_radius < args.max_radius <= 240:
        parser.error("Use gain 1-16 and 1 <= min-radius < max-radius <= 240")
    if args.headless_seconds is not None and not 0 < args.headless_seconds <= 5:
        parser.error("--headless-seconds must be greater than zero and at most 5")
    try:
        from picamera2 import Picamera2
    except ImportError:
        parser.exit(1, "Picamera2 is required on the Pi. See docs/camera_first_demo.md.\n")

    store = ShotStore(args.database) if args.pico_port else None
    if args.output is None:
        args.output = store.images_root / "captures" / "bench" if store else Path("data/recordings/bench")
    if store:
        try:
            args.output.resolve().relative_to(store.images_root.resolve())
        except ValueError:
            parser.error("Pico recording output must be inside the database folder")
        print(f"SQLite: {store.path}; evidence: {args.output}")
    settings = dict(size=[640, 480], fps=args.fps, exposure_us=args.exposure_us, gain=args.gain)
    if hasattr(Picamera2, "global_camera_info"):
        settings["camera_devices"] = {
            str(info["Num"]): {key: info.get(key) for key in ("Id", "Model")}
            for info in Picamera2.global_camera_info() if "Num" in info
        }
    duration = args.headless_seconds or 3
    buffer = FrameBuffer(max_frames=int(2 * args.fps * (duration + 1)))
    armed = False
    trigger_ns = None
    deadline_ns = None
    previous_sensor_ns = {}
    print("FREE-RUNNING cameras; no hardware synchronization or shot metrics.")
    if not args.pico_port:
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
        for camera in cameras:
            camera.start()
            stack.callback(camera.stop)
        if args.headless_seconds is None:
            stack.callback(cv2.destroyAllWindows)
        recorder = None
        if args.pico_port:
            recorder = ImpactRecorder(buffer, args.output, settings, args.pico_port,
                                      recording_store=RecordingStore(store, args.cameras, settings))
            stack.callback(recorder.close)
            print("PICO MODE: a arms microphone; impact saves automatically; t tests outputs; d cancels.")
        ready_since_ns = None
        while True:
            panels = []
            jobs = [camera.capture_request(wait=False) for camera in cameras]
            for number, (image, metadata) in zip(args.cameras, read_requests(jobs)):
                received_ns = time.monotonic_ns()
                panel = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
                ball = detect_ball(panel, args.min_radius, args.max_radius)
                buffer.append(Frame(number, received_ns, image, metadata, ball))
                if ball:
                    cv2.circle(panel, ball["center"], ball["radius"], (0, 255, 0), 2)
                sensor_ns = metadata.get("SensorTimestamp")
                prior = previous_sensor_ns.get(number)
                fps_text = f"{1e9 / (sensor_ns - prior):.1f} fps" if (
                    sensor_ns is not None and prior is not None and sensor_ns > prior
                ) else "fps unknown"
                previous_sensor_ns[number] = sensor_ns
                label = f"Cam {number}: {'BALL CANDIDATE' if ball else 'NO BALL'} | {fps_text}"
                cv2.putText(panel, label, (8, 25), cv2.FONT_HERSHEY_SIMPLEX, .5, (0, 255, 255), 1)
                cv2.putText(panel, f"Exposure {metadata.get('ExposureTime', '?')} us", (8, 48),
                            cv2.FONT_HERSHEY_SIMPLEX, .5, (0, 255, 255), 1)
                panels.append(panel)
            now_ns = time.monotonic_ns()
            if ready_since_ns is None:
                ready_since_ns = now_ns
                print("READY: both cameras delivered images.")
                if args.headless_seconds is not None:
                    trigger_ns = now_ns
                    deadline_ns = now_ns + int(args.headless_seconds * 1e9)
            if recorder:
                saved = recorder.tick(now_ns)
                if saved:
                    print(f"Saved {len(saved[1]['frames'])} frames: {saved[0] / 'manifest.json'}")
            if deadline_ns is not None and now_ns >= deadline_ns:
                folder = args.output / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
                manifest = buffer.save(folder, trigger_ns=trigger_ns, settings=settings)
                print(f"Saved {len(manifest['frames'])} frames: {folder / 'manifest.json'}")
                armed, deadline_ns = False, None
                if args.headless_seconds is not None:
                    break
            if args.headless_seconds is not None:
                continue
            status = "RECORDING" if deadline_ns else ("SOFTWARE ARMED" if armed else "READY / DISARMED")
            if recorder:
                status = recorder.state
            view = np.hstack(panels)
            cv2.putText(view, status + " | free-running / unsynchronized", (8, 470),
                        cv2.FONT_HERSHEY_SIMPLEX, .6, (0, 255, 255), 2)
            cv2.imshow("Fairway Fusion camera bench", view)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if recorder:
                if key == ord("a"):
                    recorder.arm()
                elif key == ord("d"):
                    recorder.cancel()
                elif key == ord("t"):
                    recorder.test_trigger()
                continue
            if key == ord("a") and deadline_ns is None:
                armed = True
                print("SOFTWARE ARMED: manual clip saving enabled; Pico is not armed.")
            elif key == ord("d"):
                armed, deadline_ns = False, None
                print("Software disarmed; pending clip cancelled.")
            elif key == 32 and armed and deadline_ns is None:
                trigger_ns = time.monotonic_ns()
                deadline_ns = trigger_ns + int(1e9)


if __name__ == "__main__":
    main()
