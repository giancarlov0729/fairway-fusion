"""Calculate golf ball launch metrics from a 3D velocity vector.

Coordinates:
    +X: Downrange
    +Y: Left
    +Z: Up

Velocity components must be measured in meters per second.
"""

import math

import numpy as np

from fairway_fusion.analysis.velocity import estimate_velocity


MPS_TO_MPH = 2.2369362921


def calculate_launch_angle(velocity) -> float:
    """Calculate vertical launch angle in degrees."""

    vector = np.asarray(velocity, dtype=float)

    if vector.shape != (3,) or not np.isfinite(vector).all():
        raise ValueError("Velocity must contain three finite components")

    vx, vy, vz = vector

    if not np.any(vector):
        raise ValueError("Launch angle is undefined for zero velocity")

    horizontal_speed = math.hypot(vx, vy)

    return math.degrees(math.atan2(vz, horizontal_speed))


def estimate_launch_angle(positions, timestamps) -> float:
    """Estimate vertical launch angle from timestamped 3D positions."""

    velocity = estimate_velocity(positions, timestamps)

    return calculate_launch_angle(velocity)


def calculate_ball_speed(velocity) -> float:
    """Calculate ball speed in meters per second."""

    vector = np.asarray(velocity, dtype=float)

    if vector.shape != (3,) or not np.isfinite(vector).all():
        raise ValueError("Velocity must contain three finite components")

    return float(np.linalg.norm(vector))


def calculate_launch_direction(velocity) -> float:
    """Calculate horizontal launch direction in degrees.

    Positive angles indicate leftward launch.
    Negative angles indicate rightward launch.
    """

    vector = np.asarray(velocity, dtype=float)

    if vector.shape != (3,) or not np.isfinite(vector).all():
        raise ValueError("Velocity must contain three finite components")

    vx, vy, _ = vector

    if math.hypot(vx, vy) == 0:
        raise ValueError("Launch direction is undefined without horizontal motion")

    return math.degrees(math.atan2(vy, vx))


def launch_metrics(velocity) -> dict:
    """Return standardized launch metrics for the simulation module."""

    speed_m_s = calculate_ball_speed(velocity)

    launch_angle = (
        calculate_launch_angle(velocity)
        if speed_m_s > 0
        else None
    )

    vector = np.asarray(velocity, dtype=float)

    horizontal_speed = math.hypot(vector[0], vector[1])

    launch_direction = (
        calculate_launch_direction(velocity)
        if horizontal_speed > 0
        else None
    )

    return {
        "speed_m_s": speed_m_s,
        "speed_mph": speed_m_s * MPS_TO_MPH,
        "launch_angle_deg": launch_angle,
        "launch_direction_deg": launch_direction,
    }