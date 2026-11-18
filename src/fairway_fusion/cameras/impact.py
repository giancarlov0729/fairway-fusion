"""Associate free-running evidence clips with Pico capture reports."""

import json
from queue import Empty, Queue
from threading import Event, Thread
import time
from uuid import uuid4

from fairway_fusion.hardware.pico_client import PicoClient


class ImpactRecorder:
    """Keep all serial waits off the camera thread; save one attempt per arm."""

    def __init__(self, buffer, output, settings, port, *, client_factory=PicoClient, recording_store=None):
        self.buffer, self.output, self.settings = buffer, output, settings
        self.port, self.client_factory = port, client_factory
        self.recording_store = recording_store
        self.state = "READY / DISARMED"
        self.capture_id = None
        self._thread = None
        self._events = Queue()
        self._cancel, self._test = Event(), Event()
        self._folder = None
        self._deadline = None
        self._trigger_ns = None
        self._details = {}

    @property
    def active(self):
        return self._folder is not None

    def _persist(self):
        path = self._folder / "attempt.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._details, indent=2), encoding="utf-8")
        temporary.replace(path)

    def arm(self):
        if self.active:
            return False
        self.capture_id = uuid4().hex
        self._folder = self.output / self.capture_id
        self._folder.mkdir(parents=True, exist_ok=False)
        self._details = {"capture_id": self.capture_id, "status": "arming",
                         "trigger_source": "pico_microphone", "pico_report": None,
                         "timing_verified": False, "synchronized": False}
        self._persist()
        try:
            if self.recording_store:
                self.recording_store.begin(self.capture_id)
        except Exception:
            self._folder = None
            raise
        self._cancel.clear()
        self._test.clear()
        self._trigger_ns = None
        self.state = "ARMING"
        self._thread = Thread(target=self._worker, args=(self.capture_id,), daemon=False)
        self._thread.start()
        return True

    def test_trigger(self):
        if self.state == "PICO ARMED":
            self._test.set()

    def cancel(self):
        if self.active:
            self._cancel.set()
            self.state = "CANCELLING"

    def _worker(self, capture_id):
        pico = self.client_factory(self.port)
        outcome, error, report = "interrupted", None, None
        disarmed = False
        try:
            pico.connect()
            pico.command("DISARM")
            if self._cancel.wait(.3):
                outcome = "cancelled"
                return
            pico.capture_command("CONFIG", capture_id, 5000, 5, 1000, 500)
            pico.capture_command("ARM", capture_id)
            self._events.put(("armed", None, time.monotonic_ns()))
            deadline = time.monotonic() + 12
            triggered = False
            while time.monotonic() < deadline:
                if self._cancel.is_set():
                    pico.command("DISARM")
                if self._test.is_set() and not triggered and not self._cancel.is_set():
                    pico.capture_command("TEST_TRIGGER", capture_id)
                    triggered = True
                    self._events.put(("test_source", None, time.monotonic_ns()))
                report = pico.capture_command("REPORT", capture_id)
                status = report["status"]
                if status in {"capturing", "completed"} and not triggered:
                    triggered = True
                    self._events.put(("impact", None, time.monotonic_ns()))
                if status in {"completed", "cancelled", "timed_out", "failed"}:
                    outcome = status
                    break
                self._cancel.wait(.05)
            else:
                raise TimeoutError("No terminal Pico report within 12 seconds")
        except Exception as exc:
            error = str(exc)
        finally:
            try:
                # Reopen after transport loss to make a best-effort disarm.
                if pico.firmware is None:
                    pico.connect()
                pico.command("DISARM")
                disarmed = pico.command("STATUS") in {
                    "idle", "completed", "cancelled", "timed_out", "failed"}
            except Exception as exc:
                error = (error + "; " if error else "") + "Disarm unconfirmed: " + str(exc)
            finally:
                pico.close()
            self._events.put(("terminal", {"status": outcome, "failure_reason": error,
                              "pico_report": report, "disarm_confirmed": disarmed},
                              time.monotonic_ns()))

    def tick(self, now_ns=None):
        now_ns = time.monotonic_ns() if now_ns is None else now_ns
        while True:
            try:
                kind, payload, observed_ns = self._events.get_nowait()
            except Empty:
                break
            if kind == "armed":
                if not self._cancel.is_set():
                    self.state = "PICO ARMED"
                    self._details["status"] = "armed"
                    self._persist()
                    if self.recording_store:
                        self.recording_store.update(self.capture_id, status="armed")
            elif kind in {"impact", "test_source"}:
                self._trigger_ns = observed_ns
                if kind == "test_source":
                    self._details["trigger_source"] = "pico_test_command"
            elif kind == "terminal":
                self._details.update(payload)
                if payload["failure_reason"]:
                    print("Pico recording:", payload["failure_reason"], flush=True)
                self._trigger_ns = self._trigger_ns or observed_ns
                self._details["host_event_observed_monotonic_ns"] = self._trigger_ns
                self._details["timestamp_note"] = (
                    "Host receipt of Pico report/command; not the physical impact time. "
                    "Pico boot microseconds and camera SensorTimestamp are separate clock domains.")
                self._persist()
                if self.recording_store:
                    self.recording_store.update(
                        self.capture_id, status="capturing",
                        pico_report=payload["pico_report"], error=payload["failure_reason"])
                self._deadline = now_ns + (1_000_000_000 if payload["status"] == "completed" else 0)
                self.state = "RECORDING" if payload["status"] == "completed" else payload["status"].upper()
        if self._deadline is not None and now_ns >= self._deadline:
            return self._save()
        return None

    def _save(self):
        # FrameBuffer writes manifest last. A failed save keeps attempt.json.
        try:
            manifest = self.buffer.save(self._folder, trigger_ns=self._trigger_ns,
                                        settings=self.settings, attempt=self._details)
            if self.recording_store:
                self.recording_store.finish(self.capture_id, self._folder, manifest)
        except Exception as error:
            self.state = "SAVE FAILED"
            if self.recording_store:
                self.recording_store.update(self.capture_id, status="failed", error=str(error))
            # Keep disk evidence for diagnosis; do not retry writes during close.
            self._folder = None
            self._deadline = None
            raise
        folder = self._folder
        self._folder = None
        self._deadline = None
        self.state = "SAVED: " + self._details["status"]
        return folder, manifest

    def close(self):
        if not self.active:
            return
        self._cancel.set()
        if self._thread is not None:
            self._thread.join()
        # Drain the terminal report, but do not wait for post-event footage on exit.
        self.tick()
        if self.active:
            self._details["recording_interrupted"] = True
            self._persist()
            self._save()
