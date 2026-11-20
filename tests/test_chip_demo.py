from collections import deque
from pathlib import Path
import runpy
from threading import Event
import time

import cv2
import numpy as np
import pytest

from fairway_fusion.cameras.bench import Frame, FrameBuffer
from fairway_fusion.cameras.chip import (
    TeeDeparture, calibrate_plane, measure_ball_scaled_flight, measure_chip, measure_image_motion,
    recover_short_chip,
)
from fairway_fusion.cameras.impact import ImpactRecorder
from fairway_fusion.hardware.pico_client import PicoRejected


@pytest.mark.parametrize("event_source", ["pico_microphone", "pico_test_command"])
def test_chip_results_from_frames_use_sensor_time_and_require_microphone(tmp_path, monkeypatch, event_source):
    corners = [(40, 40), (340, 40), (340, 190), (40, 190)]
    event_ns = 2_000_000_000
    buffer = FrameBuffer()
    for n in range(-35, 9):
        image = np.zeros((220, 380), dtype=np.uint8)
        # 500 px/m, 20 ms sensor steps: 2 m/s forward and 1 m/s up.
        # Host receipt lags impact by 60 ms and its cadence differs from the sensor.
        step = max(0, n + 3)
        if step < 8:
            cv2.circle(image, (75 + 20 * step, 170 - 10 * step), 10, 240, -1)
        buffer.append(Frame(0, event_ns + n * 21_000_000, image,
                            {"SensorTimestamp": event_ns + n * 20_000_000}, None))
    analyze = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/camera_bench.py"))

    def measure(frames, observed_ns):
        analyze["analyze_frames"](frames, {}, 6, 16, {})
        return measure_chip(frames, 0, observed_ns, corners, .60, .30)

    impact = Event()
    arm_requests = []

    class Pico:
        firmware = "PICO2W_CAPTURE_1"

        def connect(self):
            return self.firmware

        def command(self, name):
            return "completed" if name == "STATUS" else "DISARMED"

        def capture_command(self, name, capture_id, *timing):
            if name == "ARM":
                arm_requests.append(capture_id)
                if len(arm_requests) < 3:
                    raise PicoRejected("GATE_NOT_QUIET")
            if name == "TEST_TRIGGER":
                impact.set()
            if name == "REPORT":
                return {"status": "completed" if impact.is_set() else "armed",
                        "capture_id": capture_id}

        def close(self):
            pass

    monkeypatch.setattr(time, "monotonic_ns", lambda: event_ns)
    recorder = ImpactRecorder(buffer, tmp_path, {}, "fake", measure=measure,
                              client_factory=lambda port: Pico())
    arm_evidence = {"verification": "two_view_appearance", "position_m": None}
    assert recorder.arm(ball_lock=arm_evidence)
    timeout = time.monotonic() + 2
    while recorder.state != "PICO ARMED" and time.monotonic() < timeout:
        recorder.tick()
        time.sleep(.01)
    assert recorder.state == "PICO ARMED"
    assert recorder.armed_at_ns == event_ns
    assert arm_requests == [recorder.capture_id] * 3
    if event_source == "pico_microphone":
        impact.set()
    else:
        recorder.test_trigger()
    recorder._thread.join(timeout=2)
    assert not recorder._thread.is_alive()
    recorder.tick(event_ns)
    _, manifest = recorder.tick(event_ns + 1_000_000_000)
    assert manifest["trigger_source"] == event_source
    assert manifest["pico_report"]["capture_id"] == manifest["capture_id"]
    assert manifest["ball_lock"] == arm_evidence
    if event_source == "pico_test_command":
        assert manifest["metrics"] is None
        assert "measurement" not in manifest
    else:
        assert manifest["measurement"]["status"] == "estimated"
        assert manifest["metrics"]["speed_m_s"] == pytest.approx(2.236, abs=.09)
        assert manifest["metrics"]["launch_angle_deg"] == pytest.approx(26.565, abs=2)
        assert len(manifest["measurement"]["flight_centers_px"]) == manifest["measurement"]["sample_count"]
        assert "launch_direction_deg" not in manifest["metrics"]
        stationary = deque(frame for frame in buffer.frames if frame.host_ns < event_ns - 70_000_000)
        assert measure_chip(stationary, 0, event_ns, corners, .60, .30)["metrics"] is None


