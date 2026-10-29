"""Detect isolated, strobed white balls against a darker background."""

from dataclasses import dataclass

import cv2
import numpy as np

from fairway_fusion.errors import MeasurementRejected


@dataclass
class Ball:
    center: np.ndarray
    radius: float
    contour: np.ndarray


def detect_balls(image, threshold=100, min_radius=12, max_radius=250):
    if image is None or image.dtype != np.uint8 or image.ndim != 2:
        raise ValueError("Expected an 8-bit grayscale image")
    mask = cv2.threshold(image, threshold, 255, cv2.THRESH_BINARY)[1]
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    balls = []
    height, width = image.shape
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < np.pi * min_radius**2 or len(contour) < 12:
            continue
        (x, y), radius = cv2.minEnclosingCircle(contour)
        if not min_radius <= radius <= max_radius:
            continue
        perimeter = cv2.arcLength(contour, True)
        # Reject clipped and merged pulse images rather than guessing centres.
        if (
            4 * np.pi * area / perimeter**2 < 0.78
            or area / (np.pi * radius**2) < 0.82
            or x - radius <= 1
            or y - radius <= 1
            or x + radius >= width - 2
            or y + radius >= height - 2
        ):
            continue
        balls.append(Ball(np.array([x, y]), radius, contour.reshape(-1, 2)))
    return balls


def order_pulses(balls, direction, expected_count):
    direction = np.asarray(direction, float)
    if (
        direction.shape != (2,)
        or not np.isfinite(direction).all()
        or np.linalg.norm(direction) == 0
    ):
        raise ValueError("flight_direction must be a nonzero 2D vector")
    if len(balls) != expected_count:
        raise MeasurementRejected(
            f"Expected {expected_count} separated ball images; found {len(balls)}"
        )
    balls = sorted(balls, key=lambda b: b.center @ direction)
    for i, first in enumerate(balls):
        for second in balls[i + 1 :]:
            if np.linalg.norm(first.center - second.center) <= 1.05 * (
                first.radius + second.radius
            ):
                raise MeasurementRejected(
                    "Strobe images overlap or have insufficient separation"
                )
    return balls
