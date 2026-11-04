"""Seeded sensor fixtures, with independent truth kept OUTSIDE the replay payload."""

import copy
import numpy as np
from scipy.spatial.transform import Rotation

PRESETS = [
    {
        "id": "driver",
        "label": "Driver · controlled fade",
        "club": "Driver",
        "speed": 68,
        "elevation": 13,
        "direction": -1,
        "rpm": 2600,
        "tilt": -9,
        "seed": 11,
    },
    {
        "id": "iron",
        "label": "7 iron · stock approach",
        "club": "7 iron",
        "speed": 49,
        "elevation": 20,
        "direction": 1.2,
        "rpm": 6100,
        "tilt": 4,
        "seed": 22,
    },
    {
        "id": "wedge",
        "label": "Wedge · high spin",
        "club": "Wedge",
        "speed": 34,
        "elevation": 33,
        "direction": -0.5,
        "rpm": 8800,
        "tilt": -2,
        "seed": 33,
    },
]


def make_fixture(preset):
    rng = np.random.default_rng(preset["seed"])
    elevation, direction, tilt = np.radians(
        [preset["elevation"], preset["direction"], preset["tilt"]]
    )
    velocity = preset["speed"] * np.array(
        [
            np.cos(elevation) * np.cos(direction),
            np.cos(elevation) * np.sin(direction),
            np.sin(elevation),
        ]
    )
    omega = preset["rpm"] * 2 * np.pi / 60 * np.array([0, -np.cos(tilt), np.sin(tilt)])
    calibrations = [
        (
            Rotation.from_euler("xyz", [10, 25, -5], degrees=True).as_matrix(),
            np.array([0, 0, 0.7]),
        ),
        (
            Rotation.from_euler("xyz", [-8, -20, 6], degrees=True).as_matrix(),
            np.array([0.12, 0.02, 0.8]),
        ),
    ]
    initial = Rotation.from_euler("xyz", [0.2, 0.5, 0.1]).as_matrix()
    times = np.arange(4) * 0.0018
    packet = {
        "schema_version": 1,
        "shot_id": preset["id"],
        "label": preset["label"],
        "source": "synthetic",
        "input_stage": "Calibrated ball centres and dot-derived orientations, after computer vision",
        "timing": {
            "verified": True,
            "uncertainty_us": 1,
            "clock": "simulated Pico optical pulse centres",
        },
        "cameras": [{"R": r.tolist(), "t_m": t.tolist()} for r, t in calibrations],
        "observations": [],
    }
    for time in times:
        position = (
            np.array([0, 0, 0.021335])
            + velocity * time
            + np.array([0, 0, -0.5 * 9.80665 * time**2])
        )
        pose = Rotation.from_rotvec(omega * time).as_matrix() @ initial
        views = []
        for r, t in calibrations:
            views.append(
                {
                    "center_camera_m": (
                        r @ position + t + rng.normal(0, 0.00006, 3)
                    ).tolist(),
                    "ball_to_camera": (
                        r
                        @ Rotation.from_rotvec(rng.normal(0, 0.00035, 3)).as_matrix()
                        @ pose
                    ).tolist(),
                    "matched_dots": 6,
                    "orientation_residual_rad": 0.003,
                    "pattern_unique": True,
                }
            )
        packet["observations"].append(
            {"timestamp_us": round(time * 1e6), "views": views}
        )
    return packet, {
        "speed_m_s": preset["speed"],
        "launch_angle_deg": preset["elevation"],
        "spin_rpm": preset["rpm"],
    }


def fixtures():
    captures = {p["id"]: make_fixture(p) for p in PRESETS}
    bad = copy.deepcopy(captures["driver"][0])
    bad.update(shot_id="rejected", label="Quality check · camera mismatch")
    bad["observations"][2]["views"][1]["center_camera_m"][0] += 0.015
    captures["rejected"] = (bad, None)
    return captures
