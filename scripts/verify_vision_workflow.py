"""Verify the synthetic vision workflow in a fresh, isolated folder.

Runs Flask locally through its test client; no listening server or Pico is used.
Keeps the generated database, PNGs, replay copy and JSON report for inspection.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil
import sqlite3
import tempfile

import numpy as np

from fairway_fusion.synthetic import CASE_POSITIONS_M
from fairway_fusion.web.app import create_app


class NoHardware:
    def close(self):
        pass

    def connect(self):
        raise RuntimeError("Verification must not connect to hardware")

    def command(self, *args):
        raise RuntimeError("Verification must not send hardware commands")

    capture_command = command


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


def run_verification(output):
    config = {"TESTING": True, "DATABASE_PATH": str(output / "original" / "fairway.db"),
              "PICO_CLIENT": NoHardware()}
    app = create_app(config)
    client = app.test_client()
    store = app.extensions["shot_store"]
    sid = client.post("/api/sessions").json["session"]["id"]
    cases = []
    original_pixels = {}
    for case in CASE_POSITIONS_M:
        response = client.post("/api/captures/synthetic", json={"case": case, "session_id": sid})
        check(response.status_code == 201, f"Could not create {case}: {response.json}")
        key, process_url = response.json["capture"]["capture_id"], response.json["process_url"]
        processed = client.post(process_url)
        check(processed.status_code == 200, f"Could not process {case}: {processed.json}")
        shot = processed.json["shot"]
        expected = "rejected" if case in ("clutter", "shallow") else "accepted"
        check(shot["status"] == expected, f"Unexpected {case} outcome")
        entry = {"case": case, "capture_id": key, "shot_id": shot["id"], "status": shot["status"],
                 "reason": shot["reason"]}
        if expected == "accepted":
            positions = np.asarray(CASE_POSITIONS_M[case], dtype=float)
            velocity = (positions[1] - positions[0]) / .002
            speed = float(np.linalg.norm(velocity))
            angle = math.degrees(math.atan2(velocity[2], math.hypot(*velocity[:2])))
            direction = math.degrees(math.atan2(velocity[1], velocity[0]))
            errors = {"speed_m_s": abs(shot["speed_m_s"] - speed),
                      "launch_angle_deg": abs(shot["launch_angle_deg"] - angle),
                      "launch_direction_deg": abs(shot["launch_direction_deg"] - direction)}
            check(all(error < .6 for error in errors.values()), f"{case} exceeded synthetic tolerance")
            entry["synthetic_errors"] = errors
        check(client.post(process_url).json["shot"] == shot, f"Retry changed {case} result")
        for camera in ("a", "b"):
            image = client.get(f"/vision/captures/{key}/images/{camera}")
            check(image.status_code == 200 and image.mimetype == "image/png", "Missing original preview")
            original_pixels[(key, camera)] = hashlib.sha256(image.data).hexdigest()
        check(client.get(f"/vision/captures/{key}/overlay").status_code == 200, "Missing overlay")
        check(client.get(processed.json["result_url"]).status_code == 200, "Missing analysis page")
        cases.append(entry)

    pending = client.post("/api/captures/synthetic", json={"case": "clean"}).json
    pending_id = pending["capture"]["capture_id"]
    check(store.get_capture_shot(pending_id) is None, "Pending pair unexpectedly has a shot")
    replay = output / "replay"
    replay.mkdir()
    with sqlite3.connect(store.path) as original, sqlite3.connect(replay / "fairway.db") as copied:
        original.backup(copied)
    shutil.copytree(store.images_root / "captures", replay / "captures")
    second = create_app({**config, "DATABASE_PATH": str(replay / "fairway.db")})
    replay_client = second.test_client()
    for entry in cases:
        key = entry["capture_id"]
        response = replay_client.post(f"/api/captures/{key}/process")
        check(response.status_code == 200 and response.json["shot"]["id"] == entry["shot_id"],
              "Replay did not preserve saved shot identity")
        for camera in ("a", "b"):
            image = replay_client.get(f"/vision/captures/{key}/images/{camera}")
            check(image.status_code == 200 and hashlib.sha256(image.data).hexdigest() == original_pixels[(key, camera)],
                  "Replay changed saved image pixels")
    response = replay_client.post(pending["process_url"])
    check(response.status_code == 200 and response.json["shot"]["status"] == "accepted",
          "Copied unprocessed pair could not be analyzed")
    check(store.get_capture_shot(pending_id) is None, "Replay modified the original database")
    check(len(second.extensions["shot_store"].list_shots()) == 7, "Replay shot count is wrong")
    report = {"status": "passed", "source": "synthetic", "hardware_used": False,
              "physical_accuracy_validated": False, "flash_interval_us": 2000,
              "original_shots": 6, "replay_shots": 7, "cases": cases,
              "copied_unprocessed_pair": pending_id, "output_directory": str(output)}
    (output / "verification_report.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, help="A new folder; existing folders are never overwritten")
    args = parser.parse_args()
    if args.output_dir:
        output = args.output_dir.resolve()
        output.mkdir(parents=True, exist_ok=False)
    else:
        output = Path(tempfile.mkdtemp(prefix="fairway-vision-check-"))
    print(json.dumps(run_verification(output), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
