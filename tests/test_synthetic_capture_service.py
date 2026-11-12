import hashlib
import json

import cv2
import numpy as np
import pytest

from fairway_fusion.services.synthetic_capture_service import (
    CaptureAssetError, create_synthetic_capture, load_synthetic_capture,
)
from fairway_fusion.services.vision_service import analyze_image_pair
from fairway_fusion.storage import SessionStateError, ShotStore
from fairway_fusion.synthetic import CASE_POSITIONS_M, make_stereo_fixture


@pytest.fixture
def setup(tmp_path):
    store = ShotStore(tmp_path / "instance" / "shots.db")
    session_id = store.start_session()["id"]
    return store, session_id


def analyze(inputs):
    return analyze_image_pair(*inputs.images, *inputs.cameras, inputs.flash_interval_s,
                              source=inputs.source, timing_source=inputs.timing_source)


@pytest.mark.parametrize("case", list(CASE_POSITIONS_M))
def test_saved_pairs_reproduce_in_memory_results_after_restart(setup, case):
    store, sid = setup
    images, cameras, _ = make_stereo_fixture(case=case)
    before = analyze_image_pair(*images, *cameras, .002, source="simulation")
    record = create_synthetic_capture(store, sid, case=case)
    key = record["capture_id"]
    assert record["session_id"] == sid
    assert record["source"] == "simulation"
    assert record["status"] == "completed"
    assert record["pico_report"] is None
    assert record["measured_timing"] is None
    assert store.get_calibration(record["calibration_id"])["source"] == "synthetic"
    assert store.list_shots() == []
    restarted = ShotStore(store.path)
    loaded = load_synthetic_capture(restarted, key)
    for name, image, original in zip(("a", "b"), loaded.images, images):
        assert record[f"image_{name}_path"] == f"captures/{key}/camera_{name}.png"
        assert image.shape == (480, 640)
        assert image.dtype == np.uint8
        assert np.array_equal(image, original)
    assert loaded.session_id == sid
    assert loaded.flash_interval_s == .002
    assert analyze(loaded) == before
    assert restarted.list_shots() == []


def test_each_capture_has_its_own_files_and_calibration(setup):
    store, sid = setup
    first = create_synthetic_capture(store, sid)
    second = create_synthetic_capture(store, sid, case="leftward", flash_interval_us=1000)
    assert first["capture_id"] != second["capture_id"]
    assert first["calibration_id"] != second["calibration_id"]
    assert first["image_a_path"] != second["image_a_path"]
    assert load_synthetic_capture(store, first["capture_id"]).flash_interval_s == .002
    assert load_synthetic_capture(store, second["capture_id"]).flash_interval_s == .001


@pytest.mark.parametrize("options", [{"case": "invalid"}, {"case": None},
    {"flash_interval_us": 0}, {"flash_interval_us": True},
    {"flash_interval_us": .002}, {"flash_interval_us": 10**400}])
def test_invalid_options_leave_no_attempt_or_files(setup, options):
    store, sid = setup
    with pytest.raises(ValueError):
        create_synthetic_capture(store, sid, **options)
    assert store.list_captures() == []
    assert store.latest_calibration(None) is None
    assert not (store.images_root / "captures").exists()


@pytest.mark.parametrize("session_id", [None, True, "1", 0, -1, 999])
def test_invalid_or_mismatched_session_does_not_generate(setup, session_id):
    store, _ = setup
    with pytest.raises((ValueError, SessionStateError)):
        create_synthetic_capture(store, session_id)
    assert store.list_captures() == []


def test_ended_session_cannot_create_capture(setup):
    store, sid = setup
    store.end_session(sid)
    with pytest.raises(SessionStateError):
        create_synthetic_capture(store, sid)
    assert store.list_captures() == []


