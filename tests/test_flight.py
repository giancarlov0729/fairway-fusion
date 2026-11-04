import numpy as np
import pytest
from fairway_fusion.analysis.flight import FlightConditions, project_flight


def test_vacuum_matches_analytic_ballistics():
    velocity = np.array([40.0, 3.0, 20.0])
    conditions = FlightConditions(air_density_kg_m3=0, landing_speed_fraction=0)
    result = project_flight(velocity, [0, 0, 0], conditions, sensitivity=False)
    duration = 2 * velocity[2] / 9.80665
    assert result["flight_time_s"] == pytest.approx(duration, rel=1e-7)
    assert result["carry_m"] == pytest.approx(
        np.linalg.norm(velocity[:2]) * duration, rel=1e-7
    )
    assert result["apex_m"] == pytest.approx(velocity[2] ** 2 / (2 * 9.80665), rel=1e-5)
    assert result["total_m"] == pytest.approx(result["carry_m"])
    assert result["trajectory"][-1]["position_m"][2] == pytest.approx(0, abs=1e-7)


def test_wind_and_backspin_change_the_flight_in_expected_directions():
    v = [60, 0, 15]
    head = project_flight(
        v, [0, 0, 0], FlightConditions(wind_x_m_s=-5), sensitivity=False
    )
    tail = project_flight(
        v, [0, 0, 0], FlightConditions(wind_x_m_s=5), sensitivity=False
    )
    assert head["carry_m"] < tail["carry_m"]
    back = project_flight(v, [0, -300, 0], sensitivity=False)
    top = project_flight(v, [0, 300, 0], sensitivity=False)
    assert back["apex_m"] > top["apex_m"]


def test_side_spin_mirrors_lateral_flight():
    left = project_flight([55, 0, 17], [0, -400, 100], sensitivity=False)
    right = project_flight([55, 0, 17], [0, -400, -100], sensitivity=False)
    assert left["offline_m"] > 0
    assert left["offline_m"] == pytest.approx(-right["offline_m"], rel=1e-6)
    assert left["carry_m"] == pytest.approx(right["carry_m"], rel=1e-6)


@pytest.mark.parametrize(
    "velocity", [[float("nan"), 0, 10], [0, 0, 0], [50, 0, -1], [200, 0, 10]]
)
def test_invalid_launch_rejected(velocity):
    with pytest.raises(ValueError):
        project_flight(velocity, [0, 0, 0])


def test_sensitivity_is_bounded_and_labelled():
    result = project_flight([60, 0, 15], [0, -300, 0])
    low, high = result["carry_sensitivity_m"]
    assert low <= result["carry_m"] <= high
    assert "not a confidence interval" in result["sensitivity_note"]
