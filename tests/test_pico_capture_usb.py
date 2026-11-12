import json
from uuid import uuid4

import pytest
from flask import Flask

from fairway_fusion.hardware.pico_client import PicoClient, PicoError, PicoRejected
from fairway_fusion.web.app import create_app
from fairway_fusion.web.pico_routes import register_pico_routes
from test_pico_usb import FakeSerial


class CaptureSerial(FakeSerial):
    def __init__(self):
        super().__init__()
        self.payload = PicoClient.CAPTURE_FIRMWARE
        self.result = "OK"
        self.request = []

    def write(self, data):
        version, request_id, *self.request = data.decode("ascii").split()
        self.buffer.extend(f"{version} {request_id} {self.result} {self.payload}\n".encode())
        return len(data)


def test_capture_contract():
    device = CaptureSerial()
    client = PicoClient("fake", serial_factory=lambda **kwargs: device)
    assert client.connect() == PicoClient.CAPTURE_FIRMWARE
    capture_id = uuid4().hex
    device.payload = "CONFIGURED"
    assert client.capture_command("CONFIG", capture_id, 5000, 5, 1000, 500) == "CONFIGURED"
    assert device.request == ["CONFIG", capture_id, "5000", "5", "1000", "500"]
    device.result, device.payload = "ERR", "GATE_NOT_QUIET"
    with pytest.raises(PicoRejected, match="GATE_NOT_QUIET"):
        client.capture_command("ARM", capture_id)
    device.result, device.payload = "OK", "ARMED"
    assert client.capture_command("ARM", capture_id) == "ARMED"
    device.payload = "armed"
    assert client.command("STATUS") == "armed"
    record = dict(capture_id=capture_id, firmware_version=client.firmware,
                  status="completed", timing_source="commanded", flash_interval_us=1000)
    device.payload = json.dumps(record, separators=(",", ":"))
    assert client.capture_command("REPORT", capture_id) == record
    with pytest.raises(PicoError, match="Invalid Pico capture report"):
        client.capture_command("REPORT", uuid4().hex)
    assert device.closed


@pytest.mark.parametrize("state", sorted(PicoClient.CAPTURE_STATES))
def test_capture_firmware_flask_lifecycle(state):
    device = CaptureSerial()
    pico = PicoClient("fake", serial_factory=lambda **kwargs: device)
    app = Flask(__name__)
    app.config["PICO_CLIENT"] = pico
    register_pico_routes(app)
    browser = app.test_client()
    try:
        assert browser.get("/api/pico/status").status_code == 503
        response = browser.post("/api/pico/connect")
        assert response.status_code == 200
        assert response.json == {
            "connected": True,
            "firmware": PicoClient.CAPTURE_FIRMWARE,
            "capture_ready": False,
        }
        assert device.request == ["HELLO"]

        device.payload = state
        response = browser.get("/api/pico/status")
        assert response.status_code == 200
        assert response.json == {
            "connected": True, "state": state, "capture_ready": False,
        }
        assert device.request == ["STATUS"]

        device.payload = "DISARMED"
        response = browser.post("/api/pico/disarm")
        assert response.status_code == 200
        assert response.json == {"state": "DISARMED", "capture_ready": False}
        assert device.request == ["DISARM"]
        assert not device.closed

        assert browser.post("/api/pico/disconnect").status_code == 200
        assert device.closed
        assert browser.get("/api/pico/status").status_code == 503
    finally:
        pico.close()


def test_capture_firmware_device_page_controls(tmp_path):
    import re

    device = CaptureSerial()
    pico = PicoClient("fake", serial_factory=lambda **kwargs: device)
    app = create_app({
        "TESTING": True,
        "DATABASE_PATH": tmp_path / "devices.db",
        "PICO_CLIENT": pico,
    })
    browser = app.test_client()

    def action(name):
        page = browser.get("/devices")
        token = re.search(r'name="token" value="([^"]+)"', page.text)[1]
        return browser.post(
            "/actions/" + name, data={"token": token}, follow_redirects=True,
        )

    try:
        assert PicoClient.CAPTURE_FIRMWARE in action("connect").text
        device.payload = "armed"
        response = action("status")
        assert response.status_code == 200
        assert "armed" in response.text
        device.payload = "DISARMED"
        assert "DISARMED" in action("disarm").text
        assert "Disconnected" in action("disconnect").text
        assert device.closed
    finally:
        pico.close()
