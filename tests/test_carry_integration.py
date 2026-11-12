from dataclasses import replace
from unittest.mock import patch

import pytest

from fairway_fusion.analysis.carry import Assumptions, carry_metrics, estimate_carry
from fairway_fusion.web.app import create_app


@pytest.mark.parametrize("field", ["air_density", "ball_mass", "ball_diameter",
    "time_step_s", "drag_coeff", "backspin_rpm", "max_lift_coeff", "launch_height_m"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), True])
def test_invalid_assumptions_are_rejected(field, value):
    with pytest.raises((ValueError, TypeError)):
        estimate_carry(50, 15, replace(Assumptions(), **{field: value}))


@pytest.mark.parametrize("step", [1e-12, 1.0])
def test_step_size_is_bounded(step):
    with pytest.raises(ValueError):
        estimate_carry(50, 15, Assumptions(time_step_s=step))


def test_integration_failure_preserves_shot_response():
    with patch("fairway_fusion.analysis.carry._numeric", side_effect=RuntimeError("did not land")):
        result = carry_metrics({"speed_m_s": 50, "launch_angle_deg": 15})
    assert result["estimated_carry_m"] is None
    assert result["carry_error"] == "did not land"


def test_preview_includes_estimate_and_assumptions(tmp_path):
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "test.db")})
    response = app.test_client().post("/api/shots/preview", json={"scenario": "straight"})
    assert response.status_code == 200
    carry = response.get_json()["carry"]
    assert carry["estimated_carry_yd"] > 0
    assert carry["carry_is_estimate"] is True
    assert carry["carry_assumptions"]["backspin_rpm"] == 2500
    assert carry["carry_error"] is None
