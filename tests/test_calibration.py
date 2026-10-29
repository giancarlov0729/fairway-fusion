import cv2
import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from fairway_fusion.calibration.stereo_calibration import CameraCalibration


def test_distorted_off_axis_sphere_and_world_normals():
    r = Rotation.from_euler("y", 20, degrees=True).as_matrix()
    camera = CameraCalibration(
        [[900, 0, 640], [0, 920, 480], [0, 0, 1]],
        [-0.05, 0.01, 0, 0, 0],
        r,
        [0.02, 0.01, 0],
        (1280, 960),
    )
    center = np.array([0.12, 0.08, 0.6])
    radius = 0.021335
    axis = center / np.linalg.norm(center)
    u = np.cross(axis, [0, 1, 0])
    u /= np.linalg.norm(u)
    v = np.cross(axis, u)
    angle = np.arcsin(radius / np.linalg.norm(center))
    theta = np.linspace(0, 2 * np.pi, 120, endpoint=False)
    rays = np.cos(angle) * axis + np.sin(angle) * (
        np.cos(theta)[:, None] * u + np.sin(theta)[:, None] * v
    )
    pixels = cv2.projectPoints(
        rays, np.zeros(3), np.zeros(3), camera.K, camera.distortion
    )[0].reshape(-1, 2)
    assert camera.sphere_center(pixels, radius) == pytest.approx(center, abs=1e-6)
    normals = np.array([[0, 0, -1], [0.2, 0, -np.sqrt(0.96)]])
    points = center + radius * normals
    pixels = cv2.projectPoints(
        points, np.zeros(3), np.zeros(3), camera.K, camera.distortion
    )[0].reshape(-1, 2)
    assert camera.surface_normals(pixels, center, radius) == pytest.approx(
        normals @ r, abs=1e-6
    )


def test_reflection_is_not_a_camera_rotation():
    with pytest.raises(ValueError):
        CameraCalibration(
            np.eye(3), np.zeros(5), np.diag([1, 1, -1]), np.zeros(3), (100, 100)
        )
