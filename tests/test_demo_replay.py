import copy
import numpy as np
import pytest
from fairway_fusion.demo.fixtures import fixtures
from fairway_fusion.demo.replay import process_observations


@pytest.mark.parametrize("name", ["driver", "iron", "wedge"])
def test_recover_independent_synthetic_truth(name):
    packet, truth = fixtures()[name]
    packet["expected_metrics"] = {"spin_rpm": 999999, "carry_m": 999999}
    result = process_observations(packet)
    assert result["status"] == "accepted", result
    assert result["launch"]["speed_m_s"] == pytest.approx(truth["speed_m_s"], abs=0.05)
    assert result["launch"]["launch_angle_deg"] == pytest.approx(
        truth["launch_angle_deg"], abs=0.1
    )
    assert result["spin"]["rpm"] == pytest.approx(truth["spin_rpm"], abs=5)
    assert result["flight"]["carry_m"] > 0
    assert result["flight"]["total_m"] >= result["flight"]["carry_m"]


def test_mismatched_cameras_release_no_metrics():
    result = process_observations(fixtures()["rejected"][0])
    assert result["status"] == "rejected"
    assert all(result[k] is None for k in ("launch", "spin", "flight"))
    assert "3 mm" in result["reasons"][0]


@pytest.mark.parametrize(
    "failure",
    [
        "unverified",
        "alias",
        "missing_dots",
        "symmetric",
        "nan",
        "reflection",
        "out_of_order",
    ],
)
def test_bad_observations_are_rejected(failure):
    packet = copy.deepcopy(fixtures()["driver"][0])
    if failure == "unverified":
        packet["timing"]["verified"] = False
    if failure == "alias":
        for i, o in enumerate(packet["observations"]):
            o["timestamp_us"] = i * 16667
    if failure == "missing_dots":
        packet["observations"][0]["views"][0]["matched_dots"] = 3
    if failure == "symmetric":
        packet["observations"][0]["views"][0]["pattern_unique"] = False
    if failure == "out_of_order":
        packet["observations"][1]["timestamp_us"] = 0
    if failure == "nan":
        packet["observations"][0]["views"][0]["center_camera_m"][0] = float("nan")
    if failure == "reflection":
        packet["cameras"][0]["R"] = np.diag([1, 1, -1]).tolist()
    if failure in ("nan", "reflection"):
        with pytest.raises(ValueError):
            process_observations(packet)
    else:
        result = process_observations(packet)
        assert result["status"] == "rejected"
        assert result["flight"] is None
