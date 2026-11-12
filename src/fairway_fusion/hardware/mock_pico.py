from uuid import uuid4

from fairway_fusion.hardware.pico_client import PicoError


class MockPico:
    def __init__(self):
        self.boot_id = uuid4().hex
        self.capture_id = None
        self.status = "idle"
        self.counter = 0
        self.timing = {}

    def capture_command(self, command, capture_id, *timing):
        if command == "CONFIG":
            self.capture_id = capture_id
            self.status = "configured"
            self.timing = dict(zip(("camera_trigger_us", "flash_width_us",
                                    "flash_interval_us", "first_flash_delay_us"), timing))
        if capture_id != self.capture_id:
            raise PicoError("Mock capture is unavailable after restart.")
        if command == "ARM":
            self.status = "armed"
        if command == "REPORT":
            return {"capture_id": capture_id, "status": self.status,
                    "boot_id": self.boot_id, "trigger_counter": self.counter,
                    "source": "simulation", "timing_source": "commanded",
                    "error_code": None, **self.timing}

    def command(self, command):
        if command == "DISARM" and self.status in {"configured", "armed"}:
            self.status = "cancelled"
        return "DISARMED"

    def simulate_impact(self, capture_id):
        if self.capture_id != capture_id or self.status != "armed":
            raise ValueError("Arm this mock capture before simulating an impact.")
        self.counter += 1
        self.status = "completed"
