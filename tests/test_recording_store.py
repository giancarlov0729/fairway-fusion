import json
import sqlite3
import time

import cv2
import numpy as np
import pytest

from fairway_fusion.cameras.bench import Frame, FrameBuffer
from fairway_fusion.cameras.impact import ImpactRecorder
from fairway_fusion.cameras.recording_store import RecordingStore, camera_devices
from fairway_fusion.storage import ShotStore, database
from fairway_fusion.web.app import create_app


CAMERAS = (1, 0)
SETTINGS = {
    "camera_numbers": CAMERAS,
    "camera_devices": camera_devices([
        {"Num": 1, "Id": "/base/axi/pcie@1000120000/rp1/i2c@88000/imx296@1a", "Model": "imx296"},
        {"Num": 0, "Id": "/base/axi/pcie@1000120000/rp1/i2c@80000/imx296@1a", "Model": "imx296"},
    ]),
}


class Pico:
    firmware = "PICO2W_CAPTURE_1"

    def __init__(self, store, outcome):
        self.store = store
        self.outcome = outcome
        self.commands = []

    def connect(self):
        pass

    def close(self):
        pass

    def command(self, name):
        self.commands.append(name)
        return "idle"

    def capture_command(self, name, capture_id, *parameters):
        self.commands.append(name)
        if name == "CONFIG":
            assert self.store.get_capture(capture_id)["status"] == "preparing"
        if name == "ARM" and self.outcome == "interrupted":
            raise OSError("Pico disconnected")
        if name == "REPORT":
            return {"capture_id": capture_id, "status": self.outcome, "boot_id": "test-boot"}


def frames(t0=1_000_000):
    buffer = FrameBuffer()
    for dt in (100, 1000):
        for number in CAMERAS:
            buffer.append(Frame(number, t0 + dt, np.zeros((8, 12), np.uint8),
                                {"SensorTimestamp": t0 + dt, "ExposureTime": dt, "AnalogueGain": 2.0}, None))
    return buffer


@pytest.fixture
def store(tmp_path):
    return ShotStore(tmp_path / "fairway.db")


@pytest.mark.parametrize("outcome", ["completed", "cancelled", "timed_out", "failed", "interrupted"])
def test_pico_attempt_persists_metadata_and_reports(store, outcome):
    session = store.start_session()
    pico = Pico(store, outcome)
    adapter = RecordingStore(store)
    recorder = ImpactRecorder(frames(time.monotonic_ns()), store.images_root / "clips", SETTINGS,
                              "fake", client_factory=lambda port: pico, recording_store=adapter)
    assert recorder.arm(ball_lock={"verification": "two_view_appearance"})
    assert not recorder.arm()
    recorder._thread.join(timeout=3)
    assert not recorder._thread.is_alive()
    saved = recorder.tick() or recorder.tick(time.monotonic_ns() + 2_000_000_000)
    folder, manifest = saved
    row = ShotStore(store.path).get_capture(folder.name)
    assert row["session_id"] == session["id"]
    assert row["purpose"] == "camera_test"
    assert row["status"] == outcome
    assert row["pico_report"] == manifest["pico_report"]
    assert row["timing"] is None and row["pinned"]
    assert row["camera_metadata"]["camera_a"]["connector"] == "CAM0"
    assert row["camera_metadata"]["camera_b"]["connector"] == "CAM1"
    assert row["camera_metadata"]["camera_a"]["pixel_format"] == "R8"
    assert row["camera_metadata"]["camera_a"]["width"] == 12
    assert manifest["ball_lock"]["verification"] == "two_view_appearance"
    assert "DISARM" in pico.commands
    assert store.get_capture_shot(folder.name) is None
    assert store.image_files(folder.name)["state"] == "present"


