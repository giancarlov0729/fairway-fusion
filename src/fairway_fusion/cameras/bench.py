"""Free-running camera evidence for bring-up; no stereo or shot measurements."""

from collections import deque
from contextlib import ExitStack
from dataclasses import dataclass
import json
from pathlib import Path

import cv2
import numpy as np


def read_requests(jobs):
    """Copy both luma planes and release every request, including on errors."""
    with ExitStack() as stack:
        requests = []
        for job in jobs:
            request = job.get_result(timeout=2)
            stack.callback(request.release)
            requests.append(request)
        return [(request.make_array("main")[:480, :640].copy(), request.get_metadata())
                for request in requests]


def open_bench_cameras(stack, camera_type, numbers, *, fps, exposure_us, gain):
    """Open and configure the same free-running stream for desktop and browser."""
    cameras = []
    for number in numbers:
        camera = camera_type(number)
        stack.callback(camera.close)
        config = camera.create_video_configuration(
            main={"size": (640, 480), "format": "YUV420"}, buffer_count=4,
            controls={"AeEnable": False, "ExposureTime": exposure_us,
                      "AnalogueGain": gain, "FrameRate": fps}, queue=False,
        )
        camera.configure(config)
        cameras.append(camera)
    return cameras


@dataclass
class Frame:
    camera: int
    host_ns: int
    image: np.ndarray
    metadata: dict
    ball: dict | None


class FrameBuffer:
    """Bound memory even when the requested camera rate is not achieved."""

    def __init__(self, max_frames=180):
        self.frames = deque(maxlen=max_frames)

    def append(self, frame):
        self.frames.append(frame)

    def save(self, folder, *, trigger_ns, settings, attempt=None, measurement=None, capture_id=None):
        folder = Path(folder)
        if attempt is None:
            folder.mkdir(parents=True, exist_ok=False)
        elif not (folder / "attempt.json").is_file() or (folder / "manifest.json").exists():
            raise FileExistsError("Expected a reserved, unsaved capture folder")
        records = []
        for index, frame in enumerate(self.frames):
            name = f"camera{frame.camera}_{index:05d}.png"
            if not cv2.imwrite(str(folder / name), frame.image):
                raise OSError(f"Could not save {name}")
            records.append({
                "camera": frame.camera, "file": name,
                "width": frame.image.shape[1], "height": frame.image.shape[0],
                "pixel_format": "R8" if frame.image.ndim == 2 and frame.image.dtype == np.uint8 else None,
                "host_received_monotonic_ns": frame.host_ns,
                ("relative_to_host_event_s" if attempt or measurement is not None else "relative_to_manual_trigger_s"):
                    (frame.host_ns - trigger_ns) / 1e9,
                "metadata": {k: frame.metadata[k] for k in (
                    "SensorTimestamp", "ExposureTime", "AnalogueGain", "FrameDuration"
                ) if k in frame.metadata},
                "ball_candidate": frame.ball,
            })
        manifest = {
            "source": "physical_free_running_cameras", "synchronized": False,
            "trigger_source": "manual_host", "trigger_monotonic_ns": trigger_ns,
            "timing_verified": False, "metrics": None, "requested_settings": settings,
            "frames": records,
        }
        if capture_id is not None:
            manifest.update(capture_id=capture_id, status="completed")
        if attempt is not None:
            manifest.update(attempt)
            manifest["trigger_monotonic_ns"] = trigger_ns
        if measurement is not None:
            manifest.update(status="completed", trigger_source="camera_tee_departure",
                            measurement=measurement, metrics=measurement.get("metrics"))
        # Publish the complete manifest once all images have been saved.
        temporary = folder / "manifest.tmp"
        temporary.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        temporary.replace(folder / "manifest.json")
        return manifest
