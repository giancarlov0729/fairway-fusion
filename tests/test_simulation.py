import numpy as np
import pytest
from fairway_fusion.simulation import (
    SCENARIOS,
    generate_shot,
    calculation_input,
    analyze_samples,
    microphone_adc_example,
)


@pytest.mark.parametrize("name", SCENARIOS)
def test_known_trajectory_recovers_launch(name):
    expected_speed, expected_angle, expected_direction = SCENARIOS[name]
    result = analyze_samples(generate_shot(name))
    assert result["speed_m_s"] == pytest.approx(expected_speed)
    assert result["launch_angle_deg"] == pytest.approx(expected_angle)
    assert result["launch_direction_deg"] == pytest.approx(expected_direction)


def test_millimetres_and_milliseconds_convert_to_si():
    packet = generate_shot()
    packet["samples"] = [
        dict(t_ms=1, x_mm=50, y_mm=0, z_mm=43),
        dict(t_ms=2, x_mm=100, y_mm=0, z_mm=53),
    ]
    positions, times = calculation_input(packet)
    assert positions[0] == pytest.approx([0.05, 0, 0.043])
    assert times == pytest.approx([0, 0.001])
    assert analyze_samples(packet)["speed_m_s"] == pytest.approx(np.hypot(50, 10))


def test_noise_reproducible_and_small_error():
    assert generate_shot(seed=12, noise_mm=0.1) == generate_shot(seed=12, noise_mm=0.1)
    result = analyze_samples(generate_shot(seed=12, noise_mm=0.1))
    assert result["speed_m_s"] == pytest.approx(50, abs=0.1)


def test_adc_codes_quantize_voltage_and_trigger_without_claiming_position():
    packet = microphone_adc_example()
    assert min(packet["counts"]) >= 0 and max(packet["counts"]) <= 4095
    assert packet["volts"] == pytest.approx(np.array(packet["counts"]) * 3.3 / 4095)
    assert 1.7 < packet["trigger_time_ms"] < 2.1
    assert "samples" not in packet
