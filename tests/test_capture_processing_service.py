import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

import pytest

from fairway_fusion.services.capture_processing_service import (
    CaptureProcessingError, PROCESSING_VERSION, process_saved_capture,
)
from fairway_fusion.services.synthetic_capture_service import create_synthetic_capture
from fairway_fusion.storage import SessionStateError, ShotStore


@pytest.fixture
def setup(tmp_path):
    store = ShotStore(tmp_path / "shots.db")
    sid = store.start_session()["id"]
    return store, sid


@pytest.mark.parametrize("case", ["clean", "leftward", "blur", "uneven-light"])
def test_accepted_shot_and_capture_are_persisted(setup, case):
    store, sid = setup
    capture = create_synthetic_capture(store, sid, case=case)
    shot = process_saved_capture(store, capture["capture_id"])
    assert shot["status"] == "accepted"
    assert shot["session_id"] == sid
    assert shot["source"] == "simulation"
    assert shot["capture_id"] == capture["capture_id"]
    assert shot["speed_m_s"] == pytest.approx(62 if case != "leftward" else 62.1, abs=.7)
    assert shot["carry"]["estimated_carry_m"] > 0
    assert len(shot["samples"]["positions_m"]) == 2
    assert shot["samples"]["timestamps_s"] == [0, .002]
    restarted = ShotStore(store.path)
    assert restarted.get_shot(shot["id"]) == shot
    record = restarted.get_capture(capture["capture_id"])
    assert record["status"] == "completed"
    assert record["error"] is None
    assert record["processing_version"] == PROCESSING_VERSION
    assert record["detections"]["vision_status"] == "accepted"
    assert len(record["detections"]["matched_centers_px"]) == 2
    assert len(record["detections"]["reprojection_errors_px"]) == 2
    assert restarted.image_files(capture["capture_id"])["state"] == "present"


@pytest.mark.parametrize("case,reason", [("clutter", "expected two isolated ball images"),
                                        ("shallow", "Ambiguous stereo match")])
def test_normal_rejection_is_a_saved_shot_not_a_processing_failure(setup, case, reason):
    store, sid = setup
    key = create_synthetic_capture(store, sid, case=case)["capture_id"]
    shot = process_saved_capture(store, key)
    assert shot["status"] == "rejected"
    assert reason in shot["reason"]
    assert shot["speed_m_s"] is shot["launch_angle_deg"] is shot["carry"] is None
    assert shot["samples"] is None
    record = store.get_capture(key)
    assert record["status"] == "completed"
    assert record["error"] is None
    assert record["detections"]["vision_reason"] == shot["reason"]
    assert record["detections"]["detected_centers_px"][0]


@pytest.mark.parametrize("case", ["clean", "shallow"])
def test_repeated_processing_returns_same_shot_even_after_session_ends(setup, case):
    store, sid = setup
    key = create_synthetic_capture(store, sid, case=case)["capture_id"]
    first = process_saved_capture(store, key)
    store.end_session(sid)
    with patch("fairway_fusion.services.capture_processing_service.analyze_image_pair") as analyze:
        assert process_saved_capture(ShotStore(store.path), key) == first
    analyze.assert_not_called()
    assert len(store.list_shots()) == 1


def test_carry_error_does_not_reject_valid_launch(setup):
    store, sid = setup
    key = create_synthetic_capture(store, sid, flash_interval_us=1000)["capture_id"]
    shot = process_saved_capture(store, key)
    assert shot["status"] == "accepted"
    assert shot["speed_m_s"] > 100
    assert shot["carry"]["estimated_carry_m"] is None
    assert shot["carry"]["carry_error"]


def test_missing_images_fail_without_fabricating_rejected_shot_then_can_retry(setup):
    store, sid = setup
    record = create_synthetic_capture(store, sid)
    image = store.images_root / record["image_b_path"]
    original = image.read_bytes()
    image.unlink()
    with pytest.raises(CaptureProcessingError) as error:
        process_saved_capture(store, record["capture_id"])
    assert error.value.capture_id == record["capture_id"]
    failed = store.get_capture(record["capture_id"])
    assert failed["status"] == "failed"
    assert "CaptureAssetError" in failed["error"]
    assert failed["session_id"] == sid
    assert store.list_shots() == []
    image.write_bytes(original)
    assert process_saved_capture(store, record["capture_id"])["status"] == "accepted"
    assert store.get_capture(record["capture_id"])["error"] is None


