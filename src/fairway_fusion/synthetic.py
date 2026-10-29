"""Deterministic pinhole ray/sphere renderer for regression tests, not real validation."""

import json
from pathlib import Path

import cv2
import numpy as np
from scipy.spatial.transform import Rotation

from fairway_fusion.calibration.stereo_calibration import CameraCalibration


def make_capture(rpm=3000, spacing_us=1000, noise=0):
    size = (1000, 480)
    cameras = [
        CameraCalibration(
            [[1800, 0, 500], [0, 1800, 240], [0, 0, 1]],
            np.zeros(5),
            np.eye(3),
            [offset, 0, 0],
            size,
        )
        for offset in (0, -0.035)
    ]
    points = np.array(
        [
            [-0.45, -0.3, -1],
            [0.2, -0.4, -1],
            [0.5, 0.13, -1],
            [-0.2, 0.4, -1],
            [0.1, 0.1, -1],
            [0.05, 0.6, -1],
        ]
    )
    points /= np.linalg.norm(points, axis=1, keepdims=True)
    radius = 0.021335
    times = np.arange(3) * spacing_us * 1e-6
    positions = np.array([[-0.065 + 65 * t, 0, 0.65] for t in times])
    omega = np.array([0, 0, rpm * 2 * np.pi / 60])
    images = []
    yy, xx = np.indices((size[1], size[0]))
    rng = np.random.default_rng(7)
    for camera in cameras:
        rays = camera.rays(np.column_stack((xx.ravel(), yy.ravel())))
        image = np.full(len(rays), 15.0, float)
        for t, position in zip(times, positions):
            center = camera.R @ position + camera.t
            along = rays @ center
            disc = along**2 - (center @ center - radius**2)
            valid = disc > 0
            surface = (
                rays[valid] * (along[valid] - np.sqrt(disc[valid]))[:, None] - center
            ) / radius
            rotated = Rotation.from_rotvec(omega * t).apply(points) @ camera.R.T
            dots = np.max(surface @ rotated.T, axis=1) > np.cos(0.045)
            image[valid] = np.where(dots, 25, 220)
        image += rng.normal(0, noise, len(image))
        images.append(np.clip(image, 0, 255).astype(np.uint8).reshape(size[1], size[0]))
    timing = {
        "verified": True,
        "pulse_centers_us": (times * 1e6 + 100).tolist(),
        "pulse_widths_us": [8, 8, 8],
        "uncertainty_us": 1,
        "exposure_windows_us": [[0, float(times[-1] * 1e6 + 200)]] * 2,
        "source": "synthetic ground truth",
    }
    return images, cameras, timing, omega


def write_demo(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    images, cameras, timing, omega = make_capture(noise=1)
    for i, image in enumerate(images):
        cv2.imwrite(str(directory / f"camera{i}.png"), image)
    calibration = {
        "cameras": [
            {
                "K": c.K.tolist(),
                "distortion": c.distortion.tolist(),
                "R": c.R.tolist(),
                "t": c.t.tolist(),
                "image_size": c.image_size,
            }
            for c in cameras
        ]
    }
    (directory / "calibration.json").write_text(json.dumps(calibration, indent=2))
    (directory / "shot.json").write_text(
        json.dumps(
            {
                "shot_id": "synthetic-3000rpm",
                "images": ["camera0.png", "camera1.png"],
                "calibration": "calibration.json",
                "timing": timing,
                "ground_truth_omega_rad_s": omega.tolist(),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory")
    write_demo(parser.parse_args().directory)
