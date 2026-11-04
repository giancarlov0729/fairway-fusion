"""Pre-alpha flight projection. Coefficients are assumptions, not ball-specific calibration."""

from dataclasses import asdict, dataclass, replace

import numpy as np
from scipy.integrate import solve_ivp


@dataclass(frozen=True)
class FlightConditions:
    air_density_kg_m3: float = 1.225
    wind_x_m_s: float = 0.0
    wind_y_m_s: float = 0.0
    drag_coefficient: float = 0.25
    lift_slope: float = 1.4
    max_lift_coefficient: float = 0.32
    spin_decay_s: float = 25.0
    landing_speed_fraction: float = 0.22
    roll_deceleration_m_s2: float = 2.5

    def validate(self):
        limits = [
            (0, 1.5),
            (-20, 20),
            (-20, 20),
            (0, 0.6),
            (0, 3),
            (0, 0.6),
            (1, 1000),
            (0, 0.6),
            (0.2, 10),
        ]
        for (name, value), (low, high) in zip(asdict(self).items(), limits):
            if not np.isfinite(value) or not low <= value <= high:
                raise ValueError(f"{name} must be between {low} and {high}")


def project_flight(velocity, omega, conditions=None, *, sensitivity=True):
    """Integrate gravity, quadratic drag and spin lift to the launch-height plane.

    +X downrange, +Y left, +Z up. Carry is horizontal launch-to-first-impact range.
    Total adds a simple effective rollout; it does not model individual bounces.
    """
    c = conditions or FlightConditions()
    c.validate()
    velocity, omega = np.asarray(velocity, float), np.asarray(omega, float)
    if (
        velocity.shape != (3,)
        or omega.shape != (3,)
        or not np.isfinite(velocity).all()
        or not np.isfinite(omega).all()
        or not 1 <= np.linalg.norm(velocity) <= 100
        or velocity[2] <= 0
        or np.linalg.norm(omega) > 12000 * 2 * np.pi / 60
    ):
        raise ValueError(
            "Flight requires finite upward launch (1–100 m/s) and spin <=12000 RPM"
        )
    radius, mass, gravity = 0.021335, 0.04593, 9.80665
    area = np.pi * radius**2
    wind = np.array([c.wind_x_m_s, c.wind_y_m_s, 0])

    def derivative(t, state):
        relative = state[3:] - wind
        speed = np.linalg.norm(relative)
        acceleration = np.array([0.0, 0.0, -gravity])
        if speed > 1e-8:
            cross = np.cross(omega * np.exp(-t / c.spin_decay_s), relative)
            cross_norm = np.linalg.norm(cross)
            lift = min(
                c.max_lift_coefficient, c.lift_slope * radius * cross_norm / speed**2
            )
            dynamic = 0.5 * c.air_density_kg_m3 * area * speed**2 / mass
            acceleration -= dynamic * c.drag_coefficient * relative / speed
            if cross_norm > 1e-10:
                acceleration += dynamic * lift * cross / cross_norm
        return np.concatenate((state[3:], acceleration))

    def ground(t, state):
        return state[2]

    ground.terminal, ground.direction = True, -1
    solution = solve_ivp(
        derivative,
        (0, 30),
        np.r_[np.zeros(3), velocity],
        events=ground,
        dense_output=True,
        rtol=1e-7,
        atol=1e-9,
        max_step=0.05,
    )
    if not solution.success or not len(solution.t_events[0]):
        raise ValueError("Flight did not reach the landing plane within 30 seconds")
    duration = float(solution.t_events[0][0])
    samples = np.linspace(0, duration, 181)
    states = solution.sol(samples).T
    states[:, 2] = np.maximum(states[:, 2], 0)
    landing = solution.y_events[0][0]
    horizontal_speed = np.linalg.norm(landing[3:5])
    roll = float(
        (horizontal_speed * c.landing_speed_fraction) ** 2
        / (2 * c.roll_deceleration_m_s2)
    )
    endpoint = landing[:2] + roll * landing[3:5] / max(horizontal_speed, 1e-12)
    result = {
        "carry_m": float(np.linalg.norm(landing[:2])),
        "total_m": float(np.linalg.norm(endpoint)),
        "roll_m": roll,
        "apex_m": float(states[:, 2].max()),
        "flight_time_s": duration,
        "offline_m": float(landing[1]),
        "landing_angle_deg": float(
            np.degrees(np.arctan2(-landing[5], horizontal_speed))
        ),
        "landing_world_m": [float(landing[0]), float(landing[1]), 0.0],
        "rest_world_m": [float(endpoint[0]), float(endpoint[1]), 0.0],
        "trajectory": [
            {"t_s": float(t), "position_m": p[:3].tolist()}
            for t, p in zip(samples, states)
        ],
        "conditions": asdict(c),
        "model": "Point mass with drag and spin lift; illustrative coefficients; flat landing plane",
        "validation": "Not calibrated against real ball flights. Total includes assumed rollout.",
    }
    if sensitivity:
        carries = [result["carry_m"]]
        for drag_scale, lift_scale in ((0.8, 0.8), (0.8, 1.2), (1.2, 0.8), (1.2, 1.2)):
            variant = replace(
                c,
                drag_coefficient=c.drag_coefficient * drag_scale,
                lift_slope=c.lift_slope * lift_scale,
            )
            # The public coefficient bounds also constrain sensitivity variants.
            variant = replace(
                variant,
                drag_coefficient=min(0.6, variant.drag_coefficient),
                lift_slope=min(3.0, variant.lift_slope),
            )
            carries.append(
                project_flight(velocity, omega, variant, sensitivity=False)["carry_m"]
            )
        result["carry_sensitivity_m"] = [min(carries), max(carries)]
        result["sensitivity_note"] = (
            "Range from ±20% drag and lift-slope changes; not a confidence interval"
        )
    return result