@pytest.mark.parametrize("fail_at", [1, 2])
def test_encoding_failure_preserves_failed_attempt(setup, monkeypatch, fail_at):
    store, sid = setup
    original = cv2.imencode
    calls = 0

    def encode(*args):
        nonlocal calls
        calls += 1
        return (False, None) if calls == fail_at else original(*args)

    monkeypatch.setattr(cv2, "imencode", encode)
    with pytest.raises(CaptureAssetError) as error:
        create_synthetic_capture(store, sid)
    record = store.get_capture(error.value.capture_id)
    assert record["status"] == "failed"
    assert record["session_id"] == sid
    assert "encode" in record["error"]
    assert record["image_a_path"] is record["image_b_path"] is None
    assert store.list_shots() == []


def test_disk_write_failure_is_recorded(setup, monkeypatch):
    store, sid = setup
    original = type(store.images_root).open

    def fail_png(path, *args, **kwargs):
        if path.name == "camera_b.png":
            raise PermissionError("Cannot write Camera B image")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(type(store.images_root), "open", fail_png)
    with pytest.raises(CaptureAssetError) as error:
        create_synthetic_capture(store, sid)
    record = store.get_capture(error.value.capture_id)
    assert record["status"] == "failed"
    assert "Cannot write" in record["error"]
    assert (store.images_root / "captures" / record["capture_id"] / "camera_a.png").exists()


@pytest.mark.parametrize("damage", ["missing", "corrupt", "swap", "path", "timing", "manifest"])
def test_reload_refuses_missing_or_mismatched_inputs(setup, damage):
    store, sid = setup
    record = create_synthetic_capture(store, sid)
    key = record["capture_id"]
    a, b = (store.images_root / record[f"image_{label}_path"] for label in ("a", "b"))
    if damage == "missing":
        b.unlink()
    elif damage == "corrupt":
        b.write_bytes(b"not an image")
    elif damage == "swap":
        first, second = a.read_bytes(), b.read_bytes()
        a.write_bytes(second)
        b.write_bytes(first)
    elif damage == "path":
        store.update_capture(key, image_a_path="fairway.db")
    elif damage == "timing":
        store.update_capture(key, timing={"flash_interval_us": 1000, "fixture_case": "clean"})
    else:
        (a.parent / "manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(CaptureAssetError):
        load_synthetic_capture(store, key)


def test_matching_hash_does_not_make_unreadable_image_valid(setup):
    store, sid = setup
    record = create_synthetic_capture(store, sid)
    image = store.images_root / record["image_a_path"]
    manifest_file = image.parent / "manifest.json"
    image.write_bytes(b"broken PNG")
    manifest = json.loads(manifest_file.read_text())
    manifest["image_sha256"]["camera_a.png"] = hashlib.sha256(image.read_bytes()).hexdigest()
    manifest_file.write_text(json.dumps(manifest))
    with pytest.raises(CaptureAssetError, match="8-bit monochrome PNG"):
        load_synthetic_capture(store, record["capture_id"])


def test_loader_refuses_live_capture_and_cleanup_deleted_images(setup):
    store, sid = setup
    store.create_capture({"capture_id": "a" * 32, "source": "live", "status": "completed"})
    with pytest.raises(CaptureAssetError, match="Only simulation"):
        load_synthetic_capture(store, "a" * 32)
    record = create_synthetic_capture(store, sid)
    store.cleanup_images(keep_accepted=0, keep_other=0)
    with pytest.raises(CaptureAssetError, match="removed by cleanup"):
        load_synthetic_capture(store, record["capture_id"])


def test_real_calibration_cannot_be_substituted(setup):
    store, sid = setup
    record = create_synthetic_capture(store, sid)
    _, cameras, _ = make_stereo_fixture()
    real = store.save_calibration(*cameras, source="real")
    store.update_capture(record["capture_id"], calibration_id=real["calibration_id"])
    manifest_file = store.images_root / "captures" / record["capture_id"] / "manifest.json"
    manifest = json.loads(manifest_file.read_text())
    manifest["calibration_id"] = real["calibration_id"]
    manifest_file.write_text(json.dumps(manifest))
    with pytest.raises(CaptureAssetError, match="synthetic calibration"):
        load_synthetic_capture(store, record["capture_id"])
