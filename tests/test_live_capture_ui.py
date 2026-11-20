import re
from threading import Event
import time
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from fairway_fusion.cameras.recording_store import read_manifest
from fairway_fusion.services.recording_review import recording_measurement, review_frames
from fairway_fusion.web.app import create_app


def submit(client, action, **fields):
    page = client.get("/live").text
    token = re.search(r'name="token" value="([^"]+)"', page)[1]
    return client.post(f"/actions/live-{action}", data={"token": token, **fields})


def until(worker, check):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        state = worker.snapshot()
        if check(state):
            return state
        time.sleep(.02)
    pytest.fail(f"Timed out waiting for live state: {worker.snapshot()}")


@pytest.fixture
def rig(tmp_path, monkeypatch):
    class Rig:
        index = 0
        current = ()
        ball = Event()
        launch_at = None
        opened, closed, picos = [], [], []

    # Preserve realistic frame intervals even when the test runner pauses a
    # thread. The simulated flight advances by one position per frame.
    monkeypatch.setattr(time, "monotonic_ns", lambda: 5_000_000_000 + Rig.index * 16_666_667)

    class Camera:
        def __init__(self, number):
            self.number = number
            Rig.opened.append(number)

        def create_video_configuration(self, **settings):
            return settings

        def configure(self, settings):
            assert settings["main"] == {"size": (640, 480), "format": "YUV420"}

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            Rig.closed.append(self.number)

        def capture_request(self, wait):
            if self.number == 0:
                time.sleep(.015)
                Rig.index += 1
            image = np.full((720, 640), 46, dtype=np.uint8)
            center = (154 + self.number * 65, 293)
            elapsed = Rig.index - Rig.launch_at if Rig.launch_at is not None else -1
            if Rig.ball.is_set() and elapsed <= 4:
                if elapsed >= 0:
                    center = (center[0] + 45 * (elapsed + 1), center[1] - 18 * (elapsed + 1))
                cv2.circle(image, center, 22, 221, -1)
            timestamp = 1_000_000_000 + Rig.index * 16_666_667 + self.number * 1_000_000
            request = SimpleNamespace(make_array=lambda _: image, release=lambda: None,
                                      get_metadata=lambda: {"SensorTimestamp": timestamp, "ExposureTime": 1000})
            return SimpleNamespace(get_result=lambda timeout: request)

    class Pico:
        firmware = "PICO2W_CAPTURE_1"

        def __init__(self, port):
            self.armed = False
            self.status = "idle"
            self.commands = []
            self.launch_before_arm = Rig.launch_at
            Rig.picos.append(self)

        def connect(self):
            return self.firmware

        def close(self):
            pass

        def command(self, name):
            self.commands.append(name)
            if name == "DISARM" and self.status == "armed":
                self.status = "cancelled"
            return self.status

        def capture_command(self, name, capture_id, *parameters):
            self.commands.append(name)
            if name == "ARM":
                self.status = "armed"
            if name == "REPORT":
                if self.status == "armed" and Rig.launch_at != self.launch_before_arm:
                    self.status = "completed"
                return {"capture_id": capture_id, "status": self.status}

    app = create_app({"TESTING": True, "DATABASE_PATH": tmp_path / "live.db", "LIVE_CAPTURE_UI": True,
                      "LIVE_CAMERA_TYPE": Camera, "LIVE_PICO_FACTORY": Pico, "PICO_PORT": "fake"})
    worker = app.extensions["live_capture"]
    yield app, Rig, worker
    worker.close()
    assert sorted(Rig.closed) == sorted(Rig.opened)


