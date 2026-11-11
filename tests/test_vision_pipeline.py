import numpy as np
import pytest

from fairway_fusion.pipeline import analyze_stereo_shot
from fairway_fusion.synthetic import make_stereo_fixture


def test_recovers_launch_from_synthetic_images():
    images, cameras, truth = make_stereo_fixture()
    result = analyze_stereo_shot(*images, *cameras, flash_interval_s=0.002)

    assert result["status"] == "accepted"
    assert np.asarray(result["positions_m"]) == pytest.approx(truth, abs=0.002)
    assert result["launch"]["speed_m_s"] == pytest.approx(61.98, abs=0.6)
    assert result["launch"]["launch_angle_deg"] == pytest.approx(14.0, abs=0.5)
    assert result["launch"]["launch_direction_deg"] == pytest.approx(-3.8, abs=0.5)


def test_rejects_missing_image_and_invalid_timing():
    images, cameras, _ = make_stereo_fixture()
    assert analyze_stereo_shot(images[0], None, *cameras, 0.002)["reason"] == (
        "Missing camera B image"
    )
    assert analyze_stereo_shot(*images, *cameras, 0)["reason"] == (
        "Invalid optical flash interval"
    )


def test_rejects_merged_silhouettes():
    images, cameras, _ = make_stereo_fixture([[0, 0, 0.10], [0.025, 0, 0.10]])
    result = analyze_stereo_shot(*images, *cameras, 0.002)
    assert result["status"] == "rejected"
    assert "expected two isolated ball images" in result["reason"]
    assert "launch" not in result


def test_rejects_two_plausible_stereo_matches():
    images, cameras, _ = make_stereo_fixture([[0, 0, 0.15], [0.10, 0, 0.15]])
    result = analyze_stereo_shot(*images, *cameras, 0.002)
    assert result == {"status": "rejected", "reason": "Ambiguous stereo match"}


@pytest.mark.parametrize("case", ["leftward", "blur", "uneven-light"])
def test_recovers_launch_from_harder_synthetic_images(case):
    images, cameras, truth = make_stereo_fixture(case=case)
    result = analyze_stereo_shot(*images, *cameras, 0.002)

    assert result["status"] == "accepted"
    assert np.asarray(result["positions_m"]) == pytest.approx(truth, abs=0.002)
    assert np.sign(result["launch"]["launch_direction_deg"]) == np.sign(truth[1, 1])


@pytest.mark.parametrize("case, reason, count", [
    ("shallow", "Ambiguous stereo match", 2),
    ("clutter", "expected two isolated ball images", 3),
])
def test_rejected_cases_expose_detections_without_matches(case, reason, count):
    images, cameras, _ = make_stereo_fixture(case=case)
    diagnostics = {}
    result = analyze_stereo_shot(*images, *cameras, 0.002, diagnostics=diagnostics)

    assert result["status"] == "rejected"
    assert reason in result["reason"]
    assert len(diagnostics["detected_centers_px"][0]) == count
    assert diagnostics["matched_centers_px"] == []


def test_diagnostics_record_accepted_matches_in_flash_order():
    images, cameras, truth = make_stereo_fixture()
    diagnostics = {}
    result = analyze_stereo_shot(*images, *cameras, 0.002, diagnostics=diagnostics)

    assert result["status"] == "accepted"
    for view, camera in enumerate(cameras):
        matched = [pair[view] for pair in diagnostics["matched_centers_px"]]
        assert np.asarray(matched) == pytest.approx(camera.project(truth), abs=0.5)
