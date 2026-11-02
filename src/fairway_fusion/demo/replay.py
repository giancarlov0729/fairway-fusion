"""Replay post-CV sensor observations; never consume expected metrics as inputs."""

import time
import numpy as np
from fairway_fusion.analysis.flight import FlightConditions, project_flight
from fairway_fusion.analysis.launch_angle import launch_metrics
from fairway_fusion.analysis.spin import angular_velocity, spin_metrics
from fairway_fusion.analysis.velocity import estimate_velocity
from fairway_fusion.errors import MeasurementRejected


def finite_array(value, shape, label):
    array = np.asarray(value, float)
    if array.shape != shape or not np.isfinite(array).all():
        raise ValueError(f"{label} must be finite with shape {shape}")
    return array


def rotation(value):
    r = finite_array(value, (3, 3), "rotation")
    if not np.allclose(r @ r.T, np.eye(3), atol=1e-6) or not np.isclose(
        np.linalg.det(r), 1
    ):
        raise ValueError("Orientation must be a proper rotation matrix")
    return r


def process_observations(packet, conditions=None):
    started = time.perf_counter()
    result = {
        "status": "rejected",
        "launch": None,
        "spin": None,
        "flight": None,
        "reasons": [],
        "quality": {},
        "stages": [],
    }
    if not isinstance(packet, dict) or packet.get("schema_version") != 1:
        raise ValueError("Expected sensor-observation schema_version 1")
    if packet.get("source") not in ("synthetic", "recorded"):
        raise ValueError("source must be synthetic or recorded")
    result.update(
        shot_id=str(packet.get("shot_id", "imported"))[:100],
        source=packet["source"],
        label=str(packet.get("label", "Imported capture"))[:100],
    )
    try:
        timing = packet["timing"]
        if timing.get("verified") is not True:
            raise MeasurementRejected("Timing is unverified; no measurements released")
        error_us = float(timing["uncertainty_us"])
        if not np.isfinite(error_us) or not 0 <= error_us <= 10:
            raise MeasurementRejected("Timing uncertainty exceeds 10 µs")
        cameras, observations = packet["cameras"], packet["observations"]
        if len(cameras) != 2 or not 3 <= len(observations) <= 16:
            raise ValueError("Require two camera calibrations and 3–16 observations")
        times = finite_array(
            [o["timestamp_us"] for o in observations],
            (len(observations),),
            "timestamps",
        )
        times = (times - times[0]) * 1e-6
        if np.any(np.diff(times) <= 0):
            raise MeasurementRejected("Pulse timestamps must be strictly increasing")
        # Check sampling before doing any inference, including the maximum spin bound.
        for dt in np.diff(times):
            angular_velocity(
                np.eye(3), dt, max_rpm=10000, timing_error_s=error_us * 1e-6
            )
        result["stages"].append("Pico timing verified")
        centres, poses, dot_counts = [], [], []
        for index, calibration in enumerate(cameras):
            r, t = (
                rotation(calibration["R"]),
                finite_array(calibration["t_m"], (3,), "translation"),
            )
            points, orientations = [], []
            for observation in observations:
                if len(observation["views"]) != 2:
                    raise ValueError(
                        "Every observation requires exactly two camera views"
                    )
                view = observation["views"][index]
                count = view["matched_dots"]
                residual = float(view["orientation_residual_rad"])
                if (
                    not isinstance(count, int)
                    or not 4 <= count <= 16
                    or not np.isfinite(residual)
                    or not 0 <= residual <= 0.055
                    or view.get("pattern_unique") is not True
                ):
                    raise MeasurementRejected(
                        "Dot pattern has insufficient or ambiguous matches"
                    )
                points.append(
                    r.T
                    @ (finite_array(view["center_camera_m"], (3,), "ball centre") - t)
                )
                orientations.append(r.T @ rotation(view["ball_to_camera"]))
                dot_counts.append(count)
            centres.append(points)
            poses.append(orientations)
        centres, poses = np.array(centres), np.array(poses)
        disagreement = np.linalg.norm(centres[0] - centres[1], axis=1)
        result["quality"]["max_stereo_error_mm"] = float(disagreement.max() * 1000)
        if disagreement.max() > 0.003:
            raise MeasurementRejected("Camera positions disagree by more than 3 mm")
        # Independent orientation calibrations must also agree, not just their deltas.
        from scipy.spatial.transform import Rotation

        pose_error = Rotation.from_matrix(
            poses[0] @ poses[1].transpose(0, 2, 1)
        ).magnitude()
        if pose_error.max() > 0.08:
            raise MeasurementRejected("Camera ball orientations disagree")
        result["stages"].append("Stereo geometry and dot quality checked")
        positions = centres.mean(axis=0)
        velocity = estimate_velocity(positions, times)
        residuals = positions - (
            positions.mean(axis=0) + (times - times.mean())[:, None] * velocity
        )
        rms = float(np.sqrt(np.mean(np.sum(residuals**2, axis=1))))
        if rms > 0.0015:
            raise MeasurementRejected("Ball motion is inconsistent across observations")
        omegas = []
        for i, dt in enumerate(np.diff(times)):
            pair = [
                angular_velocity(p[i + 1] @ p[i].T, dt, 10000, error_us * 1e-6)
                for p in poses
            ]
            if np.linalg.norm(pair[0] - pair[1]) * dt > 0.04:
                raise MeasurementRejected("Camera spin measurements disagree")
            omegas.append(np.mean(pair, axis=0))
        omega = np.average(omegas, axis=0, weights=np.diff(times))
        if np.max(np.linalg.norm(omegas - omega, axis=1)) > max(
            25, 0.1 * np.linalg.norm(omega)
        ):
            raise MeasurementRejected("Spin changes inconsistently between pulses")
        launch = launch_metrics(velocity)
        if (
            not 1 <= launch["speed_m_s"] <= 100
            or not 0 < launch["launch_angle_deg"] < 85
        ):
            raise MeasurementRejected("Launch is outside the supported flight envelope")
        result.update(
            status="accepted",
            launch=launch,
            spin=spin_metrics(omega),
            velocity_world_m_s=velocity.tolist(),
            positions_world_m=positions.tolist(),
        )
        result["quality"].update(
            position_fit_rms_mm=rms * 1000,
            matched_dots_min=min(dot_counts),
            observation_count=len(times),
            pulse_spacing_us=(np.diff(times) * 1e6).tolist(),
            timing_uncertainty_us=error_us,
        )
        result["stages"].append("Launch and spin calculated from observations")
        result["flight"] = project_flight(
            velocity, omega, conditions or FlightConditions()
        )
        result["stages"].append("Flight and surface estimates integrated")
    except MeasurementRejected as exc:
        result["reasons"].append(str(exc))
    result["processing_ms"] = (time.perf_counter() - started) * 1000
    return result
