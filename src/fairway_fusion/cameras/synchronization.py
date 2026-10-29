"""Serial synchronization protocol for the Raspberry Pi Pico timing controller."""

from dataclasses import dataclass
from typing import Any


STROBE_PULSE_US = 10


class SynchronizationError(RuntimeError):
	"""Raised when the Pico does not complete the synchronization protocol."""


@dataclass(frozen=True)
class TriggerEvent:
	"""A camera trigger acknowledged by the Pico, with its hardware timestamp."""

	camera_id: str
	timestamp_us: int
	pulse_width_us: int


class PicoSynchronizer:
	"""Communicate with a Pico using an injected pySerial-compatible connection.

	The line protocol is ``HELLO`` / ``READY`` for initialization and
	``TRIGGER,<camera_id>,<pulse_width_us>`` / 
	``TRIGGERED,<camera_id>,<timestamp_us>,<pulse_width_us>`` for capture.
	"""

	def __init__(self, serial_connection: Any) -> None:
		self._serial = serial_connection
		self._initialized = False

	@classmethod
	def connect(
		cls,
		port: str,
		baudrate: int = 115200,
		timeout: float = 1.0,
	) -> "PicoSynchronizer":
		"""Open a serial port; importing pySerial is deferred until hardware use."""
		try:
			import serial
		except ImportError as exc:
			raise RuntimeError("pyserial is required to connect to the Pico") from exc

		connection = serial.Serial(port=port, baudrate=baudrate, timeout=timeout)
		return cls(connection)

	def initialize(self) -> None:
		"""Perform the Pi-to-Pico readiness handshake."""
		self._write_line("HELLO")
		response = self._read_line()
		if response != "READY":
			raise SynchronizationError(
				f"Pico handshake failed: expected READY, received {response!r}"
			)
		self._initialized = True

	def trigger_camera(self, camera_id: str) -> TriggerEvent:
		"""Trigger a camera with a 10 us strobe and return the Pico timestamp."""
		if not self._initialized:
			raise SynchronizationError("initialize the Pico synchronizer first")
		if not camera_id or "," in camera_id or "\n" in camera_id or "\r" in camera_id:
			raise ValueError("camera_id must be non-empty and contain no commas or newlines")

		self._write_line(f"TRIGGER,{camera_id},{STROBE_PULSE_US}")
		response = self._read_line()
		fields = response.split(",")
		if len(fields) != 4 or fields[0] != "TRIGGERED":
			raise SynchronizationError(f"invalid Pico trigger response: {response!r}")

		_, acknowledged_camera, timestamp_text, pulse_width_text = fields
		if acknowledged_camera != camera_id:
			raise SynchronizationError(
				f"Pico acknowledged camera {acknowledged_camera!r}, expected {camera_id!r}"
			)
		try:
			timestamp_us = int(timestamp_text)
			pulse_width_us = int(pulse_width_text)
		except ValueError as exc:
			raise SynchronizationError(
				f"invalid numeric value in Pico trigger response: {response!r}"
			) from exc
		if timestamp_us < 0:
			raise SynchronizationError("Pico timestamp must be non-negative")
		if pulse_width_us != STROBE_PULSE_US:
			raise SynchronizationError(
				f"Pico reported a {pulse_width_us} us strobe; "
				f"expected {STROBE_PULSE_US} us"
			)

		return TriggerEvent(camera_id, timestamp_us, pulse_width_us)

	def close(self) -> None:
		"""Close the underlying serial connection."""
		self._serial.close()

	def _write_line(self, message: str) -> None:
		self._serial.write(f"{message}\n".encode("ascii"))
		flush = getattr(self._serial, "flush", None)
		if flush is not None:
			flush()

	def _read_line(self) -> str:
		response = self._serial.readline()
		if not response:
			raise SynchronizationError("timed out waiting for a Pico response")
		try:
			return response.decode("ascii").strip()
		except (AttributeError, UnicodeDecodeError) as exc:
			raise SynchronizationError("Pico response must be ASCII bytes") from exc
