"""Exercise existing Pico capture firmware with all external outputs disconnected."""

import argparse
import json
from pathlib import Path
import time
from uuid import uuid4

from fairway_fusion.hardware.pico_client import PicoClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--outputs-disconnected", action="store_true", required=True,
                        help="Confirm GP2/GP3/GP4 are disconnected from cameras and lighting")
    trigger_mode = parser.add_mutually_exclusive_group()
    trigger_mode.add_argument("--test-trigger", action="store_true",
                        help="Issue firmware TEST_TRIGGER after ARM; otherwise demonstrate timeout")
    trigger_mode.add_argument("--microphone", action="store_true",
                              help="Wait for a physical microphone gate; do not issue TEST_TRIGGER")
    parser.add_argument("--output", type=Path, default=Path("data/recordings/pico-bench"))
    args = parser.parse_args()
    capture_id = uuid4().hex
    folder = args.output / capture_id
    folder.mkdir(parents=True, exist_ok=False)
    pico = PicoClient(args.port)
    try:
        print("Firmware:", pico.connect())
        print("Initial DISARM:", pico.command("DISARM"))
        # Allow the firmware's quiet-gate/cooldown intervals to elapse.
        time.sleep(.3)
        print("CONFIG:", pico.capture_command("CONFIG", capture_id, 5000, 5, 1000, 500))
        print("ARM:", pico.capture_command("ARM", capture_id))
        print("STATUS:", pico.command("STATUS"))
        if args.microphone:
            print("MICROPHONE TEST: make one sound now, within 10 seconds.", flush=True)
        print("Logic-only test: cameras are free-running separately; no optical trigger is connected.")
        if args.test_trigger:
            print("TEST_TRIGGER:", pico.capture_command("TEST_TRIGGER", capture_id))
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            report = pico.capture_command("REPORT", capture_id)
            if report["status"] in {"completed", "cancelled", "timed_out", "failed"}:
                (folder / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
                print(json.dumps(report, indent=2))
                print("Saved:", folder / "report.json")
                if report["status"] != ("completed" if args.test_trigger or args.microphone else "timed_out"):
                    raise RuntimeError("Unexpected Pico outcome; inspect the saved report")
                return
            time.sleep(.1)
        raise TimeoutError("No terminal Pico report within 12 seconds")
    finally:
        try:
            if pico.firmware is not None:
                print("Final DISARM:", pico.command("DISARM"))
        finally:
            pico.close()


if __name__ == "__main__":
    main()
