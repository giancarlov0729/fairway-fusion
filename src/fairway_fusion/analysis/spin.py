"""Signed angular velocity in the calibrated right-handed world frame."""

import numpy as np
from scipy.spatial.transform import Rotation

from fairway_fusion.errors import MeasurementRejected


def angular_velocity(matrix, dt_s, max_rpm=10000, timing_error_s=0):
    if (
        not np.isfinite([dt_s, max_rpm, timing_error_s]).all()
        or dt_s <= 0
        or max_rpm <= 0
        or timing_error_s < 0
    ):
        raise MeasurementRejected("Invalid pulse timing or spin limit")
    if 2 * timing_error_s >= dt_s * 0.1:
        raise MeasurementRejected("Pulse timing uncertainty is too large")
    # Use the worst-case interval and a margin from the pi rotation branch cut.
    if (dt_s + 2 * timing_error_s) * max_rpm * 2 * np.pi / 60 >= 0.9 * np.pi:
        raise MeasurementRejected(
            "Spin aliasing: pulse spacing is too long for max_rpm"
        )
    matrix = np.asarray(matrix, float)
    if (
        matrix.shape != (3, 3)
        or not np.isfinite(matrix).all()
        or not np.allclose(matrix @ matrix.T, np.eye(3), atol=1e-6)
        or not np.isclose(np.linalg.det(matrix), 1)
    ):
        raise ValueError("Expected a proper rotation matrix")
    omega = Rotation.from_matrix(matrix).as_rotvec() / dt_s
    if np.linalg.norm(omega) * 60 / (2 * np.pi) > max_rpm:
        raise MeasurementRejected("Measured spin exceeds configured range")
    return omega


def spin_metrics(omega):
    omega = np.asarray(omega, float)
    magnitude = float(np.linalg.norm(omega))
    return {
        "rpm": magnitude * 60 / (2 * np.pi),
        "angular_velocity_rad_s": omega.tolist(),
        "axis_world": (omega / magnitude).tolist() if magnitude > 1e-6 else None,
    }
