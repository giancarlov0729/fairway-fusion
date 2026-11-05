"""Estimate early-flight 3D velocity from calibrated ball-center measurements.

This module accepts SI-unit arrays, not raw camera pixels or simulator mm/ms
packets. It uses an ordinary least-squares constant-velocity approximation.
"""

import numpy as np


def estimate_velocity(positions, times_s):
    """
    Estimate the 3D velocity vector using least-squares regression.

    Parameters
    ----------
    positions : array-like
        Ball positions with shape (N, 3), measured in meters.
        Coordinate system: +X downrange, +Y left, +Z upward.

    times_s : array-like
        Timestamps with shape (N,), measured in seconds.

    Returns
    -------
    numpy.ndarray
        Estimated velocity [vx, vy, vz] in meters per second.

    Raises
    ------
    ValueError
        If the input data is invalid.

    Notes
    -----
    The slope is fitted independently for X, Y, and Z using all samples.
    This estimates motion over the observation window; it does not correct
    for gravity, reject outliers, or quantify measurement uncertainty.
    """

    try:
        positions = np.asarray(positions, dtype=float)
        times = np.asarray(times_s, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("Positions and timestamps must be numeric") from exc

    # Validate array dimensions
    if positions.ndim != 2 or positions.shape[1] != 3:
        raise ValueError("Positions must have shape (N, 3)")

    if times.ndim != 1:
        raise ValueError("Timestamps must be a one-dimensional array")

    if len(positions) != len(times):
        raise ValueError("Each position must have a corresponding timestamp")

    if len(positions) < 2:
        raise ValueError("At least two measurements are required")

    # Validate measurement values
    if not np.isfinite(positions).all():
        raise ValueError("Positions must contain finite values")

    if not np.isfinite(times).all():
        raise ValueError("Timestamps must contain finite values")

    # Ensure timestamps are strictly increasing
    if np.any(np.diff(times) <= 0):
        raise ValueError("Timestamps must be strictly increasing")

    # Center each axis and time so regression does not depend on the origin.
    # In particular, the slope is unaffected by a constant position offset.
    centered_times = times - np.mean(times)
    centered_positions = positions - np.mean(positions, axis=0)

    # Ordinary least-squares slope: covariance(time, position) / variance(time).
    # Broadcasting centered_times[:, None] fits all three axes at once.
    denominator = np.sum(centered_times ** 2)

    if denominator <= 0:
        raise ValueError("Timestamp variation is insufficient")

    velocity = np.sum(
        centered_times[:, None] * centered_positions,
        axis=0
    ) / denominator

    if not np.isfinite(velocity).all():
        raise ValueError("Unable to estimate a finite velocity")

    return velocity