def test_persistently_busy_gate_preserves_rejection_and_disarms(tmp_path, monkeypatch):
    monkeypatch.setattr("fairway_fusion.cameras.impact.GATE_SETTLE_TIMEOUT_S", .12)
    commands = []

    class Pico:
        firmware = "PICO2W_CAPTURE_1"

        def connect(self):
            return self.firmware

        def command(self, name):
            commands.append(name)
            return "cancelled" if name == "STATUS" else "DISARMED"

        def capture_command(self, name, capture_id, *timing):
            commands.append(name)
            if name == "ARM":
                raise PicoRejected("GATE_NOT_QUIET")

        def close(self):
            pass

    recorder = ImpactRecorder(FrameBuffer(), tmp_path, {}, "fake", client_factory=lambda port: Pico())
    recorder.arm()
    recorder._thread.join(timeout=2)
    assert not recorder._thread.is_alive()
    _, manifest = recorder.tick()
    assert commands.count("CONFIG") == 1
    assert commands.count("ARM") >= 2
    assert commands[-2:] == ["DISARM", "STATUS"]
    assert manifest["status"] == "failed"
    assert manifest["pico_rejection"] == "GATE_NOT_QUIET"
    assert manifest["disarm_confirmed"]
    assert manifest["metrics"] is None


def test_crossed_calibration_is_rejected():
    with pytest.raises(ValueError, match="four distinct corners"):
        calibrate_plane([(40, 40), (340, 190), (340, 40), (40, 190)], .60, .30)


def test_image_motion_rejects_early_flight_that_jumps_to_the_club():
    # Observations from the lab recording 273fe966. After the first true
    # detection, the tracker marked the shaft and background near the club.
    stamps = [2269366133000, 2269399437000, 2269466045000, 2269482699000]
    centers = [(237, 254), (390, 214), (552, 223), (611, 231)]
    assert measure_image_motion(0, stamps, centers) is None


def test_two_point_motion_reports_only_average_image_displacement():
    motion = measure_image_motion(1, [2546864565000, 2546881215000], [(266, 150), (340, 102)])
    assert motion["sample_count"] == 2
    assert motion["fit_max_error_px"] is None
    assert motion["speed_px_s"] == pytest.approx(5297, abs=2)
    assert motion["angle_deg"] == pytest.approx(33, abs=.1)


def test_automatic_ball_scale_uses_sensor_timing_and_rejects_a_late_jump():
    tee = {"center": (200, 250), "radius": 20}
    track = {
        "flight_sensor_timestamps_ns": [1_020_000_000, 1_040_000_000, 1_060_000_000, 1_080_000_000],
        "flight_centers_px": [(240, 230), (280, 210), (320, 190), (340, 130)],
        "flight_radii_px": [20, 21, 20, 20],
    }
    shot = measure_ball_scaled_flight(0, track, tee)
    assert shot["method"] == "ball_diameter_side_view"
    assert shot["status"] == "estimated"
    assert shot["sample_count"] == 3
    assert shot["metrics"]["speed_m_s"] == pytest.approx(2.385, abs=.001)
    assert shot["metrics"]["launch_angle_deg"] == pytest.approx(26.565, abs=.001)
    assert shot["flight_sensor_timestamps_ns"] == track["flight_sensor_timestamps_ns"][:3]
    assert shot["scale_ball_diameter_px"] == 40


