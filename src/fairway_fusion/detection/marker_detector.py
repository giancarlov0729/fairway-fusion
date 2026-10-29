"""Dark ink-dot centroids inside a detected ball; no fixed marker IDs needed."""

import cv2
import numpy as np


def detect_markers(image, ball, contrast=35, min_area=3, max_area_fraction=0.025):
    yy, xx = np.ogrid[: image.shape[0], : image.shape[1]]
    interior = (xx - ball.center[0]) ** 2 + (yy - ball.center[1]) ** 2 < (
        0.93 * ball.radius
    ) ** 2
    white = float(np.percentile(image[interior], 80))
    mask = ((image < white - contrast) & interior).astype(np.uint8)
    count, _, stats, centers = cv2.connectedComponentsWithStats(mask)
    points = []
    for i in range(1, count):
        _x, _y, w, h, area = stats[i]
        if (
            min_area <= area <= max_area_fraction * np.pi * ball.radius**2
            and min(w, h) / max(w, h) >= 0.35
        ):
            points.append(centers[i])
    return np.asarray(points, float).reshape(-1, 2)