def test_camera_failure_and_database_failure_do_not_leave_a_completed_recording(store, monkeypatch):
    adapter = RecordingStore(store)
    pico = Pico(store, "completed")
    recorder = ImpactRecorder(frames(), store.images_root / "clips", SETTINGS, "fake",
                              client_factory=lambda port: pico, recording_store=adapter)
    with monkeypatch.context() as patch:
        def unavailable(*args, **kwargs):
            raise sqlite3.OperationalError("database locked")
        patch.setattr(store, "create_capture", unavailable)
        with pytest.raises(OSError, match="database locked"):
            recorder.arm()
    assert not pico.commands and not recorder.active

    recorder.arm()
    recorder._thread.join(timeout=3)
    assert not recorder._thread.is_alive()
    assert recorder.tick() is None
    monkeypatch.setattr(cv2, "imwrite", lambda *_: False)
    with pytest.raises(OSError, match="Could not save"):
        recorder.tick(time.monotonic_ns() + 2_000_000_000)
    assert store.get_capture(recorder.capture_id)["status"] == "failed"
    assert not list(store.images_root.rglob("manifest.json"))
    assert not recorder.active


def test_recordings_reopen_through_flask_and_return_original_frame(store):
    session = store.start_session()
    adapter = RecordingStore(store)
    folder = adapter.reserve(store.images_root / "clips", status="armed")
    _, manifest = adapter.save_clip(frames(), folder, trigger_ns=1_000_101, settings=SETTINGS)
    store.create_capture({"capture_id": "d" * 32, "status": "completed", "source": "simulation"})
    client = create_app({"TESTING": True, "DATABASE_PATH": store.path}).test_client()

    listing = client.get(f"/api/recordings?session_id={session['id']}&limit=1").get_json()["recordings"]
    assert [row["capture_id"] for row in listing] == [folder.name]
    assert client.get("/api/recordings?offset=1").get_json()["recordings"] == []
    assert client.get("/api/recordings?offset=wrong").status_code == 400
    assert client.get(f"/api/recordings/{'d' * 32}").status_code == 404
    detail = client.get(f"/api/recordings/{folder.name}").get_json()
    assert detail["capture"]["camera_metadata"]["camera_a"]["exposure_us"] == 100
    assert detail["capture"]["camera_metadata"]["camera_b"]["sensor_timestamp_ns"] == 1_000_100
    assert len(detail["manifest"]["frames"]) == len(manifest["frames"]) == 4
    frame = detail["manifest"]["frames"][0]
    response = client.get(frame["url"])
    assert response.mimetype == "image/png"
    assert response.data == (folder / frame["file"]).read_bytes()
    response.close()
    assert client.get(f"/api/recordings/{folder.name}/frames/camera0_99999.png").status_code == 404
    (folder / frame["file"]).unlink()
    assert client.get(frame["url"]).status_code == 404
    store.update_capture(folder.name, pinned=False)
    assert store.cleanup_images(keep_other=0)["deleted"] == []


def test_recovery_keeps_running_writer_and_recovers_completed_manifest(store):
    adapter = RecordingStore(store)
    clips = store.images_root / "clips"
    unfinished, saved = "a" * 32, "b" * 32
    with adapter.writer():
        adapter.begin(unfinished, clips / unfinished)
        adapter.begin(saved, clips / saved)
        buffer = frames()
        buffer.save(clips / saved, capture_id=saved, trigger_ns=1_000_100, settings=SETTINGS)
        app = create_app({"TESTING": True, "DATABASE_PATH": store.path})
        assert app.test_client().get(f"/api/recordings/{unfinished}").get_json()["manifest"] is None
        assert store.get_capture(unfinished)["status"] == "preparing"
        with pytest.raises(OSError, match="recording ownership"):
            with RecordingStore(ShotStore(store.path)).writer():
                pytest.fail("Another recording writer was allowed to start")
    create_app({"TESTING": True, "DATABASE_PATH": store.path})
    with adapter.writer():
        assert store.get_capture(unfinished)["status"] == "interrupted"
        assert store.get_capture(saved)["status"] == "completed"
        assert store.get_capture(saved)["detections"]["frame_count"] == 4


