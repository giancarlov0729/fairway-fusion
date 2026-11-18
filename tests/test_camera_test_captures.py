"""Tests that camera-test captures stay separate from shot measurement.

A camera test only proves both cameras took images. It is never processed
into speed and angles, so it can never have a shot, and history shows it as
"images captured" rather than as a failed shot.

These live in their own file, separate from test_storage.py, so they do not
collide with other branches that also edit test_storage.py.

Run from the repository root:

    python -m pytest tests/test_camera_test_captures.py
"""

import pytest

from fairway_fusion.storage import SessionStateError, ShotStore

CAPTURE_ID = "0" * 31 + "1"  # a valid 32-character lowercase hex ID


@pytest.fixture
def store(tmp_path):
    """A fresh database in a temporary folder, with an open session."""
    store = ShotStore(tmp_path / "fairway.db")
    store.start_session()
    return store


def _camera_test(store, **changes):
    """Save a finished camera-test capture, as Ethan's impact recording does."""
    session = store.active_session()
    return store.create_capture({
        "capture_id": CAPTURE_ID, "status": "completed", "source": "live",
        "purpose": "camera_test", "session_id": session["id"], **changes,
    })


def test_camera_test_cannot_get_a_shot(store):
    """Saving a shot for a camera test is refused, and the capture stays
    exactly as it was."""
    _camera_test(store)
    with pytest.raises(ValueError, match="Camera-test captures cannot produce shots"):
        store.save_shot(50.0, 15.0, capture_id=CAPTURE_ID, source="live")
    assert store.latest_shot() is None
    assert store.get_capture(CAPTURE_ID)["status"] == "completed"


def test_camera_test_is_never_claimed_for_processing(store):
    """Shot processing refuses a camera test before changing anything, so it
    is never moved to 'processing' and never shows up as a failed shot."""
    _camera_test(store)
    with pytest.raises(SessionStateError, match="Camera-test"):
        store.claim_capture_processing(CAPTURE_ID, source="live",
                                       processing_version="v1")
    assert store.get_capture(CAPTURE_ID)["status"] == "completed"


def test_camera_test_images_are_kept_and_reported(store):
    """A camera test with real image files reports them as present and has no
    shot, which is how history shows 'images captured' without metrics."""
    folder = f"captures/{CAPTURE_ID}"
    for name in ("camera_a.png", "camera_b.png"):
        path = store.images_root / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake png")
    _camera_test(store, image_a_path=f"{folder}/camera_a.png",
                 image_b_path=f"{folder}/camera_b.png",
                 camera_metadata={"camera_a": {"connector": "CAM0", "width": 640},
                                  "camera_b": {"connector": "CAM1", "width": 640}})

    assert store.image_files(CAPTURE_ID)["state"] == "present"
    assert store.get_capture_shot(CAPTURE_ID) is None
    assert store.get_capture(CAPTURE_ID)["camera_metadata"]["camera_b"]["connector"] == "CAM1"