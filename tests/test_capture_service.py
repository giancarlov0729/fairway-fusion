import pytest

from fairway_fusion.hardware.pico_client import PicoError
from fairway_fusion.services.capture_service import CaptureService, MemoryCaptureStore


class Pico:
    def __init__(self, store):
        self.store = store
        self.calls = []
        self.state = "configured"
        self.fail = None

    def capture_command(self, command, capture_id, *timing):
        assert self.store.get(capture_id)
        self.calls.append((command, capture_id, timing))
        if command == self.fail:
            raise PicoError("USB timeout")
        if command == "ARM":
            self.state = "armed"
        if command == "REPORT":
            return {"capture_id": capture_id, "status": self.state, "error_code": "none"}

    def command(self, command):
        assert command == "DISARM"
        if self.fail == command:
            raise PicoError("USB timeout")
        if self.state != "completed":
            self.state = "cancelled"


@pytest.fixture
def setup():
    store = MemoryCaptureStore()
    pico = Pico(store)
    return CaptureService(pico, store), pico, store


def test_arm_record_and_single_active(setup):
    service, pico, store = setup
    record = service.arm()
    key = record["capture_id"]
    assert len(key) == 32 and int(key, 16) >= 0
    assert record["status"] == "armed"
    assert [call[0] for call in pico.calls] == ["CONFIG", "ARM"]
    assert pico.calls[0][2] == (5000, 5, 1000, 500)
    with pytest.raises(ValueError, match="already active"):
        service.arm()
    record["timing"]["flash_width_us"] = 999
    assert store.get(key)["timing"]["flash_width_us"] == 5


@pytest.mark.parametrize("state", ["completed", "cancelled", "timed_out", "failed"])
def test_terminal_report_releases_capture(setup, state):
    service, pico, _ = setup
    key = service.arm()["capture_id"]
    pico.state = "capturing"
    assert service.poll(key)["status"] == "capturing"
    pico.state = state
    assert service.poll(key)["status"] == state
    assert service.active_capture_id is None


@pytest.mark.parametrize("failure", ["CONFIG", "ARM", "REPORT", "DISARM"])
def test_transport_failure_keeps_capture_reserved(setup, failure):
    service, pico, store = setup
    if failure in {"CONFIG", "ARM"}:
        pico.fail = failure
        with pytest.raises(PicoError):
            service.arm()
    else:
        key = service.arm()["capture_id"]
        pico.fail = failure
        with pytest.raises(PicoError):
            (service.poll if failure == "REPORT" else service.cancel)(key)
    key = service.active_capture_id
    assert store.get(key)["status"] == "failed"
    with pytest.raises(ValueError):
        service.arm()
    pico.fail = None
    assert service.cancel(key)["status"] == "cancelled"
    assert service.active_capture_id is None


@pytest.mark.parametrize("finished", [False, True])
def test_cancel_preserves_already_completed_capture(setup, finished):
    service, pico, _ = setup
    key = service.arm()["capture_id"]
    if finished:
        pico.state = "completed"
    assert service.cancel(key)["status"] == ("completed" if finished else "cancelled")


def test_invalid_timing_never_contacts_pico(setup):
    service, pico, _ = setup
    for values in [(5000, 11, 1000, 500), (1505, 5, 1000, 500), (True, 5, 1000, 500)]:
        with pytest.raises(ValueError):
            service.arm(*values)
    assert not pico.calls
