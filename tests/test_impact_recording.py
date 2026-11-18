"""Exercise impact recording with real image writes and a simulated USB controller."""
import json
import time
from pathlib import Path
from threading import Event

import numpy as np
import pytest

from fairway_fusion.cameras.bench import Frame, FrameBuffer
from fairway_fusion.cameras.impact import ImpactRecorder


class Pico:
    def __init__(self, outcome="completed", fail=False):
        self.firmware = None
        self.outcome, self.fail = outcome, fail
        self.commands = []
        self.ready = Event()
        self.cancelled = False
        self.closed = False

    def connect(self):
        self.firmware = "PICO2W_CAPTURE_1"

    def command(self, name):
        self.commands.append(name)
        if name == "DISARM" and self.ready.is_set():
            self.cancelled = True
        return "cancelled" if name == "STATUS" else "DISARMED"

    def capture_command(self, name, capture_id, *timing):
        self.commands.append(name)
        assert (self.folder / "attempt.json").exists()
        if name == "ARM":
            self.ready.set()
            if self.fail:
                raise OSError("USB disconnected")
        if name == "REPORT":
            return {"capture_id": capture_id, "status": (
                "cancelled" if self.cancelled else self.outcome), "boot_id": "test-boot",
                "timing_source": "commanded"}

    def close(self):
        self.closed = True


def setup(tmp_path, pico):
    buffer = FrameBuffer(max_frames=6)
    for number in range(2):
        buffer.append(Frame(number, time.monotonic_ns(), np.zeros((8, 8), np.uint8),
                            {"SensorTimestamp": 123, "ExposureTime": 1000}, None))
    def factory(port):
        pico.folder = next(tmp_path.rglob("attempt.json")).parent
        return pico
    recorder = ImpactRecorder(buffer, tmp_path, {"fps": 30}, "fake", client_factory=factory)
    return recorder


def finish(recorder):
    recorder._thread.join(timeout=3)
    assert not recorder._thread.is_alive()
    result = recorder.tick()
    return result or recorder.tick(time.monotonic_ns() + 2_000_000_000)


@pytest.mark.parametrize("outcome", ["completed", "timed_out", "failed", "cancelled"])
def test_one_attempt_one_recording(tmp_path, outcome):
    pico = Pico(outcome)
    recorder = setup(tmp_path, pico)
    assert recorder.arm()
    assert not recorder.arm()
    folder, manifest = finish(recorder)
    assert manifest["status"] == outcome
    assert manifest["capture_id"] == manifest["pico_report"]["capture_id"]
    assert manifest["pico_report"]["boot_id"] == "test-boot"
    assert not manifest["synchronized"] and not manifest["timing_verified"]
    assert {r["camera"] for r in manifest["frames"]} == {0, 1}
    assert all("relative_to_host_event_s" in r for r in manifest["frames"])
    assert json.loads((folder / "manifest.json").read_text()) == manifest
    assert pico.closed and manifest["disarm_confirmed"]
    assert recorder.tick(time.monotonic_ns() + 3_000_000_000) is None
    recorder.close()
    assert len(list(tmp_path.rglob("manifest.json"))) == 1


def test_disconnect_preserves_evidence(tmp_path):
    recorder = setup(tmp_path, Pico(fail=True))
    recorder.arm()
    folder, manifest = finish(recorder)
    assert manifest["status"] == "interrupted"
    assert "USB disconnected" in manifest["failure_reason"]
    assert (folder / "attempt.json").exists()


def test_cancel_and_close_while_armed(tmp_path):
    pico = Pico("armed")
    recorder = setup(tmp_path, pico)
    recorder.arm()
    assert pico.ready.wait(2)
    recorder.cancel()
    recorder.close()
    manifest = json.loads(next(tmp_path.rglob("manifest.json")).read_text())
    assert manifest["status"] == "cancelled"
    assert pico.closed


def test_quit_during_post_event_window_saves_partial_evidence(tmp_path):
    recorder = setup(tmp_path, Pico())
    recorder.arm()
    recorder._thread.join(3)
    assert recorder.tick() is None
    recorder.close()
    manifest = json.loads(next(tmp_path.rglob("manifest.json")).read_text())
    assert manifest["recording_interrupted"]
    assert manifest["status"] == "completed"


def test_failed_image_save_keeps_attempt_record(tmp_path, monkeypatch):
    recorder = setup(tmp_path, Pico())
    recorder.arm()
    monkeypatch.setattr("cv2.imwrite", lambda *args: False)
    with pytest.raises(OSError):
        finish(recorder)
    assert next(tmp_path.rglob("attempt.json")).exists()
    assert not list(tmp_path.rglob("manifest.json"))


