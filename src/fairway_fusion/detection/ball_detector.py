import cv2
import numpy as np


_ANGLES = np.linspace(0, 2 * np.pi, 96, endpoint=False)
_RING_X = np.cos(_ANGLES)
_RING_Y = np.sin(_ANGLES)


def _moving_ball(blurred, brighter, min_radius, max_radius):
    """Find a filled circle that has just appeared at a new position."""
    _, mask = cv2.threshold(brighter, 18, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    height, width = blurred.shape
    best_score = 0
    best = None
    for contour in contours:
        (x, y), radius = cv2.minEnclosingCircle(contour)
        if not min_radius <= radius <= max_radius:
            continue
        if min(x - radius, y - radius, width - 1 - x - radius, height - 1 - y - radius) < 0:
            continue
        occupancy = cv2.contourArea(contour) / (np.pi * radius**2)
        if occupancy < 0.62:
            continue

        def ring(scale, image):
            xx = np.clip(np.rint(x + radius * scale * _RING_X).astype(int), 0, width - 1)
            yy = np.clip(np.rint(y + radius * scale * _RING_Y).astype(int), 0, height - 1)
            return image[yy, xx].astype(np.int16)

        # Change alone also finds moving hands and lighting. Require evidence of
        # a brighter, filled disk on the current frame before accepting it.
        outside = ring(1.12, blurred)
        support = float(np.mean(ring(0.82, blurred) - outside >= 10))
        if support < 0.50:
            continue
        core = np.concatenate([ring(scale, blurred) for scale in (0.2, 0.4, 0.6)])
        if np.mean(core > np.percentile(outside, 35) + 15) < 0.70:
            continue
        contrast = np.median(ring(0.75, brighter))
        score = contrast * support * occupancy
        if contrast >= 25 and score > best_score:
            best_score = score
            fitted_radius = max(min_radius, radius * np.sqrt(occupancy))
            best = {"center": (round(x), round(y)), "radius": round(fitted_radius),
                    "circularity": support}
    return best


def detect_moving_ball(image, previous, min_radius, max_radius):
    """Check consecutive monochrome views for a moving ball without a circle scan."""
    blurred = cv2.GaussianBlur(image, (5, 5), 0)
    brighter = cv2.subtract(blurred, cv2.GaussianBlur(previous, (5, 5), 0))
    return _moving_ball(blurred, brighter, min_radius, max_radius)


def detect_ball(frame, min_radius=5, max_radius=200, *, previous=None, prior=None):
    """Return the best supported bright, filled circle in the radius range.

    A recent frame from the same camera favors candidates that have moved.
    An optional prior (x, y, radius) is checked against the current image
    like any other proposal, so a weak rim can still support a detection.
    """
    if frame is None:
        return None
    gray = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    brighter = None
    if previous is not None:
        previous_gray = previous if previous.ndim == 2 else cv2.cvtColor(previous, cv2.COLOR_BGR2GRAY)
        # Positive change ignores the old location when a bright ball moves away.
        brighter = cv2.subtract(blurred, cv2.GaussianBlur(previous_gray, (5, 5), 0))
        moving = _moving_ball(blurred, brighter, min_radius, max_radius)
        if moving is not None:
            return moving
    candidates = [prior] if prior is not None else []
    _, binary = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for contour in contours:
        perimeter = cv2.arcLength(contour, True)
        if perimeter and 4 * np.pi * cv2.contourArea(contour) / perimeter**2 >= 0.75:
            (x, y), radius = cv2.minEnclosingCircle(contour)
            candidates.append((x, y, radius))
    # Circle proposals recover rims that merge with the tabletop in the thresholded
    # image. Use both gradient modes because weak or blurred rims can be incomplete.
    for method, dp, edge_threshold, circle_threshold in (
        (cv2.HOUGH_GRADIENT, 1, 60, 16),
        (cv2.HOUGH_GRADIENT_ALT, 1.5, 90, 0.65),
    ):
        circles = cv2.HoughCircles(
            blurred, method, dp=dp, minDist=max(8, min_radius // 2),
            param1=edge_threshold, param2=circle_threshold,
            minRadius=int(min_radius), maxRadius=int(max_radius),
        )
        if circles is not None:
            candidates.extend(circles[0])
    if not candidates:
        return None

    centers = np.asarray(candidates, dtype=np.float32)
    x, y, radius = centers.T
    height, width = gray.shape
    in_bounds = ((radius >= min_radius) & (radius <= max_radius)
                 & (x - radius >= 0) & (x + radius < width)
                 & (y - radius >= 0) & (y + radius < height))
    x, y, radius = centers[in_bounds].T
    if not radius.size:
        return None

    def ring(scale, image=blurred):
        distance = radius[:, np.newaxis] * scale
        xx = np.clip(np.rint(x[:, np.newaxis] + _RING_X * distance).astype(int), 0, width - 1)
        yy = np.clip(np.rint(y[:, np.newaxis] + _RING_Y * distance).astype(int), 0, height - 1)
        return image[yy, xx].astype(np.int16)

    inside, outside = ring(0.82), ring(1.12)
    support = np.mean(inside - outside >= 10, axis=1)
    sharpness = np.mean(ring(0.93) - ring(1.07) >= 8, axis=1)
    # The bottom arc can blend into the surface. The core still needs to be
    # bright and filled, which rules out bright rings and hollow logos.
    core = np.concatenate([ring(scale) for scale in (0.2, 0.4, 0.6)], axis=1)
    background = np.percentile(outside, 35, axis=1)
    fill = np.mean(core > background[:, np.newaxis] + 15, axis=1)
    valid = (support >= 0.58) & (fill >= 0.70)
    if not valid.any():
        return None

    # Favor stronger edges and use size to distinguish similarly supported circles.
    score = (support + 0.25 * sharpness + 0.25 * fill) * radius**0.20
    if brighter is not None:
        changed = np.mean(ring(0.82, brighter) >= 15, axis=1)
        score *= 1 + changed
    index = np.argmax(np.where(valid, score, -np.inf))
    return {
        "center": (int(round(x[index])), int(round(y[index]))),
        "radius": int(round(radius[index])),
        "circularity": float(support[index]),
    }


def detect_ball_centers(image, *, threshold=100, min_radius=5, max_radius=200):
    """Find isolated bright ball silhouettes in a uint8 monochrome image."""
    if image.ndim != 2 or image.dtype != np.uint8:
        raise ValueError("Expected an 8-bit monochrome image")

    _, mask = cv2.threshold(image, threshold, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    height, width = image.shape
    centers = []
    for contour in contours:
        (x, y), radius = cv2.minEnclosingCircle(contour)
        if not min_radius <= radius <= max_radius:
            continue
        if min(x - radius, y - radius, width - 1 - x - radius, height - 1 - y - radius) <= 0:
            continue

        area = cv2.contourArea(contour)
        # Reject merged or irregular silhouettes instead of splitting them.
        if area >= 0.82 * np.pi * radius**2:
            centers.append((x, y))
    return centers