def test_browser_runs_the_real_workflow_with_camera_and_pico_fakes(rig, tmp_path):
    app, hardware, worker = rig
    store = app.extensions["shot_store"]
    client = app.test_client()
    landing = client.get("/live")
    assert "Live capture" in landing.text and "<script" not in landing.text
    assert not hardware.opened
    assert submit(client, "start").status_code == 303
    assert submit(client, "arm").status_code == 400
    assert client.post("/api/pico/disarm").status_code == 409
    assert client.post("/api/captures/arm", json={}).status_code == 409
    assert submit(client, "scan").status_code == 303
    until(worker, lambda state: state["calibrated"] and not state["busy"])
    assert client.get("/live/preview.jpg").mimetype == "image/jpeg"
    plane = {"x0": 80, "y0": 145, "x1": 580, "y1": 145, "x2": 580, "y2": 333,
             "x3": 80, "y3": 333, "width_m": .6, "height_m": .3}
    assert submit(client, "plane", **plane).status_code == 303
    until(worker, lambda state: state["measuring"] and not state["busy"])
    assert submit(client, "arm").status_code == 303
    until(worker, lambda state: state["mode"] == "NOT READY" and not state["busy"])
    assert not hardware.picos and not store.list_captures()
    assert "No new ball confirmed" in client.get("/live").text
    hardware.ball.set()
    assert submit(client, "arm").status_code == 303
    assert submit(client, "arm").status_code == 400
    armed = until(worker, lambda state: state["mode"].startswith("ARM SUCCESSFUL"))
    assert "Hit now" in client.get("/live").text
    assert submit(client, "refresh").status_code == 303
    time.sleep(.43)  # Supply enough stationary evidence before the simulated shot.
    hardware.launch_at = hardware.index + 1
    result = until(worker, lambda state: state["last_capture"] == armed["capture_id"] and not state["busy"])
    capture_id = result["last_capture"]
    row = store.get_capture(capture_id)
    _, manifest = read_manifest(store, row)
    assert row["status"] == "completed" and row["session_id"] == store.active_session()["id"]
    assert manifest["measurement"]["metrics"], manifest["measurement"].get("reason")
    assert store.list_shots() == []
    live = client.get("/live")
    assert "Latest shot attempt" in live.text and "Provisional chip estimate" in live.text
    assert f"/recordings/{capture_id}" in live.text
    assert f"/api/recordings/{capture_id}/preview.webm" in live.text
    assert f"/recordings/{capture_id}" in client.get("/recordings").text
    review = client.get(f"/recordings/{capture_id}")
    assert "Used in calculation" in review.text and "/detection" in review.text
    assert "Next" in review.text and "Open original PNG" in review.text

    used_time = manifest["measurement"]["flight_sensor_timestamps_ns"][0]
    frame = next(item for item in manifest["frames"] if item["camera"] == 0
                 and item["metadata"]["SensorTimestamp"] == used_time)
    frame_url = f"/api/recordings/{capture_id}/frames/{frame['file']}"
    with client.get(frame_url) as response:
        original = response.data
    with client.get(f"{frame_url}/detection") as response:
        assert response.status_code == 200 and response.data != original
    with client.get(frame_url) as response:
        assert response.data == original
    with client.get(f"/api/recordings/{capture_id}/preview.webm") as response:
        if response.status_code == 200:
            video = tmp_path / "review.webm"
            video.write_bytes(response.data)
            reader = cv2.VideoCapture(str(video))
            try:
                ok, image = reader.read()
                assert ok and image.shape[:2] == (360, 960)
            finally:
                reader.release()
        else:
            # Some OpenCV distributions omit VP8. The frame review still works.
            assert response.status_code == 404

    hardware.launch_at = None
    assert submit(client, "arm").status_code == 303
    second = until(worker, lambda state: state["mode"].startswith("ARM SUCCESSFUL"))
    assert submit(client, "cancel").status_code == 303
    until(worker, lambda state: state["last_capture"] == second["capture_id"] and not state["busy"])
    assert store.get_capture(second["capture_id"])["status"] == "cancelled"
    assert "DISARM" in hardware.picos[-1].commands


def test_disabled_live_page_and_empty_recordings_do_not_touch_hardware(tmp_path):
    app = create_app({"TESTING": True, "DATABASE_PATH": tmp_path / "disabled.db", "LIVE_CAPTURE_UI": False})
    client = app.test_client()
    assert "Live capture is disabled" in client.get("/live").text
    assert submit(client, "scan").status_code == 400
    assert client.get("/live/preview.jpg").status_code == 404
    assert "No recordings in this view" in client.get("/recordings").text
    assert client.get("/recordings/" + "1" * 32).status_code == 404


def test_live_shot_uses_ball_scale_without_a_manual_plane(rig):
    app, hardware, worker = rig
    store = app.extensions["shot_store"]
    client = app.test_client()
    assert submit(client, "start").status_code == 303
    assert submit(client, "scan").status_code == 303
    until(worker, lambda state: state["calibrated"] and not state["busy"])
    preview = client.get("/live").text
    assert "Keep the cameras level" in preview
    assert "Save side plane" not in preview and "/actions/live-plane" not in preview
    hardware.ball.set()
    assert submit(client, "arm").status_code == 303
    armed = until(worker, lambda state: state["mode"].startswith("ARM SUCCESSFUL"))
    hardware.launch_at = hardware.index + 1
    capture_id = armed["capture_id"]
    until(worker, lambda state: state["last_capture"] == capture_id and not state["busy"])
    _, manifest = read_manifest(store, store.get_capture(capture_id))
    measurement = manifest["measurement"]
    assert measurement["method"] == "ball_diameter_side_view"
    assert measurement["metrics"]["speed_m_s"] == pytest.approx(2.82, rel=.10)
    assert measurement["metrics"]["launch_angle_deg"] == pytest.approx(21.8, abs=2)
    page = client.get("/live").text
    assert "Automatic side-view estimate · Camera" in page
    assert measurement["carry"]["estimated_carry_m"] > 0
    assert "Estimated carry" in page and "yd)" in page
    assert "Estimated speed" in page and "m/s" in page
    assert "Side-view angle" in page and "ball’s diameter at rest" in page
    recording = client.get(f"/recordings/{capture_id}?frames=used")
    assert "Used in calculation" in recording.text and "/detection" in recording.text

    # Old recordings omitted the airborne radii in their summary, but the
    # timestamped frame overlays provide those radii without changing files.
    tracks = measurement["locked_flight_tracks"]
    for track in tracks.values():
        track.pop("flight_radii_px")
    old = {"metrics": None, "reason": "Set a measured side plane before the next shot.",
           "locked_flight_tracks": tracks}
    manifest["measurement"] = old
    legacy = recording_measurement(manifest)
    assert legacy["metrics"] == measurement["metrics"]
    assert old["metrics"] is None
    assert sum(frame["used"] for frame in review_frames(manifest)) == legacy["sample_count"]

    for track in tracks.values():
        if track["flight_centers_px"]:
            track["flight_centers_px"][0][0] += 1
    assert recording_measurement(manifest)["metrics"] is None
