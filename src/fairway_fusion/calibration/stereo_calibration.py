"""Calibrated pinhole cameras; coordinates are metres in a shared world frame."""

from dataclasses import dataclass

import cv2
import numpy as np
from scipy.optimize import least_squares

from fairway_fusion.errors import MeasurementRejected


@dataclass
class CameraCalibration:
    K: np.ndarray
    distortion: np.ndarray
    R: np.ndarray  # world -> camera
    t: np.ndarray
    image_size: tuple[int, int]  # width, height; must match calibration resolution

    def __post_init__(self):
        self.K = np.asarray(self.K, dtype=float)
        self.distortion = np.asarray(self.distortion, dtype=float).reshape(-1)
        self.R = np.asarray(self.R, dtype=float)
        self.t = np.asarray(self.t, dtype=float).reshape(3)
        if (
            self.K.shape != (3, 3)
            or self.R.shape != (3, 3)
            or not all(
                np.isfinite(x).all() for x in (self.K, self.distortion, self.R, self.t)
            )
            or min(self.K[0, 0], self.K[1, 1]) <= 0
            or not np.allclose(self.K[2], [0, 0, 1])
            or not np.allclose(self.R @ self.R.T, np.eye(3), atol=1e-6)
            or not np.isclose(np.linalg.det(self.R), 1)
            or self.distortion.size not in (4, 5, 8, 12, 14)
            or len(self.image_size) != 2
            or min(self.image_size) <= 0
        ):
            raise ValueError("Invalid camera calibration")

    @classmethod
    def from_dict(cls, value):
        return cls(**value)

    def rays(self, pixels):
        if np.asarray(pixels).size == 0:
            return np.empty((0, 3))
        xy = cv2.undistortPoints(
            np.asarray(pixels, np.float64).reshape(-1, 1, 2), self.K, self.distortion
        ).reshape(-1, 2)
        rays = np.column_stack((xy, np.ones(len(xy))))
        return rays / np.linalg.norm(rays, axis=1, keepdims=True)

    def sphere_center(self, contour, radius_m):
        """Fit rays tangent to a sphere, avoiding the off-axis ellipse-centre bias."""
        rays = self.rays(contour)
        if len(rays) < 12 or radius_m <= 0:
            raise MeasurementRejected("Insufficient ball outline")
        direction = rays.mean(axis=0)
        direction /= np.linalg.norm(direction)
        angle = np.median(np.linalg.norm(np.cross(rays, direction), axis=1))
        if angle < 1e-6:
            raise MeasurementRejected("Ball is too small")
        initial = direction * radius_m / angle
        fit = least_squares(
            lambda c: np.linalg.norm(np.cross(rays, c), axis=1) - radius_m,
            initial,
            loss="soft_l1",
            f_scale=radius_m * 0.01,
        )
        if (
            not fit.success
            or fit.x[2] <= radius_m
            or np.sqrt(np.mean(fit.fun**2)) > radius_m * 0.04
        ):
            raise MeasurementRejected("Ball outline is not a clean sphere")
        return fit.x

    def surface_normals(self, pixels, center, radius_m, min_facing=0.3):
        rays = self.rays(pixels)
        along = rays @ center
        disc = along**2 - (center @ center - radius_m**2)
        good = disc > 0
        distances = along - np.sqrt(np.maximum(disc, 0))
        normals = (rays * distances[:, None] - center) / radius_m
        good &= (distances > 0) & (-np.sum(normals * rays, axis=1) > min_facing)
        # Row-vector camera -> world rotation.
        return normals[good] @ self.R

    def world_center(self, camera_center):
        return self.R.T @ (camera_center - self.t)
