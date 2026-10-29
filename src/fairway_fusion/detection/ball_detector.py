import cv2
import numpy as np


def detect_ball(frame, min_radius=5, max_radius=200):
    """
    Detect a golf ball in an image.

    Args:
        frame: Input image (BGR).
        min_radius: Minimum expected ball radius in pixels.
        max_radius: Maximum expected ball radius in pixels.

    Returns:
        Dictionary containing center and radius,
        or None if no ball is detected.
    """

    if frame is None:
        return None

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    blurred = cv2.GaussianBlur(gray, (5, 5), 0)

    _, binary = cv2.threshold(
        blurred,
        0,
        255,
        cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )

    contours, _ = cv2.findContours(
        binary,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE
    )

    best_ball = None
    best_score = 0

    for contour in contours:

        area = cv2.contourArea(contour)

        if area == 0:
            continue

        perimeter = cv2.arcLength(contour, True)

        if perimeter == 0:
            continue

        circularity = 4 * np.pi * area / (perimeter ** 2)

        (x, y), radius = cv2.minEnclosingCircle(contour)

        if not min_radius <= radius <= max_radius:
            continue

        if circularity < 0.75:
            continue

        score = circularity * area

        if score > best_score:
            best_score = score

            best_ball = {
                "center": (int(x), int(y)),
                "radius": int(radius),
                "circularity": float(circularity)
            }

    return best_ball