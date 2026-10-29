import cv2
import numpy as np

from fairway_fusion.detection.ball_detector import detect_ball


def create_test_image(center=(320, 240), radius=40):
    image = np.zeros((480, 640, 3), dtype=np.uint8)

    cv2.circle(
        image,
        center,
        radius,
        (255, 255, 255),
        -1
    )

    return image


def test_ball_detection():

    image = create_test_image()

    result = detect_ball(image)

    assert result is not None

    x, y = result["center"]

    assert abs(x - 320) <= 2
    assert abs(y - 240) <= 2

    assert abs(result["radius"] - 40) <= 2


def test_no_ball():

    image = np.zeros((480, 640, 3), dtype=np.uint8)

    result = detect_ball(image)

    assert result is None


def test_ball_at_different_position():

    image = create_test_image(center=(150, 100), radius=30)

    result = detect_ball(image)

    assert result is not None

    x, y = result["center"]

    assert abs(x - 150) <= 2
    assert abs(y - 100) <= 2


def test_ball_outside_radius_range():

    image = create_test_image(radius=40)

    result = detect_ball(
        image,
        min_radius=50,
        max_radius=100
    )

    assert result is None