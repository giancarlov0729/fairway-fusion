"""Estimate a golf ball's 3D velocity from timestamped positions."""

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

    # Center timestamps and positions for numerical stability
    centered_times = times - np.mean(times)
    centered_positions = positions - np.mean(positions, axis=0)

    # Calculate velocity using least-squares regression
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