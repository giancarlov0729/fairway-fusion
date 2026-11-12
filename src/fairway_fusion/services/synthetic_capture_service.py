"""Persist synthetic stereo inputs without USB access or saving shot results."""

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np

from fairway_fusion.calibration.stereo_calibration import CameraCalibration
from fairway_fusion.storage import NotFoundError, SessionStateError
from fairway_fusion.synthetic import CASE_POSITIONS_M, make_stereo_fixture


class CaptureAssetError(RuntimeError):
    def __init__(self, capture_id, message):
        self.capture_id = capture_id
        super().__init__(message)


@dataclass(frozen=True)
class SavedStereoInputs:
    capture_id: str
    session_id: int
    calibration_id: str
    source: str
    images: tuple
    cameras: tuple
    flash_interval_s: float
    timing_source: str


def _interval_seconds(value):
    if type(value) is not int or value <= 0:
        raise ValueError("flash_interval_us must be a positive integer.")
    try:
        seconds = value / 1_000_000
    except OverflowError as error:
        raise ValueError("flash_interval_us is too large.") from error
    if not math.isfinite(seconds):
        raise ValueError("flash_interval_us must be finite.")
    return seconds


def _paths(store, capture_id):
    root = Path(store.images_root).resolve()
    directory = root / "captures" / capture_id
    if directory.resolve() != directory:
        raise CaptureAssetError(capture_id, "Capture folder must not redirect through a symlink.")
    files = {name: directory / name for name in ("camera_a.png", "camera_b.png", "manifest.json")}
    if any(path.resolve() != path for path in files.values()):
        raise CaptureAssetError(capture_id, "Capture files must not redirect through symlinks.")
    return root, directory, files


def create_synthetic_capture(store, session_id, *, case="clean", flash_interval_us=2000):
    """Save a reproducible input pair, not a processed or measured golf shot.

    completed here means input generation finished. Chunk 3 will transition the
    attempt through processing and save a separate accepted/rejected shot.
    """
    if type(session_id) is not int or session_id <= 0:
        raise ValueError("session_id must be a positive integer.")
    active = store.active_session()
    if active is None or active["id"] != session_id:
        raise SessionStateError("An active matching practice session is required.")
    if not isinstance(case, str) or case not in CASE_POSITIONS_M:
        raise ValueError(f"Choose a synthetic case from {tuple(CASE_POSITIONS_M)}.")
    _interval_seconds(flash_interval_us)
    images, cameras, _ = make_stereo_fixture(case=case)
    capture_id = uuid4().hex
    timing = {"flash_interval_us": flash_interval_us, "fixture_case": case}
    store.create_capture({"capture_id": capture_id, "session_id": session_id,
                          "status": "preparing", "source": "simulation",
                          "timing": timing, "timing_source": "commanded"})
    try:
        calibration = store.save_calibration(*cameras, source="synthetic",
                                             notes="Virtual geometry from synthetic-stereo-v1")
        store.update_capture(capture_id, calibration_id=calibration["calibration_id"])
        root, directory, files = _paths(store, capture_id)
        directory.mkdir(parents=True, exist_ok=False)
        hashes = {}
        for name, image in zip(("camera_a.png", "camera_b.png"), images):
            ok, encoded = cv2.imencode(".png", image)
            if not ok:
                raise OSError(f"Could not encode {name}.")
            data = encoded.tobytes()
            with files[name].open("xb") as handle:
                handle.write(data)
            hashes[name] = hashlib.sha256(data).hexdigest()
        manifest = {
            "schema_version": 1, "generator": "synthetic-stereo-v1",
            "capture_id": capture_id, "session_id": session_id, "source": "simulation",
            "calibration_id": calibration["calibration_id"], "timing": timing,
            "timing_source": "commanded", "image_sha256": hashes,
        }
        with files["manifest.json"].open("x", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2, allow_nan=False)
        paths = {f"image_{label}_path": files[f"camera_{label}.png"].relative_to(root).as_posix()
                 for label in ("a", "b")}
        store.update_capture(capture_id, **paths)
        load_synthetic_capture(store, capture_id)
        return store.update_capture(capture_id, status="completed")
    except Exception as error:
        store.update_capture(capture_id, status="failed", error=str(error))
        raise CaptureAssetError(capture_id, f"Could not save synthetic capture: {error}") from error


def load_synthetic_capture(store, capture_id):
    """Reload verified saved inputs; never substitute another image or calibration."""
    capture = store.get_capture(capture_id)
    if capture["source"] != "simulation":
        raise CaptureAssetError(capture_id, "Only simulation captures can use this loader.")
    if capture["images_deleted_at"] is not None:
        raise CaptureAssetError(capture_id, "Capture images were removed by cleanup.")
    try:
        _, _, files = _paths(store, capture_id)
        for label in ("a", "b"):
            expected = f"captures/{capture_id}/camera_{label}.png"
            if capture[f"image_{label}_path"] != expected:
                raise ValueError(f"Missing or mismatched Camera {label.upper()} image path.")
        manifest = json.loads(files["manifest.json"].read_text(encoding="utf-8"))
        expected = {
            "schema_version": 1, "generator": "synthetic-stereo-v1",
            "capture_id": capture_id, "session_id": capture["session_id"],
            "source": "simulation", "calibration_id": capture["calibration_id"],
            "timing": capture["timing"], "timing_source": capture["timing_source"],
        }
        if not isinstance(manifest, dict) or any(manifest.get(k) != v for k, v in expected.items()):
            raise ValueError("Capture manifest does not match its database record.")
        if not isinstance(capture["timing"], dict) or capture["timing_source"] != "commanded":
            raise ValueError("Synthetic inputs require their recorded commanded timing.")
        interval = _interval_seconds(capture["timing"]["flash_interval_us"])
        calibration = store.get_calibration(capture["calibration_id"])
        if calibration["source"] != "synthetic":
            raise ValueError("Synthetic images require their synthetic calibration.")
        cameras = tuple(CameraCalibration(
            np.asarray(cam["K"], dtype=float), np.asarray(cam["distortion"], dtype=float),
            np.asarray(cam["R"], dtype=float), np.asarray(cam["t"], dtype=float),
            tuple(cam["image_size"]),
        ) for cam in (calibration["camera_a"], calibration["camera_b"]))
        images = []
        for name, camera in zip(("camera_a.png", "camera_b.png"), cameras):
            data = files[name].read_bytes()
            if hashlib.sha256(data).hexdigest() != manifest["image_sha256"][name]:
                raise ValueError(f"{name} changed or was swapped after capture.")
            image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
            if (not data.startswith(b"\x89PNG\r\n\x1a\n") or image is None
                    or image.dtype != np.uint8 or image.shape != camera.image_size[::-1]):
                raise ValueError(f"{name} is not a calibrated-size 8-bit monochrome PNG.")
            images.append(image)
        return SavedStereoInputs(capture_id, capture["session_id"], capture["calibration_id"],
                                 "simulation", tuple(images), cameras, interval, "commanded")
    except CaptureAssetError:
        raise
    except (OSError, ValueError, TypeError, KeyError, NotFoundError, cv2.error) as error:
        raise CaptureAssetError(capture_id, f"Could not load synthetic capture: {error}") from error
