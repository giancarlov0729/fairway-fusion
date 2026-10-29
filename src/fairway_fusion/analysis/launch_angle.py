"""Basic vertical launch angle from a calibrated, early-flight 3D trajectory.

Coordinates: x is downrange, y is sideways, and z is up. Inputs must be in
world coordinates with consistent distance units, NOT raw image pixels.
"""

import math

import numpy as np


def calculate_launch_angle(velocity) -> float:
    """Return elevation in degrees above the horizontal ground plane.

    ``velocity`` is (vx, vy, vz). Positive angles mean rising flight;
    negative angles mean descending flight. This does not calculate azimuth.
    """
    vector = np.asarray(velocity, dtype=float)
    if vector.shape != (3,) or not np.isfinite(vector).all():
        raise ValueError("velocity must contain three finite components")
    vx, vy, vz = vector
    if not np.any(vector):
        raise ValueError("launch angle is undefined for zero velocity")
    return math.degrees(math.atan2(vz, math.hypot(vx, vy)))


def estimate_launch_angle(positions, timestamps) -> float:
    """Fit a straight trajectory and return its vertical angle in degrees.

    Args:
        positions: N x 3 calibrated positions, preferably in meters.
        timestamps: N strictly increasing capture times in seconds.

    Supply at least two samples from the first few milliseconds AFTER impact.
    A least-squares line uses all supplied samples and supports uneven timing.
    This is an average direction over the supplied window, not a gravity/drag
    model or a validated launch measurement. It does not reject outliers.
    """
    points = np.asarray(positions, dtype=float)
    times = np.asarray(timestamps, dtype=float)
    if points.ndim != 2 or points.shape[1] != 3 or len(points) < 2:
        raise ValueError("positions must have shape (N, 3), with N >= 2")
    if times.shape != (len(points),):
        raise ValueError("provide one timestamp per position")
    if not np.isfinite(points).all() or not np.isfinite(times).all():
        raise ValueError("positions and timestamps must be finite")
    if not np.all(np.diff(times) > 0):
        raise ValueError("timestamps must be strictly increasing")

    # Subtract the first timestamp before centering to reduce numerical loss
    # when callers supply absolute rather than relative capture timestamps.
    elapsed = times - times[0]
    centered_time = elapsed - elapsed.mean()
    centered_positions = points - points.mean(axis=0)
    velocity = (centered_time @ centered_positions) / (
        centered_time @ centered_time
    )
    return calculate_launch_angle(velocity)
