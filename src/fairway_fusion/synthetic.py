"""Small hardware-free fixtures for the two-flash vision prototype."""

import cv2
import numpy as np

from fairway_fusion.calibration.stereo_calibration import CameraCalibration


DEFAULT_POSITIONS_M = [[0, 0, 0.10], [0.12, -0.008, 0.13]]
CASE_POSITIONS_M = {
    "clean": DEFAULT_POSITIONS_M,
    "leftward": [[0, 0, 0.10], [0.12, 0.012, 0.13]],
    "shallow": [[0, 0, 0.15], [0.12, 0, 0.152]],
    "blur": DEFAULT_POSITIONS_M,
    "uneven-light": DEFAULT_POSITIONS_M,
    "clutter": DEFAULT_POSITIONS_M,
}


def make_stereo_fixture(positions_m=None, *, case="clean"):
    """Render bright disks at known world positions in two monochrome images."""
    if positions_m is None:
        positions_m = CASE_POSITIONS_M[case]
    positions_m = np.asarray(positions_m, dtype=float)
    intrinsics = np.array([[540.0, 0, 320], [0, 540.0, 240], [0, 0, 1]])
    # Camera looks along world +Y, with image right = +X and image down = -Z.
    rotation = np.array([[1.0, 0, 0], [0, 0, -1], [0, 1, 0]])
    cameras = [
        CameraCalibration(intrinsics, np.zeros(5), rotation,
                          -rotation @ np.array([x, -0.8, 0.15]), (640, 480))
        for x in (-0.1, 0.1)
    ]
    images = []
    for camera in cameras:
        image = np.full((480, 640), 20, dtype=np.uint8)
        depths = (positions_m @ camera.R.T + camera.t)[:, 2]
        for index, (pixel, depth) in enumerate(zip(camera.project(positions_m), depths)):
            radius_px = intrinsics[0, 0] * 0.02135 / depth
            # OpenCV shift retains fractional-pixel centers in the fixture.
            center = tuple(np.rint(pixel * 16).astype(int))
            brightness = 135 if case == "uneven-light" and index == 1 else 230
            cv2.circle(image, center, round(radius_px * 16), brightness, -1, shift=4)
        if case == "blur":
            image = cv2.GaussianBlur(image, (9, 9), 2)
        if case == "clutter":
            cv2.circle(image, (125, 150), 14, 210, -1)
        images.append(image)
    return images, cameras, positions_m
