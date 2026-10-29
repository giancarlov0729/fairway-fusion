"""Exercise the Pi adapter without pretending to test physical synchronization."""

import sys
from types import SimpleNamespace

import numpy as np
import pytest

from fairway_fusion.cameras.capture import StereoCapture


def test_capture_releases_requests_and_closes_both_cameras(monkeypatch, tmp_path):
    events = []

    class Request:
        def make_array(self, name):
            return np.zeros((60, 60), dtype=np.uint8)

        def get_metadata(self):
            return {"SensorTimestamp": 123, "ExposureTime": 6000, "AnalogueGain": 1}

        def release(self):
            events.append("release")

    class Camera:
        def __init__(self, index):
            self.index = index

        def create_still_configuration(self, **kwargs):
            assert kwargs["queue"] is False
            assert kwargs["controls"]["AeEnable"] is False
            return kwargs

        def configure(self, config):
            self.config = config

        def start(self):
            events.append(("start", self.index))

        def stop(self):
            events.append(("stop", self.index))

        def close(self):
            events.append(("close", self.index))

        def capture_request(self, wait):
            assert wait is False
            events.append(("queue", self.index))
            return self.index

        def wait(self, job, timeout):
            assert ("queue", 0) in events and ("queue", 1) in events
            return Request()

        def camera_configuration(self):
            return self.config

    monkeypatch.setitem(sys.modules, "picamera2", SimpleNamespace(Picamera2=Camera))
    with StereoCapture(size=(60, 40)) as cameras:
        jobs = cameras.queue()
        metadata = cameras.save(jobs, tmp_path)
    assert len(metadata) == 2
    assert events.count("release") == 2
    assert ("close", 0) in events and ("close", 1) in events
    assert (tmp_path / "camera0.png").exists()


def test_failed_start_closes_device(monkeypatch):
    closed = []

    class Camera:
        def __init__(self, index):
            pass

        def create_still_configuration(self, **kwargs):
            raise RuntimeError("sensor missing")

        def close(self):
            closed.append(True)

    monkeypatch.setitem(sys.modules, "picamera2", SimpleNamespace(Picamera2=Camera))
    with pytest.raises(RuntimeError), StereoCapture():
        pass
    assert closed == [True]
