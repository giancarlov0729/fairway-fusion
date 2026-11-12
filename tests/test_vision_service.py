import json
from dataclasses import replace
from unittest.mock import patch

import numpy as np
import pytest

from fairway_fusion.analysis.carry import Assumptions
from fairway_fusion.services.vision_service import analyze_image_pair
from fairway_fusion.synthetic import make_stereo_fixture


def analyze(images, cameras, interval=0.002, **options):
    return analyze_image_pair(*images, *cameras, interval, source="simulation", **options)


@pytest.mark.parametrize("case", ["clean", "leftward", "blur", "uneven-light"])
def test_known_images_recover_metrics_and_carry(case):
    images, cameras, truth = make_stereo_fixture(case=case)
    result = analyze(images, cameras)
    assert result["status"] == "accepted"
    assert result["reason"] is None
    assert np.array(result["samples"]["positions_m"]) == pytest.approx(truth, abs=0.002)
    velocity = (truth[1] - truth[0]) / 0.002
    assert result["launch"]["speed_m_s"] == pytest.approx(np.linalg.norm(velocity), abs=0.6)
    assert result["launch"]["launch_angle_deg"] == pytest.approx(
        np.degrees(np.arctan2(velocity[2], np.linalg.norm(velocity[:2]))), abs=0.5)
    assert result["launch"]["launch_direction_deg"] == pytest.approx(
        np.degrees(np.arctan2(velocity[1], velocity[0])), abs=0.5)
    assert result["carry"]["estimated_carry_yd"] > 0
    assert result["carry"]["carry_is_estimate"] is True
    assert result["source"] == "simulation"
    assert result["timing"] == {"flash_interval_s": 0.002, "timing_source": "commanded"}
    assert len(result["diagnostics"]["matched_centers_px"]) == 2
    assert len(result["reprojection_errors_px"]) == 2
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("case,reason", [("shallow", "Ambiguous stereo match"),
                                        ("clutter", "expected two isolated ball images")])
def test_rejected_vision_retains_diagnostics_without_fabricating_metrics(case, reason):
    images, cameras, _ = make_stereo_fixture(case=case)
    with patch("fairway_fusion.services.vision_service.carry_metrics") as carry:
        result = analyze(images, cameras)
    assert result["status"] == "rejected"
    assert reason in result["reason"]
    assert result["samples"] is result["launch"] is result["carry"] is None
    assert result["diagnostics"]["detected_centers_px"][0]
    assert result["diagnostics"]["matched_centers_px"] == []
    carry.assert_not_called()
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("value", [None, True, "0.002", [], 0, -1, float("nan"), float("inf"), 10**400])
def test_bad_timing_rejects_without_running_pipeline(value):
    images, cameras, _ = make_stereo_fixture()
    with patch("fairway_fusion.services.vision_service.analyze_stereo_shot") as pipeline:
        result = analyze(images, cameras, value)
    pipeline.assert_not_called()
    assert result["status"] == "rejected"
    assert result["reason"] == "Invalid optical flash interval"
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("image", [None, "camera.png", [[1, 2]], np.zeros((480, 640, 3), np.uint8),
                                   np.zeros((480, 640), np.uint16), np.zeros((10, 10), np.uint8)])
@pytest.mark.parametrize("index", [0, 1])
def test_missing_or_invalid_images_are_rejected(image, index):
    images, cameras, _ = make_stereo_fixture()
    images[index] = image
    result = analyze(images, cameras)
    assert result["status"] == "rejected"
    assert f"{ 'A' if index == 0 else 'B'}" in result["reason"]
    assert result["launch"] is result["carry"] is None


@pytest.mark.parametrize("changes", [
    {"K": np.eye(2)}, {"K": np.diag([-1, 1, 1])},
    {"K": np.full((3, 3), np.nan)}, {"R": np.zeros((3, 3))},
    {"R": np.diag([1, 1, -1])}, {"t": [1, 2]},
    {"t": [0, float("inf"), 0]}, {"distortion": np.zeros((2, 2))},
    {"distortion": np.zeros(6)}, {"image_size": (640, 0)},
    {"image_size": (640.5, 480)}, {"image_size": None},
])
def test_invalid_calibration_is_rejected(changes):
    images, cameras, _ = make_stereo_fixture()
    cameras[1] = replace(cameras[1], **changes)
    result = analyze(images, cameras)
    assert result["status"] == "rejected"
    assert "Camera B" in result["reason"]
    assert result["launch"] is None


def test_interval_is_used_without_silently_substituting_pico_defaults():
    images, cameras, _ = make_stereo_fixture()
    slow = analyze(images, cameras, 0.002)
    fast = analyze(images, cameras, 0.001)
    assert fast["launch"]["speed_m_s"] == pytest.approx(2 * slow["launch"]["speed_m_s"])
    assert fast["samples"]["timestamps_s"] == [0, 0.001]
    assert fast["status"] == "accepted"
    assert fast["carry"]["estimated_carry_m"] is None
    assert fast["carry"]["carry_error"]


def test_accepted_downward_launch_does_not_require_a_carry_estimate():
    images, cameras, _ = make_stereo_fixture([[0, 0, .15], [.12, -.008, .12]])
    result = analyze(images, cameras)
    assert result["status"] == "accepted"
    assert result["launch"]["launch_angle_deg"] < 0
    assert result["carry"]["estimated_carry_m"] is None
    assert result["carry"]["carry_error"]


def test_assumptions_are_preserved_and_inputs_are_not_modified():
    images, cameras, _ = make_stereo_fixture()
    originals = [image.copy() for image in images]
    matrices = [camera.R.copy() for camera in cameras]
    result = analyze(images, cameras, carry_assumptions=Assumptions(backspin_rpm=3500),
                     timing_source="measured")
    assert result["carry"]["carry_assumptions"]["backspin_rpm"] == 3500
    assert result["timing"]["timing_source"] == "measured"
    for original, image, matrix, camera in zip(originals, images, matrices, cameras):
        assert np.array_equal(original, image)
        assert np.array_equal(matrix, camera.R)


def test_unexpected_pipeline_failure_propagates():
    images, cameras, _ = make_stereo_fixture()
    with patch("fairway_fusion.services.vision_service.analyze_stereo_shot",
               side_effect=RuntimeError("Unexpected processing failure")):
        with pytest.raises(RuntimeError, match="Unexpected processing failure"):
            analyze(images, cameras)


def test_invalid_provenance_or_settings_fail_explicitly():
    images, cameras, _ = make_stereo_fixture()
    with pytest.raises(ValueError, match="source"):
        analyze_image_pair(*images, *cameras, .002, source="unknown")
    with pytest.raises(ValueError, match="timing_source"):
        analyze(images, cameras, timing_source="unverified")
    with pytest.raises(TypeError, match="carry_assumptions"):
        analyze(images, cameras, carry_assumptions={})


def test_camera_vector_shapes_are_normalized_for_opencv():
    images, cameras, _ = make_stereo_fixture()
    cameras[0] = replace(cameras[0], t=cameras[0].t.reshape(1, 3),
                         distortion=cameras[0].distortion.reshape(5, 1))
    assert analyze(images, cameras)["status"] == "accepted"
