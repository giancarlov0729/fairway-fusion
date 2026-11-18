"""SQLite bookkeeping for free-running camera-test evidence."""

from pathlib import Path


class RecordingStore:
    def __init__(self, store, camera_numbers, settings):
        self.store = store
        self.camera_numbers = camera_numbers
        self.settings = settings

    def begin(self, capture_id):
        session = self.store.active_session()
        return self.store.create_capture({
            "capture_id": capture_id, "status": "preparing", "source": "live",
            "purpose": "camera_test", "session_id": session["id"] if session else None,
            "processing_version": "impact-camera-bench-v1", "pinned": True,
            "timing_source": "commanded",
            "timing": {"camera_trigger_us": 5000, "flash_width_us": 5,
                       "flash_interval_us": 1000, "first_flash_delay_us": 500},
        })

    def update(self, capture_id, **changes):
        return self.store.update_capture(capture_id, **changes)

    def finish(self, capture_id, folder, manifest):
        root = self.store.images_root.resolve()
        folder = Path(folder).resolve()
        # Evidence paths stay portable with the instance/database folder.
        relative = folder.relative_to(root)
        changes = {"status": manifest["status"], "error": manifest.get("failure_reason"),
                   "pico_report": manifest.get("pico_report"),
                   "detections": {"recording_manifest": (relative / "manifest.json").as_posix(),
                                  "frame_count": len(manifest["frames"]),
                                  "trigger_source": manifest["trigger_source"],
                                  "synchronized": False, "timing_verified": False,
                                  "disarm_confirmed": manifest.get("disarm_confirmed"),
                                  "recording_interrupted": manifest.get("recording_interrupted", False)},
                   "camera_metadata": {}}
        for label, number in zip(("camera_a", "camera_b"), self.camera_numbers):
            frames = [f for f in manifest["frames"] if f["camera"] == number]
            if not frames:
                continue
            frame = min(frames, key=lambda f: abs(f["relative_to_host_event_s"]))
            changes["image_a_path" if label == "camera_a" else "image_b_path"] = (
                relative / frame["file"]).as_posix()
            reported = frame["metadata"]
            device = self.settings.get("camera_devices", {}).get(str(number), {})
            changes["camera_metadata"][label] = {
                "camera_id": device.get("Id"), "model": device.get("Model"),
                "width": frame["width"], "height": frame["height"],
                "pixel_format": "R8", "exposure_us": reported.get("ExposureTime"),
                "analogue_gain": reported.get("AnalogueGain"),
                "sensor_timestamp_ns": reported.get("SensorTimestamp"),
            }
        return self.update(capture_id, **changes)
