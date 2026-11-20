"""Index free-running camera recordings in the shared SQLite capture store."""

from contextlib import closing, contextmanager
import json
import logging
from pathlib import Path
import re
import sqlite3
from uuid import uuid4


FRAME_NAME = re.compile(r"camera[0-9]+_[0-9]{5}\.png")
logger = logging.getLogger(__name__)


def camera_devices(camera_info):
    """Describe the API camera numbers and their known physical Pi 5 connectors."""
    devices = {}
    for info in camera_info:
        if "Num" not in info:
            continue
        identity = info.get("Id")
        # The Pi 5 routes RP1 I2C6 to MIPI0 and RP1 I2C4 to MIPI1.
        match = re.search(r"/rp1/i2c@(88000|80000)/", identity or "")
        connector = {"88000": "CAM0", "80000": "CAM1"}[match[1]] if match else None
        devices[str(info["Num"])] = {"Id": identity, "Model": info.get("Model"), "connector": connector}
    return devices


def read_manifest(store, capture):
    """Read the capture's own manifest and reject unsafe or invalid references."""
    relative = capture["recording_manifest_path"]
    if not relative:
        return None, None
    root = store.images_root.resolve()
    path = root / relative
    if (not path.is_relative_to(root) or path.name != "manifest.json"
            or path.parent.name != capture["capture_id"]):
        return None, None
    try:
        if path.resolve() != path:
            return None, None
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RuntimeError):
        return None, None
    if (not isinstance(manifest, dict) or manifest.get("capture_id") != capture["capture_id"]
            or not isinstance(manifest.get("frames"), list)
            or any(not isinstance(frame, dict) or not isinstance(frame.get("file"), str)
                   or not FRAME_NAME.fullmatch(frame["file"]) for frame in manifest["frames"])):
        return None, None
    return path, manifest


def recording_frame_path(manifest_path, filename):
    """Return a regular frame file directly within the manifest's folder."""
    if not FRAME_NAME.fullmatch(filename):
        return None
    path = manifest_path.parent / filename
    try:
        if not path.is_file() or path.resolve() != path:
            return None
    except (OSError, RuntimeError):
        return None
    return path


class RecordingStore:
    def __init__(self, store):
        self.store = store

    @contextmanager
    def writer(self, *, skip_if_busy=False):
        """Hold recovery and recording ownership for the life of this bench process."""
        # A separate SQLite lock releases on process exit without locking the
        # shared capture database against Flask or requiring a new dependency.
        lock = Path(self.store.path).resolve().with_suffix(Path(self.store.path).suffix + ".bench-lock.db")
        with closing(sqlite3.connect(lock, timeout=0)) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as error:
                if skip_if_busy and str(error) == "database is locked":
                    yield
                    return
                raise OSError(f"Unable to acquire recording ownership for {self.store.path}: {error}") from error
            self.recover()
            yield

    def recover(self):
        for capture in self.store.unfinished_recordings():
            capture_id = capture["capture_id"]
            path, manifest = read_manifest(self.store, capture)
            if manifest is not None:
                try:
                    self.finish(capture_id, path.parent, manifest)
                except (KeyError, TypeError, ValueError):
                    logger.warning("Could not recover saved recording %s", capture_id, exc_info=True)
                else:
                    continue
            self.update(capture_id, status="interrupted",
                        error=capture["error"] or "Camera bench stopped before the recording finished.")

    def begin(self, capture_id, folder, *, status="preparing"):
        relative = Path(folder).resolve().relative_to(self.store.images_root.resolve())
        try:
            return self.store.create_capture({
                "capture_id": capture_id, "status": status, "source": "live",
                "purpose": "camera_test", "pinned": True, "timing": None,
                "recording_manifest_path": (relative / "manifest.json").as_posix(),
            }, attach_active_session=True)
        except sqlite3.Error as error:
            raise OSError(f"Could not start the SQLite recording: {error}") from error

    def update(self, capture_id, **changes):
        try:
            return self.store.update_capture(capture_id, **changes)
        except sqlite3.Error as error:
            raise OSError(f"Could not update the SQLite recording: {error}") from error

    def write_failed(self, capture_id, error):
        try:
            self.update(capture_id, status="failed", error=f"Could not save recording: {error}")
        except OSError:
            logger.exception("Could not record save failure for %s", capture_id)

    def reserve(self, output, *, status):
        folder = output / uuid4().hex
        self.begin(folder.name, folder, status=status)
        return folder

    def save_clip(self, buffer, folder, *, trigger_ns, settings, measurement=None):
        capture_id = folder.name
        try:
            manifest = buffer.save(folder, trigger_ns=trigger_ns, settings=settings,
                                   capture_id=capture_id, measurement=measurement)
        except Exception as error:
            self.write_failed(capture_id, error)
            raise
        self.finish(capture_id, folder, manifest)
        return folder, manifest

    def finish(self, capture_id, folder, manifest):
        folder = Path(folder).resolve()
        relative = folder.relative_to(self.store.images_root.resolve())
        if relative.name != capture_id or manifest.get("capture_id") != capture_id:
            raise ValueError("Recording directory and manifest must match the capture ID.")
        frames = manifest["frames"]
        settings = manifest.get("requested_settings", {})
        devices = settings.get("camera_devices", {})
        camera_metadata, paths = {}, {}
        for label, number in zip(("a", "b"), settings["camera_numbers"]):
            matching = [frame for frame in frames if frame["camera"] == number]
            if not matching:
                continue
            frame = min(matching, key=lambda f: abs(f.get(
                "relative_to_host_event_s", f.get("relative_to_manual_trigger_s", 0))))
            if recording_frame_path(folder / "manifest.json", frame["file"]) is None:
                continue
            device = devices.get(str(number), {})
            reported = frame["metadata"]
            paths[f"image_{label}_path"] = (relative / frame["file"]).as_posix()
            camera_metadata[f"camera_{label}"] = {
                "model": device.get("Model"), "camera_id": device.get("Id"),
                "connector": device.get("connector"), "width": frame["width"], "height": frame["height"],
                "pixel_format": frame.get("pixel_format"), "exposure_us": reported.get("ExposureTime"),
                "analogue_gain": reported.get("AnalogueGain"), "sensor_timestamp_ns": reported.get("SensorTimestamp"),
            }
        status, reason = manifest["status"], manifest.get("failure_reason")
        if status == "completed" and (len(paths) != 2 or manifest.get("recording_interrupted")):
            status, reason = "interrupted", reason or "Camera recording ended before both views finished saving."
        return self.update(
            capture_id, status=status, error=reason, pico_report=manifest.get("pico_report"),
            detections={"frame_count": len(frames), "trigger_source": manifest["trigger_source"],
                        "synchronized": manifest["synchronized"], "timing_verified": manifest["timing_verified"],
                        "disarm_confirmed": manifest.get("disarm_confirmed"),
                        "recording_interrupted": status == "interrupted"},
            camera_metadata=camera_metadata or None, **paths,
        )
