"""Rotation-invariant triangle matching with one-to-one consensus and ambiguity rejection."""

from dataclasses import dataclass
from itertools import combinations, permutations

import numpy as np
from scipy.optimize import linear_sum_assignment
from scipy.spatial.transform import Rotation

from fairway_fusion.errors import MeasurementRejected


@dataclass
class RotationMatch:
    matrix: np.ndarray
    inliers: int
    rms_rad: float


def fit_rotation(source, target):
    u, _, vt = np.linalg.svd(source.T @ target)
    return vt.T @ np.diag([1, 1, np.linalg.det(vt.T @ u.T)]) @ u.T


def match_rotation(source, target, tolerance_rad=0.055, min_inliers=4, max_markers=16):
    """Find a proper 3D rotation; tolerate missing dots and a few false detections.

    Source/target are unit outward sphere normals in the SAME coordinate frame.
    At least four shared asymmetric dots must survive occlusion. Complexity is
    bounded by max_markers; excess detections are rejected, never silently sampled.
    """
    source, target = np.asarray(source, float), np.asarray(target, float)
    for points in (source, target):
        if (
            points.ndim != 2
            or points.shape[1] != 3
            or not np.isfinite(points).all()
            or not np.allclose(np.linalg.norm(points, axis=1), 1, atol=1e-5)
        ):
            raise ValueError("Markers must be finite unit 3D vectors")
        if len(points) < min_inliers:
            raise MeasurementRejected("Too few visible dots for spin")
        if len(points) > max_markers:
            raise MeasurementRejected(
                "Too many dot candidates; tune lighting/thresholds"
            )
    if min_inliers < 4 or not 0 < tolerance_rad < 0.2:
        raise ValueError("Require >=4 dots and a small angular tolerance")
    target_ids = np.array(list(permutations(range(len(target)), 3)))
    triples = target[target_ids]
    target_edges = np.stack(
        [
            np.linalg.norm(triples[:, i] - triples[:, j], axis=1)
            for i, j in ((0, 1), (0, 2), (1, 2))
        ],
        axis=1,
    )
    candidates = []

    def score(matrix):
        distances = np.linalg.norm(
            (source @ matrix.T)[:, None, :] - target[None, :, :], axis=2
        )
        # Dummy assignments allow unmatched dots without sacrificing valid pairs.
        cost = np.column_stack(
            (distances, np.full((len(source), len(source)), tolerance_rad))
        )
        rows, cols = linear_sum_assignment(cost)
        good = (cols < len(target)) & (cost[rows, cols] < tolerance_rad)
        return rows[good], cols[good]

    for ids in combinations(range(len(source)), 3):
        triangle = source[list(ids)]
        if np.linalg.svd(triangle, compute_uv=False)[1] < 0.12:
            continue
        edges = np.array(
            [
                np.linalg.norm(triangle[i] - triangle[j])
                for i, j in ((0, 1), (0, 2), (1, 2))
            ]
        )
        for index in np.flatnonzero(
            np.max(np.abs(target_edges - edges), axis=1) < 2 * tolerance_rad
        ):
            matrix = fit_rotation(triangle, triples[index])
            rows, cols = score(matrix)
            if len(rows) < min_inliers:
                continue
            matrix = fit_rotation(source[rows], target[cols])
            rows, cols = score(matrix)
            if (
                len(rows) < min_inliers
                or np.linalg.svd(source[rows], compute_uv=False)[1] < 0.15
            ):
                continue
            residual = np.linalg.norm(source[rows] @ matrix.T - target[cols], axis=1)
            rms = float(
                np.sqrt(np.mean((2 * np.arcsin(np.clip(residual / 2, 0, 1))) ** 2))
            )
            candidate = RotationMatch(matrix, len(rows), rms)
            for position, existing in enumerate(candidates):
                if (
                    Rotation.from_matrix(matrix @ existing.matrix.T).magnitude()
                    < tolerance_rad
                ):
                    if (-candidate.inliers, candidate.rms_rad) < (
                        -existing.inliers,
                        existing.rms_rad,
                    ):
                        candidates[position] = candidate
                    break
            else:
                candidates.append(candidate)
    if not candidates:
        raise MeasurementRejected("No consistent dot correspondence")
    candidates.sort(key=lambda c: (-c.inliers, c.rms_rad))
    best = candidates[0]
    for alternative in candidates[1:]:
        if (
            alternative.inliers == best.inliers
            and alternative.rms_rad <= best.rms_rad + tolerance_rad * 0.3
        ):
            raise MeasurementRejected("Ambiguous dot pattern; use more asymmetric dots")
    return best
