import numpy as np
import pytest

from fairway_fusion.analysis.velocity import estimate_velocity


def test_straight_shot():
    """Verify velocity estimation using simulated golf ball positions."""

    positions = np.array([
        [0.000000, 0.0, 0.021335],
        [0.048296, 0.0, 0.034276],
        [0.096593, 0.0, 0.047217],
        [0.144889, 0.0, 0.060158],
        [0.193185, 0.0, 0.073099],
        [0.241481, 0.0, 0.086040],
    ])

    times = np.array([0, 1, 2, 3, 4, 5]) / 1000.0

    velocity = estimate_velocity(positions, times)

    np.testing.assert_allclose(
        velocity,
        [48.296, 0.0, 12.941],
        atol=0.001
    )


def test_irregular_timestamps():
    """Verify regression works with nonuniform sampling intervals."""

    times = np.array([0.0, 0.001, 0.0025, 0.004, 0.006])

    expected_velocity = np.array([40.0, -2.0, 10.0])
    initial_position = np.array([0.0, 0.0, 0.02])

    positions = (
        initial_position
        + times[:, None] * expected_velocity
    )

    velocity = estimate_velocity(positions, times)

    np.testing.assert_allclose(
        velocity,
        expected_velocity,
        atol=1e-8
    )


def test_duplicate_timestamps():
    """Reject repeated timestamps."""

    positions = np.array([
        [0.0, 0.0, 0.0],
        [0.05, 0.0, 0.01],
        [0.10, 0.0, 0.02],
    ])

    times = np.array([0.0, 0.001, 0.001])

    with pytest.raises(ValueError):
        estimate_velocity(positions, times)


def test_insufficient_samples():
    """Reject trajectories containing fewer than two measurements."""

    positions = np.array([[0.0, 0.0, 0.0]])
    times = np.array([0.0])

    with pytest.raises(ValueError):
        estimate_velocity(positions, times)


def test_nonfinite_positions():
    """Reject measurements containing NaN or infinity."""

    positions = np.array([
        [0.0, 0.0, 0.0],
        [np.nan, 0.0, 0.01],
    ])

    times = np.array([0.0, 0.001])

    with pytest.raises(ValueError):
        estimate_velocity(positions, times)


def test_out_of_order_timestamps():
    """Reject timestamps that are not strictly increasing."""

    positions = np.array([
        [0.0, 0.0, 0.0],
        [0.05, 0.0, 0.01],
        [0.10, 0.0, 0.02],
    ])

    times = np.array([0.0, 0.002, 0.001])

    with pytest.raises(ValueError):
        estimate_velocity(positions, times)