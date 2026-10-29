import numpy as np
import pytest

from fairway_fusion.detection.ball_detector import detect_balls
from fairway_fusion.pipeline import analyze_shot
from fairway_fusion.synthetic import make_capture


@pytest.fixture(scope="module")
def capture():
    return make_capture(noise=1)


def test_complete_image_pipeline(capture):
    images, cameras, timing, _omega = capture
    result = analyze_shot(
        images, cameras, timing, {"flight_directions": [[1, 0], [1, 0]]}
    )
    assert result["status"] == "accepted", result
    assert result["spin"]["rpm"] == pytest.approx(3000, rel=0.05)
    assert result["spin"]["axis_world"] == pytest.approx([0, 0, 1], abs=0.05)
    assert result["launch"]["speed_m_s"] == pytest.approx(65, rel=0.03)


def test_missing_dots_preserves_launch(capture):
    images, cameras, timing, _ = capture
    # Replace ink by ball white but leave the background dark.
    cleaned = []
    for image in images:
        import cv2

        copy = image.copy()
        for ball in detect_balls(image):
            cv2.drawContours(copy, [ball.contour.astype(np.int32)], -1, 220, cv2.FILLED)
        cleaned.append(copy)
    result = analyze_shot(
        cleaned, cameras, timing, {"flight_directions": [[1, 0], [1, 0]]}
    )
    assert result["status"] == "partial"
    assert result["spin"] is None
    assert result["launch"] is not None


def test_unverified_timing_rejected(capture):
    images, cameras, timing, _ = capture
    result = analyze_shot(images, cameras, {**timing, "verified": False}, {})
    assert result["status"] == "rejected"


def test_overlapping_pulses_rejected():
    images, cameras, timing, _ = make_capture(spacing_us=250)
    result = analyze_shot(
        images, cameras, timing, {"flight_directions": [[1, 0], [1, 0]]}
    )
    assert result["status"] == "rejected"


def test_wrong_stereo_order_rejected(capture):
    images, cameras, timing, _ = capture
    result = analyze_shot(
        images, cameras, timing, {"flight_directions": [[1, 0], [-1, 0]]}
    )
    assert result["status"] == "rejected"
