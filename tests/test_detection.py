import cv2
import numpy as np
import pytest

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


def test_moving_blurred_ball_wins_over_stationary_circle():
    previous = create_test_image(center=(115, 125), radius=38)
    cv2.circle(previous, (240, 290), 22, (220, 220, 220), -1)
    frame = create_test_image(center=(115, 125), radius=38)
    ball = np.zeros_like(frame)
    cv2.circle(ball, (310, 290), 22, (220, 220, 220), -1)
    blur = np.full((1, 15), 1 / 15, dtype=np.float32)
    frame = cv2.add(frame, cv2.filter2D(ball, -1, blur))

    result = detect_ball(frame, max_radius=50, previous=previous, prior=(240, 290, 22))

    assert result is not None
    assert np.linalg.norm(np.array(result["center"]) - (310, 290)) <= 4
    assert abs(result["radius"] - 22) <= 4


def test_prior_position_requires_evidence_in_current_frame(monkeypatch):
    monkeypatch.setattr(cv2, "HoughCircles", lambda *args, **kwargs: None)
    table = np.zeros((120, 240), dtype=np.uint8)
    table[65:] = 200
    frame = table.copy()
    cv2.circle(frame, (120, 56), 20, 220, -1)
    prior = (120, 56, 20)

    assert detect_ball(frame, max_radius=35) is None
    assert detect_ball(frame, max_radius=35, prior=prior)["center"] == (120, 56)
    assert detect_ball(table, max_radius=35, prior=prior) is None


def test_airborne_ball_can_be_proposed_from_its_new_silhouette(monkeypatch):
    scene = np.full((180, 240), 115, dtype=np.uint8)
    cv2.circle(scene, (177, 70), 30, 200, -1)
    previous = scene.copy()
    cv2.circle(previous, (78, 39), 17, 160, -1)
    frame = scene.copy()
    cv2.circle(frame, (86, 94), 17, 160, -1)

    def unexpected_hough(*args, **kwargs):
        pytest.fail("A clean moving silhouette should not need a full-frame circle search.")

    monkeypatch.setattr(cv2, "HoughCircles", unexpected_hough)
    ball = detect_ball(frame, 12, 40, previous=previous, prior=(78, 39, 17))
    assert ball is not None
    assert np.linalg.norm(np.array(ball["center"]) - (86, 94)) <= 2
