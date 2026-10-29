"""Calibrate from paired chessboard images and an explicitly measured world pose."""

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from fairway_fusion.calibration.stereo_calibration import CameraCalibration


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "pairs", type=Path, help="JSON list of [left image, right image] paths"
    )
    parser.add_argument("--columns", type=int, required=True, help="Inner corners")
    parser.add_argument("--rows", type=int, required=True)
    parser.add_argument("--square-m", type=float, required=True)
    parser.add_argument(
        "--world-to-camera0",
        type=Path,
        required=True,
        help='JSON {"R":3x3,"t":[x,y,z]}',
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-rms-px", type=float, default=1.0)
    args = parser.parse_args()
    if min(args.columns, args.rows) < 3 or args.square_m <= 0:
        parser.error(
            "Require >=3 inner corners per dimension and a positive square size"
        )
    points = np.zeros((args.columns * args.rows, 3), np.float32)
    points[:, :2] = (
        np.mgrid[: args.columns, : args.rows].T.reshape(-1, 2) * args.square_m
    )
    objects, left, right = [], [], []
    size = None
    for paths in json.loads(args.pairs.read_text()):
        if len(paths) != 2:
            parser.error("Every pair must contain exactly two image paths")
        found = []
        for path in paths:
            image = cv2.imread(str(args.pairs.parent / path), cv2.IMREAD_GRAYSCALE)
            if image is None:
                parser.error(f"Unreadable image: {path}")
            current = image.shape[::-1]
            if size is not None and current != size:
                parser.error("Calibration images must have identical dimensions")
            size = current
            ok, corners = cv2.findChessboardCornersSB(image, (args.columns, args.rows))
            found.append(corners if ok else None)
        if all(p is not None for p in found):
            objects.append(points.copy())
            left.append(found[0])
            right.append(found[1])
    if len(objects) < 10:
        parser.error(
            "Need at least 10 valid paired board views with varied position and tilt"
        )
    rms0, k0, d0, _, _ = cv2.calibrateCamera(objects, left, size, None, None)
    rms1, k1, d1, _, _ = cv2.calibrateCamera(objects, right, size, None, None)
    rms, _, _, _, _, r, t, _, _ = cv2.stereoCalibrate(
        objects,
        left,
        right,
        k0,
        d0,
        k1,
        d1,
        size,
        flags=cv2.CALIB_FIX_INTRINSIC,
        criteria=(cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 100, 1e-7),
    )
    if (
        not np.isfinite([rms0, rms1, rms]).all()
        or max(rms0, rms1, rms) > args.max_rms_px
    ):
        parser.error(
            f"Calibration RMS too large: {rms0:.3f}, {rms1:.3f}, stereo {rms:.3f} px"
        )
    pose = json.loads(args.world_to_camera0.read_text())
    r0, t0 = np.array(pose["R"]), np.array(pose["t"]).reshape(3)
    cameras = [
        CameraCalibration(k0, d0, r0, t0, size),
        CameraCalibration(k1, d1, r @ r0, r @ t0 + t.ravel(), size),
    ]
    output = {
        "world_axes": "X downrange, Y left, Z up",
        "rms_px": [rms0, rms1, rms],
        "accepted_pairs": len(objects),
        "cameras": [
            {
                "K": c.K.tolist(),
                "distortion": c.distortion.tolist(),
                "R": c.R.tolist(),
                "t": c.t.tolist(),
                "image_size": c.image_size,
            }
            for c in cameras
        ],
    }
    args.output.write_text(json.dumps(output, indent=2) + "\n")
    print(f"Saved {args.output}; stereo RMS {rms:.3f} px")


if __name__ == "__main__":
    main()
