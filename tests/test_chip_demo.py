from collections import deque
from pathlib import Path
import runpy
from threading import Event
import time

import cv2
import numpy as np
import pytest

from fairway_fusion.cameras.bench import Frame, FrameBuffer
from fairway_fusion.cameras.chip import calibrate_plane, measure_chip
from fairway_fusion.cameras.impact import ImpactRecorder


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

    class Pico:
        firmware = "PICO2W_CAPTURE_1"

        def connect(self):
            return self.firmware

        def command(self, name):
            return "completed" if name == "STATUS" else "DISARMED"

        def capture_command(self, name, capture_id, *timing):
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
    assert recorder.arm()
    timeout = time.monotonic() + 2
    while recorder.state == "ARMING" and time.monotonic() < timeout:
        recorder.tick()
        time.sleep(.01)
    assert recorder.state == "PICO ARMED"
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
    if event_source == "pico_test_command":
        assert manifest["metrics"] is None
        assert "measurement" not in manifest
    else:
        assert manifest["measurement"]["status"] == "estimated"
        assert manifest["metrics"]["speed_m_s"] == pytest.approx(2.236, abs=.09)
        assert manifest["metrics"]["launch_angle_deg"] == pytest.approx(26.565, abs=2)
        assert "launch_direction_deg" not in manifest["metrics"]
        stationary = deque(frame for frame in buffer.frames if frame.host_ns < event_ns - 70_000_000)
        assert measure_chip(stationary, 0, event_ns, corners, .60, .30)["metrics"] is None


def test_crossed_calibration_is_rejected():
    with pytest.raises(ValueError, match="four distinct corners"):
        calibrate_plane([(40, 40), (340, 190), (340, 40), (40, 190)], .60, .30)
