"""Timing records use optical pulse centres, never host USB arrival times."""

import json
import zlib

import numpy as np

from fairway_fusion.errors import MeasurementRejected


def encode_packet(payload):
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return f"{body}\t{zlib.crc32(body.encode()) & 0xFFFFFFFF:08x}\n".encode()


def decode_packet(line, expected_sequence=None):
    try:
        body, crc = line.decode("ascii").strip().rsplit("\t", 1)
        if zlib.crc32(body.encode()) & 0xFFFFFFFF != int(crc, 16):
            raise ValueError("CRC mismatch")
        packet = json.loads(body)
        if not isinstance(packet, dict) or not isinstance(packet.get("sequence"), int):
            raise TypeError("Expected an object with integer sequence")
        if expected_sequence is not None and packet["sequence"] != expected_sequence:
            raise ValueError("Stale controller sequence")
        return packet
    except (ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise MeasurementRejected(f"Invalid timing packet: {exc}") from exc


def validate_timing(timing):
    if timing.get("verified") is not True:
        raise MeasurementRejected(
            "Optical timing and image/pulse association have not been verified"
        )
    times = np.asarray(timing["pulse_centers_us"], float)
    widths = np.asarray(timing["pulse_widths_us"], float)
    error = float(timing["uncertainty_us"])
    if (
        times.ndim != 1
        or not 2 <= len(times) <= 8
        or widths.shape != times.shape
        or not np.isfinite(times).all()
        or not np.isfinite(widths).all()
        or np.any(np.diff(times) <= 0)
        or np.any(widths <= 0)
        or np.any(widths > 10)
        or not np.isfinite(error)
        or not 0 <= error <= 10
    ):
        raise MeasurementRejected("Invalid or out-of-spec optical pulse timing")
    if np.any(np.diff(times) <= (widths[:-1] + widths[1:]) / 2 + 2 * error):
        raise MeasurementRejected("Optical pulses overlap")
    # Each camera exposure window must contain the complete same pulse train.
    windows = timing["exposure_windows_us"]
    if len(windows) != 2:
        raise MeasurementRejected("Two exposure windows are required")
    for start, end in windows:
        if (
            not np.isfinite([start, end]).all()
            or start > times[0] - widths[0] / 2 - error
            or end < times[-1] + widths[-1] / 2 + error
        ):
            raise MeasurementRejected("Flash falls outside a camera exposure")
    return (times - times[0]) * 1e-6, error * 1e-6
