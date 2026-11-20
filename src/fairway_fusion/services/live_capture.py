"""Run the shared ball-lock workflow for Flask without a desktop window."""

from contextlib import ExitStack
import logging
from pathlib import Path
from threading import Event, Lock, Thread
import time
from types import SimpleNamespace

import cv2

from fairway_fusion.cameras.ball_lock import StereoZone
from fairway_fusion.cameras.bench import Frame, FrameBuffer, open_bench_cameras, read_requests
from fairway_fusion.cameras.chip import calibrate_plane
from fairway_fusion.cameras.locked_bench import run_locked_bench
from fairway_fusion.cameras.recording_store import RecordingStore, camera_devices


logger = logging.getLogger(__name__)


class LiveCapture:
    def __init__(self, store, port, *, enabled, stereo_profile=None, regions=None,
                 camera_type=None, client_factory=None):
        self.enabled = enabled
        self.port = port
        self.stereo_profile = Path(stereo_profile) if stereo_profile else None
        self.regions = regions
        self.camera_type, self.client_factory = camera_type, client_factory
        self.recordings = RecordingStore(store)
        self.output = store.images_root.resolve() / "captures" / "bench"
        self._lock = Lock()
        self._stop = Event()
        self._thread = None
        self._queued = None
        self._working_on_command = False
        self._preview = None
        self._status = self._initial_status(
            "SETUP", "Start the cameras to see both views, or clear the mat and press Calibrate.")

    def _initial_status(self, mode, message):
        return dict(mode=mode, message=message, calibrated=False, busy=False, measuring=False,
                    capture_id=None, last_capture=None, stereo=bool(self.stereo_profile))

    @property
    def stopped(self):
        return self._stop.is_set()

    def close(self):
        self._stop.set()
        with self._lock:
            thread = self._thread
        if thread:
            thread.join()

    def snapshot(self):
        with self._lock:
            return {**self._status, "enabled": self.enabled, "preview": self._preview is not None,
                    "busy": self._status["busy"] or self._queued is not None or self._working_on_command}

    def preview(self):
        with self._lock:
            return self._preview

    def save_preview(self, image):
        ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 78])
        if ok:
            with self._lock:
                self._preview = encoded.tobytes()

    def publish(self, **status):
        with self._lock:
            self._status = status
            self._working_on_command = False

    def take_commands(self):
        with self._lock:
            if self._queued is None:
                return ()
            command, self._queued = self._queued, None
            self._working_on_command = True
            return (command,)

    def submit(self, action, payload=None):
        if not self.enabled or self.stopped:
            raise ValueError("Live capture is disabled. Start Flask in camera bench mode on the Pi.")
        if action == "plane":
            corners, (width, height) = payload
            if any(not (0 <= x < 640 and 0 <= y < 480) for x, y in corners):
                raise ValueError("Side plane corners must be within the 640 × 480 preview.")
            calibrate_plane(corners, width, height)
        with self._lock:
            running = self._thread is not None and self._thread.is_alive()
            busy = self._status["busy"] or self._queued is not None or self._working_on_command
            if action == "cancel":
                if running and busy:
                    self._queued = (action, None)
                return
            if busy:
                raise ValueError("A capture or setup step is already in progress.")
            if action == "arm":
                if not running or not self._status["calibrated"]:
                    raise ValueError("Scan the empty hitting area before arming.")
                if not self.port:
                    raise ValueError("Set FAIRWAY_PICO_PORT to the connected Pico before arming.")
            if action == "plane" and not running:
                raise ValueError("Start the cameras before setting the side plane.")
            if action != "preview":
                self._queued = (action, payload)
            if not running:
                self._status.update(mode="STARTING CAMERAS", message="Opening both camera feeds.", busy=True)
                self._thread = Thread(target=self._run, name="fairway-live-capture", daemon=True)
                self._thread.start()

    def _run(self):
        try:
            camera_type = self.camera_type
            if camera_type is None:
                try:
                    from picamera2 import Picamera2
                except ImportError as error:
                    raise RuntimeError("Picamera2 is needed to use Live Capture on the Pi.") from error
                camera_type = Picamera2
            numbers = (0, 1)
            stereo = StereoZone.load(self.stereo_profile, numbers) if self.stereo_profile else None
            regions = self.regions or {number: stereo.image_region(number) if stereo else (80, 200, 480, 272)
                                       for number in numbers}
            devices = camera_devices(camera_type.global_camera_info()) if hasattr(camera_type, "global_camera_info") else {}
            settings = dict(camera_numbers=list(numbers), camera_devices=devices,
                            stream_format="YUV420", size=[640, 480], fps=60, exposure_us=1000, gain=8)
            args = SimpleNamespace(cameras=numbers, chip_camera=0, chip_plane_m=(.60, .30),
                                   output=self.output, pico_port=self.port, min_radius=6,
                                   max_radius=56, acquire_min_radius=18)
            with self.recordings.writer(), ExitStack() as stack:
                cv2.setNumThreads(2)
                cameras = open_bench_cameras(stack, camera_type, numbers, fps=settings["fps"],
                                             exposure_us=settings["exposure_us"], gain=settings["gain"])
                for camera in cameras:
                    camera.start()
                    stack.callback(camera.stop)

                def read_pair():
                    jobs = [camera.capture_request(wait=False) for camera in cameras]
                    return tuple(Frame(number, time.monotonic_ns(), image, metadata, None)
                                 for number, (image, metadata) in zip(numbers, read_requests(jobs)))

                run_locked_bench(args, settings, regions, stereo, FrameBuffer(max_frames=480),
                                 read_pair, None, self._saved_message, recording_store=self.recordings,
                                 operator=self, client_factory=self.client_factory)
        except Exception as error:
            logger.exception("Live capture stopped")
            with self._lock:
                self._status = self._initial_status("CAMERAS UNAVAILABLE", str(error))
                self._queued = self._preview = None
                self._working_on_command = False
                self._thread = None

    @staticmethod
    def _saved_message(folder, manifest):
        measurement = manifest.get("measurement", {})
        if measurement.get("metrics"):
            return "Clip and provisional chip estimate saved"
        return measurement.get("reason") or f"Recording {manifest['status']}"