def test_migration_indexes_old_database_recordings(tmp_path):
    capture_id = "f" * 32
    path = tmp_path / "old.db"
    manifest_path = f"captures/bench/{capture_id}/manifest.json"
    folder = (tmp_path / manifest_path).parent
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(json.dumps({"capture_id": capture_id, "frames": []}), encoding="utf-8")
    with sqlite3.connect(path) as connection:
        for migration in database.MIGRATIONS[:5]:
            migration(connection)
        connection.execute(
            """INSERT INTO captures (capture_id, created_at, updated_at, status, purpose, source,
                                     detections_json, timing_json) VALUES (?, '', '', 'completed', 'camera_test',
                                                                          'live', ?, ?)""",
            (capture_id, json.dumps({"recording_manifest": manifest_path}), '{"camera_trigger_us": 5000}'),
        )
        connection.execute("PRAGMA user_version = 5")
    app = create_app({"TESTING": True, "DATABASE_PATH": path})
    detail = app.test_client().get(f"/api/recordings/{capture_id}").get_json()
    assert detail["capture"]["recording_manifest_path"] == manifest_path
    assert detail["capture"]["timing"] is None
    assert detail["manifest"]["capture_id"] == capture_id


def test_recording_manifest_must_match_its_capture_and_stay_in_its_folder(store):
    adapter = RecordingStore(store)
    folder = adapter.reserve(store.images_root / "clips", status="armed")
    adapter.save_clip(frames(), folder, trigger_ns=1_000_100, settings=SETTINGS)
    path = folder / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["frames"][0]["file"] = "../../secret.png"
    path.write_text(json.dumps(manifest))
    client = create_app({"TESTING": True, "DATABASE_PATH": store.path}).test_client()
    assert client.get(f"/api/recordings/{folder.name}").get_json()["manifest"] is None


def test_recording_frame_jump_keeps_selected_camera_and_filter(store):
    adapter = RecordingStore(store)
    folder = adapter.reserve(store.images_root / "clips", status="armed")
    buffer = FrameBuffer(max_frames=204)
    image = np.zeros((8, 12), np.uint8)
    for index in range(102):
        timestamp = 1_000_000 + index * 16_666_667
        for camera in CAMERAS:
            buffer.append(Frame(camera, timestamp, image, {"SensorTimestamp": timestamp}, None))
    adapter.save_clip(buffer, folder, trigger_ns=1_000_000, settings=SETTINGS)
    client = create_app({"TESTING": True, "DATABASE_PATH": store.path}).test_client()

    page = client.get(f"/recordings/{folder.name}?frames=all&camera=B&frame=100")

    assert page.status_code == 200
    assert "Frame 100 of 102 · Camera B" in page.text
    assert 'camera0_00199.png' in page.text
    assert 'name="frames" value="all"' in page.text
    assert 'name="camera" value="B"' in page.text
    assert 'name="frame" type="number" min="1" max="102"' in page.text


def test_connector_unknown_when_only_the_enum_number_is_known():
    assert camera_devices([{"Num": 0, "Id": "/usb/usb-camera"}])["0"]["connector"] is None


def test_vision_recording_preserves_session_from_arm_and_saves_provisional_metrics(store):
    adapter = RecordingStore(store)
    folder = adapter.reserve(store.images_root / "clips", status="preparing")
    store.start_session()
    measurement = {"metrics": {"speed_m_s": 2.5, "launch_angle_deg": 12.0}}
    adapter.save_clip(frames(), folder, trigger_ns=1_000_100, settings=SETTINGS, measurement=measurement)
    row = store.get_capture(folder.name)
    assert row["session_id"] is None
    assert row["detections"]["trigger_source"] == "camera_tee_departure"
    client = create_app({"TESTING": True, "DATABASE_PATH": store.path}).test_client()
    assert client.get(f"/api/recordings/{folder.name}").get_json()["manifest"]["measurement"] == measurement
    assert store.list_shots() == []
