import pytest

from fairway_fusion.cameras.synchronization import (
    decode_packet,
    encode_packet,
    validate_timing,
)
from fairway_fusion.errors import MeasurementRejected


def test_crc_and_sequence():
    packet = encode_packet({"sequence": 42, "pulse_centers_us": [10, 1010]})
    assert decode_packet(packet, 42)["sequence"] == 42
    with pytest.raises(MeasurementRejected):
        decode_packet(packet.replace(b"1010", b"1011"), 42)
    with pytest.raises(MeasurementRejected):
        decode_packet(packet, 41)


@pytest.mark.parametrize(
    "change",
    [
        {"verified": False},
        {"pulse_widths_us": [11, 8]},
        {"pulse_centers_us": [100, 100]},
        {"uncertainty_us": float("nan")},
        {"exposure_windows_us": [[0, 1000], [0, 2000]]},
    ],
)
def test_invalid_timing(change):
    timing = {
        "verified": True,
        "pulse_centers_us": [100, 1100],
        "pulse_widths_us": [8, 8],
        "uncertainty_us": 1,
        "exposure_windows_us": [[0, 2000], [0, 2000]],
    }
    timing.update(change)
    with pytest.raises(MeasurementRejected):
        validate_timing(timing)
