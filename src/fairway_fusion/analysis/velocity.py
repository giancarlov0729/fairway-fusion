import numpy as np


def estimate_velocity(positions, times_s):
    positions, times = np.asarray(positions, float), np.asarray(times_s, float)
    if (
        positions.shape != (len(times), 3)
        or len(times) < 2
        or not np.isfinite(positions).all()
        or not np.isfinite(times).all()
        or np.any(np.diff(times) <= 0)
    ):
        raise ValueError("Require ordered finite 3D positions and timestamps")
    centered = times - times.mean()
    return np.sum(
        centered[:, None] * (positions - positions.mean(axis=0)), axis=0
    ) / np.sum(centered**2)
