import json
from uuid import uuid4

import pytest

from fairway_fusion.hardware.pico_client import PicoClient, PicoError, PicoRejected
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