@pytest.mark.parametrize(("centers", "radii"), [
    ([(240, 230), (280, 210)], [20, 20]),
    ([(240, 100), (280, 80), (320, 60)], [20, 20, 20]),
    ([(240, 230), (280, 210), (320, 190)], [20, 30, 40]),
])
def test_automatic_scale_requires_a_consistent_departure(centers, radii):
    track = {
        "flight_sensor_timestamps_ns": [1_020_000_000 + i * 20_000_000 for i in range(len(centers))],
        "flight_centers_px": centers,
        "flight_radii_px": radii,
    }
    shot = measure_ball_scaled_flight(0, track, {"center": (200, 250), "radius": 20})
    assert shot["status"] == "rejected"
    assert shot["metrics"] is None


def test_ball_scale_on_lab_recording_uses_corrected_airborne_positions():
    # Camera A of c60ef952, re-tracked from the original Pi frames.
    track = {
        "flight_sensor_timestamps_ns": [2546854573000, 2546871225000, 2546887876000, 2546904528000],
        "flight_centers_px": [(300, 186), (376, 134), (440, 92), (497, 64)],
        "flight_radii_px": [24, 24, 25, 20],
    }
    shot = measure_ball_scaled_flight(0, track, {"center": (185, 280), "radius": 25})
    assert shot["sample_count"] == 4
    assert shot["metrics"]["speed_m_s"] == pytest.approx(3.955, abs=.002)
    assert shot["metrics"]["launch_angle_deg"] == pytest.approx(31.919, abs=.002)


@pytest.mark.parametrize("rising", [False, True])
def test_camera_trigger_needs_a_stationary_ball_then_forward_rise(tmp_path, rising):
    corners = [(40, 40), (340, 40), (340, 190), (40, 190)]
    detector = TeeDeparture(corners, .6, .3)
    buffer = FrameBuffer()
    stamp = 1_000_000_000
    event = None
    for i in range(27):
        flight_steps = max(0, i - 20)
        ball = {"center": (75 + 20 * flight_steps, 170 - 10 * flight_steps * rising), "radius": 10}
        host_ns = stamp + i * 20_000_000
        if event is None:
            event = detector.observe(host_ns, ball)
        if i == 20:
            assert detector.ready
        buffer.append(Frame(0, host_ns, np.zeros((8, 8), np.uint8), {"SensorTimestamp": host_ns}, ball))
    if not rising:
        assert event is None
        return
    assert event == stamp + 21 * 20_000_000
    measurement = measure_chip(buffer.frames, 0, event, corners, .6, .3)
    manifest = buffer.save(tmp_path / "vision", trigger_ns=event, settings={}, measurement=measurement)
    assert manifest["trigger_source"] == "camera_tee_departure"
    assert manifest["metrics"]["speed_m_s"] == pytest.approx(2.236, abs=.01)
    assert manifest["metrics"]["launch_angle_deg"] == pytest.approx(26.565, abs=.01)


@pytest.mark.parametrize("airborne_frames", [2, 3])
def test_short_flight_recovery_uses_motion_instead_of_stale_tee_lock(airborne_frames):
    corners = [(40, 40), (340, 40), (340, 190), (40, 190)]
    event = 1_000_000_000
    frames = []
    for i in range(-40, 10):
        image = np.full((240, 400), 55, dtype=np.uint8)
        cv2.circle(image, (245, 94), 20, 155, -1)
        if i < 0:
            cv2.circle(image, (85, 155), 20, 225, -1)
        if 0 <= i < airborne_frames:
            cv2.circle(image, (120 + 35 * i, 138 - 17 * i), 20, 225, -1)
        now = event + i * 16_666_667
        frames.append(Frame(0, now, image, {"SensorTimestamp": now}, {"center": (85, 155), "radius": 20}))
    rejected = measure_chip(frames, 0, event, corners, .6, .3)
    assert rejected["metrics"] is None
    result = recover_short_chip(frames, event, rejected, (40, 110, 90, 90), 56)
    if airborne_frames == 2:
        assert result["metrics"] is None
    else:
        assert result["sample_count"] == 3
        assert "reason" not in result
        assert result["metrics"]["speed_m_s"] == pytest.approx(4.67, abs=.25)
        assert result["flight_centers_px"][-1] == pytest.approx((190, 104), abs=3)
