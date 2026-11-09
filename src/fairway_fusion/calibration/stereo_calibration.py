"""Calibrated camera geometry in the golf coordinate frame."""

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class CameraCalibration:
    """OpenCV pinhole model. R and t map world meters to camera meters."""

    K: np.ndarray
    distortion: np.ndarray
    R: np.ndarray
    t: np.ndarray
    image_size: tuple[int, int]  # Width, height in pixels.

    @property
    def projection(self):
        return np.column_stack((self.R, self.t))

    def undistort(self, pixel):
        return cv2.undistortPoints(
            np.asarray(pixel, dtype=float).reshape(1, 1, 2), self.K, self.distortion
        ).reshape(2)

    def project(self, points_m):
        pixels, _ = cv2.projectPoints(
            np.asarray(points_m, dtype=float).reshape(-1, 3),
            cv2.Rodrigues(self.R)[0],
            self.t,
            self.K,
            self.distortion,
        )
        return pixels.reshape(-1, 2)


def triangulate_pair(pixel_a, pixel_b, camera_a, camera_b):
    """Return the world point and worst reprojection error, or None for invalid depth."""
    normalized_a = camera_a.undistort(pixel_a)
    normalized_b = camera_b.undistort(pixel_b)
    ray_a = camera_a.R.T @ np.append(normalized_a, 1)
    ray_b = camera_b.R.T @ np.append(normalized_b, 1)
    # Nearly parallel rays do not give reliable depth.
    ray_sine = np.linalg.norm(np.cross(ray_a, ray_b)) / (
        np.linalg.norm(ray_a) * np.linalg.norm(ray_b)
    )
    if ray_sine < np.sin(np.deg2rad(1)):
        return None

    point_h = cv2.triangulatePoints(
        camera_a.projection, camera_b.projection,
        normalized_a.reshape(2, 1), normalized_b.reshape(2, 1),
    ).ravel()
    if abs(point_h[3]) < 1e-12:
        return None
    point = point_h[:3] / point_h[3]
    if not np.isfinite(point).all():
        return None

    errors = []
    for pixel, camera in ((pixel_a, camera_a), (pixel_b, camera_b)):
        if (camera.R @ point + camera.t.reshape(3))[2] <= 0:
            return None
        errors.append(float(np.linalg.norm(camera.project(point)[0] - pixel)))
    if not np.isfinite(errors).all():
        return None
    return point, max(errors)
