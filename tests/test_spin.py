import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from fairway_fusion.analysis.spin import angular_velocity, spin_metrics
from fairway_fusion.errors import MeasurementRejected
from fairway_fusion.tracking.marker_tracker import match_rotation


def dots():
    points = np.array(
        [
            [-0.45, -0.3, -1],
            [0.2, -0.4, -1],
            [0.5, 0.13, -1],
            [-0.2, 0.4, -1],
            [0.1, 0.1, -1],
            [0.05, 0.6, -1],
        ]
    )
    return points / np.linalg.norm(points, axis=1, keepdims=True)


def test_rotation_with_occlusion_shuffle_and_outlier():
    points = dots()
    expected = Rotation.from_rotvec([0.13, -0.22, 0.31])
    target = expected.apply(points)[[4, 0, 3, 1, 5]]
    target = np.vstack((target, [1, 0, 0]))
    match = match_rotation(points, target)
    assert match.inliers == 5
    assert (
        Rotation.from_matrix(match.matrix @ expected.as_matrix().T).magnitude() < 1e-7
    )


@pytest.mark.parametrize("rpm", [500, 3000, 10000])
def test_known_spin(rpm):
    dt = 0.001
    matrix = Rotation.from_rotvec(
        np.array([0, 0, 1]) * rpm * 2 * np.pi / 60 * dt
    ).as_matrix()
    assert spin_metrics(angular_velocity(matrix, dt, max_rpm=11000))[
        "rpm"
    ] == pytest.approx(rpm)


def test_sixty_fps_rejected():
    with pytest.raises(MeasurementRejected, match="aliasing"):
        angular_velocity(np.eye(3), 1 / 60)


def test_symmetric_pattern_rejected():
    theta = np.arange(4) * np.pi / 2
    points = np.column_stack(
        (0.5 * np.cos(theta), 0.5 * np.sin(theta), np.full(4, -np.sqrt(0.75)))
    )
    with pytest.raises(MeasurementRejected, match="Ambiguous"):
        match_rotation(points, points)


def test_no_axis_at_zero_spin():
    assert spin_metrics(np.zeros(3))["axis_world"] is None


def test_insufficient_dots():
    with pytest.raises(MeasurementRejected, match="Too few"):
        match_rotation(dots()[:3], dots())


def test_noisy_tilted_spin():
    points = dots()
    axis = np.array([0.3, -0.4, 0.5])
    axis /= np.linalg.norm(axis)
    expected = Rotation.from_rotvec(axis * 0.5)
    target = expected.apply(points)
    target += np.random.default_rng(3).normal(0, 0.002, target.shape)
    target /= np.linalg.norm(target, axis=1, keepdims=True)
    actual = match_rotation(points, target).matrix
    assert Rotation.from_matrix(actual @ expected.as_matrix().T).magnitude() < 0.015
