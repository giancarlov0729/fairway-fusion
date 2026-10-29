import numpy as np
import pytest

from fairway_fusion.analysis.velocity import estimate_velocity


def test_velocity_with_nonuniform_timing():
    times = np.array([0, 0.0008, 0.002])
    velocity = np.array([60, 2, 15])
    assert estimate_velocity(
        np.array([1, 2, 3]) + times[:, None] * velocity, times
    ) == pytest.approx(velocity)