def test_unexpected_vision_exception_records_failure_and_preserves_inputs(setup):
    store, sid = setup
    key = create_synthetic_capture(store, sid)["capture_id"]
    with patch("fairway_fusion.services.capture_processing_service.analyze_image_pair",
               side_effect=RuntimeError("Vision implementation failed")):
        with pytest.raises(CaptureProcessingError):
            process_saved_capture(store, key)
    assert store.get_capture(key)["status"] == "failed"
    assert "RuntimeError: Vision implementation failed" in store.get_capture(key)["error"]
    assert store.list_shots() == []
    assert store.image_files(key)["state"] == "present"


def test_finalization_failure_rolls_back_shot_insert(setup):
    store, sid = setup
    key = create_synthetic_capture(store, sid)["capture_id"]
    with sqlite3.connect(store.path) as conn:
        conn.execute("""CREATE TRIGGER fail_completion BEFORE UPDATE OF status ON captures
                        WHEN NEW.status = 'completed'
                        BEGIN SELECT RAISE(ABORT, 'completion write failed'); END""")
    with pytest.raises(CaptureProcessingError):
        process_saved_capture(store, key)
    assert store.list_shots() == []
    failed = store.get_capture(key)
    assert failed["status"] == "failed"
    assert failed["detections"]["vision_status"] == "accepted"
    with sqlite3.connect(store.path) as conn:
        conn.execute("DROP TRIGGER fail_completion")
    assert process_saved_capture(store, key)["status"] == "accepted"
    assert len(store.list_shots()) == 1


def test_concurrent_requests_cannot_process_twice_or_end_session(setup):
    store, sid = setup
    key = create_synthetic_capture(store, sid)["capture_id"]
    entered, release = Event(), Event()
    from fairway_fusion.services.vision_service import analyze_image_pair

    def waiting_analysis(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return analyze_image_pair(*args, **kwargs)

    with patch("fairway_fusion.services.capture_processing_service.analyze_image_pair", waiting_analysis):
        with ThreadPoolExecutor(max_workers=1) as executor:
            work = executor.submit(process_saved_capture, store, key)
            try:
                assert entered.wait(10)
                assert store.get_capture(key)["status"] == "processing"
                assert store.cleanup_images(0, 0)["skipped"] == "A capture is in progress"
                with pytest.raises(SessionStateError):
                    process_saved_capture(ShotStore(store.path), key)
                with pytest.raises(SessionStateError):
                    store.end_session(sid)
            finally:
                release.set()
            shot = work.result(timeout=10)
    assert process_saved_capture(store, key) == shot
    assert len(store.list_shots()) == 1


def test_startup_recovery_allows_interrupted_processing_retry(setup):
    store, sid = setup
    key = create_synthetic_capture(store, sid)["capture_id"]
    assert store.claim_capture_processing(key, source="simulation", processing_version=PROCESSING_VERSION) is None
    assert store.mark_interrupted_captures() == 1
    assert process_saved_capture(ShotStore(store.path), key)["status"] == "accepted"


def test_closed_session_or_live_capture_is_not_modified(setup):
    store, sid = setup
    record = create_synthetic_capture(store, sid)
    store.end_session(sid)
    with pytest.raises(SessionStateError):
        process_saved_capture(store, record["capture_id"])
    assert store.get_capture(record["capture_id"]) == record
    live = store.create_capture({"capture_id": "a" * 32, "status": "completed", "source": "live"})
    with pytest.raises(SessionStateError):
        process_saved_capture(store, live["capture_id"])
    assert store.get_capture(live["capture_id"]) == live
    assert store.list_shots() == []


def test_changed_input_metadata_cannot_receive_stale_measurements(setup):
    store, sid = setup
    key = create_synthetic_capture(store, sid)["capture_id"]
    from fairway_fusion.services.vision_service import analyze_image_pair

    def change_timing(*args, **kwargs):
        result = analyze_image_pair(*args, **kwargs)
        store.update_capture(key, timing={"flash_interval_us": 1000, "fixture_case": "clean"})
        return result

    with patch("fairway_fusion.services.capture_processing_service.analyze_image_pair", change_timing):
        with pytest.raises(CaptureProcessingError, match="inputs changed"):
            process_saved_capture(store, key)
    assert store.list_shots() == []
    assert store.get_capture(key)["status"] == "failed"
