import numpy as np
import pytest

from fairway_fusion.analysis.launch_angle import (
    calculate_launch_angle,
    estimate_launch_angle,
)


@pytest.mark.parametrize(
    "velocity, expected",
    [((1, 0, 1), 45), ((3, 4, 5), 45), ((10, 0, 0), 0),
     ((1, 0, -1), -45), ((0, 0, 1), 90)],
)
def test_known_angles(velocity, expected):
    assert calculate_launch_angle(velocity) == pytest.approx(expected)


def test_fit_with_uneven_timing_and_translated_origin():
    times = np.array([0, 0.0005, 0.0013, 0.002])
    positions = np.array([2, -1, 0.05]) + times[:, None] * [30, 40, 50]
    assert estimate_launch_angle(positions, times) == pytest.approx(45)


def test_two_points():
    assert estimate_launch_angle([[0, 0, 0], [0.1, 0, 0.1]], [0, 0.001]) == pytest.approx(45)


def test_small_measurement_noise():
    times = np.arange(6) * 0.0005
    positions = times[:, None] * np.array([40, 0, 40 * np.tan(np.radians(15))])
    positions[:, 2] += [0, 0.0001, -0.0001, 0.0001, -0.0001, 0]
    assert estimate_launch_angle(positions, times) == pytest.approx(15, abs=0.1)


@pytest.mark.parametrize("velocity", [(0, 0, 0), (1, 2), (1, 0, float('nan'))])
def test_invalid_velocity(velocity):
    with pytest.raises(ValueError):
        calculate_launch_angle(velocity)


@pytest.mark.parametrize(
    "positions, times",
    [([[0, 0, 0]], [0]),
     ([[0, 0], [1, 1]], [0, 1]),
     ([[0, 0, 0], [1, 0, 1]], [0]),
     ([[0, 0, 0], [1, 0, 1]], [0, 0]),
     ([[0, 0, 0], [1, 0, 1]], [1, 0]),
     ([[0, 0, 0], [1, 0, float('nan')]], [0, 1]),
     ([[0, 0, 0], [1, 0, 1]], [0, float('inf')]),
     ([[1, 2, 3], [1, 2, 3]], [0, 1])],
)
def test_invalid_trajectory(positions, times):
    with pytest.raises(ValueError):
        estimate_launch_angle(positions, times)

from fairway_fusion.analysis.launch_angle import (
    calculate_ball_speed,
    calculate_launch_direction,
    launch_metrics,
)


def test_ball_speed():
    velocity = [30.0, 0.0, 40.0]
    assert np.isclose(calculate_ball_speed(velocity), 50.0)


def test_launch_direction():
    velocity = [40.0, -10.0, 15.0]

    expected = np.degrees(np.arctan2(-10.0, 40.0))

    assert np.isclose(
        calculate_launch_direction(velocity),
        expected
    )


def test_launch_metrics():
    velocity = [48.296, 0.0, 12.941]

    metrics = launch_metrics(velocity)

    assert np.isclose(metrics["speed_m_s"], 50.0, atol=0.01)
    assert np.isclose(metrics["speed_mph"], 111.85, atol=0.02)
    assert np.isclose(metrics["launch_angle_deg"], 15.0, atol=0.01)
    assert np.isclose(metrics["launch_direction_deg"], 0.0)