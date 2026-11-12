import json

import pytest

from fairway_fusion.hardware.pico_client import PicoClient, PicoError, PicoRejected
from fairway_fusion.web.app import create_app


class Pico:
    firmware = PicoClient.CAPTURE_FIRMWARE

    def __init__(self):
        self.calls = []
        self.state = "idle"
        self.fail = None

    def capture_command(self, command, capture_id, *timing):
        self.calls.append(command)
        if command == self.fail:
            raise PicoError("USB disconnected")
        if command == "ARM":
            self.state = "armed"
        if command == "REPORT":
            return {"capture_id": capture_id, "status": self.state, "error_code": None}

    def command(self, command):
        self.calls.append(command)
        if command == self.fail:
            raise PicoError("USB disconnected")
        if command == "DISARM" and self.state != "completed":
            self.state = "cancelled"
        return "DISARMED"

    def close(self):
        pass


@pytest.fixture
def setup(tmp_path):
    pico = Pico()
    app = create_app({"TESTING": True, "DATABASE_PATH": str(tmp_path / "shots.db"),
                      "PICO_CLIENT": pico})
    return app.test_client(), pico, app.extensions["shot_store"]


def test_arm_poll_cancel_and_read_saved_capture(setup):
    client, pico, store = setup
    response = client.post("/api/captures/arm", json={})
    assert response.status_code == 201
    record = response.get_json()["capture"]
    key = record["capture_id"]
    assert record["status"] == "armed"
    assert store.get_capture(key) == record
    assert client.get(f"/api/captures/{key}").get_json()["capture"]["status"] == "armed"
    response = client.post(f"/api/captures/{key}/cancel")
    assert response.status_code == 200
    assert response.get_json()["capture"]["status"] == "cancelled"
    assert "DISARM" in pico.calls
    count = len(pico.calls)
    assert client.get(f"/api/captures/{key}").get_json()["capture"]["status"] == "cancelled"
    assert len(pico.calls) == count
    assert client.post("/api/captures/arm", json={}).status_code == 201


def test_second_arm_returns_conflict_without_another_capture(setup):
    client, pico, store = setup
    key = client.post("/api/captures/arm", json={}).get_json()["capture"]["capture_id"]
    response = client.post("/api/captures/arm", json={})
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "capture_busy"
    assert response.get_json()["active_capture_id"] == key
    assert len(store.list_captures()) == 1
    assert pico.calls == ["CONFIG", "ARM"]


@pytest.mark.parametrize("firmware", [None, "PICO2W_USB_1"])
def test_arm_requires_connected_capture_firmware(setup, firmware):
    client, pico, store = setup
    pico.firmware = firmware
    response = client.post("/api/captures/arm", json={})
    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "pico_unavailable"
    assert not store.list_captures()
    assert not pico.calls


@pytest.mark.parametrize("command", ["CONFIG", "ARM", "REPORT", "DISARM"])
def test_usb_failure_returns_capture_id_for_recovery(setup, command):
    client, pico, store = setup
    if command in {"CONFIG", "ARM"}:
        pico.fail = command
        response = client.post("/api/captures/arm", json={})
    else:
        key = client.post("/api/captures/arm", json={}).get_json()["capture"]["capture_id"]
        pico.fail = command
        response = (client.get(f"/api/captures/{key}") if command == "REPORT"
                    else client.post(f"/api/captures/{key}/cancel"))
    assert response.status_code == 503
    key = response.get_json()["active_capture_id"]
    assert store.get_capture(key)["status"] == "failed"
    pico.fail = None
    assert client.post(f"/api/captures/{key}/cancel").status_code == 200


@pytest.mark.parametrize("payload", [[], None, {"width_us": 11}, {"camera_us": True},
                                     {"unknown": 1}])
def test_invalid_arm_input_does_not_contact_hardware(setup, payload):
    client, pico, store = setup
    response = client.post("/api/captures/arm", data=json.dumps(payload),
                           content_type="application/json")
    assert response.status_code == 400
    assert not pico.calls
    assert not store.list_captures()


@pytest.mark.parametrize("method,suffix", [("get", ""), ("post", "/cancel")])
def test_missing_or_invalid_capture(setup, method, suffix):
    client, pico, _ = setup
    request = getattr(client, method)
    assert request(f"/api/captures/invalid{suffix}").status_code == 400
    assert request(f"/api/captures/{'a' * 32}{suffix}").status_code == 404
    assert not pico.calls


def test_cancel_preserves_completed_waveform(setup):
    client, pico, _ = setup
    key = client.post("/api/captures/arm", json={}).get_json()["capture"]["capture_id"]
    pico.state = "completed"
    response = client.post(f"/api/captures/{key}/cancel")
    assert response.get_json()["capture"]["status"] == "completed"


def test_firmware_rejection_returns_conflict(setup, monkeypatch):
    client, pico, store = setup

    def reject(*args):
        raise PicoRejected("GATE_NOT_QUIET")

    monkeypatch.setattr(pico, "capture_command", reject)
    response = client.post("/api/captures/arm", json={})
    assert response.status_code == 409
    assert response.get_json()["error"] == {
        "code": "pico_rejected", "message": "GATE_NOT_QUIET",
    }
    assert store.get_capture(response.get_json()["active_capture_id"])["status"] == "failed"
