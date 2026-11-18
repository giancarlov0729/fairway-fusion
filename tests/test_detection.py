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

def test_ball_touching_bright_table_with_clutter():
    image = np.full((480, 640, 3), 25, dtype=np.uint8)
    cv2.rectangle(image, (0, 360), (639, 479), (200, 200, 200), -1)
    cv2.circle(image, (320, 310), 50, (220, 220, 220), -1)
    cv2.putText(image, 'G', (300, 310), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (40, 40, 40), 2)
    cv2.circle(image, (130, 130), 80, (240, 240, 240), 9)
    cv2.circle(image, (500, 200), 15, (255, 255, 255), -1)
    result = detect_ball(image, max_radius=100)
    assert result is not None
    assert np.linalg.norm(np.array(result['center']) - (320, 310)) < 4
    assert abs(result['radius'] - 50) < 4


def test_hollow_ring_is_not_a_ball():
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.circle(image, (320, 240), 70, (255, 255, 255), 8)
    assert detect_ball(image) is None


def test_largest_filled_ball_wins():
    image = create_test_image(radius=60)
    cv2.circle(image, (100, 100), 20, (255, 255, 255), -1)
    result = detect_ball(image)
    assert result is not None
    assert abs(result['radius'] - 60) < 3


def test_bright_table_alone_is_not_a_ball():
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.rectangle(image, (0, 340), (639, 479), (220, 220, 220), -1)
    assert detect_ball(image) is None
