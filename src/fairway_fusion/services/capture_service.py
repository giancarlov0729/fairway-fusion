from copy import deepcopy
from threading import Lock
from typing import Protocol
from uuid import uuid4

from fairway_fusion.hardware.pico_client import PicoError


class CaptureStore(Protocol):
    def create(self, record: dict) -> None: ...
    def get(self, capture_id: str) -> dict: ...
    def update(self, capture_id: str, **changes) -> None: ...


class MemoryCaptureStore:
    def __init__(self):
        self._records = {}
        self._lock = Lock()

    def create(self, record):
        with self._lock:
            key = record["capture_id"]
            if key in self._records:
                raise ValueError("Capture already exists.")
            self._records[key] = deepcopy(record)

    def get(self, capture_id):
        with self._lock:
            return deepcopy(self._records[capture_id])

    def update(self, capture_id, **changes):
        with self._lock:
            if "capture_id" in changes:
                raise ValueError("Capture ID cannot change.")
            self._records[capture_id].update(deepcopy(changes))


class CaptureService:
    def __init__(self, pico, store: CaptureStore):
        self.pico = pico
        self.store = store
        self._active = None
        self._lock = Lock()

    @property
    def active_capture_id(self):
        with self._lock:
            return self._active

    def arm(self, camera_us=5000, width_us=5, interval_us=1000, delay_us=500):
        values = (camera_us, width_us, interval_us, delay_us)
        if (any(type(v) is not int for v in values)
                or not 1 <= camera_us <= 20000 or not 1 <= width_us <= 10
                or not 100 <= interval_us <= 10000 or not 1 <= delay_us <= 10000
                or camera_us <= delay_us + interval_us + width_us):
            raise ValueError("Invalid capture timing.")
        with self._lock:
            if self._active is not None:
                raise ValueError("A capture is already active; resolve it first.")
            capture_id = uuid4().hex
            self.store.create({
                "capture_id": capture_id, "status": "preparing", "error": None,
                "timing": dict(zip(("camera_trigger_us", "flash_width_us",
                                    "flash_interval_us", "first_flash_delay_us"), values)),
                "timing_source": "commanded", "pico_report": None,
            })
            self._active = capture_id
            try:
                self.pico.capture_command("CONFIG", capture_id, *values)
                self.pico.capture_command("ARM", capture_id)
                self.store.update(capture_id, status="armed")
            except PicoError as error:
                self.store.update(capture_id, status="failed", error=str(error))
                raise
            return self.store.get(capture_id)

    def poll(self, capture_id):
        with self._lock:
            if capture_id != self._active:
                return self.store.get(capture_id)
            try:
                report = self.pico.capture_command("REPORT", capture_id)
                state = report.get("status")
                if (report.get("capture_id") != capture_id or state not in
                        {"configured", "armed", "capturing", "completed", "cancelled", "timed_out", "failed"}):
                    raise PicoError("Invalid capture report.")
                self.store.update(capture_id, status="preparing" if state == "configured" else state,
                                  pico_report=report, error=report.get("error_code"))
                if state in {"completed", "cancelled", "timed_out", "failed"}:
                    self._active = None
            except PicoError as error:
                self.store.update(capture_id, status="failed", error=str(error))
                raise
            return self.store.get(capture_id)

    def cancel(self, capture_id):
        with self._lock:
            if capture_id != self._active:
                return self.store.get(capture_id)
            try:
                self.pico.command("DISARM")
            except PicoError as error:
                self.store.update(capture_id, status="failed", error=str(error))
                raise
        return self.poll(capture_id)
