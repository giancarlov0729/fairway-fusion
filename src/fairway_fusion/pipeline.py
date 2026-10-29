"""Replay a dual-camera, multi-flash exposure and retain rejection reasons."""

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
import yaml

from fairway_fusion.analysis.launch_angle import launch_metrics
from fairway_fusion.analysis.spin import angular_velocity, spin_metrics
from fairway_fusion.analysis.velocity import estimate_velocity
from fairway_fusion.calibration.stereo_calibration import CameraCalibration
from fairway_fusion.cameras.synchronization import validate_timing
from fairway_fusion.detection.ball_detector import detect_balls, order_pulses
from fairway_fusion.detection.marker_detector import detect_markers
from fairway_fusion.errors import MeasurementRejected
from fairway_fusion.tracking.marker_tracker import match_rotation


def analyze_shot(images, calibration, timing, config):
    result = {
        "status": "rejected",
        "launch": None,
        "spin": None,
        "reasons": [],
        "quality": {},
    }
    try:
        times, uncertainty = validate_timing(timing)
        if len(images) != 2 or len(calibration) != 2:
            raise ValueError("Exactly two calibrated camera images are required")
        radius = float(config.get("ball_radius_m", 0.021335))
        if not np.isfinite(radius) or radius <= 0:
            raise ValueError("Invalid physical ball radius")
        positions, normals = [], []
        for index, (image, camera) in enumerate(zip(images, calibration)):
            if image is None or image.shape[::-1] != tuple(camera.image_size):
                raise MeasurementRejected(
                    "Image dimensions do not match camera calibration"
                )
            balls = order_pulses(
                detect_balls(image, **config.get("ball_detection", {})),
                config["flight_directions"][index],
                len(times),
            )
            centers = [camera.sphere_center(ball.contour, radius) for ball in balls]
            positions.append(np.array([camera.world_center(c) for c in centers]))
            normals.append(
                [
                    camera.surface_normals(
                        detect_markers(image, b, **config.get("marker_detection", {})),
                        c,
                        radius,
                    )
                    for b, c in zip(balls, centers)
                ]
            )
        disagreement = np.linalg.norm(positions[0] - positions[1], axis=1)
        result["quality"]["stereo_center_disagreement_m"] = disagreement.tolist()
        if np.max(disagreement) > config.get("max_center_disagreement_m", 0.005):
            raise MeasurementRejected(
                "Stereo ball centres disagree; check calibration and pulse order"
            )
        positions = np.mean(positions, axis=0)
        velocity = estimate_velocity(positions, times)
        result["launch"] = launch_metrics(velocity)
        result["positions_world_m"] = positions.tolist()
        try:
            omegas, diagnostics = [], []
            for i, dt in enumerate(np.diff(times)):
                pair = []
                for camera_index, camera_normals in enumerate(normals):
                    match = match_rotation(
                        camera_normals[i],
                        camera_normals[i + 1],
                        **config.get("matching", {}),
                    )
                    pair.append(
                        angular_velocity(
                            match.matrix, dt, config.get("max_rpm", 10000), uncertainty
                        )
                    )
                    diagnostics.append(
                        {
                            "interval": i,
                            "camera": camera_index,
                            "inliers": match.inliers,
                            "rms_rad": match.rms_rad,
                        }
                    )
                if np.linalg.norm(pair[0] - pair[1]) * dt > config.get(
                    "max_rotation_disagreement_rad", 0.08
                ):
                    raise MeasurementRejected("Stereo spin estimates disagree")
                omegas.append(np.mean(pair, axis=0))
            omega = np.average(omegas, axis=0, weights=np.diff(times))
            spread = np.max(np.linalg.norm(np.asarray(omegas) - omega, axis=1))
            if spread > max(30, 0.15 * np.linalg.norm(omega)):
                raise MeasurementRejected("Spin is inconsistent across flash intervals")
            result["spin"] = spin_metrics(omega)
            result["quality"]["matches"] = diagnostics
            result["status"] = "accepted"
        except MeasurementRejected as exc:
            result["status"] = "partial"
            result["reasons"].append(str(exc))
    except MeasurementRejected as exc:
        result["reasons"].append(str(exc))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--config", type=Path, default=Path("config/processing.yaml"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        manifest = json.loads(args.manifest.read_text())
        root = args.manifest.parent
        config = yaml.safe_load(args.config.read_text())
        calibration = json.loads((root / manifest["calibration"]).read_text())
        cameras = [CameraCalibration.from_dict(c) for c in calibration["cameras"]]
        images = [
            cv2.imread(str(root / p), cv2.IMREAD_GRAYSCALE) for p in manifest["images"]
        ]
        result = analyze_shot(images, cameras, manifest["timing"], config)
    except (ValueError, KeyError, TypeError, OSError, cv2.error) as exc:
        parser.exit(2, f"Input/configuration error: {exc}\n")
    result["shot_id"] = manifest.get("shot_id")
    output = json.dumps(result, indent=2, allow_nan=False)
    if args.output:
        args.output.write_text(output + "\n")
    print(output)
    return 0 if result["status"] == "accepted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