def test_preview_automatically_records_pico_event(tmp_path, monkeypatch):
    import runpy
    import sys
    from types import SimpleNamespace
    import cv2
    import fairway_fusion.cameras.impact as impact

    pico = Pico()
    def factory(port):
        pico.folder = next(tmp_path.rglob("attempt.json")).parent
        return pico
    original = impact.ImpactRecorder
    def recorder_factory(*args, **kwargs):
        return original(*args, client_factory=factory, **kwargs)
    monkeypatch.setattr(impact, "ImpactRecorder", recorder_factory)
    cameras, requests = [], []
    class Request:
        released = False
        def make_array(self, name):
            return np.zeros((720, 640), dtype=np.uint8)
        def get_metadata(self):
            return {"SensorTimestamp": time.monotonic_ns()}
        def release(self):
            self.released = True
    class Camera:
        stopped = closed = False
        def __init__(self, number):
            cameras.append(self)
        def create_video_configuration(self, **kwargs):
            return kwargs
        def configure(self, config):
            pass
        def start(self):
            pass
        def capture_request(self, wait):
            time.sleep(.01)
            request = Request()
            requests.append(request)
            return SimpleNamespace(get_result=lambda timeout: request)
        def stop(self):
            self.stopped = True
        def close(self):
            self.closed = True
    monkeypatch.setitem(sys.modules, "picamera2", SimpleNamespace(Picamera2=Camera))
    monkeypatch.setattr(cv2, "imshow", lambda *args: None)
    monkeypatch.setattr(cv2, "destroyAllWindows", lambda: None)
    started = time.monotonic()
    first = True
    def key(delay):
        nonlocal first
        if first:
            first = False
            return ord('a')
        if list(tmp_path.rglob("manifest.json")):
            return ord('q')
        assert time.monotonic() - started < 8, "Automatic recording never finished"
        return -1
    monkeypatch.setattr(cv2, "waitKey", key)
    monkeypatch.setattr(sys, "argv", ["camera_bench", "--pico-port", "fake",
                        "--outputs-disconnected", "--database", str(tmp_path / "fairway.db"),
                        "--output", str(tmp_path / "clips")])
    runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/camera_bench.py"),
                   run_name="__main__")
    manifest = json.loads(next(tmp_path.rglob("manifest.json")).read_text())
    assert manifest["status"] == "completed"
    assert manifest["trigger_source"] == "pico_microphone"
    assert all(r.released for r in requests)
    assert all(c.closed and c.stopped for c in cameras)
    assert pico.closed


def test_test_trigger_is_labelled_and_sent_once(tmp_path):
    pico = Pico("armed")
    original_command = pico.capture_command
    def command(name, capture_id, *timing):
        if name == "TEST_TRIGGER":
            pico.outcome = "completed"
        return original_command(name, capture_id, *timing)
    pico.capture_command = command
    recorder = setup(tmp_path, pico)
    recorder.arm()
    assert pico.ready.wait(2)
    recorder.tick()
    recorder.test_trigger()
    folder, manifest = finish(recorder)
    assert manifest["trigger_source"] == "pico_test_command"
    assert pico.commands.count("TEST_TRIGGER") == 1
    assert manifest["status"] == "completed"


def test_unconfirmed_disarm_is_preserved(tmp_path):
    pico = Pico()
    pico.command = lambda name: (_ for _ in ()).throw(OSError("USB gone"))
    recorder = setup(tmp_path, pico)
    recorder.arm()
    _, manifest = finish(recorder)
    assert manifest["status"] == "interrupted"
    assert not manifest["disarm_confirmed"]
    assert "Disarm unconfirmed" in manifest["failure_reason"]


@pytest.mark.parametrize("outcome", ["completed", "cancelled", "timed_out", "failed"])
def test_sqlite_recording_lifecycle(tmp_path, outcome):
    from fairway_fusion.storage import ShotStore
    from fairway_fusion.cameras.recording_store import RecordingStore
    store = ShotStore(tmp_path / 'fairway.db')
    session = store.start_session()
    pico = Pico(outcome)
    recorder = setup(tmp_path, pico)
    recorder.output = tmp_path / 'captures' / 'bench'
    recorder.recording_store = RecordingStore(store, [0, 1], {})
    recorder.arm()
    initial = store.get_capture(recorder.capture_id)
    assert initial['status'] == 'preparing'
    assert initial['purpose'] == 'camera_test'
    assert initial['session_id'] == session['id']
    _, manifest = finish(recorder)
    row = ShotStore(tmp_path / 'fairway.db').get_capture(recorder.capture_id)
    assert row['status'] == outcome
    assert row['pico_report'] == manifest['pico_report']
    assert row['pinned']
    assert row['camera_metadata']['camera_a']['exposure_us'] == 1000
    assert row['camera_metadata']['camera_a']['width'] == 8
    assert row['camera_metadata']['camera_b']['camera_id'] is None
    assert store.image_files(recorder.capture_id)['state'] == 'present'
    assert (store.images_root / row['detections']['recording_manifest']).is_file()
    assert store.list_shots() == []
    with pytest.raises(ValueError, match='Camera-test'):
        store.save_shot(session_id=session['id'], capture_id=recorder.capture_id,
                        source='live', speed_m_s=50, launch_angle_deg=15)
    assert store.cleanup_images(keep_accepted=0, keep_other=0)['deleted'] == []


def test_database_failure_never_arms_hardware(tmp_path):
    class BrokenStore:
        def begin(self, capture_id):
            raise OSError('database locked')
    pico = Pico()
    recorder = setup(tmp_path, pico)
    recorder.recording_store = BrokenStore()
    with pytest.raises(OSError, match='database locked'):
        recorder.arm()
    assert not recorder.active
    assert not pico.commands


def test_sqlite_disconnect_and_write_failure(tmp_path, monkeypatch):
    from fairway_fusion.storage import ShotStore
    from fairway_fusion.cameras.recording_store import RecordingStore
    store = ShotStore(tmp_path / 'fairway.db')
    recorder = setup(tmp_path, Pico(fail=True))
    recorder.output = tmp_path / 'captures' / 'bench'
    recorder.recording_store = RecordingStore(store, [0, 1], {})
    recorder.arm()
    finish(recorder)
    assert store.get_capture(recorder.capture_id)['status'] == 'interrupted'
    recorder.arm()
    monkeypatch.setattr('cv2.imwrite', lambda *args: False)
    with pytest.raises(OSError):
        finish(recorder)
    assert store.get_capture(recorder.capture_id)['status'] == 'failed'
    assert store.get_capture(recorder.capture_id)['image_a_path'] is None
    recorder.close()
