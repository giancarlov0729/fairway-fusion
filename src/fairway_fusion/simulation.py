"""Reproducible trajectory input at the camera reconstruction boundary."""

import numpy as np
from fairway_fusion.analysis.launch_angle import launch_metrics
from fairway_fusion.analysis.velocity import estimate_velocity


SCENARIOS = {
    "straight": (50.0, 15.0, 0.0),
    "high-launch": (35.0, 35.0, 0.0),
    "right-launch": (60.0, 12.0, -4.0),
}


def generate_shot(scenario="straight", seed=42, noise_mm=0.0):
    """Return serializable mm/ms observations, with no answer fields in the input.

    Constant velocity over a 5 ms measurement window is intentional for the team's
    first calculation contract. Real flight and camera noise require later validation.
    """
    if not np.isfinite(noise_mm) or not 0 <= noise_mm <= 2:
        raise ValueError("noise_mm must be between 0 and 2")
    speed, elevation, direction = SCENARIOS[scenario]
    e, d = np.radians([elevation, direction])
    velocity = speed * np.array(
        [np.cos(e) * np.cos(d), np.cos(e) * np.sin(d), np.sin(e)]
    )
    rng = np.random.default_rng(seed)
    samples = []
    for timestamp in range(6):
        # 1 m/s = 1 mm/ms. Ball centre starts one radius above ground.
        position = np.array([0.0, 0.0, 21.335]) + velocity * timestamp
        position += rng.normal(0, noise_mm, 3)
        samples.append(
            dict(
                t_ms=timestamp,
                x_mm=float(position[0]),
                y_mm=float(position[1]),
                z_mm=float(position[2]),
            )
        )
    return {
        "schema_version": 1,
        "shot_id": f"sim-{scenario}-{seed}",
        "source": "simulation",
        "scenario": scenario,
        "seed": seed,
        "input_stage": "reconstructed_ball_positions",
        "noise_mm": noise_mm,
        "axes": "X downrange, Y left, Z up",
        "samples": samples,
    }


def calculation_input(packet):
    """Explicit mm/ms -> SI adapter for Person 1; no UI/database dependency."""
    if (
        packet.get("schema_version") != 1
        or packet.get("input_stage") != "reconstructed_ball_positions"
    ):
        raise ValueError("Unsupported position sample contract")
    samples = packet["samples"]
    if not 2 <= len(samples) <= 1000:
        raise ValueError("Require 2–1000 samples")
    try:
        values = np.array(
            [[s[k] for k in ("t_ms", "x_mm", "y_mm", "z_mm")] for s in samples], float
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Each sample needs numeric t_ms, x_mm, y_mm, z_mm") from exc
    if not np.isfinite(values).all() or np.any(np.diff(values[:, 0]) <= 0):
        raise ValueError("Require finite coordinates and increasing timestamps")
    return values[:, 1:] * 0.001, (values[:, 0] - values[0, 0]) * 0.001


def analyze_samples(packet):
    positions, times = calculation_input(packet)
    velocity = estimate_velocity(positions, times)
    return {
        "shot_id": packet["shot_id"],
        "source": packet["source"],
        "velocity_m_s": velocity.tolist(),
        **launch_metrics(velocity),
    }


def microphone_adc_example(seed=42, bits=12, reference_v=3.3):
    """Illustrative conditioned microphone ADC samples, NOT position measurements.

    RP2040 ADC hardware has 12-bit resolution. This fixture uses native counts;
    MicroPython read_u16 values would need their own scale conversion.
    """
    if bits != 12 or not np.isfinite(reference_v) or reference_v <= 0:
        raise ValueError("This fixture expects a 12-bit ADC and positive reference")
    rng = np.random.default_rng(seed)
    times = np.arange(100) * 0.05
    volts = reference_v / 2 + rng.normal(0, 0.008, len(times))
    volts += 0.9 * np.exp(-(((times - 2) / 0.12) ** 2))
    codes = np.rint(np.clip(volts / reference_v, 0, 1) * 4095).astype(int)
    reconstructed = codes / 4095 * reference_v
    triggered = np.flatnonzero(reconstructed > 2.2)
    return {
        "source": "simulation",
        "purpose": "illustrative impact trigger only",
        "adc_bits": 12,
        "reference_v": reference_v,
        "sample_period_us": 50,
        "threshold_v": 2.2,
        "counts": codes.tolist(),
        "volts": reconstructed.tolist(),
        "trigger_time_ms": float(times[triggered[0]]) if len(triggered) else None,
    }

