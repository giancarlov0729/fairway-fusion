import cv2
import numpy as np


def detect_ball(frame, min_radius=5, max_radius=200):
    """Return the largest supported bright, filled circle in the radius range.

    Edge-based proposals allow a ball to touch a bright tabletop without
    requiring its thresholded silhouette to be an isolated contour. This is
    a geometric bench detector, not semantic golf-ball recognition.
    """
    if frame is None:
        return None
    gray = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    candidates = []
    _, binary = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for contour in contours:
        perimeter = cv2.arcLength(contour, True)
        if perimeter and 4 * np.pi * cv2.contourArea(contour) / perimeter**2 >= 0.75:
            (x, y), radius = cv2.minEnclosingCircle(contour)
            candidates.append((x, y, radius))
    circles = cv2.HoughCircles(
        cv2.GaussianBlur(gray, (9, 9), 0), cv2.HOUGH_GRADIENT, dp=1, minDist=max(8, min_radius),
        param1=80, param2=24, minRadius=int(min_radius), maxRadius=int(max_radius),
    )
    if circles is not None:
        candidates.extend(circles[0])

    height, width = gray.shape
    angles = np.linspace(0, 2 * np.pi, 96, endpoint=False)
    directions = np.array([np.cos(angles), np.sin(angles)])
    best = None
    for x, y, radius in candidates:
        if not min_radius <= radius <= max_radius:
            continue
        if x - radius < 0 or y - radius < 0 or x + radius >= width or y + radius >= height:
            continue

        def ring(scale):
            points = np.array([[x], [y]]) + directions * radius * scale
            xx = np.clip(np.rint(points[0]).astype(int), 0, width - 1)
            yy = np.clip(np.rint(points[1]).astype(int), 0, height - 1)
            return blurred[yy, xx].astype(float)

        inside, outside = ring(0.82), ring(1.12)
        support = float(np.mean(inside - outside >= 12))
        # A tabletop can hide the bottom arc; most of the rim must still agree.
        if support < 0.65:
            continue
        core = np.concatenate([ring(scale) for scale in (0.2, 0.4, 0.6)])
        # Reject hollow logos/rings while allowing dark lettering on a ball.
        background = float(np.percentile(outside, 35))
        if np.mean(core > background + 15) < 0.70:
            continue
        if best is None or radius > best[0]:
            best = (radius, x, y, support)
    if best is None:
        return None
    radius, x, y, support = best
    return {"center": (int(round(x)), int(round(y))), "radius": int(round(radius)),
            "circularity": support}


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
