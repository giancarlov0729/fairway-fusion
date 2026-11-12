"""Adapt paired camera images to a serializable vision and carry result."""

from numbers import Real

import numpy as np

from fairway_fusion.analysis.carry import Assumptions, carry_metrics
from fairway_fusion.calibration.stereo_calibration import CameraCalibration
from fairway_fusion.pipeline import analyze_stereo_shot


def _plain(value):
    if isinstance(value, np.ndarray):
        return _plain(value.tolist())
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _camera(camera, name):
    if not isinstance(camera, CameraCalibration):
        raise ValueError(f"Camera {name} requires CameraCalibration.")
    try:
        K, R, t, distortion = (
            np.array(value, dtype=float, copy=True)
            for value in (camera.K, camera.R, camera.t, camera.distortion)
        )
        size = tuple(camera.image_size)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"Camera {name} has invalid calibration values.") from error
    if (K.shape != (3, 3) or R.shape != (3, 3) or t.shape not in {(3,), (3, 1), (1, 3)}
            or distortion.ndim not in {1, 2} or distortion.size not in {4, 5, 8, 12, 14}
            or (distortion.ndim == 2 and 1 not in distortion.shape)
            or not all(np.isfinite(value).all() for value in (K, R, t, distortion))):
        raise ValueError(f"Camera {name} has invalid calibration shapes or non-finite values.")
    if (K[0, 0] <= 0 or K[1, 1] <= 0 or abs(np.linalg.det(K)) < 1e-12
            or not np.allclose(K[2], [0, 0, 1], atol=1e-8, rtol=0)
            or not np.allclose(R @ R.T, np.eye(3), atol=1e-6, rtol=0)
            or not np.isclose(np.linalg.det(R), 1, atol=1e-6, rtol=0)):
        raise ValueError(f"Camera {name} requires valid intrinsics and a proper rotation.")
    if (len(size) != 2 or any(isinstance(v, (bool, np.bool_))
            or not isinstance(v, (int, np.integer)) or v <= 0 for v in size)):
        raise ValueError(f"Camera {name} requires a positive integer image size.")
    return CameraCalibration(K, distortion.reshape(-1), R, t.reshape(3), tuple(map(int, size)))


def analyze_image_pair(image_a, image_b, camera_a, camera_b, flash_interval_s, *,
                       source, timing_source="commanded", carry_assumptions=None):
    """Process one stereo pair without saving files, records, or hardware state.

    Known invalid image/calibration/timing inputs return a rejected result.
    Invalid provenance/settings and unexpected processing errors raise, so a
    future caller can distinguish configuration bugs from rejected shots.
    """
    if source not in {"simulation", "live", "replay"}:
        raise ValueError("source must be simulation, live, or replay.")
    if timing_source not in {"commanded", "measured"}:
        raise ValueError("timing_source must be commanded or measured.")
    if carry_assumptions is not None and not isinstance(carry_assumptions, Assumptions):
        raise TypeError("carry_assumptions must be Assumptions or None.")
    result = {
        "status": "rejected", "reason": None, "source": source,
        "timing": {"flash_interval_s": None, "timing_source": timing_source},
        "samples": None, "launch": None, "carry": None,
        "reprojection_errors_px": [],
        "diagnostics": {"detected_centers_px": [[], []], "matched_centers_px": []},
    }
    try:
        interval = float(flash_interval_s)
    except (TypeError, ValueError, OverflowError):
        interval = float("nan")
    if (isinstance(flash_interval_s, bool) or not isinstance(flash_interval_s, Real)
            or not np.isfinite(interval) or interval <= 0):
        return {**result, "reason": "Invalid optical flash interval"}
    result["timing"]["flash_interval_s"] = interval
    try:
        cameras = [_camera(camera_a, "A"), _camera(camera_b, "B")]
    except ValueError as error:
        return {**result, "reason": str(error)}
    for image, name in ((image_a, "A"), (image_b, "B")):
        if image is not None and not isinstance(image, np.ndarray):
            return {**result, "reason": f"Camera {name} requires a NumPy image array."}
    vision = analyze_stereo_shot(image_a, image_b, *cameras, interval,
                                 diagnostics=result["diagnostics"])
    if vision["status"] == "rejected":
        result["reason"] = vision["reason"]
    elif vision["status"] == "accepted":
        result.update(
            status="accepted",
            samples={"positions_m": vision["positions_m"],
                     "timestamps_s": vision["timestamps_s"]},
            launch=vision["launch"],
            carry=carry_metrics(vision["launch"], carry_assumptions),
            reprojection_errors_px=vision["reprojection_errors_px"],
        )
    else:
        raise RuntimeError("Unexpected vision pipeline status.")
    return _plain(result)
