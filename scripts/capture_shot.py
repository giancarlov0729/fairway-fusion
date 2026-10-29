"""Capture one Pico-triggered shot; retain unverified timing for bench review."""

import argparse
import json
import time
from pathlib import Path

import serial

from fairway_fusion.cameras.capture import StereoCapture
from fairway_fusion.cameras.synchronization import decode_packet, encode_packet


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="New shot directory")
    parser.add_argument("--port", required=True)
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--spacing-us", type=int, default=1000)
    parser.add_argument("--delay-us", type=int, default=1000)
    parser.add_argument("--exposure-us", type=int, default=6000)
    parser.add_argument("--timeout-s", type=float, default=30)
    args = parser.parse_args()
    if not 250 <= args.spacing_us <= 2500 or args.delay_us < 10 or args.timeout_s <= 0:
        parser.error("Invalid timing arguments")
    if args.exposure_us < args.delay_us + 3 * args.spacing_us + 20:
        parser.error("Exposure must cover delay, three pulse periods, and margin")
    calibration = json.loads(args.calibration.read_text())
    sizes = [tuple(c["image_size"]) for c in calibration["cameras"]]
    if len(sizes) != 2 or sizes[0] != sizes[1]:
        parser.error("Two matching camera resolutions required")
    args.output.mkdir(parents=True, exist_ok=False)
    sequence = time.time_ns() & 0x7FFFFFFF
    command = {
        "command": "arm",
        "sequence": sequence,
        "spacing_us": args.spacing_us,
        "delay_us": args.delay_us,
    }
    with (
        serial.Serial(args.port, 115200, timeout=0.2) as controller,
        StereoCapture(size=sizes[0], exposure_us=args.exposure_us) as cameras,
    ):
        controller.reset_input_buffer()
        jobs = cameras.queue()
        controller.write(encode_packet(command))
        try:
            deadline = time.monotonic() + args.timeout_s
            while time.monotonic() < deadline:
                line = controller.readline()
                if not line:
                    continue
                packet = decode_packet(line, sequence)
                if packet.get("event") == "complete":
                    break
                if packet.get("event") == "error":
                    raise RuntimeError(packet.get("reason", "Pico error"))
            else:
                raise TimeoutError(
                    "No Pico completion; inspect trigger input and wiring"
                )
            metadata = cameras.save(jobs, args.output)
        finally:
            controller.write(encode_packet({"command": "disarm", "sequence": sequence}))
    (args.output / "calibration.json").write_text(json.dumps(calibration, indent=2))
    manifest = {
        "shot_id": str(sequence),
        "images": ["camera0.png", "camera1.png"],
        "calibration": "calibration.json",
        "camera_metadata": metadata,
        "controller_packet": packet,
        "timing": {
            "verified": False,
            "source": "commanded PIO edges; optical validation required",
            "pulse_centers_us": packet["pulse_centers_us"],
            "pulse_widths_us": packet["pulse_widths_us"],
            "uncertainty_us": None,
            "exposure_windows_us": [],
        },
    }
    (args.output / "shot.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(
        f"Captured {args.output}; timing remains unverified until optical/exposure checks are recorded."
    )


if __name__ == "__main__":
    main()
