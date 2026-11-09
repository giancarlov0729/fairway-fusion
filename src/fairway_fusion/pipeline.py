"""Measure a launch from two cameras imaging the same two flashes."""

import numpy as np

from fairway_fusion.analysis.launch_angle import launch_metrics
from fairway_fusion.analysis.velocity import estimate_velocity
from fairway_fusion.calibration.stereo_calibration import triangulate_pair
from fairway_fusion.detection.ball_detector import detect_ball_centers


def analyze_stereo_shot(
    image_a, image_b, camera_a, camera_b, flash_interval_s, *, diagnostics=None
):
    """Return a launch result or rejection for a pair of uint8 monochrome images.

    Supply calibrations in camera order and the optical interval in seconds.
    Temporal ordering assumes a forward shot along world +X.
    Pass a dict to collect detected centers and accepted matches for inspection.
    """
    detections = [[], []]
    if diagnostics is not None:
        diagnostics.update(detected_centers_px=detections, matched_centers_px=[])
    if not np.isfinite(flash_interval_s) or flash_interval_s <= 0:
        return {"status": "rejected", "reason": "Invalid optical flash interval"}

    rejection = None
    for index, (image, camera) in enumerate(((image_a, camera_a), (image_b, camera_b))):
        name = "AB"[index]
        if image is None:
            rejection = rejection or f"Missing camera {name} image"
            continue
        if image.dtype != np.uint8 or image.shape != camera.image_size[::-1]:
            rejection = rejection or f"Camera {name} requires a calibrated-size 8-bit monochrome image"
            continue
        detections[index] = detect_ball_centers(image)
        count = len(detections[index])
        if count != 2:
            rejection = rejection or f"Camera {name}: expected two isolated ball images, found {count}"
    if rejection:
        return {"status": "rejected", "reason": rejection}

    centers_a, centers_b = detections
    matches = []
    for ordered_b in (centers_b, centers_b[::-1]):
        pairs = []
        for a, b in zip(centers_a, ordered_b):
            reconstructed = triangulate_pair(a, b, camera_a, camera_b)
            if reconstructed is None or reconstructed[1] > 2.0:
                break
            point, error_px = reconstructed
            pairs.append((point, error_px, (a, b)))
        if len(pairs) == 2:
            matches.append(sorted(pairs, key=lambda pair: pair[0][0]))

    if len(matches) != 1:
        reason = "Ambiguous stereo match" if matches else "No valid stereo match"
        return {"status": "rejected", "reason": reason}

    points, errors_px, matched_centers = zip(*matches[0])
    if points[1][0] - points[0][0] < 0.001:
        return {"status": "rejected", "reason": "Insufficient downrange separation"}

    times_s = [0.0, float(flash_interval_s)]
    try:
        velocity = estimate_velocity(points, times_s)
    except ValueError as error:
        return {"status": "rejected", "reason": str(error)}
    if diagnostics is not None:
        diagnostics["matched_centers_px"] = matched_centers
    return {
        "status": "accepted",
        "positions_m": [point.tolist() for point in points],
        "timestamps_s": times_s,
        "launch": launch_metrics(velocity),
        "reprojection_errors_px": list(errors_px),
    }
