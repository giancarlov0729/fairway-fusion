"""Estimated carry distance

Standalone module: no dependencies beyond the standard library.
Estimated carry is a MODEL OUTPUT, not a measurement. Keep it separate from
measured values (speed, launch angle) when storing or displaying results.

Models
------
"vacuum"    : ideal projectile, no air. Baseline / sanity check only.
"drag"      : quadratic air drag, no spin lift. Conservative (under-estimates).
"drag_spin" : drag + Magnus lift from an ASSUMED constant backspin.
              Spin is not measured in the prototype, so this is an assumption.

Coordinate frame: x = downrange, y = up. Ball launched at launch_height_m and
carry ends when it returns to y = 0 (flat ground, no wind, sea level).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict

G = 9.80665                 # m/s^2
MPH_TO_MPS = 0.44704
M_TO_YD = 1.0936133
RPM_TO_RADS = 2 * math.pi / 60

MODELS = ("vacuum", "drag", "drag_spin")

# Physical / sanity limits for inputs
MAX_SPEED_MPS = 100.0       # ~224 mph, above any realistic golf ball speed
MIN_SPEED_MPS = 0.1


@dataclass(frozen=True)
class Assumptions:
    """Every assumption that affects the result. Stored alongside the estimate."""
    model: str = "drag_spin"
    air_density: float = 1.225          # kg/m^3 (sea level, 15 C)
    ball_mass: float = 0.04593          # kg (USGA max)
    ball_diameter: float = 0.04267      # m (R&A/USGA min)
    drag_coeff: float = 0.26            # constant Cd, typical mid-flight value
    backspin_rpm: float = 2500.0        # ASSUMED, held constant (no decay)
    max_lift_coeff: float = 0.22       # cap on Cl
    launch_height_m: float = 0.0
    time_step_s: float = 0.002          # RK4 step


@dataclass(frozen=True)
class CarryEstimate:
    carry_m: float
    carry_yd: float
    apex_height_m: float
    flight_time_s: float
    assumptions: Assumptions
    is_estimate: bool = True            # always True; flag for UI/DB

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def _validate(speed_mps: float, angle_deg: float, a: Assumptions) -> None:
    for name, v in (("ball_speed", speed_mps), ("launch_angle", angle_deg)):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise TypeError(f"{name} must be a number, got {type(v).__name__}")
        if not math.isfinite(v):
            raise ValueError(f"{name} must be finite, got {v}")
    if not MIN_SPEED_MPS <= speed_mps <= MAX_SPEED_MPS:
        raise ValueError(
            f"ball speed out of range ({speed_mps:.2f} m/s); "
            f"expected {MIN_SPEED_MPS}-{MAX_SPEED_MPS} m/s")
    if not 0 < angle_deg < 90:
        raise ValueError(f"launch angle must be between 0 and 90 degrees exclusive, got {angle_deg}")
    if a.model not in MODELS:
        raise ValueError(f"unknown model '{a.model}', choose from {MODELS}")
    for name in ("air_density", "ball_mass", "ball_diameter", "time_step_s",
                 "drag_coeff", "backspin_rpm", "max_lift_coeff", "launch_height_m"):
        value = getattr(a, name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{name} must be a number")
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite")
    if not 0.0001 <= a.time_step_s <= 0.01:
        raise ValueError("time_step_s must be between 0.0001 and 0.01 seconds")
    for name in ("air_density", "ball_mass", "ball_diameter", "time_step_s"):
        if not getattr(a, name) > 0:
            raise ValueError(f"{name} must be positive")
    if a.drag_coeff < 0 or a.backspin_rpm < 0 or a.max_lift_coeff < 0:
        raise ValueError("drag_coeff, backspin_rpm and max_lift_coeff must be >= 0")
    if a.launch_height_m < 0:
        raise ValueError("launch_height_m must be >= 0")


def _vacuum(v: float, theta: float, h: float) -> tuple[float, float, float]:
    vx, vy = v * math.cos(theta), v * math.sin(theta)
    t = (vy + math.sqrt(vy * vy + 2 * G * h)) / G
    return vx * t, h + vy * vy / (2 * G), t


def _numeric(v: float, theta: float, a: Assumptions) -> tuple[float, float, float]:
    area = math.pi * (a.ball_diameter / 2) ** 2
    k = 0.5 * a.air_density * area / a.ball_mass     # per-mass force factor
    radius = a.ball_diameter / 2
    omega = a.backspin_rpm * RPM_TO_RADS if a.model == "drag_spin" else 0.0

    def accel(vx: float, vy: float) -> tuple[float, float]:
        sp = math.hypot(vx, vy)
        cl = 0.0
        if omega > 0 and sp > 0:
            spin_ratio = radius * omega / sp
            cl = min(a.max_lift_coeff, 0.54 * spin_ratio ** 0.4)
        # drag opposes velocity; backspin lift acts perpendicular, upward
        ax = -k * sp * (a.drag_coeff * vx + cl * vy)
        ay = -G - k * sp * (a.drag_coeff * vy - cl * vx)
        return ax, ay

    x, y = 0.0, a.launch_height_m
    vx, vy = v * math.cos(theta), v * math.sin(theta)
    t, apex, dt = 0.0, y, a.time_step_s
    for _ in range(int(60 / dt)):                    # 60 s hard cap
        a1x, a1y = accel(vx, vy)
        a2x, a2y = accel(vx + 0.5 * dt * a1x, vy + 0.5 * dt * a1y)
        a3x, a3y = accel(vx + 0.5 * dt * a2x, vy + 0.5 * dt * a2y)
        a4x, a4y = accel(vx + dt * a3x, vy + dt * a3y)
        nx = x + dt * (vx + dt * (a1x + a2x + a3x) / 6)
        ny = y + dt * (vy + dt * (a1y + a2y + a3y) / 6)
        nvx = vx + dt * (a1x + 2 * a2x + 2 * a3x + a4x) / 6
        nvy = vy + dt * (a1y + 2 * a2y + 2 * a3y + a4y) / 6
        if ny <= 0 and vy < 0 or ny <= 0 < y:
            frac = y / (y - ny) if y != ny else 1.0   # interpolate landing point
            return x + frac * (nx - x), apex, t + frac * dt
        x, y, vx, vy, t = nx, ny, nvx, nvy, t + dt
        apex = max(apex, y)
    raise RuntimeError("trajectory did not land within 60 s; check assumptions")


def estimate_carry(
    ball_speed: float,
    launch_angle_deg: float,
    assumptions: Assumptions | None = None,
    speed_unit: str = "mps",
) -> CarryEstimate:
    """Estimate carry distance.

    ball_speed        : measured ball speed, in `speed_unit` ("mps" default, matching
                        launch_metrics()["speed_m_s"]; or "mph")
    launch_angle_deg  : measured vertical launch angle, degrees above horizontal
    assumptions       : model + physical assumptions (defaults to drag_spin)
    Returns a CarryEstimate with carry in metres and yards.
    Raises ValueError / TypeError on invalid input.
    """
    a = assumptions or Assumptions()
    if speed_unit not in ("mph", "mps"):
        raise ValueError("speed_unit must be 'mph' or 'mps'")
    if isinstance(ball_speed, bool) or not isinstance(ball_speed, (int, float)):
        raise TypeError(f"ball_speed must be a number, got {type(ball_speed).__name__}")
    v = ball_speed * (MPH_TO_MPS if speed_unit == "mph" else 1.0)
    _validate(v, launch_angle_deg, a)
    theta = math.radians(launch_angle_deg)

    if a.model == "vacuum":
        carry, apex, t = _vacuum(v, theta, a.launch_height_m)
    else:
        carry, apex, t = _numeric(v, theta, a)
    if not all(math.isfinite(value) for value in (carry, apex, t)):
        raise ValueError("trajectory produced non-finite results; check assumptions")
    return CarryEstimate(carry, carry * M_TO_YD, apex, t, a)


def carry_metrics(metrics: dict, assumptions: Assumptions | None = None) -> dict:
    """Adapter for the server handoff: launch_metrics() dict -> carry dict.

    Never raises for an unusable shot (e.g. launch angle None or <= 0); it returns
    None values plus "carry_error" so the preview/save path keeps working.
    Estimate fields are namespaced (estimated_*, carry_*) so they cannot be
    mistaken for measured values.
    """
    a = assumptions or Assumptions()
    empty = {
        "estimated_carry_m": None, "estimated_carry_yd": None,
        "apex_height_m": None, "flight_time_s": None,
        "carry_model": a.model, "carry_assumptions": asdict(a),
        "carry_is_estimate": True, "carry_error": None,
    }
    try:
        r = estimate_carry(metrics["speed_m_s"], metrics["launch_angle_deg"], a, "mps")
    except (KeyError, TypeError, ValueError, ArithmeticError, RuntimeError) as exc:
        return {**empty, "carry_error": str(exc)}
    return {**empty,
            "estimated_carry_m": r.carry_m, "estimated_carry_yd": r.carry_yd,
            "apex_height_m": r.apex_height_m, "flight_time_s": r.flight_time_s}
