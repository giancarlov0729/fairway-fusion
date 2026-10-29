import numpy as np


def launch_metrics(velocity):
    """World +X downrange, +Y left, +Z up; calibrate extrinsics accordingly."""
    x, y, z = np.asarray(velocity, float)
    speed = float(np.linalg.norm(velocity))
    return {
        "speed_m_s": speed,
        "speed_mph": speed * 2.2369362921,
        "launch_angle_deg": float(np.degrees(np.arctan2(z, np.hypot(x, y))))
        if speed
        else None,
        "launch_direction_deg": float(np.degrees(np.arctan2(y, x)))
        if np.hypot(x, y)
        else None,
    }
